"""Messages safe for UI and scheduler logs. Never include upstream bodies or URLs."""

import httpx


class ProviderError(Exception):
    """An application-authored, credential-free integration error."""


def safe_error(error: Exception) -> str:
    if isinstance(error, ProviderError):
        return str(error)
    if isinstance(error, httpx.TimeoutException):
        return "Provider request timed out. Try syncing again later."
    if isinstance(error, httpx.RequestError):
        return "Provider connection failed. Check network connectivity and try again."
    return "Sync failed unexpectedly. Previous data has been retained."


def transport_error(provider: str, error: httpx.RequestError) -> str:
    # Inspect locally; never forward exception text (it can contain URLs or secrets).
    cause = error
    while cause is not None:
        if "CERTIFICATE_VERIFY_FAILED" in str(cause).upper():
            return (
                f"{provider} TLS certificate verification failed. Check the Mac's date and time "
                "and the provider's server certificate. Do not disable certificate verification. "
                "Reconnecting the account does not repair a server certificate."
            )
        cause = cause.__cause__
    return f"{provider}: {safe_error(error)}"
