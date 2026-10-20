import asyncio

import httpx
import pytest

from mypocket import scheduler
from mypocket.security import crypto
from tests.test_integrations import make_enrollment


def test_scheduler_error_logs_do_not_include_raw_exception(monkeypatch, db, caplog):
    monkeypatch.setattr(scheduler, "engine", db.get_bind())

    def fail(*args):
        raise httpx.ReadTimeout("DO_NOT_EXPOSE account identifier")

    monkeypatch.setattr(scheduler.teller_sync, "sync_all", fail)
    monkeypatch.setattr(scheduler.etrade_sync, "sync_all", fail)
    summary = scheduler._run_sync_once()
    assert summary["teller_error"]
    assert summary["etrade_error"]
    assert "DO_NOT_EXPOSE" not in str(summary) + caplog.text


def test_inactive_summary_does_not_decrypt_credentials(monkeypatch, db):
    make_enrollment(db, "etrade", "needs_reauth")
    monkeypatch.setattr(scheduler, "engine", db.get_bind())
    monkeypatch.setattr(scheduler.teller_sync, "sync_all", lambda s: [])
    monkeypatch.setattr(scheduler.etrade_sync, "sync_all", lambda s: [])

    def forbidden(*args):
        raise AssertionError("Status summary must not decrypt credentials")

    monkeypatch.setattr(crypto, "decrypt", forbidden)
    assert scheduler._run_sync_once()["inactive"] == ["etrade:needs_reauth"]


def test_scheduler_and_manual_sync_include_plaid(monkeypatch, db):
    from mypocket.routes import api

    monkeypatch.setattr(scheduler, "engine", db.get_bind())
    monkeypatch.setattr(scheduler.teller_sync, "sync_all", lambda s: [])
    monkeypatch.setattr(scheduler.etrade_sync, "sync_all", lambda s: [])
    seen = []
    monkeypatch.setattr(
        scheduler.plaid_sync, "sync_all", lambda s: seen.append(True) or [{"transactions_created": 3}]
    )
    assert scheduler._run_sync_once()["plaid"][0]["transactions_created"] == 3
    assert api.trigger_full_sync(db)["plaid"][0]["transactions_created"] == 3
    assert len(seen) == 2


@pytest.mark.asyncio
async def test_retired_and_historical_connections_are_not_logged_as_failures(monkeypatch, caplog):
    ticks = 0

    async def once(_seconds):
        nonlocal ticks
        ticks += 1
        if ticks > 1:
            raise asyncio.CancelledError

    monkeypatch.setattr(scheduler.asyncio, "sleep", once)
    monkeypatch.setattr(
        scheduler,
        "_run_sync_once",
        lambda: {
            "inactive": ["teller:disabled", "etrade:archived", "etrade:needs_reauth"],
            "teller": [],
            "etrade": [{"errors": [], "transactions_created": 0}],
            "plaid": [{"errors": [], "transactions_created": 0}],
        },
    )
    with pytest.raises(asyncio.CancelledError):
        await scheduler._scheduler_loop()
    assert "completed with errors" not in caplog.text
