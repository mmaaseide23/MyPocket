"""Plaid contracts, tested with synthetic data and HTTP transports only."""

import importlib
import json
from datetime import date

import httpx
import pytest
from sqlalchemy import text
from sqlmodel import select

from mypocket.core.config import settings
from mypocket.domain.models import Account, Enrollment, Transaction


def transport(monkeypatch, plaid, handler):
    monkeypatch.setattr(plaid, "_client", lambda: httpx.Client(transport=httpx.MockTransport(handler)))


def remote_account():
    return {
        "account_id": "remote-checking",
        "name": "Synthetic Checking",
        "mask": "0000",
        "type": "depository",
        "subtype": "checking",
        "balances": {"current": 100.0, "available": 0.0, "iso_currency_code": "USD"},
    }


def tx(tid="t1", day="2026-07-03", amount=10.0, **kw):
    return {
        "transaction_id": tid,
        "account_id": "remote-checking",
        "date": day,
        "amount": amount,
        "name": "Synthetic Shop",
        "merchant_name": "Synthetic Shop",
        "pending": False,
        "pending_transaction_id": None,
        "iso_currency_code": "USD",
        **kw,
    }


def page(added=None, modified=None, removed=None, cursor="cursor-one", more=False):
    return {
        "accounts": [remote_account()],
        "added": added or [],
        "modified": modified or [],
        "removed": removed or [],
        "next_cursor": cursor,
        "has_more": more,
        "transactions_update_status": "HISTORICAL_UPDATE_COMPLETE",
    }


def make_link(db):
    models = importlib.import_module("mypocket.domain.models")
    e = Enrollment(
        provider="plaid",
        enrollment_id="synthetic-item",
        access_token="synthetic-access",
        environment="sandbox",
        status="pending_review",
        institution="Citibank",
    )
    db.add(e)
    db.commit()
    state = models.PlaidConnection(enrollment_id=e.id)
    db.add(state)
    db.commit()
    return e, state


def test_link_requests_only_transactions_and_update_reuses_item(plaid, monkeypatch):
    bodies = []

    def handler(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={"link_token": "synthetic-link"})

    transport(monkeypatch, plaid, handler)
    assert plaid.create_link_token() == "synthetic-link"
    assert bodies[0]["products"] == ["transactions"]
    assert bodies[0]["country_codes"] == ["US"]
    assert "redirect_uri" not in bodies[0]
    assert bodies[0]["account_filters"]["credit"]["account_subtypes"] == ["credit card"]


def test_production_is_blocked_until_user_confirms_trial(plaid, monkeypatch):
    monkeypatch.setattr(settings, "plaid_environment", "production")
    with pytest.raises(plaid.PlaidError, match="Trial"):
        plaid.create_link_token()


def test_untrusted_error_body_never_reaches_user(plaid, monkeypatch):
    transport(
        monkeypatch,
        plaid,
        lambda r: httpx.Response(
            400,
            json={
                "error_code": "ITEM_LOGIN_REQUIRED",
                "error_message": "DO_NOT_EXPOSE",
                "display_message": "DO_NOT_EXPOSE",
                "request_id": "DO_NOT_EXPOSE",
            },
        ),
    )
    with pytest.raises(plaid.PlaidNeedsReauth) as err:
        plaid.accounts("synthetic-access", "sandbox")
    assert "DO_NOT_EXPOSE" not in str(err.value)


def test_non_json_and_environment_mismatch_fail_closed(plaid, monkeypatch):
    transport(monkeypatch, plaid, lambda r: httpx.Response(200, text="DO_NOT_EXPOSE"))
    with pytest.raises(plaid.PlaidError):
        plaid.accounts("synthetic-access", "sandbox")
    with pytest.raises(plaid.PlaidError, match="environment"):
        plaid.accounts("synthetic-access", "production")


def test_sync_mutation_restarts_original_cursor_and_discards_partial_pages(plaid, monkeypatch):
    seen = []

    def handler(request):
        seen.append(json.loads(request.content)["cursor"])
        responses = [
            page([tx("discard")], cursor="middle", more=True),
            {"error_code": "TRANSACTIONS_SYNC_MUTATION_DURING_PAGINATION"},
            page([tx("kept")], cursor="final"),
        ]
        return httpx.Response(400 if len(seen) == 2 else 200, json=responses[len(seen) - 1])

    transport(monkeypatch, plaid, handler)
    result = plaid.sync_delta("synthetic-access", "sandbox", "original")
    assert seen == ["original", "middle", "original"]
    assert [t["transaction_id"] for t in result["added"]] == ["kept"]
    assert result["next_cursor"] == "final"


