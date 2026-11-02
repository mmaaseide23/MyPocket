"""Only invented accounts, in-memory databases, and mocked providers."""

import copy
import json
import shutil
import subprocess
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import event, inspect, select

from mypocket.core.config import settings
from mypocket.domain.models import Account, AppConfig, Enrollment
from mypocket.saved_view import ledger, service
from mypocket.saved_view.changes import compare, recorded_changes
from mypocket.saved_view.policy import FIELD_SET, SavedViewOptions, SavedViewUnavailable, minor_units
from mypocket.saved_view.schema import balances, initialize_schema, runs

T = datetime(2026, 1, 1, tzinfo=UTC)


@pytest.fixture
def saved(db):
    initialize_schema(db.get_bind())
    db.add(AppConfig(id=1, passcode_hash="synthetic-unused", created_at=T))
    db.add_all([
        Account(id=1, source="teller", name="DO-NOT-EXPORT-NAME", type="checking", institution="PRIVATE-BANK",
                external_id="DO-NOT-EXPORT-ID", balance=123.45),
        Account(id=2, source="teller", name="PRIVATE-CARD", type="credit", institution="PRIVATE-BANK", balance=20),
        Account(id=3, source="teller", name="NOT-SELECTED", type="savings", institution="PRIVATE-BANK", balance=500),
        Enrollment(id=1, provider="teller", access_token="SYNTHETIC-TOKEN-DO-NOT-EXPORT", status="active", last_error="PRIVATE-ERROR"),
    ])
    db.commit()
    return db


def approve(session, ids=(1, 2)):
    return service.record_approval(session, account_ids=list(ids), field_set=FIELD_SET, retention_seconds=86400, now=T)


def successful(session, *, amount=12345, start=T, finish=None, as_of=None):
    run_id = ledger.start_run(session, 1, "teller", now=start)
    ledger.finish_run(session, run_id, "success", [{
        "account_id": 1, "account_type": "checking", "currency": "USD", "balance_minor": amount,
        "observed_at": start + timedelta(seconds=1), "provider_as_of": as_of,
    }], now=finish or start + timedelta(seconds=2))
    return run_id


def test_feature_defaults_and_additive_schema(db):
    assert settings.saved_view_insights_enabled is False
    names = set(inspect(db.get_bind()).get_table_names())
    assert not any(name.startswith("saved_view_") for name in names)
    initialize_schema(db.get_bind())
    initialize_schema(db.get_bind())
    assert len(set(inspect(db.get_bind()).get_table_names()) - names) == 6
    assert db.execute(select(balances)).first() is None


def test_default_decorator_touches_no_storage():
    class Forbidden:
        def __getattr__(self, name):
            raise AssertionError("Disabled tracking touched storage")

    @ledger.track_sync("teller")
    def example(session, enrollment):
        ledger.observe_balance(Forbidden())
        return {"errors": []}

    assert example(Forbidden(), Forbidden()) == {"errors": []}


def test_completed_immutable_observations_and_failed_attempt(saved):
    approve(saved)
    successful(saved)
    attempt = ledger.start_run(saved, 1, "teller", now=T + timedelta(hours=1))
    ledger.finish_run(saved, attempt, "error", now=T + timedelta(hours=1, seconds=2))
    result = service.snapshot(saved, now=T + timedelta(hours=2))
    source = result["sources"][0]
    assert source["attempt_outcome"] == "error"
    assert source["last_attempt_at"] == service.stamp(T + timedelta(hours=1))
    assert source["last_successful_check_at"] == service.stamp(T + timedelta(seconds=2))
    assert source["provider_as_of"] is None
    assert result["accounts"][0]["observed_at"] == service.stamp(T + timedelta(seconds=1))
    with pytest.raises(ValueError, match="already finished"):
        ledger.finish_run(saved, attempt, "success", now=T + timedelta(hours=2))
    assert len(saved.execute(select(balances)).all()) == 1


