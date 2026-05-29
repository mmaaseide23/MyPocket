"""E*TRADE Developer API client (OAuth 1.0a).

Auth flow ("out-of-band" PIN, no callback URL):
  1. POST /oauth/request_token → request_token + request_token_secret
  2. User visits https://us.etrade.com/e/t/etws/authorize?key=KEY&token=REQUEST_TOKEN
     → user logs in, approves, sees a verifier code (5 alphanumeric chars)
  3. POST /oauth/access_token (with verifier) → access_token + access_token_secret
  4. Tokens valid until midnight ET, idle-expire after 2h; renew with /oauth/renew_access_token

Docs: https://developer.etrade.com
"""

from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs

import httpx

from mypocket.core.config import settings
from mypocket.integrations._oauth1 import sign_request

SANDBOX_BASE = "https://apisb.etrade.com"
PROD_BASE = "https://api.etrade.com"
AUTHORIZE_URL = "https://us.etrade.com/e/t/etws/authorize"


class ETradeError(Exception):
    pass


class ETradeNeedsReauth(ETradeError):
    """Tokens expired or revoked — user must redo the OAuth dance."""


def _base_url(env: str | None = None) -> str:
    e = (env or settings.etrade_environment or "sandbox").lower()
    return PROD_BASE if e in {"prod", "production", "live"} else SANDBOX_BASE


def _credentials() -> tuple[str, str]:
    if not settings.etrade_consumer_key or not settings.etrade_consumer_secret:
        raise ETradeError("ETRADE_CONSUMER_KEY / ETRADE_CONSUMER_SECRET not set in .env")
    return settings.etrade_consumer_key, settings.etrade_consumer_secret


def _signed_request(
    method: str,
    url: str,
    *,
    token: str | None = None,
    token_secret: str | None = None,
    callback: str | None = None,
    verifier: str | None = None,
    accept: str = "application/json",
) -> httpx.Response:
    consumer_key, consumer_secret = _credentials()
    header, _ = sign_request(
        method,
        url,
        consumer_key=consumer_key,
        consumer_secret=consumer_secret,
        token=token,
        token_secret=token_secret,
        callback=callback,
        verifier=verifier,
    )
    with httpx.Client(timeout=30.0) as c:
        return c.request(
            method,
            url,
            headers={"Authorization": header, "Accept": accept, "User-Agent": "mypocket/0.1"},
        )


def _parse_oauth_response(body: str) -> dict[str, str]:
    parsed = parse_qs(body)
    return {k: v[0] for k, v in parsed.items()}


def get_request_token(environment: str | None = None) -> tuple[str, str]:
    """Step 1: get a request token + secret (oob callback)."""
    url = f"{_base_url(environment)}/oauth/request_token"
    r = _signed_request("GET", url, callback="oob", accept="*/*")
    if r.status_code != 200:
        raise ETradeError(f"request_token failed {r.status_code}: {r.text[:300]}")
    data = _parse_oauth_response(r.text)
    token, secret = data.get("oauth_token"), data.get("oauth_token_secret")
    if not token or not secret:
        raise ETradeError(f"request_token bad payload: {r.text[:300]}")
    return token, secret


def authorize_url(request_token: str) -> str:
    """Step 2: URL the user visits to approve and get a verifier code."""
    consumer_key, _ = _credentials()
    return f"{AUTHORIZE_URL}?key={consumer_key}&token={request_token}"


def get_access_token(
    request_token: str,
    request_token_secret: str,
    verifier: str,
    environment: str | None = None,
) -> tuple[str, str]:
    """Step 3: exchange request token + verifier → access token + secret."""
    url = f"{_base_url(environment)}/oauth/access_token"
    r = _signed_request(
        "GET",
        url,
        token=request_token,
        token_secret=request_token_secret,
        verifier=verifier.strip(),
        accept="*/*",
    )
    if r.status_code != 200:
        raise ETradeError(f"access_token failed {r.status_code}: {r.text[:300]}")
    data = _parse_oauth_response(r.text)
    token, secret = data.get("oauth_token"), data.get("oauth_token_secret")
    if not token or not secret:
        raise ETradeError(f"access_token bad payload: {r.text[:300]}")
    return token, secret


def renew_access_token(token: str, token_secret: str, environment: str | None = None) -> None:
    """Extend an idle access token by 2h. Call before sync to be safe."""
    url = f"{_base_url(environment)}/oauth/renew_access_token"
    r = _signed_request("GET", url, token=token, token_secret=token_secret, accept="*/*")
    if r.status_code == 401:
        raise ETradeNeedsReauth("renew_access_token: token revoked or expired (midnight ET cutoff)")
    if r.status_code != 200:
        raise ETradeError(f"renew_access_token failed {r.status_code}: {r.text[:200]}")


def _api(
    path: str,
    *,
    token: str,
    token_secret: str,
    environment: str | None = None,
    params: dict[str, Any] | None = None,
) -> dict:
    base = _base_url(environment)
    qs = ""
    if params:
        pairs = []
        for k, v in params.items():
            if v is None:
                continue
            pairs.append(f"{k}={v}")
        if pairs:
            qs = "?" + "&".join(pairs)
    url = f"{base}{path}{qs}"
    r = _signed_request("GET", url, token=token, token_secret=token_secret, accept="application/json")
    if r.status_code == 401:
        raise ETradeNeedsReauth(f"401 from {path}")
    if r.status_code >= 400:
        raise ETradeError(f"{path} -> {r.status_code}: {r.text[:300]}")
    ct = r.headers.get("content-type", "")
    if ct.startswith("application/json"):
        return r.json()
    # Some endpoints fall back to XML
    return {"_xml": r.text}


def list_accounts(token: str, token_secret: str, environment: str | None = None) -> list[dict]:
    data = _api("/v1/accounts/list.json", token=token, token_secret=token_secret, environment=environment)
    accounts = (((data or {}).get("AccountListResponse") or {}).get("Accounts") or {}).get("Account") or []
    if isinstance(accounts, dict):
        accounts = [accounts]
    return accounts


def get_balance(
    account_id_key: str,
    inst_type: str,
    token: str,
    token_secret: str,
    environment: str | None = None,
) -> dict:
    return _api(
        f"/v1/accounts/{account_id_key}/balance.json",
        token=token,
        token_secret=token_secret,
        environment=environment,
        params={"instType": inst_type, "realTimeNAV": "true"},
    )


def get_portfolio(
    account_id_key: str,
    token: str,
    token_secret: str,
    environment: str | None = None,
    count: int = 250,
) -> dict:
    return _api(
        f"/v1/accounts/{account_id_key}/portfolio.json",
        token=token,
        token_secret=token_secret,
        environment=environment,
        params={"count": count, "view": "COMPLETE"},
    )


def get_transactions(
    account_id_key: str,
    token: str,
    token_secret: str,
    environment: str | None = None,
    count: int = 50,
    start_date: str | None = None,  # MM/DD/YYYY
    end_date: str | None = None,
) -> dict:
    return _api(
        f"/v1/accounts/{account_id_key}/transactions.json",
        token=token,
        token_secret=token_secret,
        environment=environment,
        params={"count": count, "startDate": start_date, "endDate": end_date},
    )
