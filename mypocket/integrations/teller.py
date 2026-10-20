"""Teller API client.

Auth model:
  - mTLS: client certificate + private key (per-application, never per-user)
  - HTTP Basic auth: username = access_token, password = "" (per-enrollment)

Docs: https://teller.io/docs
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx

from mypocket.core.config import ROOT_DIR, settings
from mypocket.integrations._errors import ProviderError, transport_error

BASE_URL = "https://api.teller.io"


class TellerError(ProviderError):
    pass


class TellerNeedsReauth(TellerError):
    """The enrollment must be re-authenticated via Teller Connect."""


def _resolve_path(p: str | None) -> Path | None:
    if not p:
        return None
    pp = Path(p)
    if not pp.is_absolute():
        pp = ROOT_DIR / pp
    return pp


def _client_cert() -> tuple[str, str]:
    cert = _resolve_path(settings.teller_cert_path)
    key = _resolve_path(settings.teller_key_path)
    if not cert or not cert.exists():
        raise TellerError(f"Teller cert not found at {cert}")
    if not key or not key.exists():
        raise TellerError(f"Teller key not found at {key}")
    return str(cert), str(key)


def _make_client(access_token: str | None = None) -> httpx.Client:
    cert_pair = _client_cert()
    auth = (access_token, "") if access_token else None
    return httpx.Client(
        base_url=BASE_URL,
        cert=cert_pair,
        auth=auth,
        timeout=30.0,
        headers={"User-Agent": "mypocket/0.1"},
    )


def _request(client: httpx.Client, method: str, path: str, **kw: Any) -> Any:
    try:
        r = client.request(method, path, **kw)
    except httpx.RequestError as e:
        raise TellerError(transport_error("Teller", e)) from None
    code = ""
    if r.is_error:
        try:
            payload = r.json()
            code = (payload.get("error") or {}).get("code", "")
        except (ValueError, AttributeError, TypeError):
            pass
        if not isinstance(code, str):
            code = ""
    if r.status_code == 404 and code.startswith("enrollment.disconnected"):
        raise TellerNeedsReauth("Teller enrollment disconnected. Use Reconnect on the Connect page.")
    if r.status_code == 401:
        raise TellerError("Teller access token is missing. Check the saved connection.")
    if r.status_code == 403:
        raise TellerNeedsReauth(
            "Teller access was rejected. Check that the client certificate belongs to this "
            "application, then reconnect if the token was revoked."
        )
    if r.is_error:
        raise TellerError(f"Teller request failed (HTTP {r.status_code}). Try again later.")
    if r.headers.get("content-type", "").startswith("application/json"):
        try:
            return r.json()
        except ValueError:
            pass
    raise TellerError("Teller returned an unexpected response. Try again later.")


def list_accounts(access_token: str) -> list[dict]:
    with _make_client(access_token) as c:
        return _request(c, "GET", "/accounts")


def get_account(access_token: str, account_id: str) -> dict:
    with _make_client(access_token) as c:
        return _request(c, "GET", f"/accounts/{account_id}")


def get_balances(access_token: str, account_id: str) -> dict:
    with _make_client(access_token) as c:
        return _request(c, "GET", f"/accounts/{account_id}/balances")


def list_transactions(
    access_token: str, account_id: str, count: int | None = None, from_id: str | None = None
) -> list[dict]:
    params: dict[str, Any] = {}
    if count is not None:
        params["count"] = count
    if from_id is not None:
        params["from_id"] = from_id
    with _make_client(access_token) as c:
        return _request(c, "GET", f"/accounts/{account_id}/transactions", params=params or None)


def get_identity(access_token: str) -> dict:
    with _make_client(access_token) as c:
        return _request(c, "GET", "/identity")