@pytest.mark.parametrize("outcome", ["partial", "error"])
def test_incomplete_checks_do_not_claim_current_balance_freshness(saved, outcome):
    approve(saved)
    successful(saved)
    account = saved.get(Account, 1)
    account.balance = 140
    saved.add(account)
    saved.commit()
    attempt = ledger.start_run(saved, 1, "teller", now=T + timedelta(hours=1))
    ledger.finish_run(saved, attempt, outcome, [{"not": "an observation"}], now=T + timedelta(hours=1, seconds=2))
    result = service.snapshot(saved, now=T + timedelta(hours=2))
    assert result["accounts"][0]["balance_minor"] == 14000
    assert result["accounts"][0]["freshness"] == "unverified"
    assert result["accounts"][0]["observed_at"] is None
    assert result["sources"][0]["last_successful_check_at"] == service.stamp(T + timedelta(seconds=2))


def test_still_running_attempt_is_not_success(saved):
    approve(saved)
    successful(saved)
    ledger.start_run(saved, 1, "teller", now=T + timedelta(hours=1))
    result = service.snapshot(saved, now=T + timedelta(hours=2))
    assert result["sources"][0]["attempt_outcome"] == "running"
    assert result["sources"][0]["last_successful_check_at"] == service.stamp(T + timedelta(seconds=2))


def test_capture_does_not_update_sync_or_manufacture_history(saved):
    approve(saved)
    one = service.snapshot(saved, now=T)
    two = service.snapshot(saved, now=T + timedelta(days=1))
    assert one["captured_at"] != two["captured_at"]
    assert one["accounts"] == two["accounts"]
    assert all(a["freshness"] == "unrecorded" and a["observed_at"] is None for a in two["accounts"])
    assert two["sources"] == []
    assert saved.execute(select(runs)).first() is None


def test_projection_excludes_sensitive_columns_and_nonselected_account(saved):
    approve(saved, (1,))
    successful(saved)
    queries = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        queries.append(statement.lower())

    event.listen(saved.get_bind(), "before_cursor_execute", capture)
    try:
        payload = service.snapshot(saved, now=T + timedelta(hours=1))
    finally:
        event.remove(saved.get_bind(), "before_cursor_execute", capture)
    encoded = json.dumps(payload)
    assert len(payload["accounts"]) == 1
    assert payload["accounts"][0]["label"] == "Checking 1"
    for forbidden in ("DO-NOT-EXPORT", "PRIVATE-", "SYNTHETIC-TOKEN", "NOT-SELECTED", "external_id", "passcode_hash",
                      "access_token", "transaction", "holding", "institution", "last_error", "account.name"):
        assert forbidden not in encoded
        assert forbidden.lower() not in " ".join(queries)


def test_approval_revoke_reapprove_and_owner_reset(saved):
    with pytest.raises(SavedViewUnavailable, match="not_approved"):
        service.snapshot(saved, now=T)
    first = approve(saved)
    service.revoke(saved)
    with pytest.raises(SavedViewUnavailable, match="not_approved"):
        service.access_state(saved)
    second = approve(saved)
    assert second["generation"] > first["generation"]
    assert second["owner_scope"] != first["owner_scope"]
    owner = saved.get(AppConfig, 1)
    owner.created_at = T + timedelta(days=1)
    saved.add(owner)
    saved.commit()
    with pytest.raises(SavedViewUnavailable, match="owner_changed"):
        service.snapshot(saved, now=T + timedelta(days=1))


def test_selected_subset_preserves_the_label_from_the_approval_screen(saved):
    saved.add(Account(id=4, source="teller", name="DO-NOT-EXPORT-SECOND-NAME", type="checking",
                      institution="SYNTHETIC-BANK", balance=10))
    saved.commit()
    choice = next(row for row in service.approval_options(saved)["accounts"] if row["id"] == 4)
    assert choice["label"] == "Checking 2"
    approve(saved, (4,))
    result = service.snapshot(saved, now=T)
    assert len(result["accounts"]) == 1
    assert result["accounts"][0]["label"] == choice["label"]


@pytest.mark.parametrize("ids", [[], [1, 1], [999], [True], [1.0]])
def test_selection_is_explicit(saved, ids):
    with pytest.raises(SavedViewUnavailable, match="invalid_selection"):
        service.record_approval(saved, account_ids=ids, field_set=FIELD_SET, retention_seconds=86400)