def test_review_does_not_import_and_approval_preserves_history(plaid, monkeypatch, db):
    sync = importlib.import_module("mypocket.integrations.plaid_sync")
    e, state = make_link(db)
    existing = Account(
        source="teller",
        external_id="old-account",
        name="Existing",
        institution="Citibank",
        type="checking",
        balance=55,
    )
    db.add(existing)
    db.commit()
    old = Transaction(
        account_id=existing.id,
        external_id="old-tx",
        tx_date=date(2026, 7, 2),
        amount=-10,
        description="Existing",
        category="Custom",
        notes="Keep me",
    )
    db.add(old)
    db.commit()
    monkeypatch.setattr(
        plaid,
        "accounts",
        lambda *a: {
            "accounts": [remote_account()],
            "item": {"item_id": "synthetic-item", "institution_id": "synthetic-bank"},
        },
    )
    monkeypatch.setattr(plaid, "institution_name", lambda *a: "Citibank")
    monkeypatch.setattr(plaid, "sync_delta", lambda *a: page([tx("overlap", day="2026-07-02"), tx()]))
    review = sync.stage_review(db, e)
    selections = [{"external_id": "remote-checking", "account_id": existing.id, "mode": "existing"}]
    plan = sync.preview_plan(db, e, review["review_id"], selections)
    assert plan["accounts"][0]["history_skipped"] == 1
    assert plan["accounts"][0]["transactions_to_import"] == 1
    assert len(db.exec(select(Transaction)).all()) == 1
    assert existing.balance == 55
    result = sync.approve_plan(db, e, plan["plan_id"])
    assert result["transactions_created"] == 1
    assert len(db.exec(select(Account)).all()) == 1
    db.refresh(old)
    db.refresh(existing)
    db.refresh(state)
    assert old.category == "Custom" and old.notes == "Keep me"
    assert existing.balance == 0.0
    assert state.cursor == "cursor-one"
    assert e.status == "active"
    stored = db.exec(text("SELECT access_token FROM enrollment")).first()[0]
    assert stored.startswith("enc-v1:") and "synthetic-access" not in stored


def test_stale_plan_rejected_after_local_history_changes(plaid, monkeypatch, db):
    sync = importlib.import_module("mypocket.integrations.plaid_sync")
    e, _ = make_link(db)
    monkeypatch.setattr(
        plaid,
        "accounts",
        lambda *a: {
            "accounts": [remote_account()],
            "item": {"item_id": "synthetic-item", "institution_id": "bank"},
        },
    )
    monkeypatch.setattr(plaid, "institution_name", lambda *a: "Citibank")
    monkeypatch.setattr(plaid, "sync_delta", lambda *a: page([tx()]))
    review = sync.stage_review(db, e)
    plan = sync.preview_plan(db, e, review["review_id"], [{"external_id": "remote-checking", "mode": "new"}])
    db.add(Account(source="teller", external_id="new", name="Changed", type="checking", institution="Citi"))
    db.commit()
    with pytest.raises(plaid.PlaidError, match="changed"):
        sync.approve_plan(db, e, plan["plan_id"])
    assert not db.exec(select(Transaction)).all()


def test_incremental_changes_pending_posted_removed_preserve_annotations(plaid, monkeypatch, db):
    sync = importlib.import_module("mypocket.integrations.plaid_sync")
    e, state = make_link(db)
    monkeypatch.setattr(
        plaid,
        "accounts",
        lambda *a: {
            "accounts": [remote_account()],
            "item": {"item_id": "synthetic-item", "institution_id": "bank"},
        },
    )
    monkeypatch.setattr(plaid, "institution_name", lambda *a: "Citibank")
    monkeypatch.setattr(plaid, "sync_delta", lambda *a: page([tx("pending", pending=True)]))
    review = sync.stage_review(db, e)
    plan = sync.preview_plan(db, e, review["review_id"], [{"external_id": "remote-checking", "mode": "new"}])
    sync.approve_plan(db, e, plan["plan_id"])
    row = db.exec(select(Transaction)).one()
    assert row.excluded
    row.notes = "Keep"
    row.category = "My category"
    db.commit()
    monkeypatch.setattr(
        plaid,
        "sync_delta",
        lambda *a: page(
            [tx("posted", amount=12, pending_transaction_id="pending")],
            removed=[{"transaction_id": "pending"}],
            cursor="two",
        ),
    )
    assert not sync.sync_enrollment(db, e)["errors"]
    rows = db.exec(select(Transaction)).all()
    assert len(rows) == 1 and rows[0].amount == -12 and not rows[0].excluded
    assert rows[0].notes == "Keep" and rows[0].category == "My category"
    monkeypatch.setattr(
        plaid, "sync_delta", lambda *a: page(modified=[tx("posted", amount=14)], cursor="three")
    )
    sync.sync_enrollment(db, e)
    assert db.exec(select(Transaction)).one().amount == -14
    monkeypatch.setattr(
        plaid, "sync_delta", lambda *a: page(removed=[{"transaction_id": "posted"}], cursor="four")
    )
    sync.sync_enrollment(db, e)
    row = db.exec(select(Transaction)).one()
    assert row.excluded and row.notes == "Keep"
    assert state.cursor == "four"


