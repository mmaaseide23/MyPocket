import shutil
import subprocess
from html.parser import HTMLParser

import pytest
from fastapi import HTTPException
from sqlmodel import select
from starlette.requests import Request

from mypocket.domain.models import Enrollment
from mypocket.routes import etrade as routes
from mypocket.routes import teller as teller_routes
from tests.test_integrations import make_enrollment


def test_etrade_reconnect_reuses_expired_enrollment(monkeypatch, db):
    enrollment = make_enrollment(db, "etrade", "needs_reauth")
    monkeypatch.setattr(routes.etrade, "get_request_token", lambda **kw: ("new-fake", "new-secret"))
    result = routes.start_oauth(routes.ETradeStartPayload(enrollment_id=enrollment.id), db)
    assert result["enrollment_id"] == enrollment.id
    assert enrollment.status == "pending_verifier"
    assert len(db.exec(select(Enrollment)).all()) == 1


def test_etrade_reconnect_does_not_overwrite_active_connection(db):
    enrollment = make_enrollment(db, "etrade")
    with pytest.raises(HTTPException) as error:
        routes.start_oauth(routes.ETradeStartPayload(enrollment_id=enrollment.id), db)
    assert error.value.status_code == 400
    assert enrollment.access_token == "fake-token"


def test_teller_repair_updates_existing_link_and_environment(db):
    enrollment = make_enrollment(db, "teller", "needs_reauth")
    enrollment.enrollment_id = "enr_fake"
    enrollment.environment = None
    db.commit()
    payload = teller_routes.TellerEnrollmentPayload(accessToken="new-fake", enrollment={"id": "enr_fake"})
    result = teller_routes.save_enrollment(payload, db)
    assert result["updated"]
    assert enrollment.status == "active"
    assert enrollment.environment == "development"
    assert len(db.exec(select(Enrollment)).all()) == 1


def test_teller_new_link_persists_environment(db):
    teller_routes.save_enrollment(
        teller_routes.TellerEnrollmentPayload(accessToken="new-fake", enrollmentId="enr_fake"), db
    )
    assert db.exec(select(Enrollment)).one().environment == "development"


class Buttons(HTMLParser):
    def __init__(self):
        super().__init__()
        self.buttons = []

    def handle_starttag(self, tag, attrs):
        if tag == "button":
            self.buttons.append(dict(attrs))


def test_connect_page_exposes_repair_for_teller_without_token(db):
    enrollment = make_enrollment(db, "teller", "needs_reauth")
    enrollment.enrollment_id = "enr_fake"
    db.commit()
    response = teller_routes.connect_page(Request({"type": "http"}), db)
    parser = Buttons()
    parser.feed(response.body.decode())
    assert any(button.get("data-enrollment-id") == "enr_fake" for button in parser.buttons)
    assert "fake-token" not in response.body.decode()


def test_connect_page_does_not_decrypt_account_credentials(monkeypatch, db):
    from mypocket.security import crypto

    make_enrollment(db, "teller", "needs_reauth")

    def forbidden(*args):
        raise AssertionError("Connection metadata must not decrypt credentials")

    monkeypatch.setattr(crypto, "decrypt", forbidden)
    response = teller_routes.connect_page(Request({"type": "http"}), db)
    assert response.status_code == 200


def test_sync_ui_rejects_partial_failures_and_explicit_event_is_used(db):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for the Connect JavaScript regression test")
    response = teller_routes.connect_page(Request({"type": "http"}), db)
    template = teller_routes.templates.get_template("connect.html")
    context = template.new_context(response.context)
    scripts = "".join(template.blocks["scripts"](context))
    import re

    javascript = "\n".join(re.findall(r"<script>(.*?)</script>", scripts, re.S))
    harness = r"""
const assert = require('node:assert/strict');
const elements = new Map();
const state = {step: 'start', verifier: 'ABCDE'};
let requestBody;
global.document = {
  getElementById(id) {
    if (!elements.has(id)) elements.set(id, {
      innerHTML: '', listeners: {}, dataset: {}, scrollIntoView() {},
      addEventListener(type, callback) { this.listeners[type] = callback; }
    });
    return elements.get(id);
  },
  querySelectorAll() { return []; }
};
global.window = {location: {reload() { throw Error('Unexpected reload'); }}};
global.TellerConnect = {setup() { return {open() {}}; }};
global.Alpine = {$data() {return state;}};
global.setTimeout = () => {throw Error('Failure must not schedule success reload');};
global.fetch = async (url, options) => {
  requestBody = options?.body;
  return {ok: true, json: async () => ({
    results: [{accounts: 1, errors: ['Reconnect required']}],
    sync: {accounts: 1, errors: ['Reconnect required']},
    authorize_url: 'https://example.test', enrollment_id: 7
  })};
};
"""
    assertions = r"""
(async () => {
  assert.throws(() => summarizeTellerSync({results: [{errors: ['Reconnect required']}]}), /Reconnect/);
  await document.getElementById('etradeSyncBtn').listeners.click();
  assert.match(document.getElementById('etradeSyncResult').innerHTML, /Reconnect required/);
  assert.doesNotMatch(document.getElementById('etradeSyncResult').innerHTML, /text-emerald/);
  const event = {currentTarget: {closest: () => document.getElementById('etradeConnectPanel')}};
  await startOauth(event, 7);
  assert.equal(JSON.parse(requestBody).enrollment_id, 7);
  await completeOauth(event);
  assert.equal(state.step, 'sync_error');
  assert.match(document.getElementById('etradeResult').innerHTML, /Reconnect required/);
})().catch(e => {console.error(e); process.exitCode = 1;});
"""
    result = subprocess.run([node], input=harness + javascript + assertions, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr


def test_etrade_restart_reuses_pending_and_records_expiry(monkeypatch, db):
    enrollment = make_enrollment(db, "etrade", "pending_verifier")
    monkeypatch.setattr(routes.etrade, "get_request_token", lambda **kw: ("new-fake", "new-secret"))
    result = routes.start_oauth(routes.ETradeStartPayload(), db)
    assert result["enrollment_id"] == enrollment.id
    assert result["expires_at"]
    assert enrollment.request_token_created_at is not None
    assert len(db.exec(select(Enrollment)).all()) == 1


def test_etrade_expired_request_is_rejected_before_exchange(monkeypatch, db):
    from datetime import UTC, datetime, timedelta

    enrollment = make_enrollment(db, "etrade", "pending_verifier")
    enrollment.request_token_created_at = datetime.now(UTC) - timedelta(minutes=6)
    db.commit()
    monkeypatch.setattr(routes.etrade, "get_access_token", lambda *a, **kw: pytest.fail("Expired token sent"))
    with pytest.raises(HTTPException) as error:
        routes.complete_oauth(routes.ETradeCompletePayload(enrollment_id=enrollment.id, verifier="ABCDE"), db)
    assert error.value.status_code == 400
    assert "expired" in error.value.detail.lower()


def test_etrade_does_not_create_another_grant_after_success(monkeypatch, db):
    make_enrollment(db, "etrade", "active")
    monkeypatch.setattr(
        routes.etrade, "get_request_token", lambda **kw: pytest.fail("Unnecessary reauthorization")
    )
    with pytest.raises(HTTPException) as err:
        routes.start_oauth(routes.ETradeStartPayload(), db)
    assert err.value.status_code == 409 and "Sync" in err.value.detail
