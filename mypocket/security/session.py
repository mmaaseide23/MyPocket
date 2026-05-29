"""Signed session cookies. Stateless: cookie contents are HMAC-signed and time-bound."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

from mypocket.security.keys import cookie_signing_key

SESSION_COOKIE_NAME = "mypocket_session"
SESSION_TTL_SECONDS = 30 * 86400  # 30 days


def _b64url_encode(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


def _b64url_decode(s: str) -> bytes:
    padding = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + padding)


def issue() -> str:
    """Create a signed session token good for SESSION_TTL_SECONDS."""
    payload = {"iat": int(time.time()), "exp": int(time.time()) + SESSION_TTL_SECONDS}
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    body = _b64url_encode(raw)
    sig = hmac.new(cookie_signing_key(), body.encode("ascii"), hashlib.sha256).digest()
    return f"{body}.{_b64url_encode(sig)}"


def verify(token: str | None) -> bool:
    """True if the token is well-formed, signature-valid, and not expired."""
    if not token or "." not in token:
        return False
    body, sig_b64 = token.rsplit(".", 1)
    expected = hmac.new(cookie_signing_key(), body.encode("ascii"), hashlib.sha256).digest()
    try:
        sig = _b64url_decode(sig_b64)
    except (ValueError, base64.binascii.Error):
        return False
    if not hmac.compare_digest(expected, sig):
        return False
    try:
        payload = json.loads(_b64url_decode(body))
    except (json.JSONDecodeError, ValueError, base64.binascii.Error):
        return False
    return int(payload.get("exp", 0)) > int(time.time())
