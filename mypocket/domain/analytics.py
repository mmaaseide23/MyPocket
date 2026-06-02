"""Analytics layer.

Mental model:
  * Cash accounts (checking, savings)   → liquid assets the user holds
  * Credit accounts                     → liabilities (debt owed)
  * Investment accounts (brokerage, ira) → invested assets (market value)

Net worth = (cash + investments) − credit owed

Income is *only* what flows into cash accounts. Investment gains, dividends paid
into a brokerage account, and refunds on credit cards do NOT count as income.

Spending is real outflow:
  * Negative cash transactions (purchases, bills, etc.)
  * + Negative credit transactions (card purchases)
  * − Positive credit transactions (refunds — these reverse prior spending)
  * Excludes Transfers (incl. credit-card payments from checking) and Investment txns
"""

from __future__ import annotations

import calendar
from collections import defaultdict
from datetime import date, timedelta

from sqlmodel import Session, select

from mypocket.domain.models import (  # noqa: F401  - Holding used in holdings_summary
    Account,
    Holding,
    Transaction,
)

# ─── Account-type groups ─────────────────────────────────────────────────────
CASH_TYPES = frozenset({"checking", "savings"})
CREDIT_TYPES = frozenset({"credit"})
INVESTMENT_TYPES = frozenset({"brokerage", "ira"})

# Legacy domain aliases used by /banking and /brokerage routes
BANK_TYPES = CASH_TYPES | CREDIT_TYPES
BROKERAGE_TYPES = INVESTMENT_TYPES

# Transfers and investment-related activity wash out — they're not real
# income or spending, just moving money between own accounts / assets.
_TRANSFER_CATEGORIES = frozenset({"Transfers", "Investment"})

# Refunds and reimbursements: positive amounts that offset prior spending.
# They are NOT income (per the user's definition of "money in to bank").
_REFUND_CATEGORIES = frozenset({"Refunds", "Reimbursements"})


# ─── Account-domain helpers ──────────────────────────────────────────────────


def _accounts_of_types(session: Session, types) -> list[Account]:
    return list(session.exec(select(Account).where(Account.type.in_(types))).all())


def accounts_in_domain(session: Session, domain: str) -> list[Account]:
    """Domain ∈ {'banking', 'brokerage'}. Banking covers both cash and credit."""
    types = BANK_TYPES if domain == "banking" else BROKERAGE_TYPES
    return _accounts_of_types(session, types)


def account_ids_in_domain(session: Session, domain: str) -> list[int]:
    return [a.id for a in accounts_in_domain(session, domain) if a.id is not None]


def get_account(session: Session, account_id: int) -> Account | None:
    return session.get(Account, account_id)


def _cash_ids(session: Session) -> list[int]:
    return [a.id for a in _accounts_of_types(session, CASH_TYPES) if a.id is not None]


def _spending_account_ids(session: Session) -> list[int]:
    """Accounts where real spending happens: cash + credit."""
    return [a.id for a in _accounts_of_types(session, CASH_TYPES | CREDIT_TYPES) if a.id is not None]


# ─── Balance stats ───────────────────────────────────────────────────────────


def cash_balance(session: Session) -> float:
    """Liquid cash across checking + savings only."""
    return round(sum((a.balance or 0.0) for a in _accounts_of_types(session, CASH_TYPES)), 2)


def credit_owed(session: Session) -> float:
    """Total debt across credit accounts. Positive number = amount owed."""
    return round(sum((a.balance or 0.0) for a in _accounts_of_types(session, CREDIT_TYPES)), 2)


def total_invested(session: Session) -> float:
    """Market value of all brokerage + IRA accounts."""
    return round(sum((a.balance or 0.0) for a in _accounts_of_types(session, INVESTMENT_TYPES)), 2)


