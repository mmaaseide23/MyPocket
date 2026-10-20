"""Minimal, read-only Plaid API client. No paid upgrade, refresh or money-movement calls.

Trial uses production endpoints; the owner must confirm their Dashboard plan.
Balance data comes from /accounts/get (cached), not a billable realtime request.
"""

from __future__ import annotations

import hashlib

import httpx

from mypocket.core.config import settings
from mypocket.integrations._errors import ProviderError, transport_error


class PlaidError(ProviderError):
    pass


class PlaidNeedsReauth(PlaidError):
    pass


class PlaidPaginationChanged(PlaidError):
    pass


def configured() -> bool:
    return bool(settings.plaid_client_id and settings.plaid_secret)


def validate_configuration(environment: str | None = None) -> str:
    env = settings.plaid_environment
    if env not in {"sandbox", "production"}:
        raise PlaidError("PLAID_ENVIRONMENT must be sandbox or production.")
    if environment is not None and environment != env:
        raise PlaidError("Plaid connection environment differs from configured credentials.")
    if not configured():
        raise PlaidError("Add your own PLAID_CLIENT_ID and PLAID_SECRET to the local .env file.")
    if env == "production" and not settings.plaid_trial_confirmed:
        raise PlaidError("Confirm your Dashboard is on the free Trial plan before enabling production.")
    return env


def _client() -> httpx.Client:
    return httpx.Client(timeout=45.0, follow_redirects=False, trust_env=False)


def _post(path: str, payload: dict, environment: str | None = None) -> dict:
    env = validate_configuration(environment)
    try:
        with _client() as client:
            response = client.post(
                f"https://{env}.plaid.com{path}",
                json={**payload, "client_id": settings.plaid_client_id, "secret": settings.plaid_secret},
                headers={"Plaid-Version": "2020-09-14"},
            )
    except httpx.RequestError as exc:
        raise PlaidError(transport_error("Plaid", exc)) from None
    try:
        data = response.json()
    except ValueError:
        raise PlaidError("Plaid returned an unexpected response. Retry later.") from None
    if not isinstance(data, dict):
        raise PlaidError("Plaid returned an unexpected response. Retry later.")
    code = data.get("error_code")
    if code or response.status_code != 200:
        if code in {
            "ITEM_LOGIN_REQUIRED",
            "ITEM_ACCESS_NOT_GRANTED",
            "ACCESS_NOT_GRANTED",
            "INVALID_ACCESS_TOKEN",
        }:
            raise PlaidNeedsReauth("Plaid requires reconnection. Use Reconnect on the Connect page.")
        if code == "TRANSACTIONS_SYNC_MUTATION_DURING_PAGINATION":
            raise PlaidPaginationChanged("Plaid data changed during pagination. Retry the whole sync.")
        if code in {"INVALID_API_KEYS", "INVALID_CLIENT_ID", "INVALID_SECRET"}:
            raise PlaidError("Plaid rejected the API configuration. Check the key environment locally.")
        if code in {"PRODUCT_NOT_ENABLED", "PRODUCTS_NOT_SUPPORTED", "INSTITUTION_NOT_SUPPORTED"}:
            raise PlaidError(
                "This connection is unavailable with the selected products or plan. Do not upgrade to paid."
            )
        if code in {"ITEM_LIMIT_REACHED", "PRODUCTION_ACCESS_NOT_ENABLED"}:
            raise PlaidError(
                "Plaid Trial access or its Item limit blocks this request. Do not upgrade to paid."
            )
        if response.status_code == 429:
            raise PlaidError("Plaid rate limit reached. Wait before trying again.")
        raise PlaidError("Plaid could not complete the request. Retry later; no account data was changed.")
    return data


def _string(data: dict, name: str) -> str:
    value = data.get(name)
    if not isinstance(value, str) or not value or len(value) > 4096:
        raise PlaidError("Plaid response is missing required fields.")
    return value


def create_link_token(access_token: str | None = None, environment: str | None = None) -> str:
    validate_configuration(environment)
    body = {
        "client_name": "MyPocket",
        "language": "en",
        "country_codes": ["US"],
        "user": {
            "client_user_id": hashlib.sha256(
                f"mypocket-personal:{settings.plaid_client_id}".encode()
            ).hexdigest()
        },
    }
    if access_token:
        body["access_token"] = access_token
    else:
        body["products"] = ["transactions"]
        body["transactions"] = {"days_requested": 730}
        body["account_filters"] = {
            "depository": {"account_subtypes": ["checking", "savings"]},
            "credit": {"account_subtypes": ["credit card"]},
        }
    # Desktop popup OAuth needs no public callback server or redirect URI.
    return _string(_post("/link/token/create", body, environment), "link_token")


def exchange(public_token: str) -> tuple[str, str]:
    data = _post("/item/public_token/exchange", {"public_token": public_token})
    return _string(data, "access_token"), _string(data, "item_id")


def accounts(access_token: str, environment: str) -> dict:
    data = _post("/accounts/get", {"access_token": access_token}, environment)
    if not isinstance(data.get("accounts"), list) or not isinstance(data.get("item"), dict):
        raise PlaidError("Plaid returned incomplete account data.")
    return data


def institution_name(institution_id: str, environment: str) -> str:
    data = _post(
        "/institutions/get_by_id", {"institution_id": institution_id, "country_codes": ["US"]}, environment
    )
    return _string(data.get("institution") or {}, "name")


def sync_delta(access_token: str, environment: str, cursor: str = "") -> dict:
    # Fetch all pages before callers write data or advance their durable cursor.
    for _attempt in range(3):
        current = cursor
        combined = {"added": [], "modified": [], "removed": []}
        try:
            for _page in range(200):
                data = _post(
                    "/transactions/sync",
                    {"access_token": access_token, "cursor": current, "count": 500},
                    environment,
                )
                if (
                    not isinstance(data.get("has_more"), bool)
                    or not isinstance(data.get("next_cursor"), str)
                    or not isinstance(data.get("accounts"), list)
                ):
                    raise PlaidError("Plaid returned incomplete transaction data.")
                for name in ("added", "modified", "removed"):
                    if not isinstance(data.get(name), list):
                        raise PlaidError("Plaid returned incomplete transaction data.")
                    combined[name].extend(data[name])
                combined.update(
                    {
                        k: data.get(k)
                        for k in ("accounts", "next_cursor", "has_more", "transactions_update_status")
                    }
                )
                if not data["has_more"]:
                    return combined
                if data["next_cursor"] == current:
                    raise PlaidError("Plaid pagination did not advance. Data was retained.")
                current = data["next_cursor"]
            raise PlaidError("Plaid returned too many pages. Data was retained.")
        except PlaidPaginationChanged:
            continue
    raise PlaidError("Plaid data kept changing during pagination. Retry later; data was retained.")
