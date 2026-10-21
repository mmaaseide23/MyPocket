"""Nonsecret process-level polling configuration, shared with the UI."""

import os


def _seconds(name: str, default: int, minimum: int = 1) -> int:
    try:
        return max(minimum, int(os.getenv(name, str(default))))
    except ValueError:
        return default


# Existing override continues to control E*TRADE and Teller only.
SYNC_INTERVAL_SECONDS = _seconds("MYPOCKET_SYNC_INTERVAL_SECONDS", 6 * 3600)
PLAID_SYNC_INTERVAL_SECONDS = _seconds("MYPOCKET_PLAID_SYNC_INTERVAL_SECONDS", 30 * 60, minimum=300)
SYNC_INITIAL_DELAY_SECONDS = _seconds("MYPOCKET_SYNC_INITIAL_DELAY_SECONDS", 30)
PLAID_MAX_RETRY_SECONDS = max(6 * 3600, PLAID_SYNC_INTERVAL_SECONDS)


def _duration(seconds: int) -> str:
    if seconds % 3600 == 0:
        count, unit = seconds // 3600, "hour"
    elif seconds % 60 == 0:
        count, unit = seconds // 60, "minute"
    else:
        count, unit = seconds, "second"
    return f"{count} {unit}{'' if count == 1 else 's'}"


def sync_schedule() -> dict[str, str]:
    return {
        "plaid": _duration(PLAID_SYNC_INTERVAL_SECONDS),
        "other": _duration(SYNC_INTERVAL_SECONDS),
        "startup": _duration(SYNC_INITIAL_DELAY_SECONDS),
        "retry_max": _duration(PLAID_MAX_RETRY_SECONDS),
    }
