from datetime import UTC, datetime
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from sqlmodel import select

from mypocket.core.config import settings
from mypocket.domain.models import Account, Enrollment, Holding, Transaction
from mypocket.integrations import _oauth1, etrade, etrade_sync, teller, teller_sync


def transport(monkeypatch, handler):
    client = httpx.Client
    monkeypatch.setattr(
        httpx, "Client", lambda **kwargs: client(**kwargs, transport=httpx.MockTransport(handler))
    )


@pytest.mark.parametrize("exchange", [False, True])
def test_etrade_token_flow_uses_documented_get_with_header_only(monkeypatch, exchange):
    def handler(request):
        assert request.method == "GET"
        assert request.content == b""
        assert not request.url.query
        header = request.headers["Authorization"]
        assert 'oauth_verifier="ABCDE"' in header if exchange else 'oauth_callback="oob"' in header
        return httpx.Response(200, text="oauth_token=test-token&oauth_token_secret=test-secret")

    transport(monkeypatch, handler)
    result = (
        etrade.get_access_token("test-request", "test-secret", "ABCDE")
        if exchange
        else etrade.get_request_token()
    )
    assert result == ("test-token", "test-secret")


def test_authorize_url_round_trips_reserved_characters(monkeypatch):
    monkeypatch.setattr(settings, "etrade_consumer_key", "key+&=")
    query = parse_qs(urlsplit(etrade.authorize_url("token+/=&")).query)
    assert query == {"key": ["key+&="], "token": ["token+/=&"]}


def test_oauth_query_values_are_decoded_before_signing():
    assert _oauth1._split_query("https://example.test/?x=a%2Fb&x=c+d&empty=") == {
        "x": ["a/b", "c d"],
        "empty": [""],
    }


@pytest.mark.parametrize("environment", ["prodution", "sandbox"])
def test_etrade_rejects_invalid_or_mismatched_environment(environment):
    with pytest.raises(etrade.ETradeError):
        etrade._base_url(environment)


@pytest.mark.parametrize(
    "body",
    [
        "oauth_token=DO_NOT_EXPOSE",
        "oauth_token_secret=DO_NOT_EXPOSE",
    ],
)
def test_bad_oauth_response_does_not_expose_partial_credentials(monkeypatch, body):
    transport(monkeypatch, lambda request: httpx.Response(200, text=body))
    with pytest.raises(etrade.ETradeError) as error:
        etrade.get_request_token()
    assert "DO_NOT_EXPOSE" not in str(error.value)


def test_signature_failure_is_not_misreported_as_expired_authorization(monkeypatch):
    transport(
        monkeypatch,
        lambda request: httpx.Response(401, text="oauth_problem=signature_invalid&private=DO_NOT_EXPOSE"),
    )
    with pytest.raises(etrade.ETradeError) as error:
        etrade.renew_access_token("test-token", "test-secret")
    assert not isinstance(error.value, etrade.ETradeNeedsReauth)
    assert "signature" in str(error.value).lower()
    assert "DO_NOT_EXPOSE" not in str(error.value)


def test_expired_etrade_token_requires_user_reauthorization(monkeypatch):
    transport(monkeypatch, lambda request: httpx.Response(401, text="oauth_problem=token_expired"))
    with pytest.raises(etrade.ETradeNeedsReauth):
        etrade.renew_access_token("test-token", "test-secret")


def test_unexpected_etrade_response_is_not_an_empty_success(monkeypatch):
    transport(monkeypatch, lambda request: httpx.Response(200, text="<html>Unavailable</html>"))
    with pytest.raises(etrade.ETradeError):
        etrade.list_accounts("test-token", "test-secret")


@pytest.mark.parametrize(
    "status,code",
    [
        (404, "enrollment.disconnected.user_action.mfa_required"),
        (404, "enrollment.disconnected.credentials_invalid"),
        (403, "invalid_token"),
    ],
)
def test_teller_disconnection_requires_repair_without_leaking_response(status, code):
    with (
        httpx.Client(
            transport=httpx.MockTransport(
                lambda r: httpx.Response(status, json={"error": {"code": code, "message": "DO_NOT_EXPOSE"}})
            )
        ) as client,
        pytest.raises(teller.TellerNeedsReauth) as error,
    ):
        teller._request(client, "GET", "https://example.test/accounts/DO_NOT_EXPOSE")
    assert "DO_NOT_EXPOSE" not in str(error.value)


