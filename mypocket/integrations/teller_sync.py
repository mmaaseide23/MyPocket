"""Pull data from Teller API → upsert into MyPocket DB."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from datetime import date as Date

from sqlmodel import Session, select

from mypocket.domain.categorize import categorize
from mypocket.domain.models import Account, Enrollment, Transaction
from mypocket.integrations import teller

logger = logging.getLogger(__name__)


def _parse_date(s: str | None) -> Date | None:
    if not s:
        return None
    try:
        return datetime.strptime(s[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _account_type(t: str | None, subtype: str | None) -> str:
    """Map Teller's (type, subtype) to our internal type."""
    t = (t or "").lower()
    s = (subtype or "").lower()
    if "credit" in t:
        return "credit"
    if "investment" in t or "brokerage" in s:
        return "brokerage"
    if "ira" in s or "retirement" in s:
        return "ira"
    if "saving" in s:
        return "savings"
    if "checking" in s or t == "depository":
        return "checking"
    return s or t or "other"


def sync_enrollment(session: Session, enrollment: Enrollment) -> dict:
    """Pull all accounts + balances + transactions for one enrollment."""
    out = {
        "enrollment_id": enrollment.id,
        "accounts": 0,
        "transactions_created": 0,
        "transactions_skipped": 0,
        "errors": [],
    }

    try:
        teller_accounts = teller.list_accounts(enrollment.access_token)
    except teller.TellerNeedsReauth as e:
        enrollment.status = "needs_reauth"
        enrollment.last_error = str(e)
        session.add(enrollment)
        session.commit()
        out["errors"].append(f"needs_reauth: {e}")
        return out
    except Exception as e:
        enrollment.last_error = str(e)
        session.add(enrollment)
        session.commit()
        out["errors"].append(f"list_accounts failed: {e}")
        return out

    for ta in teller_accounts:
        out["accounts"] += 1
        try:
            counts = _sync_one_account(session, enrollment, ta)
            out["transactions_created"] += counts["created"]
            out["transactions_skipped"] += counts["skipped"]
        except Exception as e:
            out["errors"].append(f"account {ta.get('id')}: {e}")

    enrollment.last_synced = datetime.now(UTC)
    enrollment.status = "active" if not out["errors"] else enrollment.status
    enrollment.last_error = None if not out["errors"] else (out["errors"][-1])[:500]
    session.add(enrollment)
    session.commit()
    return out


def _sync_one_account(session: Session, enrollment: Enrollment, ta: dict) -> dict:
    ext_id = ta.get("id")
    institution_name = (ta.get("institution") or {}).get("name") or enrollment.institution or "Bank"
    enrollment.institution = enrollment.institution or institution_name
    enrollment.institution_id = enrollment.institution_id or (ta.get("institution") or {}).get("id")

    account = session.exec(
        select(Account).where(Account.external_id == ext_id, Account.source == "teller")
    ).first()
    if account is None:
        account = Account(
            source="teller",
            external_id=ext_id,
            name=ta.get("name") or f"{institution_name} {ta.get('last_four') or ''}".strip(),
            type=_account_type(ta.get("type"), ta.get("subtype")),
            institution=institution_name,
            currency=ta.get("currency") or "USD",
        )

    # Try to fetch balance. Semantic depends on account type:
    #   • credit: store the outstanding balance OWED (positive number = debt).
    #     `ledger` is the current statement balance; `available` is unused credit
    #     and was previously being stored — which inflated net worth.
    #   • depository: prefer `available` (immediately spendable); fall back to ledger.
    try:
        bal = teller.get_balances(enrollment.access_token, ext_id)
        if account.type == "credit":
            raw = bal.get("ledger") or bal.get("available")
            if raw is not None:
                # Some banks return credit balances as negative; we standardize on
                # positive = "you owe this much".
                account.balance = abs(float(raw))
        else:
            raw = bal.get("available") or bal.get("ledger")
            if raw is not None:
                account.balance = float(raw)
    except Exception as e:
        logger.warning("teller: balance fetch failed for %s: %s", ext_id, e)

    account.last_synced = datetime.now(UTC)
    session.add(account)
    session.commit()
    session.refresh(account)

    # Fetch transactions. Teller returns most recent first.
    try:
        txns = teller.list_transactions(enrollment.access_token, ext_id, count=500)
    except teller.TellerNeedsReauth:
        enrollment.status = "needs_reauth"
        session.add(enrollment)
        session.commit()
        raise
    except Exception as e:
        logger.warning("teller: transactions fetch failed for %s: %s", ext_id, e)
        txns = []

    existing_ext_ids = {
        t.external_id
        for t in session.exec(select(Transaction).where(Transaction.account_id == account.id)).all()
        if t.external_id
    }

    created = 0
    skipped = 0
    for t in txns:
        tid = t.get("id")
        if not tid:
            continue
        if tid in existing_ext_ids:
            skipped += 1
            continue

        tx_date = _parse_date(t.get("date")) or Date.today()
        amount_raw = t.get("amount")
        try:
            amount = float(amount_raw)
        except (TypeError, ValueError):
            continue

        details = t.get("details") or {}
        counterparty = (
            (details.get("counterparty") or {}) if isinstance(details.get("counterparty"), dict) else {}
        )
        merchant_name = counterparty.get("name") or t.get("description") or ""
        teller_category = details.get("category")
        description = t.get("description") or merchant_name or "Transaction"

        match = categorize(description, amount, account_type=account.type)
        # Prefer Teller's category if our rules left it Uncategorized
        if match.category == "Uncategorized" and teller_category:
            match.category = teller_category.replace("_", " ").title()

        status = t.get("status") or "posted"

        session.add(
            Transaction(
                account_id=account.id,
                external_id=tid,
                tx_date=tx_date,
                amount=amount,
                description=description,
                merchant=merchant_name or match.merchant,
                category=match.category,
                subcategory=match.subcategory,
                transaction_type=status,
                raw=json.dumps(t),
            )
        )
        created += 1

    session.commit()
    return {"created": created, "skipped": skipped}


def sync_all(session: Session) -> list[dict]:
    enrollments = session.exec(select(Enrollment).where(Enrollment.provider == "teller")).all()
    return [sync_enrollment(session, e) for e in enrollments]
