"""Repeated sync and connection controls with historical authorizations."""

import shutil
import subprocess
from datetime import date
from pathlib import Path

from sqlmodel import select
from starlette.requests import Request

from mypocket.domain.models import Enrollment, Holding
from mypocket.integrations import etrade, etrade_sync
from mypocket.routes import etrade as routes
from mypocket.routes import teller as connect_routes
from tests.test_integrations import make_enrollment


def test_repeated_sync_ignores_expired_history_when_active_grant_exists(monkeypatch, db):
    old = make_enrollment(db, "etrade", "needs_reauth")
    active = make_enrollment(db, "etrade")
    calls = []
    monkeypatch.setattr(etrade, "renew_access_token", lambda *a, **k: calls.append("renew"))
    monkeypatch.setattr(etrade, "list_accounts", lambda *a, **k: [])
    for _ in range(2):
        result = routes.trigger_sync(db)
        assert len(result["results"]) == 1
        assert result["results"][0]["enrollment_id"] == active.id
        assert not result["results"][0]["errors"]
    assert len(calls) == 2
    assert db.get(Enrollment, old.id).status == "needs_reauth"
    assert len(db.exec(select(Enrollment)).all()) == 2


def test_only_expired_connection_still_reports_reauthorization(db):
    make_enrollment(db, "etrade", "needs_reauth")
    assert routes.trigger_sync(db)["results"][0]["errors"]


def test_connect_page_does_not_offer_reconnect_for_old_grant_with_active_replacement(db):
    old = make_enrollment(db, "etrade", "needs_reauth")
    make_enrollment(db, "etrade")
    response = connect_routes.connect_page(Request({"type": "http"}), db)
    html = response.body.decode()
    assert f"startOauth(event, {old.id})" not in html
    assert "Earlier authorization" in html
    assert "Current authorization is active" in html


def test_complete_view_price_is_imported_and_repeat_sync_remains_idempotent(monkeypatch, db):
    enrollment = make_enrollment(db, "etrade")
    monkeypatch.setattr(etrade, "renew_access_token", lambda *a, **k: None)
    monkeypatch.setattr(
        etrade,
        "list_accounts",
        lambda *a, **k: [{"accountIdKey": "synthetic-account", "accountType": "BROKERAGE"}],
    )
    monkeypatch.setattr(
        etrade,
        "get_balance",
        lambda *a, **k: {"BalanceResponse": {"Computed": {"RealTimeValues": {"totalAccountValue": 120}}}},
    )
    monkeypatch.setattr(
        etrade,
        "get_portfolio",
        lambda *a, **k: {
            "PortfolioResponse": {
                "AccountPortfolio": [
                    {
                        "Position": [
                            {
                                "Product": {"symbol": "TEST", "securityType": "EQ"},
                                "quantity": 2,
                                "totalCost": 100,
                                "marketValue": 120,
                                "Complete": {"lastTrade": 60},
                            }
                        ]
                    }
                ]
            }
        },
    )
    monkeypatch.setattr(etrade, "get_transactions", lambda *a, **k: {"TransactionListResponse": {}})
    for _ in range(2):
        result = etrade_sync.sync_enrollment(db, enrollment)
        assert not result["errors"]
    holdings = db.exec(select(Holding).where(Holding.as_of == date.today())).all()
    assert len(holdings) == 1
    assert holdings[0].last_price == 60


def test_global_sync_surfaces_provider_error_instead_of_reloading_as_success():
    template = (Path(__file__).parents[1] / "mypocket/templates/base.html").read_text()
    script = template.split("// ─── Sync buttons", 1)[1].split("</script>", 1)[0]
    script = "// Sync buttons" + script
    harness = r"""
const assert = require('node:assert/strict');
let handler;
let alerts = [];
const button = {disabled: false, querySelector() {return null;},
  addEventListener(_type, callback) {handler = callback;}};
global.document = {querySelectorAll() {return [button];}};
global.window = {location: {reload() {throw Error('Must not reload on a provider failure');}}};
global.alert = message => alerts.push(message);
global.fetch = async () => ({ok:true, json: async () => ({
  teller: [{errors:['TLS certificate verification failed.']}],
  etrade: [{accounts:2, errors:[]}], plaid:[]
})});
"""
    assertions = r"""
(async () => {
  await handler();
  assert.equal(alerts.length, 1);
  assert.match(alerts[0], /Teller: TLS certificate verification failed/);
  assert.equal(button.disabled, false);
})().catch(e => { console.error(e); process.exitCode = 1; });
"""
    result = subprocess.run(
        [shutil.which("node"), "-e", harness + script + assertions], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