def test_teller_missing_auth_is_not_bank_reauthorization():
    with (
        httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(401))) as client,
        pytest.raises(teller.TellerError) as error,
    ):
        teller._request(client, "GET", "https://example.test/accounts")
    assert not isinstance(error.value, teller.TellerNeedsReauth)


def test_teller_certificate_error_is_actionable_and_private():
    def handler(request):
        raise httpx.ConnectError(
            "[SSL: CERTIFICATE_VERIFY_FAILED] certificate has expired DO_NOT_EXPOSE",
            request=request,
        )

    with (
        httpx.Client(transport=httpx.MockTransport(handler)) as client,
        pytest.raises(teller.TellerError) as error,
    ):
        teller._request(client, "GET", "https://example.test/accounts")
    assert "certificate" in str(error.value).lower()
    assert "DO_NOT_EXPOSE" not in str(error.value)
    assert not isinstance(error.value, teller.TellerNeedsReauth)


def make_enrollment(db, provider, status="active"):
    enrollment = Enrollment(
        provider=provider,
        access_token="fake-token",
        access_token_secret="fake-secret",
        status=status,
        environment="production" if provider == "etrade" else "development",
        last_synced=datetime(2026, 1, 1, tzinfo=UTC),
    )
    db.add(enrollment)
    db.commit()
    db.refresh(enrollment)
    return enrollment


@pytest.mark.parametrize(
    "provider,endpoint",
    [
        ("teller", "get_balances"),
        ("teller", "list_transactions"),
        ("etrade", "get_balance"),
        ("etrade", "get_portfolio"),
        ("etrade", "get_transactions"),
    ],
)
@pytest.mark.parametrize("reauth", [False, True])
def test_partial_sync_failure_preserves_freshness_and_reports_error(
    monkeypatch, db, caplog, provider, endpoint, reauth
):
    module = teller if provider == "teller" else etrade
    sync = teller_sync if provider == "teller" else etrade_sync
    enrollment = make_enrollment(db, provider)
    previous = enrollment.last_synced
    if provider == "teller":
        monkeypatch.setattr(module, "list_accounts", lambda *a, **k: [{"id": "private-account"}])
        monkeypatch.setattr(module, "get_balances", lambda *a, **k: {"available": "10"})
        monkeypatch.setattr(module, "list_transactions", lambda *a, **k: [])
        error_type = teller.TellerNeedsReauth
    else:
        monkeypatch.setattr(module, "renew_access_token", lambda *a, **k: None)
        monkeypatch.setattr(module, "list_accounts", lambda *a, **k: [{"accountIdKey": "private-account"}])
        monkeypatch.setattr(module, "get_balance", lambda *a, **k: {"BalanceResponse": {}})
        monkeypatch.setattr(module, "get_portfolio", lambda *a, **k: {"PortfolioResponse": {}})
        monkeypatch.setattr(module, "get_transactions", lambda *a, **k: {"TransactionListResponse": {}})
        error_type = etrade.ETradeNeedsReauth

    def fail(*args, **kwargs):
        if reauth:
            raise error_type("Reconnect required")
        raise httpx.ReadTimeout("DO_NOT_EXPOSE private-account")

    monkeypatch.setattr(module, endpoint, fail)
    result = sync.sync_enrollment(db, enrollment)
    assert result["errors"]
    assert enrollment.last_synced == previous
    if reauth:
        assert enrollment.status == "needs_reauth"
    assert "DO_NOT_EXPOSE" not in str(result) + str(enrollment.last_error) + caplog.text
    assert "private-account" not in str(result) + str(enrollment.last_error) + caplog.text
    account = db.exec(select(Account)).first()
    assert account is None or account.last_synced is None


def test_inactive_etrade_enrollment_reports_reconnect_instead_of_empty_success(db):
    make_enrollment(db, "etrade", "needs_reauth")
    results = etrade_sync.sync_all(db)
    assert len(results) == 1
    assert results[0]["errors"]


@pytest.mark.parametrize("provider", ["teller", "etrade"])
def test_successful_empty_sync_updates_timestamp(monkeypatch, db, provider):
    module, sync = (teller, teller_sync) if provider == "teller" else (etrade, etrade_sync)
    enrollment = make_enrollment(db, provider)
    previous = enrollment.last_synced
    monkeypatch.setattr(module, "list_accounts", lambda *a, **kw: [])
    if provider == "etrade":
        monkeypatch.setattr(module, "renew_access_token", lambda *a, **kw: None)
    result = sync.sync_enrollment(db, enrollment)
    assert not result["errors"]
    assert enrollment.last_synced != previous


