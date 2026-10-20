"""Background sync scheduler.

Runs Teller + E*TRADE + approved Plaid sync at a fixed cadence while the FastAPI app is alive.
Uses a plain asyncio task — no extra dependencies. Survives transient errors;
logs failures and keeps going.

Caveats:
  * Only runs while the uvicorn process is up. If the laptop sleeps or the
    server is killed, syncing pauses until the next launch.
  * Single-process design — fine for a single-user local app.
"""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import suppress
from datetime import UTC, datetime

from sqlmodel import Session, select

from mypocket.core.db import engine
from mypocket.domain.models import Enrollment
from mypocket.integrations import etrade_sync, plaid_sync, teller_sync
from mypocket.integrations._errors import safe_error

logger = logging.getLogger(__name__)

# Tunable via env so it's easy to override without code changes.
SYNC_INTERVAL_SECONDS = int(os.getenv("MYPOCKET_SYNC_INTERVAL_SECONDS", str(6 * 3600)))
# Wait a few seconds after startup before the first sync so the app comes up
# cleanly (Keychain prompts, route registration, etc. all settle first).
SYNC_INITIAL_DELAY_SECONDS = int(os.getenv("MYPOCKET_SYNC_INITIAL_DELAY_SECONDS", "30"))


def _run_sync_once() -> dict:
    """Sync both providers in one DB session. Returns counts for logging."""
    summary = {"teller": [], "etrade": [], "plaid": [], "started_at": datetime.now(UTC).isoformat()}
    with Session(engine) as session:
        # Read status metadata without decrypting tokens for inactive enrollments.
        summary["inactive"] = [
            f"{e.provider}:{e.status}"
            for e in session.exec(
                select(Enrollment.provider, Enrollment.status).where(Enrollment.status != "active")
            ).all()
        ]
        try:
            summary["teller"] = teller_sync.sync_all(session)
        except Exception as e:
            session.rollback()
            logger.warning("scheduler: teller sync failed: %s", safe_error(e))
            summary["teller_error"] = safe_error(e)
        try:
            summary["etrade"] = etrade_sync.sync_all(session)
        except Exception as e:
            session.rollback()
            logger.warning("scheduler: etrade sync failed: %s", safe_error(e))
            summary["etrade_error"] = safe_error(e)
        try:
            summary["plaid"] = plaid_sync.sync_all(session)
        except Exception as e:
            session.rollback()
            logger.warning("scheduler: plaid sync failed: %s", safe_error(e))
            summary["plaid_error"] = safe_error(e)
    return summary


def _collect_errors(results: list[dict], provider_error: str | None) -> list[str]:
    """Flatten the per-enrollment `errors` lists that sync_all returns, plus any
    provider-level exception recorded by _run_sync_once. Without this the
    scheduler cannot tell "nothing new" apart from "totally broken"."""
    errs = [str(e) for r in results for e in (r.get("errors") or [])]
    if provider_error:
        errs.append(str(provider_error))
    return errs


async def _scheduler_loop() -> None:
    """Sleep, sync, repeat. Sync runs in a thread so blocking httpx calls
    don't stall the event loop."""
    logger.info(
        "scheduler: starting; first sync in %ds, then every %ds",
        SYNC_INITIAL_DELAY_SECONDS,
        SYNC_INTERVAL_SECONDS,
    )
    await asyncio.sleep(SYNC_INITIAL_DELAY_SECONDS)
    while True:
        try:
            summary = await asyncio.to_thread(_run_sync_once)
            p_results = summary.get("plaid", []) or []
            p_errs = _collect_errors(p_results, summary.get("plaid_error"))
            if p_errs:
                logger.warning("scheduler: plaid sync failed: %s", "; ".join(p_errs)[:300])
            elif p_results:
                logger.info(
                    "scheduler: plaid sync done — +%d tx",
                    sum(r.get("transactions_created", 0) for r in p_results),
                )
            t_results = summary.get("teller", []) or []
            e_results = summary.get("etrade", []) or []
            t_new = sum(r.get("transactions_created", 0) for r in t_results)
            e_new = sum(r.get("transactions_created", 0) for r in e_results)
            t_errs = _collect_errors(t_results, summary.get("teller_error"))
            e_errs = _collect_errors(e_results, summary.get("etrade_error"))
            # A provider that returned zero transactions because it *failed* must
            # not look identical to one that simply had nothing new. Log at
            # WARNING with the reason so a broken enrollment is visible in the log.
            # Deliberately disabled or historical records are not current failures.
            # Active adapters report their own actionable authorization errors.
            if t_errs or e_errs:
                logger.warning(
                    "scheduler: sync completed with errors — "
                    "teller +%d tx (%d error(s): %s), etrade +%d tx (%d error(s): %s)",
                    t_new,
                    len(t_errs),
                    "; ".join(t_errs)[:300] or "none",
                    e_new,
                    len(e_errs),
                    "; ".join(e_errs)[:300] or "none",
                )
            else:
                logger.info(
                    "scheduler: sync done — teller +%d tx, etrade +%d tx",
                    t_new,
                    e_new,
                )
        except Exception as e:
            # Defensive: don't let the loop die.
            logger.warning("scheduler: tick failed unexpectedly: %s", safe_error(e))
        await asyncio.sleep(SYNC_INTERVAL_SECONDS)


def start(loop: asyncio.AbstractEventLoop | None = None) -> asyncio.Task:
    """Schedule the loop on the running event loop. Returns the task handle so
    `main.lifespan` can cancel it on shutdown."""
    loop = loop or asyncio.get_event_loop()
    return loop.create_task(_scheduler_loop(), name="mypocket-sync-scheduler")


async def stop(task: asyncio.Task) -> None:
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task
