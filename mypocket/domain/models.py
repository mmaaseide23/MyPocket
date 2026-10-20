from datetime import UTC, date, datetime

from sqlmodel import Field, Relationship, SQLModel

from mypocket.security.sqltypes import EncryptedString


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Account(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    source: str  # "citi_csv", "etrade_csv", "teller", "etrade_api"
    external_id: str | None = Field(default=None, index=True)
    name: str
    type: str  # "checking", "savings", "brokerage", "ira", "credit"
    institution: str
    currency: str = "USD"
    balance: float | None = None
    archived: bool = False  # Keep history, exclude this account from active dashboards.
    last_synced: datetime | None = None
    created_at: datetime = Field(default_factory=_utcnow)

    transactions: list["Transaction"] = Relationship(back_populates="account")
    holdings: list["Holding"] = Relationship(back_populates="account")


class Transaction(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    account_id: int = Field(foreign_key="account.id", index=True)
    external_id: str | None = Field(default=None, index=True)
    tx_date: date = Field(index=True)
    amount: float  # signed: negative = outflow, positive = inflow
    description: str
    merchant: str | None = None
    category: str | None = Field(default=None, index=True)
    subcategory: str | None = None
    transaction_type: str | None = Field(default=None, index=True)
    excluded: bool = False  # Plaid pending/removed rows retain history but are not counted
    # for investment txns:
    symbol: str | None = Field(default=None, index=True)
    quantity: float | None = None
    price: float | None = None
    commission: float | None = None
    notes: str | None = None
    raw: str | None = None
    created_at: datetime = Field(default_factory=_utcnow)

    account: Account | None = Relationship(back_populates="transactions")


class Holding(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    account_id: int = Field(foreign_key="account.id", index=True)
    symbol: str = Field(index=True)
    name: str | None = None
    quantity: float
    cost_basis: float | None = None
    avg_cost: float | None = None
    market_value: float | None = None
    last_price: float | None = None
    day_change: float | None = None
    day_change_pct: float | None = None
    total_gain: float | None = None
    total_gain_pct: float | None = None
    as_of: date = Field(default_factory=date.today)
    created_at: datetime = Field(default_factory=_utcnow)

    account: Account | None = Relationship(back_populates="holdings")


class AppConfig(SQLModel, table=True):
    """Singleton row (id=1) holding app-wide settings — currently just the passcode hash."""

    id: int | None = Field(default=None, primary_key=True)
    passcode_hash: str
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime | None = None


class Enrollment(SQLModel, table=True):
    """A Teller / E*TRADE / future-API enrollment: one provider link + tokens."""

    id: int | None = Field(default=None, primary_key=True)
    provider: str  # "teller", "etrade", ...
    enrollment_id: str | None = Field(default=None, index=True)
    institution: str | None = None
    institution_id: str | None = None
    user_id: str | None = None  # provider's user id, if any
    access_token: str = Field(sa_type=EncryptedString)  # encrypted at rest
    access_token_secret: str | None = Field(default=None, sa_type=EncryptedString)
    request_token_created_at: datetime | None = None
    status: str = "active"  # "active", "needs_reauth", "disconnected", "pending_verifier"
    environment: str | None = None  # "sandbox" / "production" for E*TRADE
    last_synced: datetime | None = None
    last_error: str | None = None
    created_at: datetime = Field(default_factory=_utcnow)


class PlaidConnection(SQLModel, table=True):
    enrollment_id: int = Field(primary_key=True, foreign_key="enrollment.id")
    cursor: str = ""
    approved: bool = False
    review_json: str | None = Field(default=None, sa_type=EncryptedString)
    review_id: str | None = None
    reviewed_at: datetime | None = None
    plan_json: str | None = Field(default=None, sa_type=EncryptedString)
    plan_id: str | None = None


class PlaidAccount(SQLModel, table=True):
    enrollment_id: int = Field(primary_key=True, foreign_key="enrollment.id")
    external_id: str = Field(primary_key=True)
    account_id: int | None = Field(default=None, foreign_key="account.id", index=True)
    import_from: date | None = None


class PlaidTransaction(SQLModel, table=True):
    enrollment_id: int = Field(primary_key=True, foreign_key="enrollment.id")
    external_id: str = Field(primary_key=True)
    transaction_id: int | None = Field(default=None, foreign_key="transaction.id", index=True)


class PlaidLinkFlow(SQLModel, table=True):
    id: str = Field(primary_key=True)
    session_hash: str
    environment: str
    enrollment_id: int | None = None
    link_token: str = Field(sa_type=EncryptedString)
    created_at: datetime = Field(default_factory=_utcnow)
    result_id: int | None = None
    public_token_hash: str | None = None


class CitiReset(SQLModel, table=True):
    """Recoverable local replacement; old records remain in their archived account."""

    id: int | None = Field(default=None, primary_key=True)
    original_id: int = Field(foreign_key="account.id")
    replacement_id: int = Field(foreign_key="account.id")
    enrollment_id: int = Field(foreign_key="enrollment.id")
    backup_name: str
    restored: bool = False
    created_at: datetime = Field(default_factory=_utcnow)
