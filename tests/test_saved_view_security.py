"""Opt-in integration and session security, with invented in-memory records."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlmodel import Session

from mypocket.core.config import settings
from mypocket.domain.models import Account, AppConfig
from mypocket.saved_view.api import create_router
from mypocket.saved_view.identity import sign_out_everywhere
from mypocket.saved_view.policy import SavedViewOptions
from mypocket.saved_view.schema import balances, consents, initialize_schema, runs
from mypocket.security import session as sessions

ORIGIN = "https://fixture.invalid"


@pytest.fixture
def owner(db, monkeypatch):
    from mypocket.core import db as core_db

    initialize_schema(db.get_bind())
    db.add(AppConfig(id=1, passcode_hash="synthetic-unused", created_at=datetime.now(UTC)))
    db.add(Account(id=1, source="teller", name="DO-NOT-EXPORT", type="checking", institution="Synthetic", balance=100))
    db.commit()
    monkeypatch.setattr(core_db, "engine", db.get_bind())
    monkeypatch.setattr(sessions, "cookie_signing_key", lambda: b"synthetic-only-session-key")
    monkeypatch.setattr(settings, "saved_view_enabled", True)
    monkeypatch.setattr(settings, "saved_view_origin", ORIGIN)
    return db


def application(owner, *, history=False):
    app = FastAPI()
    app.include_router(create_router(options=SavedViewOptions(enabled=True, origin=ORIGIN),
                                    session_factory=lambda: Session(owner.get_bind()), verify_session=sessions.verify,
                                    history_available=history))
    return app


def policy(**changes):
    return {"account_ids": [1], "field_set": "generic-balances-v1", "retention_seconds": 86400,
            "history_enabled": False, "confirm_fields": True, "confirm_phrase": True,
            "confirm_offline_limit": True, **changes}


def test_approval_ui_is_public_and_empty_but_data_requires_authentication(owner):
    with TestClient(application(owner), base_url=ORIGIN) as client:
        response = client.get("/saved-view/")
        assert response.status_code == 200
        assert "DO-NOT-EXPORT" not in response.text
        assert " checked" not in response.text
        assert '<option value="">Choose' in response.text
        assert "set-cookie" not in response.headers
        assert "unsafe-" not in response.headers["content-security-policy"]
        assert "default-src 'none'" in response.headers["content-security-policy"]
        assert client.get("/saved-view/snapshot").status_code == 401
        assert client.get("/saved-view/options").status_code == 401
        assert client.get("/saved-view/not-allowlisted").status_code == 404
        assert client.get("/saved-view/sw.js").headers["service-worker-allowed"] == "/saved-view/"


@pytest.mark.parametrize("change", [
    {"confirm_fields": False}, {"confirm_phrase": False}, {"confirm_offline_limit": False},
    {"account_ids": []}, {"retention_seconds": 0}, {"field_set": "include-everything"},
    {"history_enabled": True}, {"phrase": "MUST-NOT-ECHO"}, {"confirm_fields": 1},
])
def test_approval_requires_exact_explicit_policy_and_never_echoes_input(owner, change):
    token = sessions.issue()
    with TestClient(application(owner), base_url=ORIGIN) as client:
        client.cookies.set(sessions.SESSION_COOKIE_NAME, token)
        response = client.post("/saved-view/approve", json=policy(**change), headers={"Origin": ORIGIN})
        assert response.status_code in {400, 403}
        assert "MUST-NOT-ECHO" not in response.text
        assert owner.execute(select(consents)).first() is None


def test_explicit_approval_grants_export_without_history_by_default(owner):
    token = sessions.issue()
    with TestClient(application(owner), base_url=ORIGIN) as client:
        client.cookies.set(sessions.SESSION_COOKIE_NAME, token)
        assert client.post("/saved-view/approve", json=policy()).status_code == 403
        response = client.post("/saved-view/approve", json=policy(), headers={"Origin": ORIGIN})
        assert response.status_code == 200
        assert client.get("/saved-view/snapshot").status_code == 200
        assert owner.execute(select(consents.c.history_enabled)).scalar_one() is False
        assert owner.execute(select(balances)).first() is None
        assert owner.execute(select(runs)).first() is None


def test_session_epoch_rejects_legacy_reset_and_signed_out_cookies(owner, monkeypatch):
    monkeypatch.setattr(settings, "saved_view_enabled", False)
    legacy = sessions.issue()
    monkeypatch.setattr(settings, "saved_view_enabled", True)
    assert sessions.verify(legacy) is False
    token = sessions.issue()
    assert sessions.verify(token)
    app = owner.get(AppConfig, 1)
    app.created_at += timedelta(seconds=1)
    owner.add(app)
    owner.commit()
    assert sessions.verify(token) is False
    fresh = sessions.issue()
    assert sessions.verify(fresh)
    sign_out_everywhere(owner)
    assert sessions.verify(fresh) is False


def test_secure_cookie_flags_and_original_logout_cleanup(owner, monkeypatch):
    from mypocket.core.db import get_session
    from mypocket.routes import auth
    from mypocket.security import middleware
    from mypocket.security.headers import SecurityHeadersMiddleware

    response = RedirectResponse("/")
    auth._set_session_cookie(response)
    cookie = response.headers["set-cookie"]
    assert "Secure" in cookie and "HttpOnly" in cookie and "SameSite=lax" in cookie
    token = sessions.issue()
    monkeypatch.setattr(middleware, "engine", owner.get_bind())
    app = application(owner)
    app.include_router(auth.router)
    app.dependency_overrides[get_session] = lambda: owner
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(middleware.AuthMiddleware)
    with TestClient(app, base_url=ORIGIN) as client:
        client.cookies.set(sessions.SESSION_COOKIE_NAME, token)
        assert client.post("/logout", headers={"Origin": "https://wrong.invalid"}).status_code == 403
        assert sessions.verify(token)
        response = client.post("/logout", headers={"Origin": ORIGIN})
        assert response.url.path == "/saved-view/cleared"
        assert "Removing this device" in response.text
        assert "unsafe-" not in response.headers["content-security-policy"]
        assert sessions.verify(token) is False
        assert client.get("http://fixture.invalid/saved-view/").status_code == 400
        assert client.get("/saved-view/", headers={"Host": "wrong.invalid"}).status_code == 400


def test_full_disabled_mode_keeps_saved_view_absent():
    assert settings.saved_view_enabled is False
    assert settings.saved_view_origin is None
    assert create_router().routes == []
