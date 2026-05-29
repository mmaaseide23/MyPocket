import logging

from sqlalchemy import inspect, text
from sqlmodel import Session, SQLModel, create_engine

from mypocket.core.config import DATA_DIR, settings

logger = logging.getLogger(__name__)

DATA_DIR.mkdir(parents=True, exist_ok=True)

engine = create_engine(
    settings.database_url,
    echo=False,
    connect_args={"check_same_thread": False} if settings.database_url.startswith("sqlite") else {},
)


def init_db() -> None:
    from mypocket.domain import models  # noqa: F401  - register models

    SQLModel.metadata.create_all(engine)
    _apply_lightweight_migrations()
    _encrypt_legacy_tokens()
    _recategorize_credit_refunds()
    _recategorize_p2p_inflows()


def _apply_lightweight_migrations() -> None:
    """Add columns that newer model versions introduced; drop tables that
    earlier versions defined but newer versions removed."""
    insp = inspect(engine)
    table_names = set(insp.get_table_names())
    table_columns: dict[str, set[str]] = {
        name: {c["name"] for c in insp.get_columns(name)} for name in table_names
    }

    additions: list[tuple[str, str, str]] = [
        ("enrollment", "access_token_secret", "TEXT"),
        ("enrollment", "environment", "TEXT"),
    ]
    # Tables that earlier versions defined but the current models no longer use.
    drops: list[str] = ["price", "categoryrule"]

    with engine.begin() as conn:
        for table, col, sql_type in additions:
            if table in table_columns and col not in table_columns[table]:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {sql_type}"))
        for table in drops:
            if table in table_names:
                conn.execute(text(f"DROP TABLE {table}"))
                logger.info("db: dropped unused table %s", table)


def _encrypt_legacy_tokens() -> None:
    """One-time migration: encrypt any plaintext enrollment tokens already in the DB.

    Uses raw SQL (bypasses ORM type decorators) to see the stored bytes directly.
    Idempotent: rows already encrypted (have the `enc-v1:` prefix) are skipped.
    """
    from mypocket.security.crypto import PREFIX, encrypt

    insp = inspect(engine)
    if "enrollment" not in insp.get_table_names():
        return

    with engine.begin() as conn:
        rows = conn.execute(text("SELECT id, access_token, access_token_secret FROM enrollment")).fetchall()
        for row in rows:
            updates: dict[str, str] = {}
            if row.access_token and not row.access_token.startswith(PREFIX):
                updates["access_token"] = encrypt(row.access_token)
            if row.access_token_secret and not row.access_token_secret.startswith(PREFIX):
                updates["access_token_secret"] = encrypt(row.access_token_secret)
            if updates:
                set_clause = ", ".join(f"{k} = :{k}" for k in updates)
                conn.execute(
                    text(f"UPDATE enrollment SET {set_clause} WHERE id = :id"),
                    {"id": row.id, **updates},
                )
                logger.info("security: encrypted plaintext tokens for enrollment id=%s", row.id)


def _recategorize_p2p_inflows() -> None:
    """One-time fix: positive Venmo/Zelle/Cash App/PayPal transactions were
    previously tagged 'Transfers > P2P'. Inbound P2P is a reimbursement (someone
    paying you back) — it should offset spending instead of washing out.
    Idempotent — once retagged, the WHERE clause matches nothing."""
    with engine.begin() as conn:
        result = conn.execute(
            text(
                "UPDATE \"transaction\" SET category='Reimbursements' "
                "WHERE category='Transfers' AND subcategory='P2P' AND amount > 0"
            )
        )
        if result.rowcount:
            logger.info(
                "analytics: recategorized %d P2P inflows → 'Reimbursements'",
                result.rowcount,
            )


def _recategorize_credit_refunds() -> None:
    """One-time fix: positive transactions on credit cards were previously
    categorized as 'Income' by the unscoped fallback. They're refunds, not
    income. This sweeps existing rows so historical analytics reflect reality.
    Idempotent — once flipped to 'Refunds' the WHERE clause matches nothing."""
    with engine.begin() as conn:
        result = conn.execute(
            text(
                "UPDATE \"transaction\" SET category='Refunds', subcategory=NULL "
                "WHERE category='Income' AND amount > 0 "
                "AND account_id IN (SELECT id FROM account WHERE type='credit')"
            )
        )
        if result.rowcount:
            logger.info(
                "analytics: recategorized %d credit-card 'Income' rows → 'Refunds'",
                result.rowcount,
            )


def get_session():
    with Session(engine) as session:
        yield session
