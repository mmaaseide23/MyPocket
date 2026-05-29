from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from mypocket.core.db import get_session
from mypocket.domain import analytics
from mypocket.domain.models import Account, Holding, Transaction
from mypocket.integrations import etrade_sync, teller_sync

router = APIRouter()


@router.get("/summary")
def get_summary(days: int = 30, session: Session = Depends(get_session)):
    return {
        "net_worth": analytics.net_worth(session),
        "income": analytics.income(session, days=days),
        "spending": analytics.spending(session, days=days),
        "window_days": days,
        "accounts": analytics.account_breakdown(session),
    }


@router.post("/sync")
def trigger_full_sync(session: Session = Depends(get_session)):
    """Manual sync: pulls Teller + E*TRADE in sequence. The background scheduler
    runs this periodically, but the UI exposes it for on-demand refresh."""
    return {
        "teller": teller_sync.sync_all(session),
        "etrade": etrade_sync.sync_all(session),
    }


@router.delete("/accounts/{account_id}")
def delete_account(account_id: int, session: Session = Depends(get_session)):
    """Delete an account and all its transactions + holdings. Irreversible."""
    account = session.get(Account, account_id)
    if not account:
        raise HTTPException(404, "Account not found")
    for t in session.exec(select(Transaction).where(Transaction.account_id == account_id)).all():
        session.delete(t)
    for h in session.exec(select(Holding).where(Holding.account_id == account_id)).all():
        session.delete(h)
    session.delete(account)
    session.commit()
    return {"ok": True}


@router.get("/flows")
def get_flows(
    months: int = 12,
    account_id: int | None = None,
    domain: str | None = None,
    session: Session = Depends(get_session),
):
    account_ids = _resolve_scope(session, account_id, domain)
    return analytics.monthly_flows(session, months=months, account_ids=account_ids)


@router.get("/categories")
def get_categories(
    days: int = 30,
    top: int = 10,
    account_id: int | None = None,
    domain: str | None = None,
    session: Session = Depends(get_session),
):
    account_ids = _resolve_scope(session, account_id, domain)
    return analytics.category_breakdown(session, days=days, top_n=top, account_ids=account_ids)


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
    months: int = 12,
    account_id: int | None = None,
    domain: str | None = None,
    session: Session = Depends(get_session),
):
    account_ids = _resolve_scope(session, account_id, domain)
    return analytics.dividend_history(session, months=months, account_ids=account_ids)


@router.get("/spending/trend")
def get_spending_trend(
    period: str = "month",
    n: int = 6,
    session: Session = Depends(get_session),
):
    """Return the last `n` periods of income/spending/net/savings_rate.
    `period` ∈ {'week', 'month'}."""
    return analytics.by_period(session, weeks=(period == "week"), n=n)