def net_worth(session: Session, account_ids: list[int] | None = None) -> float:
    """Assets minus liabilities. Credit balances are subtracted (they're debt)."""
    q = select(Account)
    if account_ids is not None:
        q = q.where(Account.id.in_(account_ids))
    accounts = session.exec(q).all()
    total = 0.0
    for a in accounts:
        bal = a.balance or 0.0
        total += -bal if a.type in CREDIT_TYPES else bal
    return round(total, 2)


# ─── Period flows: income, spending, savings rate ────────────────────────────


def _sum_inflow(session: Session, start: date, end: date, account_ids: list[int]) -> float:
    """Positive amounts on the given accounts, excluding transfers AND refunds/
    reimbursements (those aren't 'income' — they offset spending instead)."""
    if not account_ids:
        return 0.0
    txns = session.exec(
        select(Transaction).where(
            Transaction.tx_date >= start,
            Transaction.tx_date <= end,
            Transaction.amount > 0,
            Transaction.account_id.in_(account_ids),
        )
    ).all()
    excluded = _TRANSFER_CATEGORIES | _REFUND_CATEGORIES
    return sum(t.amount for t in txns if t.category not in excluded)


def _sum_spend(session: Session, start: date, end: date, account_ids: list[int]) -> float:
    """Gross spending in window: sum of NEGATIVE transactions only.

    Refunds (positive amounts) are tracked separately by `_sum_refunds` — netting
    them in here makes the "how much did I spend" stat go to $0 in months where
    refunds happen to exceed purchases, which is confusing. Apps like Monarch
    show gross + refunds as separate lines for the same reason.
    """
    if not account_ids:
        return 0.0
    txns = session.exec(
        select(Transaction).where(
            Transaction.tx_date >= start,
            Transaction.tx_date <= end,
            Transaction.amount < 0,
            Transaction.account_id.in_(account_ids),
        )
    ).all()
    total = sum(-t.amount for t in txns if t.category not in _TRANSFER_CATEGORIES)
    return total


def _sum_refunds(session: Session, start: date, end: date, account_ids: list[int]) -> float:
    """Money credited back. Includes:
      • Any positive transaction on a credit account (refunds/returns to card)
      • 'Refunds' or 'Reimbursements' on cash accounts (e.g. friends paying you
        back for a meal you fronted on your card).
    Excludes Transfers and Investment activity.
    """
    if not account_ids:
        return 0.0
    accounts = {a.id: a for a in session.exec(select(Account)).all()}
    txns = session.exec(
        select(Transaction).where(
            Transaction.tx_date >= start,
            Transaction.tx_date <= end,
            Transaction.amount > 0,
            Transaction.account_id.in_(account_ids),
        )
    ).all()
    total = 0.0
    for t in txns:
        if t.category in _TRANSFER_CATEGORIES:
            continue
        acc = accounts.get(t.account_id)
        if acc is None:
            continue
        if acc.type in CREDIT_TYPES:
            # Any positive on a credit card is a refund/reversal (transfers already filtered).
            total += t.amount
        elif t.category in _REFUND_CATEGORIES:
            # Refunds tagged on cash + P2P reimbursements (e.g. Venmo back from friends).
            total += t.amount
    return total


def income(
    session: Session,
    days: int = 30,
    account_ids: list[int] | None = None,
) -> float:
    """Money flowing INTO cash accounts (checking/savings) over the last `days`.

    Brokerage dividends and gains are NOT income here — those land in the
    brokerage account, not your checking. `account_ids` overrides the scope
    when a single-account drill-down needs a per-account income figure.
    """
    cutoff = date.today() - timedelta(days=days)
    end = date.today()
    ids = account_ids if account_ids is not None else _cash_ids(session)
    # When a non-cash account_id is passed (e.g. credit), inflow is refunds, not income.
    return round(_sum_inflow(session, cutoff, end, ids), 2)


