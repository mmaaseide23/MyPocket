"""Minimal OAuth 1.0a signer (HMAC-SHA1) for E*TRADE.

Spec: https://oauth.net/core/1.0a/
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
from urllib.parse import quote, urlsplit, urlunsplit


def _pct(s: str | int | float) -> str:
    """RFC 3986 percent-encoding (uppercase hex, unreserved chars unencoded)."""
    return quote(str(s), safe="-._~")


def _nonce() -> str:
    return secrets.token_hex(16)


def _normalize_url(url: str) -> str:
    """Drop query string (params signed separately) and lowercase scheme/host."""
    scheme, netloc, path, _query, _frag = urlsplit(url)
    return urlunsplit((scheme.lower(), netloc.lower(), path, "", ""))


def _split_query(url: str) -> dict[str, list[str]]:
    """Return query params from a URL as a dict[name -> list[value]]."""
    _scheme, _netloc, _path, query, _frag = urlsplit(url)
    out: dict[str, list[str]] = {}
    if not query:
        return out
    for pair in query.split("&"):
        if not pair:
            continue
        if "=" in pair:
            k, v = pair.split("=", 1)
        else:
            k, v = pair, ""
        out.setdefault(k, []).append(v)
    return out


def _signature_base_string(method: str, url: str, all_params: dict[str, str | list[str]]) -> str:
    """Build the OAuth signature base string per RFC 5849."""
    # Collect (encoded_key, encoded_value) pairs
    encoded: list[tuple[str, str]] = []
    for k, v in all_params.items():
        if isinstance(v, list):
            for item in v:
                encoded.append((_pct(k), _pct(item)))
        else:
            encoded.append((_pct(k), _pct(v)))
    encoded.sort()
    param_string = "&".join(f"{k}={v}" for k, v in encoded)
    return f"{method.upper()}&{_pct(_normalize_url(url))}&{_pct(param_string)}"


def _signing_key(consumer_secret: str, token_secret: str | None) -> str:
    return f"{_pct(consumer_secret)}&{_pct(token_secret or '')}"


def _sign(base_string: str, signing_key: str) -> str:
    digest = hmac.new(signing_key.encode(), base_string.encode(), hashlib.sha1).digest()
    return base64.b64encode(digest).decode()


def build_oauth_params(
    consumer_key: str,
    *,
    token: str | None = None,
    callback: str | None = None,
    verifier: str | None = None,
    nonce: str | None = None,
    timestamp: int | None = None,
) -> dict[str, str]:
    params: dict[str, str] = {
        "oauth_consumer_key": consumer_key,
        "oauth_nonce": nonce or _nonce(),
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp": str(timestamp or int(time.time())),
        "oauth_version": "1.0",
    }
    if token:
        params["oauth_token"] = token
    if callback:
        params["oauth_callback"] = callback
    if verifier:
        params["oauth_verifier"] = verifier
    return params


def sign_request(
    method: str,
    url: str,
    *,
    consumer_key: str,
    consumer_secret: str,
    token: str | None = None,
    token_secret: str | None = None,
    callback: str | None = None,
    verifier: str | None = None,
    extra_params: dict[str, str] | None = None,
) -> tuple[str, dict[str, str]]:
    """Sign an OAuth 1.0a request. Returns (authorization_header, oauth_params)."""
    oauth_params = build_oauth_params(
        consumer_key,
        token=token,
        callback=callback,
        verifier=verifier,
    )

    # Combine OAuth params + query params + body params (here we only use OAuth + query)
    all_params: dict[str, str | list[str]] = dict(oauth_params)
    for k, vs in _split_query(url).items():
        if k in all_params:
            existing = all_params[k]
            if isinstance(existing, list):
                existing.extend(vs)
            else:
                all_params[k] = [existing] + vs
        else:
            all_params[k] = vs if len(vs) > 1 else vs[0]
    if extra_params:
        for k, v in extra_params.items():
            all_params[k] = v

    base_string = _signature_base_string(method, url, all_params)
    key = _signing_key(consumer_secret, token_secret)
    signature = _sign(base_string, key)
    oauth_params["oauth_signature"] = signature

    # Build Authorization header (only the oauth_* params go in there)
    header_pairs = [f'{_pct(k)}="{_pct(v)}"' for k, v in sorted(oauth_params.items())]
    header = "OAuth " + ", ".join(header_pairs)
    return header, oauth_params
