"""Additive feature tables, separate from the application's default metadata."""

from sqlalchemy import BigInteger, Boolean, Column, DateTime, Integer, MetaData, String, Table

metadata = MetaData()

consents = Table(
    "saved_view_consent", metadata,
    Column("id", Integer, primary_key=True),
    Column("active", Boolean, nullable=False, default=False),
    Column("owner_scope", String(36), nullable=False),
    Column("generation", Integer, nullable=False),
    Column("app_created_at", DateTime(timezone=True), nullable=False),
    Column("approved_at", DateTime(timezone=True), nullable=False),
    Column("field_set", String(32), nullable=False),
    Column("history_enabled", Boolean, nullable=False, default=False),
    Column("retention_seconds", Integer, nullable=False),
)
account_scopes = Table(
    "saved_view_account_scope", metadata,
    Column("account_id", Integer, primary_key=True),
    Column("public_ref", String(36), nullable=False, unique=True),
    Column("account_type", String(16), nullable=False),
    Column("label", String(32), nullable=False),
)
sources = Table(
    "saved_view_source", metadata,
    Column("enrollment_id", Integer, primary_key=True),
    Column("public_ref", String(36), nullable=False, unique=True),
    Column("provider", String(16), nullable=False),
)
runs = Table(
    "saved_view_sync_run", metadata,
    Column("id", String(36), primary_key=True),
    Column("enrollment_id", Integer, nullable=False, index=True),
    Column("started_at", DateTime(timezone=True), nullable=False),
    Column("finished_at", DateTime(timezone=True)),
    Column("outcome", String(16), nullable=False),
    Column("provider_as_of", DateTime(timezone=True)),
)
balances = Table(
    "saved_view_balance_point", metadata,
    Column("run_id", String(36), primary_key=True),
    Column("account_id", Integer, primary_key=True, index=True),
    Column("balance_minor", BigInteger),
    Column("account_type", String(16), nullable=False),
    Column("currency", String(3), nullable=False),
    Column("observed_at", DateTime(timezone=True), nullable=False),
    Column("provider_as_of", DateTime(timezone=True)),
)
identities = Table(
    "saved_view_identity", metadata,
    Column("id", Integer, primary_key=True),
    Column("session_epoch", String(36), nullable=False),
    Column("app_created_at", DateTime(timezone=True), nullable=False),
)


def initialize_schema(engine):
    """Explicit, additive and idempotent. Does not read/backfill old account data."""
    metadata.create_all(engine, checkfirst=True)
