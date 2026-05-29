"""Safe redirect-target validation. Defends against open-redirect attacks via `?next=`."""

from __future__ import annotations

from urllib.parse import urlparse


def safe_next(candidate: str | None, default: str = "/") -> str:
    """Return `candidate` only if it's a same-origin path. Otherwise return `default`.

    Rejects:
      - absolute URLs (`http://...`, `//evil.com`, `https://...`)
      - protocol-relative variants (`//evil.com`, `/\\evil.com`)
      - paths that don't start with a single `/`
      - encoded variants like `/%2f%2fevil.com` (parsed by browsers as `//evil.com`)
    """
    if not candidate:
        return default
    # Reject anything that contains backslashes (browsers normalize \ to / in URLs)
    if "\\" in candidate:
        return default
    # urlparse on a same-origin path returns scheme='', netloc=''. Any other shape is suspicious.
    parsed = urlparse(candidate)
    if parsed.scheme or parsed.netloc:
        return default
    # Must start with exactly one slash (rejects "//evil.com", "/\\evil.com", "evil.com")
    if not candidate.startswith("/") or candidate.startswith("//"):
        return default
    # Reject percent-encoded slashes that browsers may treat as protocol-relative
    lowered = candidate.lower()
    if "%2f%2f" in lowered or "%5c" in lowered:
        return default
    return candidate
