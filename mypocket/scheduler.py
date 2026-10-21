"""One serialized background polling task, with separate provider deadlines.

Citi/Plaid uses ordinary cached accounts/get and incremental transactions/sync.
No paid refresh, webhooks, extra Items, services or provider access are enabled.
Polling pauses while the Mac sleeps or this single-process app is stopped.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from datetime import UTC, datetime
from time import monotonic

from sqlmodel import Session, select

from mypocket.core.db import engine
from mypocket.core.sync_schedule import (
    PLAID_MAX_RETRY_SECONDS,
    PLAID_SYNC_INTERVAL_SECONDS,
    SYNC_INITIAL_DELAY_SECONDS,
    SYNC_INTERVAL_SECONDS,
)
from mypocket.domain.models import Enrollment
from mypocket.integrations import etrade_sync, plaid_sync, teller_sync
from mypocket.integrations._errors import safe_error

logger = logging.getLogger(__name__)

PROVIDERS = ("teller", "etrade", "plaid")


def _run_sync_once(providers: tuple[str, ...] = PROVIDERS) -> dict:
    """Only poll due providers; every adapter retains its existing safeguards."""
    summary = {name: [] for name in PROVIDERS}
    summary["started_at"] = datetime.now(UTC).isoformat()
    adapters = {"teller": teller_sync, "etrade": etrade_sync, "plaid": plaid_sync}
    with Session(engine) as session:
        # Status metadata only; no token decryption for inactive connections.
        summary["inactive"] = [
            f"{e.provider}:{e.status}"
            for e in session.exec(
                select(Enrollment.provider, Enrollment.status).where(
                    Enrollment.provider.in_(providers), Enrollment.status != "active"
                )
            ).all()
        ]
        for name in providers:
            try:
                summary[name] = adapters[name].sync_all(session)
            except Exception as exc:
                session.rollback()
                summary[name + "_error"] = safe_error(exc)
    return summary


def _collect_errors(results: list[dict], provider_error: str | None) -> list[str]:
    """Flatten the per-enrollment `errors` lists that sync_all returns, plus any
    provider-level exception recorded by _run_sync_once. Without this the
    scheduler cannot tell "nothing new" apart from "totally broken"."""
    errs = [str(e) for r in results for e in (r.get("errors") or [])]
    if provider_error:
        errs.append(str(provider_error))
    return errs


def _retry_delay(provider: str, failures: int) -> int:
    if provider != "plaid":
        return SYNC_INTERVAL_SECONDS
    # 30m normally; consecutive errors wait 1h, 2h, 4h, then at most 6h.
    # Clamp the exponent as well so long outages never create huge integers.
    return min(PLAID_SYNC_INTERVAL_SECONDS * 2 ** min(failures, 16), PLAID_MAX_RETRY_SECONDS)


async def _scheduler_loop() -> None:
    """Await each batch; schedule from completion, with no overlapping jobs.

    After sleep/wake there is one due batch, never a burst of missed intervals.
    The adapters' existing bank lock also serializes Plaid with manual imports
    and syncs. No separate faster task can overlap the ordinary startup batch.
    """
    logger.info(
        "scheduler: starting in %ds; Citi/Plaid every %ds; other providers every %ds",
        SYNC_INITIAL_DELAY_SECONDS,
        PLAID_SYNC_INTERVAL_SECONDS,
        SYNC_INTERVAL_SECONDS,
    )
    await asyncio.sleep(SYNC_INITIAL_DELAY_SECONDS)
    next_due = {name: monotonic() for name in PROVIDERS}
    failures = {name: 0 for name in PROVIDERS}
    while True:
        now = monotonic()
        due = tuple(name for name in PROVIDERS if next_due[name] <= now)
        if not due:
            await asyncio.sleep(max(0, min(next_due.values()) - now))
            continue
        try:
            summary = await asyncio.to_thread(_run_sync_once, due)
        except Exception as exc:
            summary = {name + "_error": safe_error(exc) for name in due}
        completed = monotonic()
        for name in due:
            errors = _collect_errors(summary.get(name, []) or [], summary.get(name + "_error"))
            failures[name] = failures[name] + 1 if errors else 0
            delay = _retry_delay(name, failures[name])
            next_due[name] = completed + delay
            if errors:
                logger.warning(
                    "scheduler: %s sync failed; next check in %ds: %s", name, delay, "; ".join(errors)[:300]
                )
            elif summary.get(name):
                # Do not add financial counts or payloads to more frequent logs.
                logger.info("scheduler: %s sync completed; next check in %ds", name, delay)


def start(loop: asyncio.AbstractEventLoop | None = None) -> asyncio.Task:
    """Schedule the loop on the running event loop. Returns the task handle so
    `main.lifespan` can cancel it on shutdown."""
    loop = loop or asyncio.get_event_loop()
    return loop.create_task(_scheduler_loop(), name="mypocket-sync-scheduler")


async def stop(task: asyncio.Task) -> None:
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task
