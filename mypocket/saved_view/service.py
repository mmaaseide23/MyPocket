"""Minimal export projection. No routes or activation are installed by this module.

Only explicitly selected account IDs can be projected. Queries intentionally select
columns rather than credential-bearing Enrollment/AppConfig ORM entities.
"""

from datetime import UTC, datetime
from functools import wraps
from uuid import uuid4

from sqlalchemy import delete, insert, select, update

from mypocket.domain.models import Account, AppConfig, Enrollment
from mypocket.integrations._bank_lock import bank_lock
from mypocket.saved_view.policy import (
    ACCOUNT_LABELS,
    FIELD_SET,
    SavedViewUnavailable,
    minor_units,
    retention,
    supplied_time,
    utc,
)
from mypocket.saved_view.schema import account_scopes, balances, consents, runs, sources


def stamp(value):
    return utc(value).isoformat().replace("+00:00", "Z") if value else None


def _owner_created_at(session):
    value = session.execute(select(AppConfig.created_at).where(AppConfig.id == 1)).scalar_one_or_none()
    if value is None:
        raise SavedViewUnavailable("owner_unavailable")
    return utc(value)


def policy_change(operation):
    @wraps(operation)
    def serialized(*args, **kwargs):
        # Serialize consent/removal with an already-running provider check so a
        # late completion cannot recreate history after revocation.
        with bank_lock:
            return operation(*args, **kwargs)
    return serialized


@policy_change
def record_approval(session, *, account_ids, field_set, retention_seconds, history_enabled=False, now=None):
    """Internal integration seam, NOT an HTTP endpoint or automatic consent.

    A future authenticated approval screen must supply the explicit policy and
    account list. This phase invokes it only in synthetic tests.
    """
    now = supplied_time(now or datetime.now(UTC))
    if field_set != FIELD_SET:
        raise SavedViewUnavailable("unsupported_fields")
    retention_seconds = retention(retention_seconds)
    if type(history_enabled) is not bool:
        raise SavedViewUnavailable("invalid_history_choice")
    if (not isinstance(account_ids, (list, tuple)) or not 1 <= len(account_ids) <= 20
            or any(type(value) is not int or value < 1 for value in account_ids)
            or len(set(account_ids)) != len(account_ids)):
        raise SavedViewUnavailable("invalid_selection")
    selected = session.execute(select(Account.id, Account.type, Account.currency).where(
        Account.id.in_(account_ids), Account.archived == False,  # noqa: E712
    )).all()
    if len(selected) != len(account_ids):
        raise SavedViewUnavailable("invalid_selection")
    if any(row.type not in ACCOUNT_LABELS or row.currency != "USD" for row in selected):
        raise SavedViewUnavailable("unsupported_account")
    owner_created = _owner_created_at(session)
    previous = session.execute(select(consents.c.generation).where(consents.c.id == 1)).scalar_one_or_none()
    session.execute(delete(account_scopes))
    session.execute(delete(consents))
    if not history_enabled:
        clear_history(session)
    else:
        session.execute(delete(balances).where(balances.c.account_id.not_in(account_ids)))
    session.execute(insert(consents).values(
        id=1, active=True, owner_scope=str(uuid4()), generation=(previous or 0) + 1,
        app_created_at=owner_created, approved_at=now, field_set=field_set, retention_seconds=retention_seconds,
        history_enabled=history_enabled,
    ))
    counts = {}
    for row in sorted(selected, key=lambda account: account.id):
        counts[row.type] = counts.get(row.type, 0) + 1
        session.execute(insert(account_scopes).values(
            account_id=row.id, public_ref=str(uuid4()), account_type=row.type,
            label=f"{ACCOUNT_LABELS[row.type]} {counts[row.type]}",
        ))
    session.commit()
    return access_state(session)


def _consent(session):
    row = session.execute(select(consents).where(consents.c.id == 1)).mappings().first()
    if not row or not row["active"]:
        raise SavedViewUnavailable("not_approved")
    if utc(row["app_created_at"]) != _owner_created_at(session):
        raise SavedViewUnavailable("owner_changed")
    if row["field_set"] != FIELD_SET:
        raise SavedViewUnavailable("unsupported_fields")
    retention(row["retention_seconds"])
    return row


def access_state(session):
    row = _consent(session)
    return {"kind": "saved-view-access", "version": 1, "active": True,
            "owner_scope": row["owner_scope"], "generation": row["generation"],
            "field_set": FIELD_SET, "retention_seconds": row["retention_seconds"]}


@policy_change
def revoke(session):
    """Stop future exports. Disconnected devices learn this only when reachable."""
    session.execute(update(consents).where(consents.c.id == 1).values(
        active=False, generation=consents.c.generation + 1,
    ))
    session.execute(delete(account_scopes))
    clear_history(session)
    session.commit()


