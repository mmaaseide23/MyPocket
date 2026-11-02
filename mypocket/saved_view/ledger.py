"""Record local sync attempts and immutable balance observations, only when opted in."""

from contextvars import ContextVar
from datetime import UTC, datetime, timedelta
from functools import wraps
from uuid import uuid4

from sqlalchemy import delete, insert, select, update

from mypocket.core.config import settings
from mypocket.integrations._bank_lock import bank_lock
from mypocket.saved_view.policy import SavedViewUnavailable, minor_units, supplied_time, utc
from mypocket.saved_view.schema import account_scopes, balances, runs, sources
from mypocket.saved_view.service import _consent

_observations: ContextVar[dict | None] = ContextVar("saved_view_observations", default=None)
_approved_accounts: ContextVar[frozenset] = ContextVar("saved_view_accounts", default=frozenset())
HISTORY_DAYS = 30  # Proposed local history lifetime; collection defaults to off.


def start_run(session, enrollment_id, provider, *, now=None):
    now = supplied_time(now or datetime.now(UTC))
    if provider not in {"plaid", "etrade", "teller"}:
        raise ValueError("Unsupported sync provider")
    if not session.execute(select(sources.c.enrollment_id).where(sources.c.enrollment_id == enrollment_id)).first():
        session.execute(insert(sources).values(enrollment_id=enrollment_id, public_ref=str(uuid4()), provider=provider))
    run_id = str(uuid4())
    session.execute(insert(runs).values(id=run_id, enrollment_id=enrollment_id, started_at=now, outcome="running"))
    session.commit()
    return run_id


def observe_balance(account, *, now=None, provider_as_of=None):
    """Adapters call only after receiving a balance; never infer provider time."""
    pending = _observations.get()
    if pending is None:
        return
    if account.id not in _approved_accounts.get():
        return
    now = supplied_time(now or datetime.now(UTC))
    upstream = supplied_time(provider_as_of, no_later_than=now) if provider_as_of else None
    pending[account.id] = {"account_id": account.id, "balance_minor": minor_units(account.balance),
                           "account_type": account.type, "currency": account.currency,
                           "observed_at": now, "provider_as_of": upstream}


def finish_run(session, run_id, outcome, observations=(), *, now=None, provider_as_of=None):
    now = supplied_time(now or datetime.now(UTC))
    if outcome not in {"success", "partial", "error"}:
        raise ValueError("Invalid completion outcome")
    if outcome != "success" and provider_as_of is not None:
        raise ValueError("Failed checks cannot establish provider freshness")
    previous = session.execute(select(runs).where(runs.c.id == run_id)).mappings().one()
    if previous["outcome"] != "running" or now < utc(previous["started_at"]):
        raise ValueError("Check is already finished or completion precedes its start")
    upstream = supplied_time(provider_as_of, no_later_than=now) if provider_as_of else None
    rows = []
    if outcome == "success":
        for observation in observations:
            row = dict(observation)
            row["observed_at"] = supplied_time(row["observed_at"], no_later_than=now)
            if row["observed_at"] < utc(previous["started_at"]):
                raise ValueError("Observation precedes its check")
            if row.get("provider_as_of"):
                row["provider_as_of"] = supplied_time(row["provider_as_of"], no_later_than=row["observed_at"])
            rows.append({"run_id": run_id, **row})
    if rows:
        session.execute(insert(balances), rows)
    session.execute(update(runs).where(runs.c.id == run_id).values(finished_at=now, outcome=outcome, provider_as_of=upstream))
    cutoff = now - timedelta(days=HISTORY_DAYS)
    expired = select(runs.c.id).where(runs.c.started_at < cutoff)
    session.execute(delete(balances).where(balances.c.run_id.in_(expired)))
    session.execute(delete(runs).where(runs.c.started_at < cutoff))
    session.commit()


def track_sync(provider):
    def decorate(sync):
        @wraps(sync)
        def tracked(session, enrollment, *args, **kwargs):
            if not settings.saved_view_insights_enabled or enrollment.provider != provider or enrollment.status != "active":
                return sync(session, enrollment, *args, **kwargs)
            with bank_lock:
                try:
                    consent = _consent(session)
                except SavedViewUnavailable:
                    return sync(session, enrollment, *args, **kwargs)
                if not consent["history_enabled"]:
                    return sync(session, enrollment, *args, **kwargs)
                allowed = frozenset(session.execute(select(account_scopes.c.account_id)).scalars())
                run_id = start_run(session, enrollment.id, provider)
                pending = {}
                token = _observations.set(pending)
                account_token = _approved_accounts.set(allowed)
                try:
                    result = sync(session, enrollment, *args, **kwargs)
                except Exception:
                    session.rollback()
                    finish_run(session, run_id, "error")
                    raise
                else:
                    outcome = ("partial" if pending else "error") if result.get("errors") else "success"
                    finish_run(session, run_id, outcome, pending.values())
                    return result
                finally:
                    _observations.reset(token)
                    _approved_accounts.reset(account_token)
        return tracked
    return decorate
