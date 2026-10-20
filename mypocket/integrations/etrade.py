"""E*TRADE Developer API client (OAuth 1.0a).

Auth flow ("out-of-band" PIN, no callback URL):
  1. GET /oauth/request_token → request_token + request_token_secret
  2. User visits https://us.etrade.com/e/t/etws/authorize?key=KEY&token=REQUEST_TOKEN
     → user logs in, approves, sees a verifier code (5 alphanumeric chars)
  3. GET /oauth/access_token (with verifier) → access_token + access_token_secret
  4. Tokens valid until midnight ET, idle-expire after 2h; renew with /oauth/renew_access_token

Docs: https://developer.etrade.com
"""

from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs, urlencode

import httpx

from mypocket.core.config import settings
from mypocket.integrations._errors import ProviderError, transport_error
from mypocket.integrations._oauth1 import sign_request

SANDBOX_BASE = "https://apisb.etrade.com"
PROD_BASE = "https://api.etrade.com"
AUTHORIZE_URL = "https://us.etrade.com/e/t/etws/authorize"


class ETradeError(ProviderError):
    def __init__(self, message: str, *, status_code: int | None = None, provider_code: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.provider_code = provider_code


class ETradeNeedsReauth(ETradeError):
    """Tokens expired or revoked — user must redo the OAuth dance."""


def _base_url(env: str | None = None) -> str:
    aliases = {"prod": "production", "live": "production"}
    configured = (settings.etrade_environment or "sandbox").strip().lower()
    configured = aliases.get(configured, configured)
    selected = (env or configured).strip().lower()
    selected = aliases.get(selected, selected)
    if selected not in {"sandbox", "production"} or configured not in {"sandbox", "production"}:
        raise ETradeError("ETRADE_ENVIRONMENT must be sandbox or production.")
    if selected != configured:
        raise ETradeError("E*TRADE enrollment environment differs from the configured key environment.")
    return PROD_BASE if selected == "production" else SANDBOX_BASE


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

    headers = {"Authorization": header, "Accept": accept, "User-Agent": "mypocket/0.1"}
    try:
        with httpx.Client(timeout=30.0) as c:
            # OAuth parameters appear once, in the Authorization header.
            return c.request(method, url, headers=headers)
    except httpx.RequestError as e:
        raise ETradeError(transport_error("E*TRADE", e)) from None


def _check_response(response: httpx.Response, operation: str, *, access_token: bool = False) -> None:
    if response.status_code == 200:
        return
    problem = _parse_oauth_response(response.text).get("oauth_problem", "")
    if problem in {
        "signature_invalid",
        "consumer_key_unknown",
        "consumer_key_rejected",
        "timestamp_refused",
        "nonce_used",
        "parameter_absent",
        "parameter_rejected",
    }:
        raise ETradeError(
            "E*TRADE rejected OAuth configuration or signature. Check the consumer key environment "
            "and Mac's clock; reauthorizing alone may not fix this."
        )
    if response.status_code == 401 and access_token:
        raise ETradeNeedsReauth(
            "E*TRADE authorization expired or was rejected. Reconnect on the Connect page. "
            "Renewal cannot extend tokens past midnight US Eastern time."
        )
    raise _response_error(response, operation)


def _response_error(response: httpx.Response, operation: str) -> ETradeError:
    # Only documented error codes leave the client, never free-form provider messages.
    code = None
    try:
        payload = response.json()
        error = payload.get("Error", {}) if isinstance(payload, dict) else {}
        candidate = str(error.get("code", "")) if isinstance(error, dict) else ""
        if candidate in {
            "53",
            "100",
            "101",
            "102",
            "103",
            "300",
            "301",
            "404",
            "611",
            "612",
            "1001",
            "1002",
            "1003",
            "1004",
            "1005",
            "1006",
            "1007",
            "1008",
            "1009",
            "1010",
            "1011",
            "1012",
            "1013",
            "1014",
            "1015",
            "1100",
            "1101",
            "1102",
            "1103",
            "2001",
            "2002",
            "2003",
            "2004",
            "2005",
            "2006",
            "2007",
            "2008",
        }:
            code = candidate
    except ValueError:
        pass
    suffix = f"; provider code {code}" if code else ""
    return ETradeError(
        f"E*TRADE {operation} failed (HTTP {response.status_code}{suffix}).",
        status_code=response.status_code,
        provider_code=code,
    )


def _parse_oauth_response(body: str) -> dict[str, str]:
    parsed = parse_qs(body)
    return {k: v[0] for k, v in parsed.items()}


def get_request_token(environment: str | None = None) -> tuple[str, str]:
    """Step 1: get a request token + secret using the documented GET endpoint."""
    url = f"{_base_url(environment)}/oauth/request_token"
    r = _signed_request("GET", url, callback="oob", accept="*/*")
    _check_response(r, "request token")
    data = _parse_oauth_response(r.text)
    token, secret = data.get("oauth_token"), data.get("oauth_token_secret")
    if not token or not secret:
        raise ETradeError("E*TRADE request token response is missing required fields.")
    return token, secret


def authorize_url(request_token: str) -> str:
    """Step 2: URL the user visits to approve and get a verifier code."""
    consumer_key, _ = _credentials()
    return f"{AUTHORIZE_URL}?{urlencode({'key': consumer_key, 'token': request_token})}"


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
    _check_response(r, "access token")
    data = _parse_oauth_response(r.text)
    token, secret = data.get("oauth_token"), data.get("oauth_token_secret")
    if not token or not secret:
        raise ETradeError("E*TRADE access token response is missing required fields.")
    return token, secret


def renew_access_token(token: str, token_secret: str, environment: str | None = None) -> None:
    """Extend an idle access token by 2h. Call before sync to be safe."""
    url = f"{_base_url(environment)}/oauth/renew_access_token"
    r = _signed_request("GET", url, token=token, token_secret=token_secret, accept="*/*")
    _check_response(r, "renewal", access_token=True)


def _api(
    path: str,
    *,
    token: str,
    token_secret: str,
    environment: str | None = None,
    params: dict[str, Any] | None = None,
) -> dict:
    base = _base_url(environment)
    query = urlencode({k: v for k, v in (params or {}).items() if v is not None})
    qs = f"?{query}" if query else ""
    url = f"{base}{path}{qs}"
    r = _signed_request("GET", url, token=token, token_secret=token_secret, accept="application/json")
    endpoint = path.rsplit("/", 1)[-1]
    operation = {
        "balance.json": "balances",
        "portfolio.json": "portfolio",
        "transactions.json": "transactions",
        "list.json": "account list",
    }.get(endpoint, "data request")
    root = {
        "balance.json": "BalanceResponse",
        "portfolio.json": "PortfolioResponse",
        "transactions.json": "TransactionListResponse",
        "list.json": "AccountListResponse",
    }.get(endpoint)
    if r.status_code == 204 and endpoint in {"portfolio.json", "transactions.json"}:
        return {root: {}}
    _check_response(r, operation, access_token=True)
    ct = r.headers.get("content-type", "")
    if ct.startswith("application/json"):
        try:
            payload = r.json()
            if isinstance(payload, dict):
                if "Error" in payload:
                    raise _response_error(r, operation)
                if root and isinstance(payload.get(root), dict):
                    return payload
        except ValueError:
            pass
    raise ETradeError("E*TRADE returned an unexpected response. Try again later.")


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
    start_date: str | None = None,  # MMDDYYYY
    end_date: str | None = None,
) -> dict:
    # E*TRADE documents a maximum of 50; collect pages before returning so a
    # later-page failure cannot be mistaken for a complete transaction history.
    page_size = max(1, min(count, 50))
    marker = None
    seen = set()
    collected = []
    for _ in range(200):
        payload = _api(
            f"/v1/accounts/{account_id_key}/transactions.json",
            token=token,
            token_secret=token_secret,
            environment=environment,
            params={"count": page_size, "startDate": start_date, "endDate": end_date, "marker": marker},
        )
        response = payload["TransactionListResponse"]
        if not response and not collected:
            return payload
        rows = response.get("Transaction") or []
        if isinstance(rows, dict):
            rows = [rows]
        if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
            raise ETradeError("E*TRADE returned an unexpected transaction response.")
        collected.extend(rows)
        more = response.get("moreTransactions")
        if more in (False, "false") or (more not in (True, "true") and len(rows) < page_size):
            return {"TransactionListResponse": {"Transaction": collected}}
        next_marker = response.get("marker")
        if not isinstance(next_marker, (str, int)) or not str(next_marker) or str(next_marker) in seen:
            raise ETradeError("E*TRADE transaction pagination did not advance. Try again later.")
        marker = str(next_marker)
        seen.add(marker)
    raise ETradeError("E*TRADE transaction pagination exceeded the safety limit.")