def spending(
    session: Session,
    days: int = 30,
    account_ids: list[int] | None = None,
) -> float:
    """Gross spending over the last `days` — sum of negative transactions.
    Refunds are NOT subtracted here (use `refunds()` for that view)."""
    cutoff = date.today() - timedelta(days=days)
    end = date.today()
    ids = account_ids if account_ids is not None else _spending_account_ids(session)
    return round(_sum_spend(session, cutoff, end, ids), 2)


def refunds(
    session: Session,
    days: int = 30,
    account_ids: list[int] | None = None,
) -> float:
    """Money refunded over the window (positive credit txns + Refunds on cash)."""
    cutoff = date.today() - timedelta(days=days)
    end = date.today()
    ids = account_ids if account_ids is not None else _spending_account_ids(session)
    return round(_sum_refunds(session, cutoff, end, ids), 2)


def savings_rate(session: Session, days: int = 30) -> float | None:
    """Percent of income kept (not spent). None when there's no income.
    Uses gross spending — refunds aren't treated as income for savings purposes."""
    inc = income(session, days=days)
    if inc <= 0:
        return None
    spent = spending(session, days=days)
    return round(100 * (inc - spent) / inc, 1)


# ─── Period breakdowns: by_week, by_month, period_compare ────────────────────


def _iter_weekly_buckets(n: int):
    """Yield (label, start, end) for the last n ISO weeks, oldest first."""
    today = date.today()
    # Monday of the current week
    monday_this_week = today - timedelta(days=today.weekday())
    for i in range(n - 1, -1, -1):
        start = monday_this_week - timedelta(days=7 * i)
        end = start + timedelta(days=6)
        # Don't go past today for the current week
        end = min(end, today)
        label = start.strftime("%b %-d")
        yield (label, start, end)


def _iter_monthly_buckets(n: int):
    """Yield (label, start, end) for the last n calendar months, oldest first."""
    today = date.today()
    months = []
    y, m = today.year, today.month
    for _ in range(n):
        months.append((y, m))
        m -= 1
        if m == 0:
            m = 12
            y -= 1
    for y, m in reversed(months):
        _, last_day = calendar.monthrange(y, m)
        start = date(y, m, 1)
        end = date(y, m, last_day)
        if y == today.year and m == today.month:
            end = today
        label = date(y, m, 1).strftime("%b")
        yield (label, start, end)


def by_period(session: Session, *, weeks: bool = False, n: int = 6) -> list[dict]:
    """Return income/spending/net for each of the last N weeks or months."""
    cash_ids = _cash_ids(session)
    spend_ids = _spending_account_ids(session)
    iterator = _iter_weekly_buckets(n) if weeks else _iter_monthly_buckets(n)
    out = []
    for label, start, end in iterator:
        inc = round(_sum_inflow(session, start, end, cash_ids), 2)
        spent = round(_sum_spend(session, start, end, spend_ids), 2)
        sr = round(100 * (inc - spent) / inc, 1) if inc > 0 else None
        out.append(
            {
                "label": label,
                "start": start.isoformat(),
                "end": end.isoformat(),
                "income": inc,
                "spending": spent,
                "net": round(inc - spent, 2),
                "savings_rate": sr,
            }
        )
    return out


def period_compare(session: Session, *, weeks: bool = False) -> dict:
    """Compare current period to the previous one (week or month)."""
    today = date.today()
    if weeks:
        cur_start = today - timedelta(days=today.weekday())
        cur_end = today
        prev_end = cur_start - timedelta(days=1)
        prev_start = prev_end - timedelta(days=6)
        label = "week"
    else:
        cur_start = today.replace(day=1)
        cur_end = today
        prev_end = cur_start - timedelta(days=1)
        prev_start = prev_end.replace(day=1)
        label = "month"

    cash_ids = _cash_ids(session)
    spend_ids = _spending_account_ids(session)

    def _measure(start: date, end: date) -> dict:
        return {
            "income": round(_sum_inflow(session, start, end, cash_ids), 2),
            "spending": round(_sum_spend(session, start, end, spend_ids), 2),
            "start": start.isoformat(),
            "end": end.isoformat(),
        }

    cur = _measure(cur_start, cur_end)
    prev = _measure(prev_start, prev_end)

    def _delta_pct(now: float, before: float) -> float | None:
        if before <= 0:
            return None
        return round(100 * (now - before) / before, 1)

    return {
        "period": label,
        "current": cur,
        "previous": prev,
        "spending_delta_pct": _delta_pct(cur["spending"], prev["spending"]),
        "income_delta_pct": _delta_pct(cur["income"], prev["income"]),
    }


