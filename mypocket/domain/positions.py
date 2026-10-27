"""Read-only position presentation from saved snapshots, never provider calls.

Value differences include trades/transfers/splits and are not investment returns.
Daily P/L is used only for sources with known position-level semantics.
"""

from collections import Counter, defaultdict
from datetime import UTC, date, datetime, timedelta
from math import isfinite

from sqlmodel import Session, select

from mypocket.domain.models import Account, Holding
from mypocket.domain.windows import Window, trailing_days

RANGES = {"7d": 7, "30d": 30, "90d": 90, "1y": 365, "all": None}
BROKERAGE_TYPES = ("brokerage", "ira")


def number(value):
    return float(value) if value is not None and isfinite(value) else None


def utc(value):
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _scope(session, account_id=None):
    accounts = session.exec(
        select(Account).where(Account.archived.is_(False), Account.type.in_(BROKERAGE_TYPES))
    ).all()
    unique = {}
    for account in accounts:
        key = (account.source, account.external_id) if account.external_id else ("local", account.id)
        rank = (utc(account.last_synced).timestamp() if account.last_synced else 0, account.id)
        if key not in unique or rank > unique[key][0]:
            unique[key] = (rank, account)
    result = [value[1] for value in unique.values()]
    return [a for a in result if account_id is None or a.id == account_id]


def _records(session, accounts, today):
    if not accounts:
        return []
    return session.exec(
        select(Holding)
        .where(Holding.account_id.in_([a.id for a in accounts]), Holding.as_of <= today)
        .order_by(Holding.as_of, Holding.id)
    ).all()


def _point(holding, account):
    known_daily = account.source in {"etrade_api", "demo"}
    cost, value = number(holding.cost_basis), number(holding.market_value)
    gain = number(holding.total_gain)
    gain_source = "Reported total gain/loss" if gain is not None else None
    if (
        gain is None
        and cost is not None
        and cost > 0
        and value is not None
        and number(holding.quantity) is not None
        and holding.quantity >= 0
    ):
        gain = value - cost
        gain_source = "Value less recorded cost basis"
    pct = number(holding.total_gain_pct) if cost is None or cost > 0 else None
    if pct is None and cost is not None and cost > 0 and gain is not None:
        pct = 100 * gain / cost
    if gain_source is None and pct is not None:
        gain_source = "Reported total gain/loss"
    return {
        "date": holding.as_of.isoformat(),
        "quantity": number(holding.quantity),
        "value": value,
        "basis": cost,
        "price": number(holding.last_price),
        "daily_gain": number(holding.day_change) if known_daily else None,
        "daily_pct": number(holding.day_change_pct) if known_daily else None,
        "total_gain": gain,
        "total_pct": pct,
        "gain_source": gain_source,
    }


def _range_options(series, today):
    labels = {"7d": "7D", "30d": "1M", "90d": "3M", "1y": "1Y", "all": "All"}
    options = []
    for key, days in RANGES.items():
        start = trailing_days(days, today).start if days else date.min
        points = [p for p in series if start <= date.fromisoformat(p["date"]) <= today]
        metrics = [
            field for field in ("value", "daily_gain") if sum(p[field] is not None for p in points) >= 2
        ]
        if metrics:
            options.append({"value": key, "label": labels[key], "metrics": metrics})
    return options


def _top_summary(rows):
    def complete(field):
        return sum(p[field] for p in rows) if rows and all(p[field] is not None for p in rows) else None

    cost, gain = complete("basis"), complete("total_gain")
    return {
        "total_value": complete("value"),
        "total_cost": cost,
        "total_gain": gain,
        "total_gain_pct": 100 * gain / cost if gain is not None and cost is not None and cost > 0 else None,
        "day_gain": complete("daily_gain") if len({p["date"] for p in rows}) == 1 else None,
    }


