"""Synthetic reset/restore tests; no provider, credentials or real database."""

import importlib
import sqlite3
from datetime import date

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from mypocket.domain.models import Account, Enrollment, Transaction
from tests.test_plaid import make_link, page, remote_account, tx


@pytest.fixture
def disk_db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'app.db'}")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session, tmp_path / "app.db"
    engine.dispose()


def prepare(session, plaid, monkeypatch):
    sync = importlib.import_module("mypocket.integrations.plaid_sync")
    e, _ = make_link(session)
    old = Account(source="teller", name="Old checking", type="checking", institution="Citi", balance=10)
    credit = Account(source="teller", name="Unlinked credit", type="credit", institution="Citi", balance=20)
    broker = Account(source="etrade_api", name="Broker", type="brokerage", institution="E*TRADE", balance=30)
    session.add_all([old, credit, broker])
    session.commit()
    for a in (old, credit, broker):
        session.add(
            Transaction(
                account_id=a.id,
                tx_date=date(2026, 7, 2),
                amount=-1,
                description="Retain",
                transaction_type="pending",
                category="Custom",
                notes="Retain this",
            )
        )
    session.commit()
    monkeypatch.setattr(
        plaid,
        "accounts",
        lambda *a: {
            "accounts": [remote_account()],
            "item": {"item_id": "synthetic-item", "institution_id": "bank"},
        },
    )
    monkeypatch.setattr(plaid, "institution_name", lambda *a: "Citi")
    monkeypatch.setattr(plaid, "sync_delta", lambda *a: page([tx("older", day="2026-01-02"), tx()]))
    review = sync.stage_review(session, e)
    plan = sync.preview_plan(
        session,
        e,
        review["review_id"],
        [{"external_id": "remote-checking", "mode": "replace", "account_id": old.id}],
    )
    return sync, e, old, credit, broker, plan


def test_reset_is_atomic_backed_up_and_scope_isolated(disk_db, plaid, monkeypatch):
    from mypocket.citi_reset import restore
    from mypocket.domain import analytics
    from mypocket.domain.models import CitiReset, PlaidAccount

    db, path = disk_db
    sync, e, old, credit, broker, plan = prepare(db, plaid, monkeypatch)
    original = {t.id: t.model_dump() for t in db.exec(select(Transaction)).all()}
    untouched = [credit.model_dump(), broker.model_dump()]
    assert not plan["blocked"] and plan["accounts"][0]["archived"] == 1
    assert plan["accounts"][0]["history_skipped"] == 0
    assert plan["accounts"][0]["import_from"] is None
    assert plan["accounts"][0]["received_from"] == "2026-01-02"
    assert sync.approve_plan(db, e, plan["plan_id"])["transactions_created"] == 2
    reset = db.exec(select(CitiReset)).one()
    saved = path.parent / "backups" / reset.backup_name
    with sqlite3.connect(saved) as conn:
        assert conn.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert conn.execute("SELECT archived FROM account WHERE id=?", (old.id,)).fetchone() == (0,)
        assert conn.execute('SELECT count(*) FROM "transaction"').fetchone() == (3,)
    assert saved.stat().st_mode & 0o077 == 0
    assert old.archived
    fresh = db.get(Account, reset.replacement_id)
    assert fresh.source == "plaid" and fresh.balance == 0 and not fresh.archived
    assert [credit.model_dump(), broker.model_dump()] == untouched
    assert all(db.get(Transaction, i).model_dump() == row for i, row in original.items())
    assert old.id not in analytics.account_ids_in_domain(db, "banking")
    assert sync.sync_enrollment(db, e)["transactions_created"] == 0
    assert sync.sync_enrollment(db, e)["transactions_created"] == 0
    assert len(db.exec(select(Transaction)).all()) == 5
    db.rollback()
    restore(path)
    db.expire_all()
    assert not db.get(Account, old.id).archived and db.get(Account, fresh.id).archived
    assert db.get(Enrollment, e.id).status == "paused"
    assert all(db.get(Transaction, i).model_dump() == row for i, row in original.items())
    assert len(db.exec(select(Transaction)).all()) == 5
    assert db.exec(select(PlaidAccount)).one().account_id == fresh.id
    assert restore(path) is None  # repeat restoration is a no-op


def test_reset_failure_rolls_back_archive_and_import(disk_db, plaid, monkeypatch):
    from mypocket.domain.models import CitiReset, PlaidAccount

    db, path = disk_db
    sync, e, old, _credit, _broker, plan = prepare(db, plaid, monkeypatch)

    def fail(*args, **kwargs):
        raise RuntimeError("Synthetic failed import")

    monkeypatch.setattr(sync, "_apply", fail)
    with pytest.raises(RuntimeError, match="Synthetic"):
        sync.approve_plan(db, e, plan["plan_id"])
    db.refresh(old)
    assert not old.archived
    assert not db.exec(select(CitiReset)).all()
    assert not db.exec(select(PlaidAccount)).all()
    assert len(db.exec(select(Account)).all()) == 3
    assert len(db.exec(select(Transaction)).all()) == 3
    assert len(list((path.parent / "backups").glob("*.sqlite3"))) == 1


def test_archived_teller_account_is_never_refetched(db, monkeypatch):
    from mypocket.integrations import teller, teller_sync

    a = Account(
        source="teller",
        external_id="old",
        name="Archived",
        type="checking",
        institution="Citi",
        archived=True,
    )
    e = Enrollment(provider="teller", access_token="synthetic", status="active")
    db.add_all([a, e])
    db.commit()

    def forbidden(*args):
        raise AssertionError("Archived bank data must not be refreshed")

    monkeypatch.setattr(teller, "get_balances", forbidden)
    assert teller_sync._sync_one_account(db, e, {"id": "old"}) == {"created": 0, "skipped": 0}


def test_backup_failure_prevents_reset(disk_db, plaid, monkeypatch):
    from mypocket import citi_reset

    db, _path = disk_db
    sync, e, old, _credit, _broker, plan = prepare(db, plaid, monkeypatch)

    def fail(*args):
        raise OSError("Synthetic backup failure")

    monkeypatch.setattr(citi_reset, "verified_backup", fail)
    with pytest.raises(OSError):
        sync.approve_plan(db, e, plan["plan_id"])
    db.refresh(old)
    assert not old.archived
    assert len(db.exec(select(Account)).all()) == 3
    assert len(db.exec(select(Transaction)).all()) == 3


def test_replace_rejects_unrelated_account_and_empty_fresh_history(disk_db, plaid, monkeypatch):
    db, _path = disk_db
    sync, e, old, _credit, broker, _plan = prepare(db, plaid, monkeypatch)
    review = sync.stage_review(db, e)
    with pytest.raises(plaid.PlaidError, match="mapping"):
        sync.preview_plan(
            db,
            e,
            review["review_id"],
            [{"external_id": "remote-checking", "mode": "replace", "account_id": broker.id}],
        )
    monkeypatch.setattr(plaid, "sync_delta", lambda *a: page([]))
    review = sync.stage_review(db, e)
    with pytest.raises(plaid.PlaidError, match="Fresh history"):
        sync.preview_plan(
            db,
            e,
            review["review_id"],
            [{"external_id": "remote-checking", "mode": "replace", "account_id": old.id}],
        )
    assert not old.archived
