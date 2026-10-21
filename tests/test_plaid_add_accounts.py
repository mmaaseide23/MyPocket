"""Adding a card to a synthetic existing Item must not rewrite checking."""

import copy
from pathlib import Path

import pytest
from sqlmodel import select

from mypocket.domain import analytics
from mypocket.domain.models import Account, PlaidAccount, Transaction
from mypocket.integrations import plaid_sync
from tests.test_plaid import make_link, page, remote_account, tx


def card():
    return {
        **remote_account(),
        "account_id": "remote-card",
        "name": "Synthetic Card",
        "type": "credit",
        "subtype": "credit card",
        "balances": {"current": 50.0, "available": 950.0, "iso_currency_code": "USD"},
    }


def prepare(db, plaid, monkeypatch):
    enrollment, state = make_link(db)
    accounts = [remote_account()]
    delta = page([tx()])
    monkeypatch.setattr(
        plaid,
        "accounts",
        lambda *a: {
            "accounts": copy.deepcopy(accounts),
            "item": {
                "item_id": "synthetic-item",
                "institution_id": "synthetic-citi",
            },
        },
    )
    monkeypatch.setattr(plaid, "institution_name", lambda *a: "Citibank")
    monkeypatch.setattr(plaid, "sync_delta", lambda *a: copy.deepcopy(delta))
    review = plaid_sync.stage_review(db, enrollment)
    plan = plaid_sync.preview_plan(
        db,
        enrollment,
        review["review_id"],
        [
            {"external_id": "remote-checking", "mode": "new"},
        ],
    )
    plaid_sync.approve_plan(db, enrollment, plan["plan_id"])
    checking = db.exec(select(Account)).one()
    old = db.exec(select(Transaction)).one()
    old.category, old.notes = "Custom category", "Preserve my note"
    db.add(old)
    db.commit()
    accounts.append(card())
    delta["accounts"] = copy.deepcopy(accounts)
    delta["added"] += [tx("card-purchase", account_id="remote-card", amount=50)]
    delta["next_cursor"] = "new-card-cursor"
    backups = []
    monkeypatch.setattr(
        "mypocket.citi_reset.backup_session", lambda *a: backups.append(True) or Path("backup")
    )
    return enrollment, state, accounts, delta, checking, old, backups


def selections(checking):
    return [
        {"external_id": "remote-checking", "mode": "keep", "account_id": checking.id},
        {"external_id": "remote-card", "mode": "new"},
    ]


def test_addition_review_keeps_checking_and_imports_card_once(db, plaid, monkeypatch):
    e, state, accounts, delta, checking, old, backups = prepare(db, plaid, monkeypatch)
    archived = Account(
        source="teller",
        external_id="old-card",
        name="Archived card",
        type="credit",
        institution="Citibank",
        archived=True,
        balance=999,
    )
    db.add(archived)
    db.commit()
    before = checking.model_dump(), old.model_dump(), state.cursor
    review = plaid_sync.stage_review(db, e)
    assert e.status == "active" and state.approved
    assert review["accounts"][0]["locked"]
    assert review["accounts"][1]["archived_copies"] == 1
    plan = plaid_sync.preview_plan(db, e, review["review_id"], selections(checking))
    assert plan["accounts"][0]["transactions_to_import"] == 0
    assert plan["accounts"][1]["transactions_to_import"] == 1
    assert not backups
    result = plaid_sync.approve_plan(db, e, plan["plan_id"])
    assert result["transactions_created"] == 1 and result["accounts"] == 1
    assert backups == [True]
    assert before == (checking.model_dump(), old.model_dump(), state.cursor)
    assert archived.archived
    assert len(db.exec(select(PlaidAccount)).all()) == 2
    for _ in range(2):
        assert plaid_sync.sync_enrollment(db, e)["transactions_created"] == 0
    assert len(db.exec(select(Transaction)).all()) == 2
    assert old.category == "Custom category" and old.notes == "Preserve my note"
    assert analytics.credit_owed(db) == 50
    assert analytics.net_worth(db) == -50  # checking available=0; archived balance excluded


def test_added_but_unreviewed_card_does_not_stop_checking_sync(db, plaid, monkeypatch):
    e, state, accounts, delta, checking, old, _ = prepare(db, plaid, monkeypatch)
    result = plaid_sync.sync_enrollment(db, e)
    assert result["errors"] == []
    assert result["unreviewed_accounts"] == 1
    assert len(db.exec(select(Account)).all()) == 1
    assert len(db.exec(select(Transaction)).all()) == 1
    assert e.status == "active"