def overview(session: Session, account_id=None, *, today=None, now=None):
    today = today or date.today()
    now = now or datetime.now(UTC)
    accounts = _scope(session, account_id)
    records = _records(session, accounts, today)
    by_account = defaultdict(list)
    for holding in records:
        by_account[holding.account_id].append(holding)
    rows, warnings = [], []
    account_rows = []
    for account in accounts:
        currency = (account.currency or "Unspecified").upper()
        account_rows.append(
            {
                "id": account.id,
                "name": account.name,
                "institution": account.institution,
                "type": account.type,
                "currency": currency,
                "balance": number(account.balance),
            }
        )
        history = by_account[account.id]
        if not history:
            warnings.append(f"{account.name}: no saved holding snapshots. Account cash is not a holding.")
            continue
        latest = max(h.as_of for h in history)
        current = [h for h in history if h.as_of == latest]
        duplicates = Counter(h.symbol for h in current)
        newer_sync = account.last_synced is not None and utc(account.last_synced).astimezone().date() > latest
        if newer_sync:
            warnings.append(
                f"{account.name}: the account sync is newer than its holding snapshots. Current positions cannot be confirmed; last recorded positions are shown."
            )
        for holding in current:
            point = _point(holding, account)
            stamp = utc(holding.created_at)
            stale = latest < today or (utc(now) - stamp).total_seconds() > 86400
            first = min(h.as_of for h in history if h.symbol == holding.symbol)
            symbol_rows = [h for h in history if h.symbol == holding.symbol]
            date_counts = Counter(h.as_of for h in symbol_rows)
            series = [_point(h, account) for h in symbol_rows if date_counts[h.as_of] == 1]
            available_ranges = [] if duplicates[holding.symbol] > 1 else _range_options(series, today)
            rows.append(
                {
                    **point,
                    "id": holding.id,
                    "ranges": available_ranges,
                    "has_history": bool(available_ranges),
                    "symbol": holding.symbol,
                    "name": holding.name
                    if holding.name not in {holding.symbol, "EQ", "MF", "MMF", "OPTN", "BOND"}
                    else None,
                    "account": account.name,
                    "account_id": account.id,
                    "currency": currency,
                    "saved_at": stamp.isoformat(),
                    "stale": stale,
                    "future_stamp": stamp > utc(now),
                    "status_unconfirmed": newer_sync,
                    "ambiguous": duplicates[holding.symbol] > 1,
                    "tracking_since": first.isoformat(),
                    "daily_source": "Simulated daily P/L"
                    if account.source == "demo"
                    else "Broker-reported daily P/L",
                    "avg_cost": number(holding.avg_cost),
                }
            )
    rows.sort(
        key=lambda h: (
            h["currency"],
            h["value"] is None,
            -(h["value"] or 0),
            h["symbol"],
            h["account_id"],
            h["id"],
        )
    )
    totals = []
    for currency in sorted({r["currency"] for r in rows}):
        subset = [r for r in rows if r["currency"] == currency]
        missing = sum(r["value"] is None for r in subset)
        allocation = [
            {"symbol": p["symbol"], "account": p["account"], "value": p["value"]}
            for p in subset
            if p["value"] is not None and p["value"] > 0
        ]
        denominator = sum(p["value"] for p in allocation)
        repeated = Counter(p["symbol"] for p in allocation)
        for point in allocation:
            point["percentage"] = point["value"] / denominator * 100
            point["percentage_label"] = (
                "<0.1%" if point["percentage"] < 0.1 else f"{point['percentage']:.1f}%"
            )
            point["label"] = point["symbol"] + (
                " · " + point["account"] if repeated[point["symbol"]] > 1 else ""
            )
        totals.append(
            {
                "currency": currency,
                "value": None if missing else sum(r["value"] for r in subset),
                "missing": missing,
                "count": len(subset),
                "summary": _top_summary(subset),
                "allocation": allocation,
                "allocation_total": denominator,
            }
        )
    return {
        "positions": rows,
        "totals": totals,
        "accounts": account_rows,
        "warnings": warnings,
        "as_of": today.isoformat(),
    }


def history(session: Session, position_id: int, range_name="30d", *, today=None):
    if range_name not in RANGES:
        raise ValueError("Choose 7d, 30d, 90d, 1y, or all")
    today = today or date.today()
    record = session.get(Holding, position_id)
    if record is None:
        return None
    accounts = _scope(session, record.account_id)
    if not accounts:
        return None
    account = accounts[0]
    records = _records(session, accounts, today)
    if not records or record.as_of != max(h.as_of for h in records):
        return None
    symbol_rows = [h for h in records if h.symbol == record.symbol]
    first = min(h.as_of for h in symbol_rows)
    window = (
        Window(first, record.as_of + timedelta(days=1))
        if range_name == "all"
        else trailing_days(RANGES[range_name], today)
    )
    grouped = defaultdict(list)
    for row in records:
        if window.start <= row.as_of < window.stop:
            grouped[row.as_of].append(row)
    latest_duplicates = sum(h.as_of == record.as_of for h in symbol_rows) > 1
    points = []
    previous_quantity = None
    quantity_changed = False
    ambiguous_dates = False
    for day, items in sorted(grouped.items()):
        matching = [h for h in items if h.symbol == record.symbol]
        if len(matching) != 1 or latest_duplicates:
            ambiguous_dates = ambiguous_dates or len(matching) > 1
            points.append(
                {
                    "date": day.isoformat(),
                    "value": None,
                    "basis": None,
                    "quantity": None,
                    "daily_gain": None,
                    "daily_pct": None,
                    "gap": "Multiple positions share this symbol"
                    if matching
                    else "Position not recorded in this account snapshot",
                }
            )
            previous_quantity = None
            continue
        point = _point(matching[0], account)
        changed = previous_quantity is not None and point["quantity"] != previous_quantity
        quantity_changed = quantity_changed or changed
        point["quantity_changed"] = changed
        points.append(point)
        previous_quantity = point["quantity"]
    valid = [point for point in points if point["value"] is not None]
    delta = valid[-1]["value"] - valid[0]["value"] if len(valid) > 1 else None
    return {
        "position_id": position_id,
        "symbol": record.symbol,
        "currency": (account.currency or "Unspecified").upper(),
        "range": range_name,
        "range_start": window.start.isoformat(),
        "range_end": window.end.isoformat(),
        "range_label": window.label,
        "tracking_since": first.isoformat(),
        "points": points,
        "quantity_changed": quantity_changed,
        "ambiguous": latest_duplicates or ambiguous_dates,
        "value_change": delta,
        "comparison_start": valid[0]["date"] if len(valid) > 1 else None,
        "comparison_end": valid[-1]["date"] if len(valid) > 1 else None,
    }
