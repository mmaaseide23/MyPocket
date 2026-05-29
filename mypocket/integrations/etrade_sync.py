"""Pull E*TRADE data via the official API → upsert into MyPocket DB.

Designed to handle multiple E*TRADE accounts (e.g. brokerage + Roth IRA) under
one enrollment, since one OAuth grant covers all the user's accounts.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from datetime import date as Date

from sqlmodel import Session, select

from mypocket.core.utils import to_float
from mypocket.domain.categorize import categorize
from mypocket.domain.models import Account, Enrollment, Holding, Transaction
from mypocket.integrations import etrade
from mypocket.integrations.etrade import ETradeNeedsReauth

logger = logging.getLogger(__name__)


def _account_type_from_etrade(account_type: str | None, account_desc: str | None) -> str:
    s = (f"{account_type or ''} {account_desc or ''}").lower()
    if "roth" in s:
        return "ira"
    if "ira" in s or "retirement" in s:
        return "ira"
    if "brokerage" in s or "margin" in s or "cash" in s:
        return "brokerage"
    return "brokerage"


def _ms_to_date(ms: int | str | None) -> Date | None:
    if ms is None:
        return None
    try:
        ts = int(ms) / 1000.0
        return datetime.fromtimestamp(ts, tz=UTC).date()
    except (ValueError, TypeError):
        return None


def _normalize_tx_type(t: str | None) -> str | None:
    if not t:
        return None
    return t.strip().lower().replace(" ", "_")


def sync_enrollment(session: Session, enrollment: Enrollment) -> dict:
    out = {
        "enrollment_id": enrollment.id,
        "accounts": 0,
        "transactions_created": 0,
        "transactions_skipped": 0,
        "holdings_loaded": 0,
        "errors": [],
    }

    if enrollment.provider != "etrade":
        out["errors"].append("not an etrade enrollment")
        return out

    # Renew access token before fetching (extends idle expiry by 2h)
    try:
        etrade.renew_access_token(
            enrollment.access_token,
            enrollment.access_token_secret,
            environment=enrollment.environment,
        )
    except ETradeNeedsReauth as e:
        enrollment.status = "needs_reauth"
        enrollment.last_error = str(e)
        session.add(enrollment)
        session.commit()
        out["errors"].append(f"needs_reauth: {e}")
        return out
    except Exception as e:
        # Renew can fail for transient reasons; continue and let API call fail if truly broken
        out["errors"].append(f"renew warning: {e}")

    try:
        accounts = etrade.list_accounts(
            enrollment.access_token,
            enrollment.access_token_secret,
            environment=enrollment.environment,
        )
    except ETradeNeedsReauth as e:
        enrollment.status = "needs_reauth"
        enrollment.last_error = str(e)
        session.add(enrollment)
        session.commit()
        out["errors"].append(f"needs_reauth: {e}")
        return out
    except Exception as e:
        enrollment.last_error = str(e)
        session.add(enrollment)
        session.commit()
        out["errors"].append(f"list_accounts failed: {e}")
        return out

    for ea in accounts:
        out["accounts"] += 1
        try:
            counts = _sync_one_account(session, enrollment, ea)
            out["transactions_created"] += counts.get("created", 0)
            out["transactions_skipped"] += counts.get("skipped", 0)
            out["holdings_loaded"] += counts.get("holdings", 0)
        except Exception as e:
            out["errors"].append(f"account {ea.get('accountIdKey')}: {e}")

    enrollment.last_synced = datetime.now(UTC)
    if not out["errors"]:
        enrollment.status = "active"
        enrollment.last_error = None
    else:
        enrollment.last_error = (out["errors"][-1])[:500]
    session.add(enrollment)
    session.commit()
    return out


def _sync_one_account(session: Session, enrollment: Enrollment, ea: dict) -> dict:
    account_id_key = ea.get("accountIdKey")
    if not account_id_key:
        return {"created": 0, "skipped": 0, "holdings": 0}

    account_type_raw = ea.get("accountType")
    account_desc = ea.get("accountDesc") or ea.get("accountName")
    inst_type = ea.get("institutionType", "BROKERAGE")
    account_type = _account_type_from_etrade(account_type_raw, account_desc)

    # Upsert account
    account = session.exec(
        select(Account).where(Account.external_id == account_id_key, Account.source == "etrade_api")
    ).first()
    if account is None:
        account = Account(
            source="etrade_api",
            external_id=account_id_key,
            name=account_desc or f"E*TRADE {account_type.title()}",
            type=account_type,
            institution="E*TRADE",
        )

    # Balance
    try:
        bal = etrade.get_balance(
            account_id_key,
            inst_type,
            enrollment.access_token,
            enrollment.access_token_secret,
            environment=enrollment.environment,
        )
        nav = ((bal.get("BalanceResponse") or {}).get("Computed") or {}).get("RealTimeValues") or {}
        total = nav.get("totalAccountValue")
        if total is not None:
            account.balance = float(total)
    except Exception as e:
        logger.warning("etrade: balance fetch failed for %s: %s", account_id_key, e)

    account.last_synced = datetime.now(UTC)
    session.add(account)
    session.commit()
    session.refresh(account)

    # Portfolio (holdings) — replace snapshot
    holdings_count = 0
    try:
        pf = etrade.get_portfolio(
            account_id_key,
            enrollment.access_token,
            enrollment.access_token_secret,
            environment=enrollment.environment,
        )
        positions = _extract_positions(pf)
        for h in session.exec(select(Holding).where(Holding.account_id == account.id)).all():
            session.delete(h)
        session.flush()
        for p in positions:
            symbol = ((p.get("Product") or {}).get("symbol") or p.get("symbolDescription") or "").strip()
            if not symbol:
                continue
            qty = to_float(p.get("quantity"))
            cost = to_float(p.get("totalCost"))
            value = to_float(p.get("marketValue"))
            last_price = to_float(
                p.get("Quick", {}).get("lastTrade") if isinstance(p.get("Quick"), dict) else None
            )
            total_gain = to_float(p.get("totalGain"))
            total_gain_pct = to_float(p.get("totalGainPct"))
            day_change = to_float(p.get("daysGain"))
            day_change_pct = to_float(p.get("daysGainPct"))
            avg_cost = (cost / qty) if (cost is not None and qty) else None
            session.add(
                Holding(
                    account_id=account.id,
                    symbol=symbol,
                    name=(p.get("Product") or {}).get("securityType") or p.get("symbolDescription"),
                    quantity=qty or 0.0,
                    cost_basis=cost,
                    avg_cost=avg_cost,
                    market_value=value,
                    last_price=last_price,
                    day_change=day_change,
                    day_change_pct=day_change_pct,
                    total_gain=total_gain,
                    total_gain_pct=total_gain_pct,
                    as_of=Date.today(),
                )
            )
            holdings_count += 1
        session.commit()
    except Exception as e:
        logger.warning("etrade: portfolio fetch failed for %s: %s", account_id_key, e)

    # Transactions
    existing_ext_ids = {
        t.external_id
        for t in session.exec(select(Transaction).where(Transaction.account_id == account.id)).all()
        if t.external_id
    }

    created = 0
    skipped = 0
    try:
        txr = etrade.get_transactions(
            account_id_key,
            enrollment.access_token,
            enrollment.access_token_secret,
            environment=enrollment.environment,
            count=200,
        )
        txns = _extract_transactions(txr)
        for t in txns:
            tid = str(t.get("transactionId") or "")
            if not tid:
                continue
            if tid in existing_ext_ids:
                skipped += 1
                continue
            tx_date = _ms_to_date(t.get("transactionDate")) or Date.today()
            amount = to_float(t.get("amount")) or 0.0
            tx_type = t.get("transactionType") or ""
            description = (
                t.get("description")
                or t.get("memo")
                or t.get("displaySymbol")
                or tx_type
                or "E*TRADE transaction"
            )
            symbol = None
            brokerage = t.get("brokerage") or {}
            if isinstance(brokerage, dict):
                product = brokerage.get("product") or {}
                if isinstance(product, dict):
                    symbol = product.get("symbol")
            symbol = symbol or t.get("symbol")

            norm_type = _normalize_tx_type(tx_type)
            match = categorize(f"{tx_type} {description}", amount, account_type=account.type)
            if norm_type in {"bought", "buy"}:
                match.category, match.subcategory, norm_type = "Investment", "Buy", "buy"
            elif norm_type in {"sold", "sell"}:
                match.category, match.subcategory, norm_type = "Investment", "Sell", "sell"
            elif norm_type and "dividend" in norm_type:
                match.category, match.subcategory = "Income", "Dividends"
            elif norm_type and "interest" in norm_type:
                match.category, match.subcategory = "Income", "Interest"
            elif norm_type and "contribution" in norm_type:
                match.category, match.subcategory = "Transfers", "Contribution"

            session.add(
                Transaction(
                    account_id=account.id,
                    external_id=tid,
                    tx_date=tx_date,
                    amount=amount,
                    description=description,
                    merchant=symbol or match.merchant,
                    category=match.category,
                    subcategory=match.subcategory,
                    transaction_type=norm_type,
                    symbol=symbol,
                    quantity=to_float(t.get("quantity")),
                    price=to_float(t.get("price")),
                    commission=to_float(t.get("commission")) or to_float(t.get("fee")),
                    raw=json.dumps(t),
                )
            )
            created += 1
        session.commit()
    except Exception as e:
        logger.warning("etrade: transactions fetch failed for %s: %s", account_id_key, e)

    return {"created": created, "skipped": skipped, "holdings": holdings_count}


def _extract_positions(payload: dict) -> list[dict]:
    """E*TRADE returns positions under a few possible nesting paths; unwrap defensively."""
    if not isinstance(payload, dict):
        return []
    resp = payload.get("PortfolioResponse") or {}
    accs = resp.get("AccountPortfolio") or []
    if isinstance(accs, dict):
        accs = [accs]
    out: list[dict] = []
    for a in accs:
        positions = a.get("Position") or []
        if isinstance(positions, dict):
            positions = [positions]
        out.extend(positions)
    return out


def _extract_transactions(payload: dict) -> list[dict]:
    if not isinstance(payload, dict):
        return []
    resp = payload.get("TransactionListResponse") or {}
    txns = resp.get("Transaction") or []
    if isinstance(txns, dict):
        txns = [txns]
    return txns


def sync_all(session: Session) -> list[dict]:
    enrollments = session.exec(
        select(Enrollment).where(Enrollment.provider == "etrade", Enrollment.status == "active")
    ).all()
    return [sync_enrollment(session, e) for e in enrollments]