@pytest.mark.parametrize("duration", [0, 3599, 604801, True])
def test_retention_bounds(saved, duration):
    with pytest.raises(ValueError, match="Retention"):
        service.record_approval(saved, account_ids=[1], field_set=FIELD_SET, retention_seconds=duration)


def test_currency_and_type_changes_fail_closed(saved):
    approve(saved)
    account = saved.get(Account, 1)
    account.currency = "EUR"
    saved.add(account)
    saved.commit()
    with pytest.raises(SavedViewUnavailable, match="selection_changed"):
        service.snapshot(saved, now=T)
    with pytest.raises(SavedViewUnavailable, match="unsupported_account"):
        approve(saved)


@pytest.mark.parametrize("value,expected", [(12.345, 1234), (12.355, 1236), (-0.01, -1), (None, None), (0, 0)])
def test_money_rounding_is_explicit(value, expected):
    assert minor_units(value) == expected


@pytest.mark.parametrize("value", [float("nan"), float("inf"), 1e20, "private invalid data"])
def test_invalid_money_safe_errors(value):
    with pytest.raises(SavedViewUnavailable, match="^invalid_balance$"):
        minor_units(value)


def test_change_breakdown_credit_unknown_and_scope(saved):
    approve(saved)
    old = service.snapshot(saved, now=T)
    new = copy.deepcopy(old)
    new["captured_at"] = service.stamp(T + timedelta(hours=1))
    new["accounts"][0]["balance_minor"] += 1000
    new["accounts"][1]["balance_minor"] -= 200
    result = compare(old, new)
    assert result["net_change_minor"] == 1200
    assert all(row["cause"] == "not_determined" for row in result["contributions"])
    new["accounts"][0]["balance_minor"] = None
    assert compare(old, new)["net_change_minor"] is None
    new["accounts"].pop(0)
    result = compare(old, new)
    assert "removed_from_view" in {row["kind"] for row in result["contributions"]}
    assert result["net_change_minor"] == -12345 + 200
    new["owner_scope"] = "different-owner"
    with pytest.raises(SavedViewUnavailable, match="incomparable"):
        compare(old, new)


def test_no_history_pruning_relabels_old_data_as_new(saved):
    approve(saved)
    successful(saved)
    attempt = ledger.start_run(saved, 1, "teller", now=T + timedelta(days=31))
    ledger.finish_run(saved, attempt, "error", now=T + timedelta(days=31, seconds=1))
    assert saved.execute(select(balances)).first() is None
    payload = service.snapshot(saved, now=T + timedelta(days=31, hours=1))
    assert payload["accounts"][0]["freshness"] == "unrecorded"


def test_recorded_change_history_has_explicit_windows_and_no_invented_causes(saved):
    approve(saved)
    successful(saved, amount=10000)
    successful(saved, amount=12345, start=T + timedelta(hours=1))
    result = recorded_changes(saved, now=T + timedelta(hours=2))
    checking, credit = result["accounts"]
    assert checking["status"] == "compared"
    assert checking["balance_change_minor"] == 2345
    assert checking["net_worth_contribution_minor"] == 2345
    assert checking["previous_observed_at"] == service.stamp(T + timedelta(seconds=1))
    assert checking["current_observed_at"] == service.stamp(T + timedelta(hours=1, seconds=1))
    assert checking["cause"] == "not_determined"
    assert credit["status"] == "insufficient_history"
    assert "net_change_minor" not in result


