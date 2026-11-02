"""Entirely synthetic integration app. In-memory DB, blocked providers/Keychain.

Only tests import this module. It never imports the production app or scheduler.
"""

import socket
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

FIXTURE_COOKIE = "mypocket_fixture_saved_view"


def create_fixture(origin):
    if (Path(__file__).resolve().parents[1] / ".env").exists():
        raise RuntimeError("Run only in an isolated source checkout without a live environment file")

    # Check the isolation precondition BEFORE importing settings (which may load
    # an environment file), database modules or credential-bearing model types.
    from mypocket.core.config import settings
    from mypocket.domain.models import Account, AppConfig, Enrollment
    from mypocket.saved_view.api import create_router
    from mypocket.saved_view.ledger import observe_balance, track_sync
    from mypocket.saved_view.policy import SavedViewOptions
    from mypocket.saved_view.schema import initialize_schema
    from mypocket.security import crypto, keys
    from mypocket.security import session as sessions

    def forbidden(*args, **kwargs):
        raise AssertionError("Provider and Keychain access are forbidden in this fixture")

    socket.socket.connect = forbidden
    keys._get_or_create = forbidden
    crypto.master_encryption_key = lambda: b"x" * 32
    sessions.cookie_signing_key = lambda: b"synthetic-fixture-cookie-key-only"
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    from mypocket.core import db as core_db

    core_db.engine = engine
    settings.saved_view_enabled = True
    settings.saved_view_insights_enabled = True
    settings.saved_view_origin = origin
    SQLModel.metadata.create_all(engine)
    initialize_schema(engine)
    with Session(engine) as db:
        db.add(AppConfig(id=1, passcode_hash="synthetic-unused", created_at=datetime.now(UTC)))
        db.add_all([
            Account(id=1, source="teller", name="Fictional checking", institution="Fictional", type="checking", balance=4800),
            Account(id=2, source="teller", name="Fictional savings", institution="Fictional", type="savings", balance=12000),
            Account(id=3, source="teller", name="Fictional credit", institution="Fictional", type="credit", balance=900),
            Enrollment(id=1, provider="teller", access_token="fictional-not-a-provider-token", status="active"),
        ])
        db.commit()
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.include_router(create_router(options=SavedViewOptions(enabled=True, origin=origin, synthetic_loopback=True),
                                    session_factory=lambda: Session(engine), verify_session=sessions.verify,
                                    history_available=True, synthetic_label=True, session_cookie_name=FIXTURE_COOKIE))

    def completed(request):
        return (RedirectResponse("/saved-view/", status_code=303) if "text/html" in request.headers.get("accept", "")
                else JSONResponse({"fictional": True}))

    @app.post("/fixture/login")
    def login(request: Request):
        response = completed(request)
        # Cookies are shared across ports. This fixture must never replace the
        # real app's cookie when testing on a private HTTPS route on the same host.
        response.set_cookie(FIXTURE_COOKIE, sessions.issue(), httponly=True, samesite="strict",
                            secure=origin.startswith("https://"))
        return response

    @track_sync("teller")
    def fictional_check(db, enrollment):
        for account in db.exec(select(Account)).all():
            account.balance += 10
            db.add(account)
            observe_balance(account)
        db.commit()
        return {"errors": []}

    @app.post("/fixture/check")
    def check(request: Request):
        if not sessions.verify(request.cookies.get(FIXTURE_COOKIE)):
            return JSONResponse({"error": "denied"}, status_code=401)
        with Session(engine) as db:
            fictional_check(db, db.get(Enrollment, 1))
        return completed(request)

    @app.post("/fixture/reset")
    def reset():
        with Session(engine) as db:
            owner = db.get(AppConfig, 1)
            owner.created_at = datetime.now(UTC) + timedelta(seconds=1)
            db.add(owner)
            db.commit()
        return {"fictional": True}

    return app