# ─── Per-account & domain helpers (kept stable for existing routes) ──────────


def account_breakdown(session: Session, account_ids: list[int] | None = None) -> list[dict]:
    q = select(Account)
    if account_ids is not None:
        q = q.where(Account.id.in_(account_ids))
    accounts = session.exec(q).all()
    return [
        {
            "id": a.id,
            "name": a.name,
            "institution": a.institution,
            "type": a.type,
            "balance": a.balance or 0.0,
            "last_synced": a.last_synced.isoformat() if a.last_synced else None,
        }
        for a in accounts
    ]


def account_summary(session: Session, account: Account, days: int = 30) -> dict:
    """Per-account snapshot. For credit accounts, 'income' is refunds and
    'spending' is net purchases minus refunds — semantically clean."""
    aid = account.id
    return {
        "id": aid,
        "name": account.name,
        "institution": account.institution,
        "type": account.type,
        "balance": account.balance or 0.0,
        "last_synced": account.last_synced.isoformat() if account.last_synced else None,
        "income": income(session, days=days, account_ids=[aid]),
        "spending": spending(session, days=days, account_ids=[aid]),
    }


# ─── Category breakdown ──────────────────────────────────────────────────────


def category_breakdown(
    session: Session,
    days: int = 30,
    top_n: int = 10,
    account_ids: list[int] | None = None,
) -> list[dict]:
    """Spending grouped by category with each item's % of total spending.
    Default scope: cash + credit accounts (where real spending happens)."""
    cutoff = date.today() - timedelta(days=days)
    if account_ids is None:
        account_ids = _spending_account_ids(session)
    if not account_ids:
        return []
    txns = session.exec(
        select(Transaction).where(
            Transaction.tx_date >= cutoff,
            Transaction.amount < 0,
            Transaction.account_id.in_(account_ids),
        )
    ).all()
    by_cat: dict[str, float] = defaultdict(float)
    for t in txns:
        # Skip transfers/investments (washes out) AND refund-y categories
        # (those are positive amounts anyway but defensive).
        if t.category in _TRANSFER_CATEGORIES or t.category in _REFUND_CATEGORIES:
            continue
        by_cat[t.category or "Uncategorized"] += -t.amount
    total = sum(by_cat.values())
    rows = [
        {
            "category": k,
            "amount": round(v, 2),
            "pct": round(100 * v / total, 1) if total > 0 else 0.0,
        }
        for k, v in by_cat.items()
    ]
    rows.sort(key=lambda r: r["amount"], reverse=True)
    return rows[:top_n]


# ─── Time series ─────────────────────────────────────────────────────────────


