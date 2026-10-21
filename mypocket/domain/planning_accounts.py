"""Read saved investment account totals, without holdings queries or provider calls."""

from datetime import UTC, datetime, timedelta
from math import isfinite

from sqlmodel import Session, select

from mypocket.domain.models import Account


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def saved_accounts(session: Session, *, now: datetime | None = None) -> dict:
    now = _utc(now or datetime.now(UTC))
    warnings: set[str] = set()
    accounts = session.exec(
        select(Account).where(Account.archived == False, Account.type.in_(["brokerage", "ira"]))  # noqa: E712
    ).all()
    unique = {}
    for account in accounts:
        if account.source.endswith("_csv"):
            warnings.add("csv_accounts")
            continue
        if account.currency.upper() != "USD":
            warnings.add("non_usd")
            continue
        key = (account.source, account.external_id) if account.external_id else ("local", account.id)
        if key in unique:
            warnings.add("duplicate_accounts")

            def rank(a):
                return (_utc(a.last_synced).timestamp() if a.last_synced else 0, a.id or 0)

            if rank(account) <= rank(unique[key]):
                continue
        unique[key] = account

    dates = []
    values = []
    for account in unique.values():
        if account.balance is not None and isfinite(account.balance):
            values.append(account.balance)
        if account.last_synced is None:
            warnings.add("unknown_sync_date")
        else:
            synced = _utc(account.last_synced)
            dates.append(synced)
            if now - synced > timedelta(hours=24):
                warnings.add("stale_totals")
            elif synced > now + timedelta(minutes=5):
                warnings.add("future_sync_date")

    missing = len(unique) - len(values)
    # Never present a partial sum as the whole investment portfolio.
    total = sum(values) if unique and not missing else None
    if not unique:
        status = "empty"
    elif missing:
        status = "missing"
    elif not isfinite(total) or total < 0 or total > 1e9:
        status = "unsupported"
        if not isfinite(total):
            total = None
    else:
        status = "ready"
    return {
        "status": status,
        "starting_value": total,
        "account_count": len(unique),
        "missing_count": missing,
        "last_synced_start": min(dates).isoformat() if dates else None,
        "last_synced_end": max(dates).isoformat() if dates else None,
        "warnings": sorted(warnings),
    }