def test_unreviewed_connections_do_not_sync_or_decrypt(plaid, monkeypatch, db):
    sync = importlib.import_module("mypocket.integrations.plaid_sync")
    make_link(db)
    from mypocket.security import crypto

    monkeypatch.setattr(crypto, "decrypt", lambda *a: pytest.fail("Unreviewed credentials decrypted"))
    assert sync.sync_all(db) == []


def test_failed_sync_rolls_back_balance_rows_and_cursor(plaid, monkeypatch, db):
    sync = importlib.import_module("mypocket.integrations.plaid_sync")
    e, state = make_link(db)
    models = importlib.import_module("mypocket.domain.models")
    a = Account(source="plaid", name="Test", type="checking", institution="Citi", balance=50)
    db.add(a)
    db.commit()
    db.add(models.PlaidAccount(enrollment_id=e.id, external_id="remote-checking", account_id=a.id))
    state.approved = True
    state.cursor = "old"
    e.status = "active"
    db.add(state)
    db.add(e)
    db.commit()
    monkeypatch.setattr(
        plaid, "accounts", lambda *a: {"accounts": [remote_account()], "item": {"item_id": "synthetic-item"}}
    )
    monkeypatch.setattr(plaid, "sync_delta", lambda *a: page([tx("valid"), tx("bad", amount=float("nan"))]))
    result = sync.sync_enrollment(db, e)
    db.refresh(a)
    db.refresh(state)
    assert result["errors"] and a.balance == 50 and state.cursor == "old"
    assert not db.exec(select(Transaction)).all()


def test_pending_legacy_history_blocks_cutover(plaid, monkeypatch, db):
    sync = importlib.import_module("mypocket.integrations.plaid_sync")
    e, state = make_link(db)
    a = Account(source="teller", name="Test", type="checking", institution="Citi")
    db.add(a)
    db.commit()
    db.add(
        Transaction(
            account_id=a.id,
            tx_date=date(2026, 7, 2),
            amount=-10,
            description="Pending",
            transaction_type="pending",
        )
    )
    db.commit()
    monkeypatch.setattr(
        plaid,
        "accounts",
        lambda *a: {
            "accounts": [remote_account()],
            "item": {"item_id": "synthetic-item", "institution_id": "bank"},
        },
    )
    monkeypatch.setattr(plaid, "institution_name", lambda *a: "Citi")
    monkeypatch.setattr(plaid, "sync_delta", lambda *a: page([tx()]))
    review = sync.stage_review(db, e)
    # A previously valid draft must not remain approvable after a blocked preview.
    old_plan = sync.preview_plan(
        db, e, review["review_id"], [{"external_id": "remote-checking", "mode": "new"}]
    )
    plan = sync.preview_plan(
        db,
        e,
        review["review_id"],
        [{"external_id": "remote-checking", "mode": "existing", "account_id": a.id}],
    )
    assert plan["blocked"] and plan["plan_id"] is None
    row = plan["accounts"][0]
    assert row["legacy_pending"] == 1
    assert row["legacy_pending_from"] == row["legacy_pending_through"] == "2026-07-02"
    assert row["preserved"] == 1 and row["transactions_to_import"] == 1
    assert row["import_from"] == "2026-07-03"
    db.refresh(state)
    assert state.plan_id is None and state.plan_json is None and not state.approved
    with pytest.raises(plaid.PlaidError, match="Preview"):
        sync.approve_plan(db, e, old_plan["plan_id"])
    assert len(db.exec(select(Transaction)).all()) == 1
    assert len(db.exec(select(Account)).all()) == 1


def test_removed_and_pending_rows_do_not_affect_analytics(db):
    from mypocket.domain import analytics

    a = Account(source="plaid", name="Test", type="checking", institution="Citi")
    db.add(a)
    db.commit()
    for excluded, amount in ((False, -10), (True, -200)):
        db.add(
            Transaction(
                account_id=a.id,
                tx_date=date.today(),
                amount=amount,
                description="Shop",
                category="Food",
                excluded=excluded,
            )
        )
    db.commit()
    assert len(analytics.recent_transactions(db, account_id=a.id)) == 1
    assert analytics.spending(db, days=30) == 10