@pytest.mark.parametrize("provider", ["teller", "etrade"])
def test_successful_account_sync_remains_idempotent(monkeypatch, db, provider):
    """Keep successful persistence and duplicate detection intact while fixing failures."""
    enrollment = make_enrollment(db, provider)
    if provider == "teller":
        monkeypatch.setattr(
            teller,
            "list_accounts",
            lambda *a, **kw: [
                {
                    "id": "synthetic-account",
                    "name": "Test account",
                    "type": "depository",
                    "subtype": "checking",
                    "currency": "USD",
                    "institution": {"name": "Test Bank"},
                }
            ],
        )
        monkeypatch.setattr(teller, "get_balances", lambda *a, **kw: {"available": "100"})
        monkeypatch.setattr(
            teller,
            "list_transactions",
            lambda *a, **kw: [
                {
                    "id": "synthetic-transaction",
                    "date": "2026-01-01",
                    "amount": "-5",
                    "description": "Test transaction",
                    "status": "posted",
                    "details": {},
                }
            ],
        )
        sync = teller_sync
    else:
        monkeypatch.setattr(etrade, "renew_access_token", lambda *a, **kw: None)
        monkeypatch.setattr(
            etrade,
            "list_accounts",
            lambda *a, **kw: [
                {
                    "accountIdKey": "synthetic-account",
                    "accountType": "BROKERAGE",
                    "accountDesc": "Test account",
                    "institutionType": "BROKERAGE",
                }
            ],
        )
        monkeypatch.setattr(
            etrade,
            "get_balance",
            lambda *a, **kw: {
                "BalanceResponse": {"Computed": {"RealTimeValues": {"totalAccountValue": 100}}}
            },
        )
        monkeypatch.setattr(
            etrade,
            "get_portfolio",
            lambda *a, **kw: {
                "PortfolioResponse": {
                    "AccountPortfolio": [
                        {
                            "Position": [
                                {
                                    "Product": {"symbol": "TEST", "securityType": "EQ"},
                                    "quantity": 1,
                                    "marketValue": 100,
                                    "totalCost": 100,
                                }
                            ]
                        }
                    ]
                }
            },
        )
        monkeypatch.setattr(
            etrade,
            "get_transactions",
            lambda *a, **kw: {
                "TransactionListResponse": {
                    "Transaction": [
                        {
                            "transactionId": "synthetic-transaction",
                            "transactionDate": 1767225600000,
                            "amount": -5,
                            "transactionType": "Fee",
                            "description": "Test transaction",
                        }
                    ]
                }
            },
        )
        sync = etrade_sync

    first = sync.sync_enrollment(db, enrollment)
    second = sync.sync_enrollment(db, enrollment)
    assert not first["errors"] and not second["errors"]
    assert first["transactions_created"] == 1
    assert second["transactions_created"] == 0
    assert second["transactions_skipped"] == 1
    account = db.exec(select(Account)).one()
    assert account.balance == 100
    assert account.last_synced is not None
    assert len(db.exec(select(Transaction)).all()) == 1
    if provider == "etrade":
        assert len(db.exec(select(Holding)).all()) == 1


@pytest.mark.parametrize(
    "operation,call",
    [
        ("balances", lambda: etrade.get_balance("DO_NOT_EXPOSE", "BROKERAGE", "token", "secret")),
        ("portfolio", lambda: etrade.get_portfolio("DO_NOT_EXPOSE", "token", "secret")),
        ("transactions", lambda: etrade.get_transactions("DO_NOT_EXPOSE", "token", "secret")),
    ],
)
def test_etrade_server_error_names_safe_operation_not_account(monkeypatch, operation, call):
    transport(monkeypatch, lambda r: httpx.Response(500, json={"Error": {"message": "DO_NOT_EXPOSE"}}))
    with pytest.raises(etrade.ETradeError) as err:
        call()
    assert operation in str(err.value) and "HTTP 500" in str(err.value)
    assert "DO_NOT_EXPOSE" not in str(err.value)
    assert not isinstance(err.value, etrade.ETradeNeedsReauth)