def clear_history(session):
    session.execute(delete(balances))
    session.execute(delete(runs))
    session.execute(delete(sources))


def approval_options(session):
    """Online-only account choices; names, numbers and balances are never queried."""
    _owner_created_at(session)
    rows = session.execute(select(Account.id, Account.type, Account.currency).where(
        Account.archived == False,  # noqa: E712
    ).order_by(Account.id)).all()
    counts, choices = {}, []
    for row in rows:
        if row.type not in ACCOUNT_LABELS or row.currency != "USD":
            continue
        counts[row.type] = counts.get(row.type, 0) + 1
        choices.append({"id": row.id, "label": f"{ACCOUNT_LABELS[row.type]} {counts[row.type]}", "type": row.type})
    return {"field_set": FIELD_SET, "accounts": choices, "retention_choices": [3600, 43200, 86400, 259200, 604800]}


def source_freshness(session, enrollment_id, *, now):
    source = session.execute(select(sources).where(sources.c.enrollment_id == enrollment_id)).mappings().one()
    # Never load access_token, access_token_secret, last_error, or institution.
    state = session.execute(select(Enrollment.status).where(Enrollment.id == enrollment_id)).scalar_one_or_none()
    latest = session.execute(select(runs).where(runs.c.enrollment_id == enrollment_id)
                             .order_by(runs.c.started_at.desc(), runs.c.id.desc())).mappings().first()
    success = session.execute(select(runs).where(runs.c.enrollment_id == enrollment_id, runs.c.outcome == "success")
                              .order_by(runs.c.finished_at.desc(), runs.c.id.desc())).mappings().first()
    dates = [latest["started_at"] if latest else None, success["finished_at"] if success else None]
    if any(value and utc(value) > now for value in dates):
        raise SavedViewUnavailable("invalid_history")
    return {"ref": source["public_ref"], "provider": source["provider"],
            "connection_state": state if state in {"active", "needs_reauth", "disconnected"} else "unavailable",
            "last_attempt_at": stamp(latest["started_at"]) if latest else None,
            "attempt_outcome": latest["outcome"] if latest else "unknown",
            "last_successful_check_at": stamp(success["finished_at"]) if success else None,
            "provider_as_of": stamp(success["provider_as_of"]) if success else None}


def snapshot(session, *, now=None):
    """Read a consistent local copy; this never initiates a provider refresh."""
    now = supplied_time(now or datetime.now(UTC))
    grant = access_state(session)
    rows = session.execute(select(
        Account.id, Account.type, Account.currency, Account.balance,
        account_scopes.c.public_ref, account_scopes.c.account_type, account_scopes.c.label,
    ).join(account_scopes, account_scopes.c.account_id == Account.id)
      .where(Account.archived == False).order_by(Account.id)).all()  # noqa: E712
    if not rows:
        raise SavedViewUnavailable("no_selected_accounts")
    projected, source_rows = [], {}
    for row in rows:
        if row.currency != "USD" or row.type != row.account_type or row.type not in ACCOUNT_LABELS:
            raise SavedViewUnavailable("selection_changed")
        amount = minor_units(row.balance)
        point = session.execute(select(balances, runs.c.enrollment_id).join(runs, balances.c.run_id == runs.c.id)
                                .where(balances.c.account_id == row.id, runs.c.outcome == "success")
                                .order_by(balances.c.observed_at.desc(), runs.c.id.desc())).mappings().first()
        freshness, source_ref, observed, upstream = "unrecorded", None, None, None
        if point:
            if utc(point["observed_at"]) > now:
                raise SavedViewUnavailable("invalid_history")
            enrollment_id = point["enrollment_id"]
            if enrollment_id not in source_rows:
                source_rows[enrollment_id] = source_freshness(session, enrollment_id, now=now)
            source_ref = source_rows[enrollment_id]["ref"]
            matches = (point["balance_minor"] == amount and point["account_type"] == row.type
                       and point["currency"] == row.currency)
            # A partial sync may have updated a live balance. The older successful
            # run does not prove the freshness of that replacement value.
            freshness = "recorded" if matches else "unverified"
            if matches:
                observed, upstream = stamp(point["observed_at"]), stamp(point["provider_as_of"])
        projected.append({"ref": row.public_ref, "label": row.label, "type": row.type,
                          "balance_minor": amount, "source_ref": source_ref, "observed_at": observed,
                          "provider_as_of": upstream, "freshness": freshness})
    return {"kind": "saved-view", "version": 1, "owner_scope": grant["owner_scope"],
            "generation": grant["generation"], "field_set": FIELD_SET,
            "retention_seconds": grant["retention_seconds"], "captured_at": stamp(now),
            "currency": "USD", "accounts": projected, "sources": list(source_rows.values())}