def monthly_flows(
    session: Session,
    months: int = 12,
    account_ids: list[int] | None = None,
) -> list[dict]:
    """Return [{month, inflow, outflow, net}, …] for the last N months.

    inflow/outflow scoped the same way as income()/spending(): inflow is to cash
    accounts only; outflow includes credit purchases.
    """
    cash_ids = account_ids if account_ids is not None else _cash_ids(session)
    spend_ids = account_ids if account_ids is not None else _spending_account_ids(session)

    cutoff = (date.today().replace(day=1) - timedelta(days=31 * months)).replace(day=1)
    if not cash_ids and not spend_ids:
        return []
    inflow_txns = (
        session.exec(
            select(Transaction).where(
                Transaction.tx_date >= cutoff,
                Transaction.amount > 0,
                Transaction.account_id.in_(cash_ids) if cash_ids else (1 == 0),
            )
        ).all()
        if cash_ids
        else []
    )
    spend_txns = (
        session.exec(
            select(Transaction).where(
                Transaction.tx_date >= cutoff,
                Transaction.account_id.in_(spend_ids) if spend_ids else (1 == 0),
            )
        ).all()
        if spend_ids
        else []
    )

    rows: dict[str, dict] = {}
    # Inflow = income to cash, excluding both transfers AND refunds/reimbursements.
    for t in inflow_txns:
        if t.category in _TRANSFER_CATEGORIES or t.category in _REFUND_CATEGORIES:
            continue
        month = t.tx_date.strftime("%Y-%m")
        rows.setdefault(month, {"inflow": 0.0, "outflow": 0.0})
        rows[month]["inflow"] += t.amount
    # Outflow = gross spending, excluding transfers. Refunds/reimbursements are
    # tracked separately and don't reduce the bar visualization here (keeps the
    # chart consistent with the gross "Spent" stat).
    for t in spend_txns:
        if t.category in _TRANSFER_CATEGORIES:
            continue
        if t.amount < 0:
            month = t.tx_date.strftime("%Y-%m")
            rows.setdefault(month, {"inflow": 0.0, "outflow": 0.0})
            rows[month]["outflow"] += -t.amount
    out = []
    for month in sorted(rows.keys()):
        inflow = round(rows[month]["inflow"], 2)
        outflow = round(max(0.0, rows[month]["outflow"]), 2)
        out.append({"month": month, "inflow": inflow, "outflow": outflow, "net": round(inflow - outflow, 2)})
    return out


def recent_transactions(
    session: Session,
    limit: int = 50,
    account_id: int | None = None,
    account_ids: list[int] | None = None,
    category: str | None = None,
    search: str | None = None,
) -> list[dict]:
    q = select(Transaction)
    if account_id is not None:
        q = q.where(Transaction.account_id == account_id)
    elif account_ids is not None:
        q = q.where(Transaction.account_id.in_(account_ids))
    if category is not None:
        q = q.where(Transaction.category == category)
    q = q.order_by(Transaction.tx_date.desc(), Transaction.id.desc()).limit(limit * 4)
    rows = session.exec(q).all()
    if search:
        s = search.lower()
        rows = [r for r in rows if s in (r.description or "").lower() or s in (r.merchant or "").lower()]
    accounts = {a.id: a for a in session.exec(select(Account)).all()}
    return [
        {
            "id": t.id,
            "date": t.tx_date.isoformat(),
            "description": t.description,
            "merchant": t.merchant,
            "amount": t.amount,
            "category": t.category,
            "subcategory": t.subcategory,
            "account": accounts.get(t.account_id).name if accounts.get(t.account_id) else "?",
            "symbol": t.symbol,
            "type": t.transaction_type,
        }
        for t in rows[:limit]
    ]


