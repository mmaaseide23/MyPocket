"""Global refresh contracts, using only in-memory data and mocked providers."""

from datetime import UTC, datetime

import pytest
from sqlmodel import select

from mypocket.domain.models import Account, Enrollment, PlaidAccount, Transaction
from mypocket.integrations import plaid_sync
from mypocket.routes import api
from mypocket.security import crypto
from tests.test_plaid import make_link, page, remote_account, tx


@pytest.mark.parametrize("failed_provider", ["teller", "etrade"])
def test_manual_sync_continues_to_plaid_after_an_earlier_provider_exception(monkeypatch, db, failed_provider):
    seen = []

    def fail(session):
        session.add(Account(source="test", name="Must rollback", type="checking", institution="Test"))
        session.flush()
        raise RuntimeError("DO_NOT_EXPOSE synthetic private payload")

    for name in ("teller", "etrade", "plaid"):
        monkeypatch.setattr(
            getattr(api, name + "_sync"),
            "sync_all",
            lambda session, p=name: seen.append(p) or [{"errors": []}],
        )
    monkeypatch.setattr(getattr(api, failed_provider + "_sync"), "sync_all", fail)
    result = api.trigger_full_sync(db)
    assert "plaid" in seen
    assert result[failed_provider][0]["errors"]
    assert result["plaid"] == [{"errors": []}]
    assert "DO_NOT_EXPOSE" not in str(result)
    assert not db.exec(select(Account)).all()


@pytest.mark.parametrize(
    "status,approved", [("needs_reauth", True), ("pending_review", False), ("active", False)]
)
def test_plaid_blocked_status_is_visible_without_credentials_or_network(monkeypatch, db, status, approved):
    enrollment, state = make_link(db)
    enrollment.status, state.approved = status, approved
    db.add(enrollment)
    db.add(state)
    db.commit()
    db.expunge_all()

    def forbidden(*args):
        pytest.fail("A blocked connection must not decrypt credentials or call providers")

    monkeypatch.setattr(crypto, "decrypt", forbidden)
    monkeypatch.setattr(plaid_sync.plaid, "accounts", forbidden)
    monkeypatch.setattr(plaid_sync.plaid, "sync_delta", forbidden)
    result = plaid_sync.sync_all(db)
    assert len(result) == 1
    assert result[0]["errors"]
    assert result[0]["status"] == ("needs_reauth" if status == "needs_reauth" else "pending_review")


@pytest.mark.parametrize("status", ["archived", "disabled", "disconnected"])
def test_retired_plaid_connections_remain_silent_and_untouched(db, monkeypatch, status):
    enrollment, state = make_link(db)
    enrollment.status, state.approved = status, True
    db.add(enrollment)
    db.add(state)
    db.commit()
    db.expunge_all()
    monkeypatch.setattr(crypto, "decrypt", lambda *args: pytest.fail("Retired credentials decrypted"))
    assert plaid_sync.sync_all(db) == []


def test_manual_sync_imports_plaid_and_repeated_empty_delta_updates_sync_time(db, monkeypatch, plaid):
    enrollment, state = make_link(db)
    enrollment.status, state.approved = "active", True
    account = Account(source="plaid", name="Fictional Checking", institution="Test", type="checking")
    db.add(account)
    db.add(enrollment)
    db.add(state)
    db.commit()
    db.add(PlaidAccount(enrollment_id=enrollment.id, external_id="remote-checking", account_id=account.id))
    db.commit()
    monkeypatch.setattr(api.teller_sync, "sync_all", lambda session: [])
    monkeypatch.setattr(api.etrade_sync, "sync_all", lambda session: [{"errors": []}])
    monkeypatch.setattr(
        plaid,
        "accounts",
        lambda *args: {"accounts": [remote_account()], "item": {"item_id": "synthetic-item"}},
    )
    cursors = []

    def changes(token, environment, cursor):
        cursors.append(cursor)
        return page([tx()] if not cursor else [], cursor="saved-cursor")

    monkeypatch.setattr(plaid, "sync_delta", changes)
    first = api.trigger_full_sync(db)["plaid"][0]
    assert first["transactions_created"] == 1 and not first["errors"]
    enrollment.last_synced = account.last_synced = datetime(2000, 1, 1, tzinfo=UTC)
    db.add(enrollment)
    db.add(account)
    db.commit()
    second = api.trigger_full_sync(db)["plaid"][0]
    assert second["transactions_created"] == 0 and second["accounts"] == 1 and not second["errors"]
    db.refresh(enrollment)
    db.refresh(account)
    assert enrollment.last_synced.year > 2000 and account.last_synced.year > 2000
    assert cursors == ["", "saved-cursor"]
    assert len(db.exec(select(Transaction)).all()) == 1
    assert db.exec(select(Enrollment)).one().status == "active"


def test_auth_failure_is_still_reported_on_the_next_sync(db, monkeypatch, plaid):
    enrollment, state = make_link(db)
    enrollment.status, state.approved = "active", True
    db.add(enrollment)
    db.add(state)
    db.commit()

    def auth_failure(*args):
        raise plaid.PlaidNeedsReauth("Plaid requires reconnection.")

    monkeypatch.setattr(plaid, "accounts", auth_failure)
    assert plaid_sync.sync_all(db)[0]["errors"]
    db.expunge_all()
    monkeypatch.setattr(crypto, "decrypt", lambda *args: pytest.fail("Expired credentials decrypted"))
    assert plaid_sync.sync_all(db)[0]["status"] == "needs_reauth"


def test_global_sync_ui_distinguishes_success_partial_failure_skipped_and_no_changes():
    import shutil
    import subprocess
    from pathlib import Path

    result = subprocess.run(
        [shutil.which("node"), "tests/global_sync_ui.cjs"],
        cwd=Path(__file__).parents[1],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
