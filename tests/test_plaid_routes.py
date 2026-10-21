"""User-visible HTTP boundaries; no real credentials or account access."""

import importlib

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import select

from mypocket.core.db import get_session
from mypocket.domain.models import Account, Enrollment
from tests.test_plaid import make_link


@pytest.fixture
def client(plaid, db, monkeypatch):
    routes = importlib.import_module("mypocket.routes.plaid")
    from mypocket.security.headers import SecurityHeadersMiddleware

    app = FastAPI()
    app.add_middleware(SecurityHeadersMiddleware)
    app.include_router(routes.router)
    app.dependency_overrides[get_session] = lambda: db
    with TestClient(app) as client:
        client.cookies.set("mypocket_session", "synthetic-session")
        client.headers.update({"X-MyPocket-Request": "1", "Origin": "http://testserver"})
        yield client


def test_link_creation_reuses_flow_and_never_initializes_extra_products(client, plaid, monkeypatch):
    calls = []
    monkeypatch.setattr(plaid, "create_link_token", lambda *a: calls.append(a) or "synthetic-link")
    first = client.post("/api/plaid/link_token", json={})
    second = client.post("/api/plaid/link_token", json={})
    assert first.status_code == 200 and second.json() == first.json()
    assert len(calls) == 1
    assert first.headers["cache-control"] == "no-store"


def test_exchange_stages_encrypted_link_without_importing_accounts(client, plaid, monkeypatch, db):
    monkeypatch.setattr(plaid, "create_link_token", lambda *a: "synthetic-link")
    monkeypatch.setattr(plaid, "exchange", lambda *a: ("synthetic-access", "synthetic-item"))
    flow = client.post("/api/plaid/link_token", json={}).json()
    body = {"flow_id": flow["flow_id"], "public_token": "synthetic-public"}
    response = client.post("/api/plaid/exchange", json=body)
    assert response.status_code == 200 and response.json()["status"] == "pending_review"
    assert "synthetic-access" not in response.text
    assert not db.exec(select(Account)).all()
    assert db.exec(select(Enrollment)).one().status == "pending_review"
    again = client.post("/api/plaid/exchange", json=body)
    assert again.json() == response.json()
    assert len(db.exec(select(Enrollment)).all()) == 1
    assert client.post("/api/plaid/link_token", json={}).status_code == 409


def test_cross_origin_and_missing_custom_header_rejected_before_requests(client, plaid, monkeypatch):
    monkeypatch.setattr(plaid, "create_link_token", lambda *a: pytest.fail("Unsafe request reached provider"))
    assert (
        client.post("/api/plaid/link_token", json={}, headers={"Origin": "https://evil.test"}).status_code
        == 403
    )
    del client.headers["X-MyPocket-Request"]
    assert client.post("/api/plaid/link_token", json={}).status_code == 403


def test_validation_does_not_echo_token_inputs(client):
    response = client.post(
        "/api/plaid/exchange", json={"flow_id": "DO_NOT_EXPOSE", "public_token": {"DO_NOT_EXPOSE": "value"}}
    )
    assert response.status_code == 422 and "DO_NOT_EXPOSE" not in response.text


def test_flow_bound_to_browser_session(client, plaid, monkeypatch):
    monkeypatch.setattr(plaid, "create_link_token", lambda *a: "synthetic-link")
    flow = client.post("/api/plaid/link_token", json={}).json()
    client.cookies.clear()
    client.cookies.set("mypocket_session", "different-session")
    response = client.post(
        "/api/plaid/exchange", json={"flow_id": flow["flow_id"], "public_token": "fake-public"}
    )
    assert response.status_code == 400


def test_update_flow_uses_existing_access_and_keeps_pending_review(client, plaid, monkeypatch, db):
    e, _ = make_link(db)
    calls = []
    monkeypatch.setattr(plaid, "create_link_token", lambda *a: calls.append(a) or "update-link")
    monkeypatch.setattr(plaid, "accounts", lambda *a: {"accounts": [], "item": {"item_id": "synthetic-item"}})
    flow = client.post("/api/plaid/link_token", json={"enrollment_id": e.id}).json()
    assert calls == [("synthetic-access", "sandbox")]
    response = client.post("/api/plaid/reconnect_complete", json={"flow_id": flow["flow_id"]})
    assert response.status_code == 200
    db.refresh(e)
    assert e.status == "pending_review"
    assert len(db.exec(select(Enrollment)).all()) == 1


def test_active_connection_exposes_add_and_review_without_second_item(client, plaid, db):
    e, state = make_link(db)
    state.approved = True
    e.status = "active"
    db.add(e)
    db.add(state)
    db.commit()
    response = client.get("/plaid")
    assert "Add Citi accounts" in response.text
    assert "Review accounts" in response.text
    assert "keep checking selected" in response.text.lower()
    assert "Open Plaid Link</button>" not in response.text
    assert client.post("/api/plaid/link_token", json={}).status_code == 409


def test_plaid_page_never_decrypts_tokens(client, plaid, monkeypatch, db):
    make_link(db)
    from mypocket.security import crypto

    monkeypatch.setattr(crypto, "decrypt", lambda *a: pytest.fail("Metadata decrypted"))
    response = client.get("/plaid")
    assert response.status_code == 200 and "Review accounts" in response.text
    assert "synthetic-access" not in response.text
    assert "https://cdn.plaid.com" in response.headers["content-security-policy"]


def test_auth_gate_protects_plaid_routes(monkeypatch, db):
    from mypocket.main import app
    from mypocket.security.middleware import AuthMiddleware

    monkeypatch.setattr(AuthMiddleware, "_setup_complete", lambda *a: True)
    client = TestClient(app)
    response = client.post("/api/plaid/link_token", json={})
    assert response.status_code == 401


def test_sandbox_cannot_apply_mapping_to_dashboard(client, db):
    e, _ = make_link(db)
    response = client.post(
        "/api/plaid/approve", json={"enrollment_id": e.id, "plan_id": "fake", "confirmed": True}
    )
    assert response.status_code == 400 and "Sandbox" in response.json()["detail"]
    assert not db.exec(select(Account)).all()


def test_generic_remove_cannot_break_plaid_ownership(db):
    from fastapi import HTTPException

    from mypocket.routes.teller import delete_enrollment

    e, _ = make_link(db)
    with pytest.raises(HTTPException) as error:
        delete_enrollment(e.id, db)
    assert error.value.status_code == 409
    assert db.get(Enrollment, e.id) is not None
