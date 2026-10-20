"""Documented E*TRADE data contracts, with synthetic responses only."""

import json

import httpx
import pytest

from mypocket.integrations import etrade
from tests.test_integrations import make_enrollment, transport


def test_transactions_limit_and_pagination(monkeypatch):
    calls = []

    def handler(request):
        calls.append(request)
        assert request.url.params["count"] == "50"
        if len(calls) == 1:
            return httpx.Response(
                200,
                json={
                    "TransactionListResponse": {
                        "Transaction": [{"transactionId": str(n)} for n in range(50)],
                        "marker": "synthetic+/marker",
                        "moreTransactions": True,
                        "next": "https://untrusted.invalid/do-not-follow",
                    }
                },
            )
        assert request.url.params["marker"] == "synthetic+/marker"
        assert request.url.host == "api.etrade.com"
        return httpx.Response(
            200,
            json={
                "TransactionListResponse": {
                    "Transaction": {"transactionId": "last"},
                    "moreTransactions": False,
                }
            },
        )

    transport(monkeypatch, handler)
    data = etrade.get_transactions("synthetic", "token", "secret", count=200)
    assert len(data["TransactionListResponse"]["Transaction"]) == 51
    assert len(calls) == 2


@pytest.mark.parametrize(
    "getter,root",
    [
        (etrade.get_transactions, "TransactionListResponse"),
        (etrade.get_portfolio, "PortfolioResponse"),
    ],
)
def test_documented_no_content_is_empty_success(monkeypatch, getter, root):
    transport(monkeypatch, lambda request: httpx.Response(204))
    assert getter("synthetic", "token", "secret") == {root: {}}


def test_no_content_balance_is_not_success(monkeypatch):
    transport(monkeypatch, lambda request: httpx.Response(204))
    with pytest.raises(etrade.ETradeError, match="balances"):
        etrade.get_balance("synthetic", "BROKERAGE", "token", "secret")


def test_safe_error_reports_category_status_and_documented_code(monkeypatch):
    transport(
        monkeypatch,
        lambda request: httpx.Response(
            500, json={"Error": {"code": 300, "message": "DO_NOT_EXPOSE account-and-token"}}
        ),
    )
    with pytest.raises(etrade.ETradeError) as error:
        etrade.get_transactions("private-account", "token", "secret")
    assert str(error.value) == "E*TRADE transactions failed (HTTP 500; provider code 300)."
    assert error.value.status_code == 500
    assert error.value.provider_code == "300"


def test_repeated_transaction_marker_fails_without_partial_success(monkeypatch):
    transport(
        monkeypatch,
        lambda request: httpx.Response(
            200,
            json={
                "TransactionListResponse": {
                    "Transaction": [{"transactionId": "synthetic"}],
                    "marker": "same",
                    "moreTransactions": True,
                }
            },
        ),
    )
    with pytest.raises(etrade.ETradeError, match="pagination"):
        etrade.get_transactions("synthetic", "token", "secret")


def test_error_payload_in_success_is_not_empty_data(monkeypatch):
    transport(
        monkeypatch,
        lambda request: httpx.Response(200, json={"Error": {"code": 611, "message": "DO_NOT_EXPOSE"}}),
    )
    with pytest.raises(etrade.ETradeError, match="611"):
        etrade.get_portfolio("synthetic", "token", "secret")


def test_read_only_diagnosis_does_not_renew_import_or_change_authorization(monkeypatch, db):
    from sqlmodel import select
    from starlette.requests import Request

    from mypocket.domain.models import Account, Enrollment, Transaction
    from mypocket.routes import etrade as routes

    enrollment = make_enrollment(db, "etrade")
    previous = enrollment.last_synced

    def forbidden(*args, **kwargs):
        raise AssertionError("Diagnosis must not renew or sync")

    monkeypatch.setattr(etrade, "renew_access_token", forbidden)
    monkeypatch.setattr(routes.etrade_sync, "sync_all", forbidden)
    monkeypatch.setattr(
        etrade,
        "list_accounts",
        lambda *a, **k: [{"accountIdKey": "private-account", "institutionType": "BROKERAGE"}],
    )
    calls = []

    def api(path, **kwargs):
        calls.append(path.rsplit("/", 1)[-1])
        if path.endswith("portfolio.json"):
            raise etrade.ETradeError(
                "E*TRADE portfolio failed (HTTP 500; provider code 611).",
                status_code=500,
                provider_code="611",
            )
        return {"ignored": "DO_NOT_EXPOSE"}

    monkeypatch.setattr(etrade, "_api", api)
    request = Request(
        {
            "type": "http",
            "scheme": "http",
            "server": ("testserver", 80),
            "path": "/",
            "headers": [(b"host", b"testserver"), (b"x-mypocket-diagnostic", b"1")],
        }
    )
    result = json.loads(routes.diagnose(request, db).body)
    assert calls == ["balance.json", "portfolio.json", "transactions.json"]
    assert result["checks"][2]["status"] == "error"
    assert result["checks"][2]["provider_code"] == "611"
    assert "private-account" not in str(result)
    assert "DO_NOT_EXPOSE" not in str(result)
    assert not db.exec(select(Account)).all()
    assert not db.exec(select(Transaction)).all()
    db.expire_all()
    saved = db.get(Enrollment, enrollment.id)
    assert saved.status == "active" and saved.last_synced == previous
    assert saved.access_token == "fake-token" and saved.last_error is None


@pytest.mark.parametrize(
    "headers",
    [
        [(b"host", b"testserver")],
        [
            (b"host", b"testserver"),
            (b"x-mypocket-diagnostic", b"1"),
            (b"origin", b"https://untrusted.invalid"),
        ],
    ],
)
def test_read_only_check_requires_same_origin_intent(headers, db):
    from fastapi import HTTPException
    from starlette.requests import Request

    from mypocket.routes import etrade as routes

    request = Request(
        {"type": "http", "scheme": "http", "server": ("testserver", 80), "path": "/", "headers": headers}
    )
    with pytest.raises(HTTPException) as error:
        routes.diagnose(request, db)
    assert error.value.status_code == 403


def test_unknown_provider_code_and_free_text_remain_private(monkeypatch):
    transport(
        monkeypatch,
        lambda request: httpx.Response(
            500, json={"Error": {"code": "DO_NOT_EXPOSE", "message": "DO_NOT_EXPOSE"}}
        ),
    )
    with pytest.raises(etrade.ETradeError) as error:
        etrade.get_balance("private-account", "BROKERAGE", "token", "secret")
    assert str(error.value) == "E*TRADE balances failed (HTTP 500)."