def test_python_export_to_javascript_encryption_contract(saved):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is required for the cross-language contract check")
    approve(saved)
    successful(saved)
    payload = service.snapshot(saved, now=T + timedelta(hours=1))
    script = """
      import {webcrypto} from 'node:crypto';
      import {sealSavedView, openSavedView} from './mypocket/saved_view/client.mjs';
      let input = ''; for await (const chunk of process.stdin) input += chunk;
      const value = JSON.parse(input), options = {now: Date.parse(value.captured_at), crypto: webcrypto};
      const record = await sealSavedView(value, 'synthetic contract test phrase', options);
      const result = await openSavedView(record, 'synthetic contract test phrase', options);
      if (JSON.stringify(value) !== JSON.stringify(result)) throw new Error('Contract mismatch');
      process.stdout.write('synthetic roundtrip passed');
    """
    result = subprocess.run([node, "--input-type=module", "-e", script], input=json.dumps(payload),
                            text=True, capture_output=True, check=True, timeout=30)
    assert result.stdout == "synthetic roundtrip passed"


def test_change_query_excludes_checks_completed_after_capture(saved, monkeypatch):
    from mypocket.saved_view import changes

    approve(saved)
    successful(saved, amount=10000)
    successful(saved, amount=12345, start=T + timedelta(hours=1))
    captured = service.snapshot(saved, now=T + timedelta(hours=1, seconds=10))
    successful(saved, amount=99999, start=T + timedelta(hours=2))
    monkeypatch.setattr(changes, "snapshot", lambda *a, **k: captured)
    result = changes.recorded_changes(saved)
    assert result["accounts"][0]["status"] == "compared"
    assert result["accounts"][0]["balance_change_minor"] == 2345


def test_tracking_success_partial_exception_and_context_cleanup(saved, monkeypatch):
    monkeypatch.setattr(settings, "saved_view_insights_enabled", True)
    service.record_approval(saved, account_ids=[1], field_set=FIELD_SET, retention_seconds=86400, history_enabled=True)
    enrollment = SimpleNamespace(id=1, provider="teller", status="active")
    account = SimpleNamespace(id=1, balance=10, type="checking", currency="USD")

    @ledger.track_sync("teller")
    def synthetic(session, enrollment, outcome):
        ledger.observe_balance(account)
        if outcome == "raise":
            raise RuntimeError("synthetic provider failure")
        return {"errors": ["synthetic"] if outcome == "partial" else []}

    synthetic(saved, enrollment, "success")
    synthetic(saved, enrollment, "partial")
    with pytest.raises(RuntimeError, match="synthetic provider failure"):
        synthetic(saved, enrollment, "raise")
    assert {row.outcome for row in saved.execute(select(runs)).all()} == {"success", "partial", "error"}
    assert len(saved.execute(select(balances)).all()) == 1
    assert ledger._observations.get() is None


def test_history_collection_requires_approval_and_only_records_selected_accounts(saved, monkeypatch):
    monkeypatch.setattr(settings, "saved_view_insights_enabled", True)
    enrollment = SimpleNamespace(id=1, provider="teller", status="active")

    @ledger.track_sync("teller")
    def synthetic(session, enrollment):
        for account_id in [1, 2, 3]:
            ledger.observe_balance(session.get(Account, account_id))
        return {"errors": []}

    synthetic(saved, enrollment)
    assert saved.execute(select(runs)).first() is None
    approve(saved)
    synthetic(saved, enrollment)
    assert saved.execute(select(runs)).first() is None
    service.record_approval(saved, account_ids=[1], field_set=FIELD_SET, retention_seconds=86400, history_enabled=True)
    synthetic(saved, enrollment)
    assert saved.execute(select(balances.c.account_id)).scalars().all() == [1]
    service.revoke(saved)
    synthetic(saved, enrollment)
    assert saved.execute(select(balances)).first() is None
    assert saved.execute(select(runs)).first() is None






@pytest.mark.parametrize("origin", [None, "http://fixture.invalid", "https://user:pass@fixture.invalid", "https://fixture.invalid/path", "https://fixture.invalid:99999"])
def test_enabled_api_requires_explicit_https_origin(origin):
    with pytest.raises(ValueError):
        SavedViewOptions(enabled=True, origin=origin)


