from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlmodel import Session

from mypocket.core.db import get_session
from mypocket.core.templating import templates
from mypocket.domain import analytics

router = APIRouter()


def _merge_performance(weekly: dict, monthly: dict) -> list[dict]:
    w_map = {r["symbol"]: r for r in weekly["rows"]}
    m_map = {r["symbol"]: r for r in monthly["rows"]}
    seen: set[str] = set()
    syms: list[str] = []
    for r in weekly["rows"] + monthly["rows"]:
        if r["symbol"] not in seen:
            seen.add(r["symbol"])
            syms.append(r["symbol"])
    rows = []
    for sym in syms:
        w = w_map.get(sym, {})
        m = m_map.get(sym, {})
        rows.append({
            "symbol": sym,
            "current_value": w.get("current_value") or m.get("current_value"),
            "weekly_gain": w.get("gain"),
            "weekly_gain_pct": w.get("gain_pct"),
            "monthly_gain": m.get("gain"),
            "monthly_gain_pct": m.get("gain_pct"),
        })
    return rows


def _trend_periods(days: int) -> tuple[int, int]:
    """How many (months, weeks) fit cleanly inside the selected window.

    The window is a hard upper bound — the trend charts only show full periods
    that fall within `days`. So:
      * days=7  → 0 months (no full month fits), 1 week
      * days=30 → 1 month, 4 weeks
      * days=90 → 3 months, 12 weeks
      * days=365 → 12 months, 12 weeks (12 is the readability cap)
    A 0 means the corresponding chart should be hidden — the user explicitly
    asked for "blank" when the window can't contain a full period.
    """
    months = min(max(days // 30, 0), 12)
    weeks = min(max(days // 7, 0), 12)
    return (months, weeks)


@router.get("/", response_class=HTMLResponse)
def overview(request: Request, days: int = 30, session: Session = Depends(get_session)):
    months_n, weeks_n = _trend_periods(days)
    ctx = {
        "request": request,
        "active": "overview",
        "window_days": days,
        "months_n": months_n,
        "weeks_n": weeks_n,
        # Stats keyed off the selected window
        "net_worth": analytics.net_worth(session),
        "cash_balance": analytics.cash_balance(session),
        "credit_owed": analytics.credit_owed(session),
        "total_invested": analytics.total_invested(session),
        "income": analytics.income(session, days=days),
        "spending": analytics.spending(session, days=days),
        "savings_rate": analytics.savings_rate(session, days=days),
        "month_compare": analytics.period_compare(session, weeks=False),
        "week_compare": analytics.period_compare(session, weeks=True),
        "accounts": analytics.account_breakdown(session),
        "recent": analytics.recent_transactions(session, limit=10),
    }
    return templates.TemplateResponse(request, "overview.html", ctx)


@router.get("/banking", response_class=HTMLResponse)
def banking(request: Request, days: int = 30, session: Session = Depends(get_session)):
    account_ids = analytics.account_ids_in_domain(session, "banking")
    ctx = {
        "request": request,
        "active": "banking",
        "window_days": days,
        "cash_balance": analytics.cash_balance(session),
        "credit_owed": analytics.credit_owed(session),
        "net_worth": analytics.net_worth(session, account_ids=account_ids),
        "income": analytics.income(session, days=days),
        "spending": analytics.spending(session, days=days, account_ids=account_ids),
        "savings_rate": analytics.savings_rate(session, days=days),
        "accounts": analytics.account_breakdown(session, account_ids=account_ids),
        "recent": analytics.recent_transactions(session, limit=10, account_ids=account_ids),
        "categories": analytics.category_breakdown(session, days=days, account_ids=account_ids, top_n=8),
    }
    return templates.TemplateResponse(request, "banking.html", ctx)


@router.get("/banking/{account_id}", response_class=HTMLResponse)
def banking_account(
    account_id: int,
    request: Request,
    days: int = 30,
    session: Session = Depends(get_session),
):
    account = analytics.get_account(session, account_id)
    if not account or account.type not in analytics.BANK_TYPES:
        raise HTTPException(404, "Bank account not found")
    summary = analytics.account_summary(session, account, days=days)
    ctx = {
        "request": request,
        "active": "banking",
        "window_days": days,
        "account": summary,
        "recent": analytics.recent_transactions(session, limit=50, account_id=account_id),
        "categories": analytics.category_breakdown(session, days=days, account_ids=[account_id], top_n=8),
    }
    return templates.TemplateResponse(request, "banking_account.html", ctx)


@router.get("/brokerage", response_class=HTMLResponse)
def brokerage(request: Request, months: int = 12, session: Session = Depends(get_session)):
    account_ids = analytics.account_ids_in_domain(session, "brokerage")
    summary = analytics.holdings_summary(session, account_ids=account_ids)
    day_gain = round(
        sum((h["day_change"] or 0) for h in summary["holdings"] if h["day_change"] is not None), 2
    )
    day_gain_has_data = any(h["day_change"] is not None for h in summary["holdings"])
    weekly = analytics.holdings_period_gains(session, account_ids=account_ids, period_days=7)
    monthly = analytics.holdings_period_gains(session, account_ids=account_ids, period_days=30)
    ctx = {
        "request": request,
        "active": "brokerage",
        "window_months": months,
        "accounts": analytics.account_breakdown(session, account_ids=account_ids),
        "summary": summary,
        "dividends": analytics.dividend_history(session, months=months, account_ids=account_ids),
        "day_gain": day_gain,
        "day_gain_has_data": day_gain_has_data,
        "performance_has_data": weekly["has_data"] or monthly["has_data"],
        "weekly_total": weekly["total_gain"],
        "monthly_total": monthly["total_gain"],
        "performance_rows": _merge_performance(weekly, monthly),
    }
    return templates.TemplateResponse(request, "brokerage.html", ctx)


@router.get("/brokerage/{account_id}", response_class=HTMLResponse)
def brokerage_account(
    account_id: int,
    request: Request,
    months: int = 12,
    session: Session = Depends(get_session),
):
    account = analytics.get_account(session, account_id)
    if not account or account.type not in analytics.BROKERAGE_TYPES:
        raise HTTPException(404, "Brokerage account not found")
    summary = analytics.account_summary(session, account, days=30)
    holdings = analytics.holdings_summary(session, account_ids=[account_id])
    day_gain = round(
        sum((h["day_change"] or 0) for h in holdings["holdings"] if h["day_change"] is not None), 2
    )
    day_gain_has_data = any(h["day_change"] is not None for h in holdings["holdings"])
    weekly = analytics.holdings_period_gains(session, account_ids=[account_id], period_days=7)
    monthly = analytics.holdings_period_gains(session, account_ids=[account_id], period_days=30)
    ctx = {
        "request": request,
        "active": "brokerage",
        "window_months": months,
        "account": summary,
        "holdings": holdings,
        "dividends": analytics.dividend_history(session, months=months, account_ids=[account_id]),
        "recent": analytics.recent_transactions(session, limit=50, account_id=account_id),
        "day_gain": day_gain,
        "day_gain_has_data": day_gain_has_data,
        "performance_has_data": weekly["has_data"] or monthly["has_data"],
        "weekly_total": weekly["total_gain"],
        "monthly_total": monthly["total_gain"],
        "performance_rows": _merge_performance(weekly, monthly),
    }
    return templates.TemplateResponse(request, "brokerage_account.html", ctx)


@router.get("/investments")
def investments_redirect() -> RedirectResponse:
    """Legacy alias — Investments was renamed to Brokerage."""
    return RedirectResponse("/brokerage", status_code=301)


@router.get("/spending", response_class=HTMLResponse)
def spending(request: Request, days: int = 30, session: Session = Depends(get_session)):
    months_n, weeks_n = _trend_periods(days)
    ctx = {
        "request": request,
        "active": "spending",
        "window_days": days,
        "months_n": months_n,
        "weeks_n": weeks_n,
        "income": analytics.income(session, days=days),
        "spending": analytics.spending(session, days=days),
        "refunds": analytics.refunds(session, days=days),
        "savings_rate": analytics.savings_rate(session, days=days),
        "month_compare": analytics.period_compare(session, weeks=False),
        "week_compare": analytics.period_compare(session, weeks=True),
        "by_month": analytics.by_period(session, weeks=False, n=months_n),
        "by_week": analytics.by_period(session, weeks=True, n=weeks_n),
        "categories": analytics.category_breakdown(session, days=days, top_n=20),
    }
    return templates.TemplateResponse(request, "spending.html", ctx)


@router.get("/transactions", response_class=HTMLResponse)
def transactions_page(request: Request, session: Session = Depends(get_session)):
    ctx = {
        "request": request,
        "active": "transactions",
        "transactions": analytics.recent_transactions(session, limit=200),
        "accounts": analytics.account_breakdown(session),
    }
    return templates.TemplateResponse(request, "transactions.html", ctx)
