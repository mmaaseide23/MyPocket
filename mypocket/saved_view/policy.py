from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from urllib.parse import urlsplit

ACCOUNT_LABELS = {"checking": "Checking", "savings": "Savings", "brokerage": "Investments", "ira": "Retirement", "credit": "Credit"}
FIELD_SET = "generic-balances-v1"
DEFAULT_RETENTION_SECONDS = 86400
MAX_MINOR = 100_000_000_000_000


class SavedViewUnavailable(ValueError):
    """A safe public reason code; never include source values in messages."""


@dataclass(frozen=True)
class SavedViewOptions:
    enabled: bool = False
    origin: str | None = None
    synthetic_loopback: bool = False  # Test factory only; production never sets this.

    def __post_init__(self):
        if self.enabled:
            parsed = urlsplit(self.origin or "")
            # Accessing .port also rejects malformed / out-of-range ports.
            _ = parsed.port
            test_http = self.synthetic_loopback and parsed.scheme == "http" and parsed.hostname == "127.0.0.1"
            if ((parsed.scheme != "https" and not test_http) or not parsed.hostname or parsed.username or parsed.password
                    or parsed.path or parsed.query or parsed.fragment):
                raise ValueError("An explicit HTTPS origin is required")


def utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    # Existing SQLite columns conventionally contain UTC without a tzinfo.
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def supplied_time(value: datetime, *, no_later_than: datetime | None = None) -> datetime:
    if value.tzinfo is None:
        raise ValueError("A timezone-aware timestamp is required")
    value = value.astimezone(UTC)
    if no_later_than and value > no_later_than:
        raise ValueError("Timestamp cannot be later than the completed check")
    return value


def retention(value: int) -> int:
    if type(value) is not int or not 3600 <= value <= 7 * 86400:
        raise ValueError("Retention must be between one hour and seven days")
    return value


def minor_units(value) -> int | None:
    if value is None:
        return None
    try:
        number = Decimal(str(value))
        if not number.is_finite():
            raise ValueError
        result = int((number * 100).quantize(Decimal("1"), rounding=ROUND_HALF_EVEN))
    except (InvalidOperation, ValueError, OverflowError):
        raise SavedViewUnavailable("invalid_balance") from None
    if abs(result) > MAX_MINOR:
        raise SavedViewUnavailable("invalid_balance")
    return result
