"""Background sync scheduler.

Runs Teller + E*TRADE sync at a fixed cadence while the FastAPI app is alive.
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

from sqlmodel import Session

from mypocket.core.db import engine
from mypocket.integrations import etrade_sync, teller_sync

logger = logging.getLogger(__name__)

# Tunable via env so it's easy to override without code changes.
SYNC_INTERVAL_SECONDS = int(os.getenv("MYPOCKET_SYNC_INTERVAL_SECONDS", str(6 * 3600)))
# Wait a few seconds after startup before the first sync so the app comes up
# cleanly (Keychain prompts, route registration, etc. all settle first).
SYNC_INITIAL_DELAY_SECONDS = int(os.getenv("MYPOCKET_SYNC_INITIAL_DELAY_SECONDS", "30"))


def _run_sync_once() -> dict:
    """Sync both providers in one DB session. Returns counts for logging."""
    summary = {"teller": [], "etrade": [], "started_at": datetime.now(UTC).isoformat()}
    with Session(engine) as session:
        try:
            summary["teller"] = teller_sync.sync_all(session)
        except Exception as e:
            logger.exception("scheduler: teller sync failed: %s", e)
            summary["teller_error"] = str(e)
        try:
            summary["etrade"] = etrade_sync.sync_all(session)
        except Exception as e:
            logger.exception("scheduler: etrade sync failed: %s", e)
            summary["etrade_error"] = str(e)
    return summary


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
            t_results = summary.get("teller", []) or []
            e_results = summary.get("etrade", []) or []
            t_new = sum(r.get("transactions_created", 0) for r in t_results)
            e_new = sum(r.get("transactions_created", 0) for r in e_results)
            logger.info(
                "scheduler: sync done — teller +%d tx, etrade +%d tx",
                t_new,
                e_new,
            )
        except Exception as e:
            # Defensive: don't let the loop die.
            logger.exception("scheduler: tick failed unexpectedly: %s", e)
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
