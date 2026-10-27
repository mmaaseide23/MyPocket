from datetime import date, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session, select

from mypocket.core.db import get_session
from mypocket.domain import analytics, positions
from mypocket.domain.models import Account, CitiReset, Holding, PlaidAccount, Transaction
from mypocket.domain.windows import Window, calendar_months, trailing_days
from mypocket.integrations import etrade_sync, plaid_sync, teller_sync
from mypocket.integrations._errors import safe_error

router = APIRouter()


def _as_of(value: date | None) -> date:
    today = date.today()
    if value is not None and value > today:
        raise HTTPException(422, "The reporting date cannot be in the future")
    return value or today


@router.get("/summary")
def get_summary(days: int = Query(30, ge=1, le=366), session: Session = Depends(get_session)):
    return {
        "net_worth": analytics.net_worth(session),
        "income": analytics.income(session, days=days),
        "spending": analytics.spending(session, days=days),
        "window_days": days,
        "accounts": analytics.account_breakdown(session),
    }


@router.post("/sync")
def trigger_full_sync(session: Session = Depends(get_session)):
    """Refresh each provider independently, matching the scheduler's isolation."""
    summary = {}
    for name, adapter in (("teller", teller_sync), ("etrade", etrade_sync), ("plaid", plaid_sync)):
        try:
            summary[name] = adapter.sync_all(session)
        except Exception as exc:
            session.rollback()
            # Earlier providers may have committed successfully. Preserve those
            # results and still attempt the remaining providers; never expose
            # raw upstream exceptions, URLs, credentials or response bodies.
            summary[name] = [{"errors": [safe_error(exc)]}]
    return summary


@router.delete("/accounts/{account_id}")
def delete_account(account_id: int, session: Session = Depends(get_session)):
    if session.exec(
        select(CitiReset).where(
            (CitiReset.original_id == account_id) | (CitiReset.replacement_id == account_id)
        )
    ).first():
        raise HTTPException(
            409, "This account belongs to a recoverable Citi replacement. Use scoped recovery."
        )
    """Delete an account and all its transactions + holdings. Irreversible."""
    account = session.get(Account, account_id)
    if not account:
        raise HTTPException(404, "Account not found")
    if session.exec(select(PlaidAccount).where(PlaidAccount.account_id == account_id)).first():
        raise HTTPException(
            409, "This account has an active Plaid mapping. Review ownership before deleting history."
        )
    for t in session.exec(select(Transaction).where(Transaction.account_id == account_id)).all():
        session.delete(t)
    for h in session.exec(select(Holding).where(Holding.account_id == account_id)).all():
        session.delete(h)
    session.delete(account)
    session.commit()
    return {"ok": True}


@router.get("/flows")
def get_flows(
    months: int = Query(12, ge=1, le=120),
    days: int | None = Query(None, ge=1, le=366),
    period: Literal["day", "week", "month"] = "month",
    as_of: date | None = None,
    account_id: int | None = None,
    domain: str | None = None,
    session: Session = Depends(get_session),
):
    anchor = _as_of(as_of)
    window = trailing_days(days, anchor) if days is not None else calendar_months(months, anchor)
    account_ids = _resolve_scope(session, account_id, domain)
    return analytics.monthly_flows(
        session, months=months, account_ids=account_ids, window=window, period=period
    )


@router.get("/categories")
def get_categories(
    days: int = Query(30, ge=1, le=366),
    as_of: date | None = None,
    top: int = 10,
    account_id: int | None = None,
    domain: str | None = None,
    session: Session = Depends(get_session),
):
    account_ids = _resolve_scope(session, account_id, domain)
    return analytics.category_breakdown(
        session, days=days, top_n=top, account_ids=account_ids, window=trailing_days(days, _as_of(as_of))
    )


def _resolve_scope(
    session: Session,
    account_id: int | None,
    domain: str | None,
) -> list[int] | None:
    """Translate either an explicit account_id or a domain name into an account_ids filter."""
    if account_id is not None:
        return [account_id]
    if domain in ("banking", "brokerage"):
        return analytics.account_ids_in_domain(session, domain)
    return None


@router.get("/transactions")
def get_transactions(
    limit: int = 50,
    account_id: int | None = None,
    category: str | None = None,
    q: str | None = None,
    session: Session = Depends(get_session),
):
    return analytics.recent_transactions(
        session,
        limit=limit,
        account_id=account_id,
        category=category,
        search=q,
    )


@router.get("/holdings")
def get_holdings(
    account_id: int | None = None,
    domain: str | None = None,
    session: Session = Depends(get_session),
):
    account_ids = _resolve_scope(session, account_id, domain)
    return analytics.holdings_summary(session, account_ids=account_ids)


@router.get("/dividends")
def get_dividends(
    months: int = Query(12, ge=1, le=120),
    as_of: date | None = None,
    account_id: int | None = None,
    domain: str | None = None,
    session: Session = Depends(get_session),
):
    account_ids = _resolve_scope(session, account_id, domain)
    return analytics.dividend_history(
        session, months=months, account_ids=account_ids, window=calendar_months(months, _as_of(as_of))
    )


@router.get("/spending/trend")
def get_spending_trend(
    period: Literal["day", "week", "month"] = "month",
    n: int = Query(6, ge=1, le=120),
    days: int | None = Query(None, ge=1, le=366),
    as_of: date | None = None,
    session: Session = Depends(get_session),
):
    """Calendar buckets clipped to a pinned range; legacy n counts calendar periods."""
    anchor = _as_of(as_of)
    if days is not None:
        window = trailing_days(days, anchor)
    elif period == "week":
        window = Window(anchor - timedelta(days=anchor.weekday() + 7 * (n - 1)), anchor + timedelta(days=1))
    elif period == "day":
        window = trailing_days(n, anchor)
    else:
        window = calendar_months(n, anchor)
    return analytics.by_period(session, window=window, period=period)


@router.get("/positions/{position_id}/history")
def position_history(
    position_id: int,
    range: Literal["7d", "30d", "90d", "1y", "all"] = "30d",
    session: Session = Depends(get_session),
):
    result = positions.history(session, position_id, range)
    if result is None:
        raise HTTPException(404, "Saved position not found in the latest active account snapshot")
    return result
