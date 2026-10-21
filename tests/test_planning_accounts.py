"""Fictional account totals only. Never use a configured app database."""

from datetime import UTC, datetime, timedelta

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import event

from mypocket.domain.models import Account, Holding
from mypocket.domain.planning_accounts import saved_accounts

NOW = datetime(2026, 10, 20, 12, tzinfo=UTC)


def account(db, **changes):
    values = dict(
        name="Fictional",
        institution="Example",
        source="etrade_api",
        type="brokerage",
        balance=1000,
        last_synced=NOW,
    )
    values.update(changes)
    row = Account(**values)
    db.add(row)
    db.commit()
    return row


def test_combined_account_totals_include_cash_once_without_reading_holdings(db):
    a = account(db, balance=1000)
    account(db, balance=500, type="ira")
    db.add(Holding(account_id=a.id, symbol="FICTION", quantity=1, market_value=900))
    db.commit()
    queries = []
    event.listen(
        db.get_bind(), "before_cursor_execute", lambda c, u, statement, p, x, m: queries.append(statement)
    )
    result = saved_accounts(db, now=NOW)
    assert result["starting_value"] == 1500  # Includes the fictional $100 account cash once.
    assert result["account_count"] == 2
    assert result["status"] == "ready"
    assert not any("holding" in q.lower() for q in queries)
    assert set(result) == {
        "status",
        "starting_value",
        "account_count",
        "missing_count",
        "last_synced_start",
        "last_synced_end",
        "warnings",
    }


def test_active_scope_excludes_archived_csv_foreign_and_noninvestment_accounts(db):
    account(db)
    account(db, archived=True)
    account(db, source="etrade_csv")
    account(db, currency="EUR")
    for kind in ["checking", "savings", "credit"]:
        account(db, type=kind)
    result = saved_accounts(db, now=NOW)
    assert result["starting_value"] == 1000
    assert result["account_count"] == 1
    assert set(result["warnings"]) == {"csv_accounts", "non_usd"}


def test_duplicate_account_identity_counts_latest_copy_once(db):
    account(db, external_id="fiction-id", balance=900, last_synced=NOW - timedelta(hours=1))
    account(db, external_id="fiction-id", balance=1000, last_synced=NOW.replace(tzinfo=None))
    account(db, external_id="separate-fiction-id", balance=200)
    result = saved_accounts(db, now=NOW)
    assert result["starting_value"] == 1200
    assert result["account_count"] == 2
    assert result["warnings"] == ["duplicate_accounts"]


def test_missing_total_never_silently_projects_partial_portfolio(db):
    account(db)
    account(db, balance=None)
    result = saved_accounts(db, now=NOW)
    assert result["status"] == "missing"
    assert result["starting_value"] is None
    assert result["missing_count"] == 1
    assert result["account_count"] == 2


def test_zero_negative_and_empty_are_distinguished(db):
    assert saved_accounts(db, now=NOW)["status"] == "empty"
    a = account(db, balance=0)
    assert saved_accounts(db, now=NOW)["status"] == "ready"
    assert saved_accounts(db, now=NOW)["starting_value"] == 0
    a.balance = -100
    db.add(a)
    db.commit()
    assert saved_accounts(db, now=NOW)["status"] == "unsupported"
    assert saved_accounts(db, now=NOW)["starting_value"] == -100


def test_stale_unknown_and_future_sync_times_are_explicit(db):
    account(db, last_synced=NOW - timedelta(hours=25))
    account(db, last_synced=NOW - timedelta(hours=1))
    account(db, last_synced=None)
    account(db, last_synced=NOW + timedelta(days=1))
    result = saved_accounts(db, now=NOW)
    assert result["starting_value"] == 4000
    assert set(result["warnings"]) == {"stale_totals", "unknown_sync_date", "future_sync_date"}
    assert result["last_synced_start"] == (NOW - timedelta(hours=25)).isoformat()
    assert result["last_synced_end"] == (NOW + timedelta(days=1)).isoformat()


def test_endpoint_auth_same_origin_no_store_and_no_writes(db, monkeypatch):
    from mypocket.core.db import get_session
    from mypocket.routes import planning_accounts
    from mypocket.security import session as session_mod
    from mypocket.security.middleware import AuthMiddleware

    account(db)
    app = FastAPI()
    app.include_router(planning_accounts.router)
    app.dependency_overrides[get_session] = lambda: db
    app.add_middleware(AuthMiddleware)
    monkeypatch.setattr(AuthMiddleware, "_setup_complete", lambda self: True)
    monkeypatch.setattr(session_mod, "verify", lambda token: token == "fiction-session")
    with TestClient(app) as client:
        assert client.get("/api/planning/accounts").status_code == 401
        client.cookies.set(session_mod.SESSION_COOKIE_NAME, "fiction-session")
        assert client.get("/api/planning/accounts").status_code == 403
        headers = {"X-MyPocket-Request": "1"}
        assert (
            client.get(
                "/api/planning/accounts", headers={**headers, "Sec-Fetch-Site": "cross-site"}
            ).status_code
            == 403
        )
        assert (
            client.get(
                "/api/planning/accounts", headers={**headers, "Origin": "https://example.invalid"}
            ).status_code
            == 403
        )
        result = client.get("/api/planning/accounts", headers=headers)
        assert result.status_code == 200
        assert result.headers["Cache-Control"] == "no-store"
        assert result.json()["starting_value"] == 1000
        assert client.post("/api/planning/accounts", headers=headers).status_code == 405
        assert client.get("/api/planning/holdings", headers=headers).status_code == 404
