"""Authenticated API checks separated from pure projection/history tests."""

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import Session

from mypocket.saved_view.api import create_router
from mypocket.saved_view.policy import SavedViewOptions
from tests.test_saved_view import approve, saved  # noqa: F401 - imported pytest fixture


def test_api_auth_no_store_origin_and_revocation(saved):  # noqa: F811 - imported pytest fixture
    approve(saved)
    app = FastAPI()
    app.include_router(create_router(
        options=SavedViewOptions(enabled=True, origin="https://fixture.invalid"),
        session_factory=lambda: Session(saved.get_bind()), verify_session=lambda token: token == "synthetic-session",
    ))
    with TestClient(app, base_url="https://fixture.invalid") as client:
        assert client.get("/saved-view/snapshot").status_code == 401
        client.cookies.set("mypocket_session", "synthetic-session")
        response = client.get("/saved-view/snapshot")
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store, private"
        assert response.headers["vary"] == "Cookie"
        assert client.get("/saved-view/snapshot", headers={"Origin": "https://wrong.invalid"}).status_code == 401
        assert client.get("/saved-view/access", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 401
        assert client.get("/saved-view/access", headers={"Host": "wrong.invalid"}).status_code == 401
        assert client.get("http://fixture.invalid/saved-view/access").status_code == 401
        assert client.get("/saved-view/changes").status_code == 200
        assert client.post("/saved-view/revoke").status_code == 403
        assert client.post("/saved-view/revoke", headers={"Origin": "https://fixture.invalid"}).status_code == 200
        assert client.get("/saved-view/access").status_code == 403
        assert client.get("/saved-view/snapshot").status_code == 403



def test_disabled_api_does_not_touch_dependencies():
    def forbidden(*args):
        raise AssertionError("Disabled API accessed a dependency")

    app = FastAPI()
    app.include_router(create_router(session_factory=forbidden, verify_session=forbidden))
    with TestClient(app) as client:
        assert client.get("/saved-view/access").status_code == 404
        assert client.get("/saved-view/snapshot").status_code == 404