@pytest.mark.parametrize("provider", ["teller", "etrade"])
def test_existing_adapters_record_only_received_balances(db, monkeypatch, provider):
    from mypocket.integrations import etrade, etrade_sync, teller, teller_sync

    initialize_schema(db.get_bind())
    monkeypatch.setattr(settings, "saved_view_insights_enabled", True)
    enrollment = Enrollment(provider=provider, access_token="synthetic", access_token_secret="synthetic",
                            status="active", environment="production" if provider == "etrade" else "development")
    db.add(enrollment)
    db.commit()
    if provider == "teller":
        monkeypatch.setattr(teller, "list_accounts", lambda *a, **k: [{"id": "synthetic", "type": "depository", "subtype": "checking"}])
        monkeypatch.setattr(teller, "get_balances", lambda *a, **k: {"available": "100"})
        monkeypatch.setattr(teller, "list_transactions", lambda *a, **k: [])
        adapter = teller_sync
    else:
        monkeypatch.setattr(etrade, "renew_access_token", lambda *a, **k: None)
        monkeypatch.setattr(etrade, "list_accounts", lambda *a, **k: [{"accountIdKey": "synthetic", "accountType": "BROKERAGE"}])
        monkeypatch.setattr(etrade, "get_balance", lambda *a, **k: {"BalanceResponse": {"Computed": {"RealTimeValues": {"totalAccountValue": 100}}}})
        monkeypatch.setattr(etrade, "get_portfolio", lambda *a, **k: {"PortfolioResponse": {}})
        monkeypatch.setattr(etrade, "get_transactions", lambda *a, **k: {"TransactionListResponse": {}})
        adapter = etrade_sync
    # First ordinary sync creates a selectable synthetic account without history.
    monkeypatch.setattr(settings, "saved_view_insights_enabled", False)
    assert not adapter.sync_enrollment(db, enrollment)["errors"]
    db.add(AppConfig(id=1, passcode_hash="synthetic-unused", created_at=T))
    db.commit()
    account_id = db.execute(select(Account.id)).scalar_one()
    service.record_approval(db, account_ids=[account_id], field_set=FIELD_SET, retention_seconds=86400, history_enabled=True)
    monkeypatch.setattr(settings, "saved_view_insights_enabled", True)
    assert not adapter.sync_enrollment(db, enrollment)["errors"]
    assert not adapter.sync_enrollment(db, enrollment)["errors"]
    points = db.execute(select(balances)).all()
    assert len(points) == 2
    assert all(point.balance_minor == 10000 and point.provider_as_of is None for point in points)
    assert points[0].account_id == points[1].account_id


def test_plaid_adapter_records_balance_only_after_completed_sync(db, plaid, monkeypatch):
    from mypocket.domain.models import PlaidAccount
    from mypocket.integrations import plaid_sync
    from tests.test_plaid import make_link, page, remote_account, tx

    initialize_schema(db.get_bind())
    monkeypatch.setattr(settings, "saved_view_insights_enabled", True)
    enrollment, state = make_link(db)
    account = Account(source="plaid", name="Synthetic", institution="Synthetic", type="checking", balance=50)
    db.add(account)
    db.commit()
    db.add(PlaidAccount(enrollment_id=enrollment.id, external_id="remote-checking", account_id=account.id))
    state.approved = True
    enrollment.status = "active"
    db.add(state)
    db.add(enrollment)
    db.commit()
    db.add(AppConfig(id=1, passcode_hash="synthetic-unused", created_at=T))
    db.commit()
    service.record_approval(db, account_ids=[account.id], field_set=FIELD_SET, retention_seconds=86400, history_enabled=True)
    monkeypatch.setattr(plaid, "accounts", lambda *a: {"accounts": [remote_account()], "item": {"item_id": "synthetic-item"}})
    monkeypatch.setattr(plaid, "sync_delta", lambda *a: page())
    assert not plaid_sync.sync_enrollment(db, enrollment).get("errors")
    # The existing depository adapter intentionally uses available=0, not
    # current=100. Recording must preserve that actual fetched value.
    assert db.execute(select(balances.c.balance_minor)).scalar_one() == 0
    monkeypatch.setattr(plaid, "sync_delta", lambda *a: page([tx("bad", amount=float("nan"))]))
    assert plaid_sync.sync_enrollment(db, enrollment)["errors"]
    assert len(db.execute(select(balances)).all()) == 1
