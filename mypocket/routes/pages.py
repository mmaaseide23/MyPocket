from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlmodel import Session

from mypocket.core.db import get_session
from mypocket.core.templating import templates
from mypocket.domain import analytics
from mypocket.domain.windows import DAY_CHOICES, MONTH_CHOICES, calendar_months, trailing_days

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
        rows.append(
            {
                "symbol": sym,
                "current_value": w.get("current_value") or m.get("current_value"),
                "weekly_gain": w.get("gain"),
                "weekly_gain_pct": w.get("gain_pct"),
                "weekly_range": w.get("range"),
                "monthly_gain": m.get("gain"),
                "monthly_gain_pct": m.get("gain_pct"),
                "monthly_range": m.get("range"),
            }
        )
    return rows


def _day_context(days: int) -> dict:
    if days not in DAY_CHOICES:
        raise HTTPException(422, "Choose 7, 30, 90, 180, or 365 days")
    window = trailing_days(days)
    return {
        "window_days": days,
        "window": window,
        "window_label": window.label,
        "as_of": window.end.isoformat(),
        "trend_period": "day" if days <= 30 else "month",
        "trend_label": "Daily" if days <= 30 else "Calendar-month",
    }


def _month_context(months: int) -> dict:
    if months not in MONTH_CHOICES:
        raise HTTPException(422, "Choose 3, 6, 12, 24, or 36 calendar months")
    window = calendar_months(months)
    return {
        "window_months": months,
        "window": window,
        "window_label": window.label,
        "as_of": window.end.isoformat(),
    }


@router.get("/", response_class=HTMLResponse)
def overview(request: Request, days: int = Query(30, ge=1, le=365), session: Session = Depends(get_session)):
    window_ctx = _day_context(days)
    window = window_ctx["window"]
    ctx = {
        "request": request,
        "active": "overview",
        **window_ctx,
        # Stats keyed off the selected window
        "net_worth": analytics.net_worth(session),
        "cash_balance": analytics.cash_balance(session),
        "credit_owed": analytics.credit_owed(session),
        "total_invested": analytics.total_invested(session),
        "income": analytics.income(session, days=days, window=window),
        "spending": analytics.spending(session, days=days, window=window),
        "savings_rate": analytics.savings_rate(session, days=days, window=window),
        "month_compare": analytics.period_compare(session, weeks=False, as_of=window.end),
        "week_compare": analytics.period_compare(session, weeks=True, as_of=window.end),
        "accounts": analytics.account_breakdown(session),
        "recent": analytics.recent_transactions(session, limit=10),
    }
    return templates.TemplateResponse(request, "overview.html", ctx)


@router.get("/banking", response_class=HTMLResponse)
def banking(request: Request, days: int = Query(30, ge=1, le=365), session: Session = Depends(get_session)):
    window_ctx = _day_context(days)
    window = window_ctx["window"]
    account_ids = analytics.account_ids_in_domain(session, "banking")
    ctx = {
        "request": request,
        "active": "banking",
        **window_ctx,
        "cash_balance": analytics.cash_balance(session),
        "credit_owed": analytics.credit_owed(session),
        "net_worth": analytics.net_worth(session, account_ids=account_ids),
        "income": analytics.income(session, days=days, window=window),
        "spending": analytics.spending(session, days=days, account_ids=account_ids, window=window),
        "savings_rate": analytics.savings_rate(session, days=days, window=window),
        "accounts": analytics.account_breakdown(session, account_ids=account_ids),
        "recent": analytics.recent_transactions(session, limit=10, account_ids=account_ids),
        "categories": analytics.category_breakdown(
            session, days=days, account_ids=account_ids, top_n=8, window=window
        ),
    }
    return templates.TemplateResponse(request, "banking.html", ctx)


@router.get("/banking/{account_id}", response_class=HTMLResponse)
def banking_account(
    account_id: int,
    request: Request,
    days: int = Query(30, ge=1, le=365),
    session: Session = Depends(get_session),
):
    window_ctx = _day_context(days)
    window = window_ctx["window"]
    account = analytics.get_account(session, account_id)
    if not account or account.type not in analytics.BANK_TYPES:
        raise HTTPException(404, "Bank account not found")
    summary = analytics.account_summary(session, account, days=days, window=window)
    ctx = {
        "request": request,
        "active": "banking",
        **window_ctx,
        "account": summary,
        "recent": analytics.recent_transactions(session, limit=50, account_id=account_id),
        "categories": analytics.category_breakdown(
            session, days=days, account_ids=[account_id], top_n=8, window=window
        ),
    }
    return templates.TemplateResponse(request, "banking_account.html", ctx)


@router.get("/brokerage", response_class=HTMLResponse)
def brokerage(
    request: Request, months: int = Query(12, ge=1, le=36), session: Session = Depends(get_session)
):
    window_ctx = _month_context(months)
    window = window_ctx["window"]
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
        **window_ctx,
        "accounts": analytics.account_breakdown(session, account_ids=account_ids),
        "summary": summary,
        "dividends": analytics.dividend_history(
            session, months=months, account_ids=account_ids, window=window
        ),
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
    months: int = Query(12, ge=1, le=36),
    session: Session = Depends(get_session),
):
    window_ctx = _month_context(months)
    window = window_ctx["window"]
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
        **window_ctx,
        "account": summary,
        "holdings": holdings,
        "dividends": analytics.dividend_history(
            session, months=months, account_ids=[account_id], window=window
        ),
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
def spending(request: Request, days: int = Query(30, ge=1, le=365), session: Session = Depends(get_session)):
    window_ctx = _day_context(days)
    window = window_ctx["window"]
    ctx = {
        "request": request,
        "active": "spending",
        **window_ctx,
        "income": analytics.income(session, days=days, window=window),
        "spending": analytics.spending(session, days=days, window=window),
        "refunds": analytics.refunds(session, days=days, window=window),
        "savings_rate": analytics.savings_rate(session, days=days, window=window),
        "month_compare": analytics.period_compare(session, weeks=False, as_of=window.end),
        "week_compare": analytics.period_compare(session, weeks=True, as_of=window.end),
        "by_month": analytics.by_period(session, window=window, period=window_ctx["trend_period"]),
        "by_week": analytics.by_period(session, window=window, period="week"),
        "categories": analytics.category_breakdown(session, days=days, top_n=20, window=window),
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