def test_teller_skips_accounts_managed_by_plaid(plaid, monkeypatch, db):
    from mypocket.domain.models import PlaidAccount
    from mypocket.integrations import teller_sync

    e, _ = make_link(db)
    teller = Enrollment(provider="teller", access_token="fake", environment="development")
    a = Account(
        source="teller", external_id="old-id", name="Test", type="checking", institution="Citi", balance=25
    )
    db.add(teller)
    db.add(a)
    db.commit()
    db.add(PlaidAccount(enrollment_id=e.id, external_id="remote-checking", account_id=a.id))
    db.commit()
    monkeypatch.setattr(
        teller_sync.teller, "get_balances", lambda *a: pytest.fail("Teller overwrote Plaid balance")
    )
    result = teller_sync._sync_one_account(db, teller, {"id": "old-id"})
    assert result["created"] == 0 and a.balance == 25


def test_sync_reloads_durable_cursor_after_another_session_changed_it(plaid, monkeypatch, db):
    from sqlmodel import Session

    from mypocket.domain.models import PlaidAccount, PlaidConnection

    sync = importlib.import_module("mypocket.integrations.plaid_sync")
    e, state = make_link(db)
    a = Account(source="plaid", name="Test", type="checking", institution="Citi")
    db.add(a)
    db.commit()
    db.add(PlaidAccount(enrollment_id=e.id, external_id="remote-checking", account_id=a.id))
    e.status = "active"
    state.approved = True
    state.cursor = "old"
    db.add(e)
    db.add(state)
    db.commit()
    assert state.cursor == "old"
    with Session(db.get_bind()) as other:
        latest = other.get(PlaidConnection, e.id)
        latest.cursor = "new"
        other.add(latest)
        other.commit()
    monkeypatch.setattr(
        plaid, "accounts", lambda *a: {"accounts": [remote_account()], "item": {"item_id": "synthetic-item"}}
    )
    cursors = []
    monkeypatch.setattr(
        plaid, "sync_delta", lambda token, env, cursor: cursors.append(cursor) or page(cursor="final")
    )
    sync.sync_enrollment(db, e)
    assert cursors == ["new"]


def test_legacy_schema_migration_preserves_rows_and_defaults_exclusion(monkeypatch):
    from sqlmodel import create_engine

    from mypocket.core import db as db_module

    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE enrollment (id INTEGER PRIMARY KEY, access_token TEXT)"))
        conn.execute(text('CREATE TABLE "transaction" (id INTEGER PRIMARY KEY, notes TEXT)'))
        conn.execute(text("INSERT INTO \"transaction\" (id,notes) VALUES (1,'synthetic annotation')"))
    monkeypatch.setattr(db_module, "engine", engine)
    db_module._apply_lightweight_migrations()
    db_module._apply_lightweight_migrations()
    with engine.connect() as conn:
        row = conn.execute(text('SELECT notes,excluded FROM "transaction"')).one()
        assert tuple(row) == ("synthetic annotation", 0)
    engine.dispose()


def test_same_batch_pending_posted_order_and_removal_are_idempotent(plaid, monkeypatch, db):
    from mypocket.domain.models import PlaidAccount

    sync = importlib.import_module("mypocket.integrations.plaid_sync")
    e, state = make_link(db)
    a = Account(source="plaid", name="Test", type="checking", institution="Citi")
    db.add(a)
    db.commit()
    db.add(PlaidAccount(enrollment_id=e.id, external_id="remote-checking", account_id=a.id))
    state.approved = True
    e.status = "active"
    db.add(state)
    db.add(e)
    db.commit()
    monkeypatch.setattr(
        plaid, "accounts", lambda *a: {"accounts": [remote_account()], "item": {"item_id": "synthetic-item"}}
    )
    delta = page(
        [tx("posted", amount=12, pending_transaction_id="pending"), tx("pending", pending=True)],
        removed=[{"transaction_id": "pending"}],
    )
    monkeypatch.setattr(plaid, "sync_delta", lambda *a: delta)
    assert not sync.sync_enrollment(db, e)["errors"]
    rows = db.exec(select(Transaction)).all()
    assert len(rows) == 1 and not rows[0].excluded and rows[0].amount == -12
    assert not sync.sync_enrollment(db, e)["errors"]
    assert len(db.exec(select(Transaction)).all()) == 1
    monkeypatch.setattr(
        plaid,
        "sync_delta",
        lambda *a: page(modified=[tx("posted", amount=13)], removed=[{"transaction_id": "posted"}]),
    )
    assert not sync.sync_enrollment(db, e)["errors"]
    assert db.exec(select(Transaction)).one().excluded
