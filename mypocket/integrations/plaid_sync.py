"""Staged Plaid cutover and atomic cursor-based sync.

Normal matching retains local identity and imports after the last historical date.
Explicit replacement instead archives the old account and imports available history
into a fresh account, atomically, after a verified backup. Plaid pending/removed rows
are retained but excluded from analytics; updates preserve categories and notes.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import secrets
from datetime import UTC, date, datetime, timedelta

from sqlmodel import Session, select

from mypocket.domain.categorize import categorize
from mypocket.domain.models import (
    Account,
    CitiReset,
    Enrollment,
    Holding,
    PlaidAccount,
    PlaidConnection,
    PlaidTransaction,
    Transaction,
)
from mypocket.integrations import plaid
from mypocket.integrations._bank_lock import bank_lock
from mypocket.integrations._errors import safe_error


def _fail(message="Plaid returned invalid data. Previous data has been retained."):
    raise plaid.PlaidError(message)


def _id(value):
    if not isinstance(value, str) or not value or len(value) > 4096:
        _fail()
    return value


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
        _fail()
    return float(value)


def _account(remote):
    if not isinstance(remote, dict):
        _fail()
    _id(remote.get("account_id"))
    kind, subtype = remote.get("type"), remote.get("subtype")
    if kind == "credit" and subtype == "credit card":
        local_type = "credit"
    elif kind == "depository" and subtype in {"checking", "savings"}:
        local_type = subtype
    else:
        _fail("Only Citi checking, savings and credit cards are supported by this adapter.")
    balances = remote.get("balances") or {}
    if balances.get("iso_currency_code") != "USD":
        _fail("Only USD Citi accounts can be imported by this adapter.")
    value = balances.get("current") if local_type == "credit" else balances.get("available")
    if value is None:
        value = balances.get("current")
    # Negative credit balances mean the bank owes the user; preserve the sign.
    balance = None if value is None else _number(value)
    return local_type, balance


def _validate_delta(delta):
    for group in ("added", "modified", "removed"):
        if not isinstance(delta.get(group), list):
            _fail()
        for item in delta[group]:
            if not isinstance(item, dict):
                _fail()
            _id(item.get("transaction_id"))
            if group == "removed":
                continue
            _id(item.get("account_id"))
            try:
                date.fromisoformat(item.get("date"))
            except (ValueError, TypeError):
                _fail()
            _number(item.get("amount"))
            if not isinstance(item.get("pending"), bool) or item.get("iso_currency_code") != "USD":
                _fail()
            if not isinstance(item.get("name"), str):
                _fail()
    if not isinstance(delta.get("next_cursor"), str):
        _fail()


def _state(session, enrollment):
    state = session.get(PlaidConnection, enrollment.id)
    if not state:
        _fail("Plaid connection state is missing. No data was changed.")
    return state


def _fingerprint(session):
    # Private digest only. Includes notes/categories so a stale plan cannot overwrite edits.
    records = []
    for model in (Account, Transaction, PlaidAccount):
        records.append([r.model_dump(mode="json") for r in session.exec(select(model)).all()])
    return hashlib.sha256(json.dumps(records, sort_keys=True).encode()).hexdigest()


def _eligible_accounts(session):
    owned = set(session.exec(select(PlaidAccount.account_id)).all())
    return [
        a
        for a in session.exec(select(Account)).all()
        if a.id not in owned
        and not a.archived
        and a.source in {"teller", "citi_csv"}
        and a.type in {"checking", "savings", "credit"}
        and a.currency == "USD"
        and re.match(r"^(citi|citibank)\b", a.institution, re.I)
    ]


def stage_review(session: Session, enrollment: Enrollment) -> dict:
    with bank_lock:
        state = _state(session, enrollment)
        if state.approved:
            _fail("This connection is already mapped. Use Sync or Reconnect instead.")
        data = plaid.accounts(enrollment.access_token, enrollment.environment)
        if data["item"].get("item_id") != enrollment.enrollment_id:
            _fail("Plaid returned a different connection. No data was changed.")
        institution = plaid.institution_name(_id(data["item"].get("institution_id")), enrollment.environment)
        if not re.match(r"^(citi|citibank)\b", institution, re.I):
            _fail("Choose Citi in Plaid Link. This adapter only supports Citi; nothing was imported.")
        remote = data["accounts"]
        if not remote or len({_id(a.get("account_id")) for a in remote}) != len(remote):
            _fail("Plaid returned no accounts or duplicate accounts.")
        for a in remote:
            _account(a)
        delta = plaid.sync_delta(enrollment.access_token, enrollment.environment, "")
        _validate_delta(delta)
        if delta.get("transactions_update_status") != "HISTORICAL_UPDATE_COMPLETE":
            _fail("Plaid is still preparing history. Wait a few minutes, then Review again; do not relink.")
        # Reduce initial patches to the final snapshot, including any modifications/removals.
        current = {t["transaction_id"]: t for t in delta["added"] + delta["modified"]}
        for r in delta["removed"]:
            current.pop(r["transaction_id"], None)
        delta = {**delta, "added": list(current.values()), "modified": [], "removed": []}
        if any(t["account_id"] not in {a["account_id"] for a in remote} for t in delta["added"]):
            _fail("Plaid account selection changed. Review again before importing.")
        state.review_json = json.dumps({"accounts": remote, "delta": delta})
        state.review_id = secrets.token_urlsafe(24)
        state.reviewed_at = datetime.now(UTC)
        state.plan_id = state.plan_json = None
        enrollment.institution = institution
        enrollment.status = "pending_review"
        session.add(state)
        session.add(enrollment)
        session.commit()
        return {
            "review_id": state.review_id,
            "accounts": [
                {
                    "external_id": a["account_id"],
                    "name": a.get("name") or "Citi account",
                    "mask": a.get("mask") or "",
                    "type": _account(a)[0],
                    "balance": _account(a)[1],
                }
                for a in remote
            ],
            "existing_accounts": [
                {
                    "id": a.id,
                    "name": a.name,
                    "type": a.type,
                    "last_date": max(
                        (
                            t.tx_date
                            for t in session.exec(
                                select(Transaction).where(Transaction.account_id == a.id)
                            ).all()
                        ),
                        default=None,
                    ),
                }
                for a in _eligible_accounts(session)
            ],
        }


def _review(state, review_id=None):
    issued = state.reviewed_at
    if (
        not state.review_json
        or state.approved
        or not issued
        or datetime.now(UTC) - issued.replace(tzinfo=UTC) > timedelta(minutes=15)
        or review_id is not None
        and review_id != state.review_id
    ):
        _fail("Review expired or changed. Load a fresh review before continuing.")
    return json.loads(state.review_json)


def preview_plan(session: Session, enrollment: Enrollment, review_id: str, selections: list[dict]) -> dict:
    with bank_lock:
        state = _state(session, enrollment)
        snapshot = _review(state, review_id)
        remote = {a["account_id"]: a for a in snapshot["accounts"]}
        if len(selections) != len(remote) or {x.get("external_id") for x in selections} != set(remote):
            _fail("Choose an explicit action for every linked account.")
        eligible = {a.id: a for a in _eligible_accounts(session)}
        used = set()
        plan = []
        for choice in selections:
            remote_id = choice["external_id"]
            a = remote[remote_id]
            mode = choice.get("mode")
            local_id = choice.get("account_id")
            start = None
            preserved = 0
            archived = 0
            legacy_pending = []
            if mode in {"existing", "replace"}:
                local = eligible.get(local_id)
                if not local or local_id in used or local.type != _account(a)[0]:
                    _fail("Existing account mapping is invalid, already managed, or has the wrong type.")
                used.add(local_id)
                history = session.exec(select(Transaction).where(Transaction.account_id == local_id)).all()
                legacy_pending = [
                    t.tx_date for t in history if t.transaction_type == "pending" and not t.excluded
                ]
                if mode == "replace":
                    if session.exec(select(Holding.id).where(Holding.account_id == local_id)).first():
                        _fail("This account has investment records and cannot use a Citi replacement.")
                    archived = len(history)
                else:
                    last = max((t.tx_date for t in history), default=None)
                    start = last + timedelta(days=1) if last else None
                    preserved = len(history)
                name = local.name
            elif mode == "new" or mode == "ignore":
                local_id = None
                name = a.get("name") or "Citi account"
            else:
                _fail("Choose existing, replace, genuinely new, or ignore for every account.")
            transactions = [t for t in snapshot["delta"]["added"] if t["account_id"] == remote_id]
            if mode == "replace" and (not transactions or _account(a)[1] is None):
                _fail("Fresh history and a balance are required before replacing this account.")
            dates = [t["date"] for t in transactions]
            skipped = sum(bool(start and date.fromisoformat(t["date"]) < start) for t in transactions)
            plan.append(
                {
                    "external_id": remote_id,
                    "mode": mode,
                    "account_id": local_id,
                    "name": name,
                    "import_from": start.isoformat() if start else None,
                    "preserved": preserved,
                    "archived": archived,
                    "received_from": min(dates) if dates else None,
                    "received_through": max(dates) if dates else None,
                    "history_skipped": skipped,
                    "transactions_to_import": len(transactions) - skipped if mode != "ignore" else 0,
                    "pending": sum(t["pending"] for t in transactions),
                    "legacy_pending": len(legacy_pending),
                    "legacy_pending_from": min(legacy_pending).isoformat() if legacy_pending else None,
                    "legacy_pending_through": max(legacy_pending).isoformat() if legacy_pending else None,
                    "balance": _account(a)[1],
                    "type": _account(a)[0],
                }
            )
        if all(x["mode"] == "ignore" for x in plan):
            _fail("Select at least one account to import.")
        blocked = any(p["legacy_pending"] and p["mode"] != "replace" for p in plan)
        # Show useful counts, but leave no approvable draft while history needs
        # reconciliation. Also invalidate a previous draft for other selections.
        state.plan_id = None if blocked else secrets.token_urlsafe(24)
        state.plan_json = (
            None if blocked else json.dumps({"accounts": plan, "fingerprint": _fingerprint(session)})
        )
        session.add(state)
        session.commit()
        return {
            "plan_id": state.plan_id,
            "blocked": blocked,
            "accounts": plan,
            "warning": "Replace archives the selected old account and its history, then imports only the history Plaid supplies. "
            "A verified local backup is required. Accounts not selected for replacement remain unchanged. "
            "Existing history is retained through its last date. Older Plaid history is skipped, not reconciled. "
            "Check for gaps or incomplete historical days before approving. Pending legacy entries block Existing mode; Replace archives them. "
            "New means this is genuinely absent from MyPocket. Teller stops updating mapped or archived accounts.",
        }


def approve_plan(session: Session, enrollment: Enrollment, plan_id: str) -> dict:
    with bank_lock:
        session.expire_all()
        state = _state(session, enrollment)
        snapshot = _review(state)
        if state.plan_id != plan_id or not state.plan_json:
            _fail("Preview this mapping before approving it.")
        plan = json.loads(state.plan_json)
        if plan["fingerprint"] != _fingerprint(session):
            _fail("Local data changed after preview. Review the mapping again.")
        try:
            backup = None
            if any(p["mode"] == "replace" for p in plan["accounts"]):
                from mypocket.citi_reset import backup_session

                backup = backup_session(session)
                # The backup takes the database write lock. Recheck after acquiring it.
                session.expire_all()
                if plan["fingerprint"] != _fingerprint(session):
                    _fail("Local data changed during backup. Review the mapping again.")
            for p in plan["accounts"]:
                local_id = p["account_id"]
                old = None
                if p["mode"] == "replace":
                    old = session.get(Account, local_id)
                    if old not in _eligible_accounts(session):
                        _fail("Replacement account changed; no reset made.")
                    old.archived = True
                    session.add(old)
                if p["mode"] in {"new", "replace"}:
                    a = Account(
                        source="plaid",
                        external_id=p["external_id"],
                        name=p["name"],
                        type=p["type"],
                        institution="Citibank",
                        currency="USD",
                    )
                    session.add(a)
                    session.flush()
                    local_id = a.id
                    if old:
                        session.add(
                            CitiReset(
                                original_id=old.id,
                                replacement_id=a.id,
                                enrollment_id=enrollment.id,
                                backup_name=backup.name,
                            )
                        )
                session.add(
                    PlaidAccount(
                        enrollment_id=enrollment.id,
                        external_id=p["external_id"],
                        account_id=local_id,
                        import_from=date.fromisoformat(p["import_from"]) if p["import_from"] else None,
                    )
                )
            session.flush()
            counts = _apply(session, enrollment, snapshot["accounts"], snapshot["delta"])
            state.approved = True
            state.cursor = snapshot["delta"]["next_cursor"]
            state.review_json = state.review_id = state.plan_json = state.plan_id = None
            enrollment.status = "active"
            enrollment.last_synced = datetime.now(UTC)
            enrollment.last_error = None
            session.add(state)
            session.add(enrollment)
            session.commit()
            return counts
        except Exception:
            session.rollback()
            raise


def _apply(session, enrollment, remote_accounts, delta):
    _validate_delta(delta)
    mapping = {
        m.external_id: m
        for m in session.exec(select(PlaidAccount).where(PlaidAccount.enrollment_id == enrollment.id)).all()
    }
    remote_ids = {_id(a.get("account_id")) for a in remote_accounts}
    if remote_ids != set(mapping):
        _fail(
            "Plaid account selection changed. Sync paused to prevent duplicate or missing accounts; review required."
        )
    counts = {
        "accounts": 0,
        "transactions_created": 0,
        "transactions_modified": 0,
        "transactions_removed": 0,
        "errors": [],
    }
    for remote in remote_accounts:
        _kind, balance = _account(remote)
        m = mapping[remote["account_id"]]
        if m.account_id is None:
            continue
        a = session.get(Account, m.account_id)
        if not a or a.type != _kind:
            _fail("A mapped local account changed or is missing. Sync paused; review required.")
        if balance is not None:
            a.balance = balance
        a.last_synced = datetime.now(UTC)
        session.add(a)
        counts["accounts"] += 1
    # A batch can contain both pending and posted IDs in arbitrary order.
    updates = sorted(delta["added"] + delta["modified"], key=lambda t: not t["pending"])
    for remote in updates:
        m = mapping.get(remote["account_id"])
        if m is None:
            _fail("Plaid returned an unreviewed account. Sync paused; no data changed.")
        if m.account_id is None:
            continue
        tid = remote["transaction_id"]
        link = session.get(PlaidTransaction, (enrollment.id, tid))
        if link and link.transaction_id is None:
            continue  # historical overlap permanently belongs to the old source
        t = session.get(Transaction, link.transaction_id) if link else None
        if link and t and t.external_id != f"plaid:{tid}":
            continue  # Superseded pending alias must not overwrite the posted row.
        pending_id = remote.get("pending_transaction_id")
        if not t and pending_id:
            pending = session.get(PlaidTransaction, (enrollment.id, pending_id))
            t = (
                session.get(Transaction, pending.transaction_id)
                if pending and pending.transaction_id
                else None
            )
        tx_date = date.fromisoformat(remote["date"])
        if t and t.account_id != m.account_id:
            _fail("Plaid changed a transaction account. Manual reconciliation is required.")
        if t is None and m.import_from and tx_date < m.import_from:
            session.add(PlaidTransaction(enrollment_id=enrollment.id, external_id=tid))
            session.flush()
            continue
        amount = -_number(remote["amount"])
        if t is None:
            match = categorize(remote["name"], amount, account_type=session.get(Account, m.account_id).type)
            t = Transaction(
                account_id=m.account_id,
                tx_date=tx_date,
                amount=amount,
                description=remote["name"],
                category=match.category,
                subcategory=match.subcategory,
            )
            counts["transactions_created"] += 1
        else:
            counts["transactions_modified"] += 1
        t.external_id = f"plaid:{tid}"
        t.tx_date = tx_date
        t.amount = amount
        t.description = remote["name"]
        t.merchant = remote.get("merchant_name") or None
        t.transaction_type = "pending" if remote["pending"] else "posted"
        t.excluded = remote["pending"]
        # No provider payload/token is persisted in raw; preserve existing annotations.
        session.add(t)
        session.flush()
        if link is None:
            session.add(PlaidTransaction(enrollment_id=enrollment.id, external_id=tid, transaction_id=t.id))
            session.flush()
    # Removals win over same-ID modifications; a superseded pending ID cannot
    # exclude its posted replacement because external_id now names the posted ID.
    for remote in delta["removed"]:
        link = session.get(PlaidTransaction, (enrollment.id, remote["transaction_id"]))
        t = session.get(Transaction, link.transaction_id) if link and link.transaction_id else None
        if t and t.external_id == f"plaid:{remote['transaction_id']}":
            t.excluded = True
            session.add(t)
            counts["transactions_removed"] += 1
    return counts


def sync_enrollment(session: Session, enrollment: Enrollment) -> dict:
    with bank_lock:
        session.expire_all()
        state = _state(session, enrollment)
        if not state.approved or enrollment.status != "active":
            return {"errors": ["Review or reconnect this Plaid connection before syncing."]}
        try:
            data = plaid.accounts(enrollment.access_token, enrollment.environment)
            if data["item"].get("item_id") != enrollment.enrollment_id:
                _fail("Plaid returned a different connection. No data changed.")
            delta = plaid.sync_delta(enrollment.access_token, enrollment.environment, state.cursor)
            counts = _apply(session, enrollment, data["accounts"], delta)
            state.cursor = delta["next_cursor"]
            enrollment.last_synced = datetime.now(UTC)
            enrollment.last_error = None
            session.add(state)
            session.add(enrollment)
            session.commit()
            return counts
        except Exception as exc:
            session.rollback()
            if isinstance(exc, plaid.PlaidNeedsReauth):
                enrollment.status = "needs_reauth"
            enrollment.last_error = safe_error(exc)
            session.add(enrollment)
            session.commit()
            return {"transactions_created": 0, "errors": [safe_error(exc)]}


def sync_all(session: Session) -> list[dict]:
    ids = session.exec(
        select(Enrollment.id)
        .join(PlaidConnection, PlaidConnection.enrollment_id == Enrollment.id)
        .where(
            Enrollment.provider == "plaid", Enrollment.status == "active", PlaidConnection.approved.is_(True)
        )
    ).all()  # noqa: E712
    return [sync_enrollment(session, session.get(Enrollment, eid)) for eid in ids]