def holdings_summary(session: Session, account_ids: list[int] | None = None) -> dict:
    q = select(Holding)
    if account_ids is not None:
        q = q.where(Holding.account_id.in_(account_ids))
    all_rows = session.exec(q).all()
    # Keep only the most recent snapshot per (account_id, symbol)
    seen: dict[tuple, Holding] = {}
    for h in all_rows:
        key = (h.account_id, h.symbol)
        if key not in seen or h.as_of > seen[key].as_of:
            seen[key] = h
    holdings = list(seen.values())
    accounts = {a.id: a for a in session.exec(select(Account)).all()}
    total_value = sum((h.market_value or 0.0) for h in holdings)
    total_cost = sum((h.cost_basis or 0.0) for h in holdings if h.cost_basis is not None)
    total_gain = sum((h.total_gain or 0.0) for h in holdings if h.total_gain is not None)
    rows = []
    for h in holdings:
        acc = accounts.get(h.account_id)
        rows.append(
            {
                "id": h.id,
                "account": acc.name if acc else "?",
                "account_id": h.account_id,
                "symbol": h.symbol,
                "name": h.name,
                "quantity": h.quantity,
                "avg_cost": h.avg_cost,
                "cost_basis": h.cost_basis,
                "last_price": h.last_price,
                "market_value": h.market_value,
                "day_change": h.day_change,
                "day_change_pct": h.day_change_pct,
                "total_gain": h.total_gain,
                "total_gain_pct": h.total_gain_pct,
            }
        )
    rows.sort(key=lambda r: r["market_value"] or 0.0, reverse=True)
    return {
        "total_value": round(total_value, 2),
        "total_cost": round(total_cost, 2),
        "total_gain": round(total_gain, 2),
        "total_gain_pct": round(100 * total_gain / total_cost, 2) if total_cost else 0.0,
        "holdings": rows,
    }


def dividend_history(
    session: Session,
    months: int = 12,
    account_ids: list[int] | None = None,
) -> list[dict]:
    """Sum of dividend transactions grouped by YYYY-MM, oldest first."""
    cutoff = (date.today().replace(day=1) - timedelta(days=31 * months)).replace(day=1)
    q = select(Transaction).where(
        Transaction.tx_date >= cutoff,
        Transaction.subcategory == "Dividends",
    )
    if account_ids is not None:
        q = q.where(Transaction.account_id.in_(account_ids))
    by_month: dict[str, float] = defaultdict(float)
    for t in session.exec(q).all():
        by_month[t.tx_date.strftime("%Y-%m")] += t.amount
    return [{"month": m, "amount": round(by_month[m], 2)} for m in sorted(by_month)]


def holdings_period_gains(
    session: Session,
    account_ids: list[int] | None = None,
    period_days: int = 7,
) -> dict:
    """Per-symbol gain over `period_days` by comparing holding snapshots.

    Requires at least two syncs spaced `period_days` apart to show data.
    Returns has_data=False (and None gains) until historical snapshots exist.
    """
    q = select(Holding)
    if account_ids is not None:
        q = q.where(Holding.account_id.in_(account_ids))
    all_rows = session.exec(q).all()

    # Latest snapshot per (account_id, symbol)
    current: dict[tuple, Holding] = {}
    for h in all_rows:
        key = (h.account_id, h.symbol)
        if key not in current or h.as_of > current[key].as_of:
            current[key] = h

    # Latest snapshot that is at least period_days old
    cutoff = date.today() - timedelta(days=period_days)
    past: dict[tuple, Holding] = {}
    for h in all_rows:
        if h.as_of > cutoff:
            continue
        key = (h.account_id, h.symbol)
        if key not in past or h.as_of > past[key].as_of:
            past[key] = h

    rows = []
    for key, curr in current.items():
        curr_val = curr.market_value or 0.0
        if curr_val <= 0:
            continue
        past_h = past.get(key)
        past_val = past_h.market_value if past_h else None
        gain = round(curr_val - past_val, 2) if past_val is not None else None
        gain_pct = round(gain / past_val * 100, 2) if (gain is not None and past_val) else None
        rows.append({
            "symbol": curr.symbol,
            "current_value": round(curr_val, 2),
            "past_value": round(past_val, 2) if past_val is not None else None,
            "gain": gain,
            "gain_pct": gain_pct,
        })

    rows.sort(key=lambda r: r["current_value"], reverse=True)
    has_data = any(r["past_value"] is not None for r in rows)
    total_gain = round(sum(r["gain"] for r in rows if r["gain"] is not None), 2)
    return {
        "period_days": period_days,
        "has_data": has_data,
        "total_gain": total_gain if has_data else None,
        "rows": rows,
    }