def test_existing_mapping_cannot_be_replaced_or_dropped(db, plaid, monkeypatch):
    e, state, accounts, delta, checking, old, _ = prepare(db, plaid, monkeypatch)
    review = plaid_sync.stage_review(db, e)
    for mode in ["new", "ignore", "replace"]:
        choices = selections(checking)
        choices[0]["mode"] = mode
        with pytest.raises(plaid.PlaidError, match="Existing Plaid"):
            plaid_sync.preview_plan(db, e, review["review_id"], choices)
    accounts.pop(0)
    with pytest.raises(plaid.PlaidError, match="missing"):
        plaid_sync.stage_review(db, e)
    assert e.status == "active" and len(db.exec(select(Account)).all()) == 1


def test_backup_failure_cannot_import_card(db, plaid, monkeypatch):
    e, state, accounts, delta, checking, old, _ = prepare(db, plaid, monkeypatch)
    review = plaid_sync.stage_review(db, e)
    plan = plaid_sync.preview_plan(db, e, review["review_id"], selections(checking))

    def fail(*args):
        raise RuntimeError("backup failed")

    monkeypatch.setattr("mypocket.citi_reset.backup_session", fail)
    with pytest.raises(RuntimeError, match="backup failed"):
        plaid_sync.approve_plan(db, e, plan["plan_id"])
    assert len(db.exec(select(Account)).all()) == 1
    assert len(db.exec(select(Transaction)).all()) == 1


def test_card_payments_are_transfers_and_purchases_are_expenses(db, plaid, monkeypatch):
    e, state, accounts, delta, checking, old, _ = prepare(db, plaid, monkeypatch)
    delta["added"] += [
        tx("card-payment", account_id="remote-card", amount=-50, name="AUTOPAY PAYMENT"),
        tx("card-refund", account_id="remote-card", amount=-5, name="Refund from Synthetic Shop"),
    ]
    review = plaid_sync.stage_review(db, e)
    plan = plaid_sync.preview_plan(db, e, review["review_id"], selections(checking))
    plaid_sync.approve_plan(db, e, plan["plan_id"])
    by_id = {t.external_id: t for t in db.exec(select(Transaction)).all()}
    assert by_id["plaid:card-payment"].category == "Transfers"
    assert by_id["plaid:card-purchase"].amount == -50
    assert by_id["plaid:card-refund"].category != "Transfers"
    payment = tx(
        "checking-payment",
        amount=50,
        name="Synthetic Bill",
        personal_finance_category={"detailed": "LOAN_PAYMENTS_CREDIT_CARD_PAYMENT"},
    )
    delta["added"].append(payment)
    plaid_sync.sync_enrollment(db, e)
    assert (
        db.exec(select(Transaction).where(Transaction.external_id == "plaid:checking-payment")).one().category
        == "Transfers"
    )
    assert analytics.spending(db, days=365) == 60  # checking purchase 10 plus card purchase 50
    assert analytics.refunds(db, days=365) == 5  # payment is not a refund
    assert analytics.income(db, days=365) == 0


def test_update_mode_enables_account_selection_without_new_products(plaid, monkeypatch):
    bodies = []
    monkeypatch.setattr(plaid, "_post", lambda path, body, env: bodies.append(body) or {"link_token": "link"})
    plaid.create_link_token("synthetic-access", "sandbox")
    assert bodies[0]["update"] == {"account_selection_enabled": True}
    assert "products" not in bodies[0] and "additional_consented_products" not in bodies[0]


def test_expired_or_changed_addition_preview_cannot_import(db, plaid, monkeypatch):
    from datetime import UTC, datetime, timedelta

    e, state, accounts, delta, checking, old, _ = prepare(db, plaid, monkeypatch)
    review = plaid_sync.stage_review(db, e)
    plan = plaid_sync.preview_plan(db, e, review["review_id"], selections(checking))
    old.notes = "New edit after preview"
    db.add(old)
    db.commit()
    with pytest.raises(plaid.PlaidError, match="Local data changed"):
        plaid_sync.approve_plan(db, e, plan["plan_id"])
    state.reviewed_at = datetime.now(UTC) - timedelta(minutes=16)
    db.add(state)
    db.commit()
    with pytest.raises(plaid.PlaidError, match="expired"):
        plaid_sync.approve_plan(db, e, plan["plan_id"])
    assert len(db.exec(select(Account)).all()) == 1


@pytest.mark.parametrize(
    "description",
    [
        "AUTOPAY PAYMENT REFUND",
        "Rewards payment",
        "Online payment reversed",
        "Merchant refund",
    ],
)
def test_refunds_and_rewards_do_not_become_card_payments(description):
    from mypocket.domain.categorize import categorize

    assert categorize(description, 10, account_type="credit").category != "Transfers"
