"""Demo isolation against poisoned real-data/provider dependencies, synthetic only."""

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def demo_client(monkeypatch):
    from mypocket.core import db, templating
    from mypocket.integrations import etrade_sync, plaid_sync, teller_sync
    from mypocket.main import app
    from mypocket.security import middleware, session

    def forbidden(*args, **kwargs):
        raise AssertionError("A demo request reached the real DB or a provider")

    monkeypatch.setattr(db, "Session", forbidden)
    monkeypatch.setattr(templating, "Session", forbidden)
    monkeypatch.setattr(middleware.AuthMiddleware, "_setup_complete", lambda self: True)
    monkeypatch.setattr(session, "verify", lambda token: token == "synthetic-session")
    for provider in (etrade_sync, plaid_sync, teller_sync):
        monkeypatch.setattr(provider, "sync_all", forbidden)
    # Do not enter the application lifespan: it starts actual scheduled jobs.
    client = TestClient(app)
    client.cookies.set(session.SESSION_COOKIE_NAME, "synthetic-session")
    client.cookies.set("mp-demo", "1")
    yield client
    client.close()


@pytest.mark.parametrize(
    "path",
    [
        "/",
        "/banking",
        "/banking/1",
        "/banking/3",
        "/brokerage",
        "/brokerage/4",
        "/spending",
        "/transactions",
        "/planning",
        "/connect",
        "/plaid",
        "/api/summary",
        "/api/flows",
        "/api/categories",
        "/api/transactions",
        "/api/holdings",
        "/api/dividends",
        "/api/spending/trend",
        "/api/planning/accounts",
    ],
)
def test_demo_pages_and_apis_never_reach_real_dependencies(demo_client, path):
    response = demo_client.get(path, headers={"X-MyPocket-Request": "1"})
    assert response.status_code == 200
    assert response.headers["X-MyPocket-Mode"] == "demo"
    assert "no-store" in response.headers["Cache-Control"]
    assert "https://production.plaid.com" not in response.headers["Content-Security-Policy"]
    if "text/html" in response.headers["content-type"]:
        assert "Fictional demo" in response.text
        assert 'data-demo-mode="1"' in response.text
        assert "Exit demo" in response.text
        assert "cdn.plaid.com" not in response.text
        assert "cdn.teller.io" not in response.text


@pytest.mark.parametrize(
    "method,path",
    [
        ("post", "/api/plaid/link_token"),
        ("post", "/api/plaid/exchange"),
        ("post", "/api/plaid/reconnect_complete"),
        ("post", "/api/plaid/review"),
        ("post", "/api/plaid/preview"),
        ("post", "/api/plaid/approve"),
        ("post", "/api/plaid/sync"),
        ("post", "/api/teller/save_enrollment"),
        ("post", "/api/teller/sync"),
        ("delete", "/api/teller/enrollment/1"),
        ("post", "/api/etrade/start_oauth"),
        ("post", "/api/etrade/complete_oauth"),
        ("post", "/api/etrade/diagnose"),
        ("post", "/api/etrade/sync"),
        ("delete", "/api/accounts/1"),
        ("put", "/future-mutation"),
    ],
)
def test_demo_mutations_cannot_reach_database_or_providers(demo_client, method, path):
    assert getattr(demo_client, method)(path).status_code == 403


def test_demo_unknown_routes_do_not_fall_back_and_sync_is_simulated(demo_client):
    for path in [
        "/api/private-future-endpoint",
        "/api/etrade/diagnose",
        "/openapi.json",
        "/docs",
        "/banking/9999",
    ]:
        assert demo_client.get(path).status_code == 404
    before = demo_client.get("/api/summary").json()
    result = demo_client.post("/api/sync").json()
    assert result["demo"] is True
    assert demo_client.get("/api/summary").json() == before
    planned = demo_client.get("/api/planning/accounts", headers={"X-MyPocket-Request": "1"}).json()
    assert planned["starting_value"] == 70000
    assert planned["account_count"] == 2
    assert before["net_worth"] == 85900


def test_demo_cookie_does_not_grant_authentication(demo_client):
    from mypocket.security.session import SESSION_COOKIE_NAME

    demo_client.cookies.delete(SESSION_COOKIE_NAME)
    assert demo_client.get("/", follow_redirects=False).headers["location"].startswith("/login")
    assert demo_client.get("/api/summary").status_code == 401
    assert (
        demo_client.post(
            "/demo/enter", headers={"accept": "application/json", "X-MyPocket-Request": "1"}
        ).status_code
        == 401
    )


def test_mode_changes_are_same_origin_and_exit_requires_unlock(demo_client):
    from mypocket.security.session import SESSION_COOKIE_NAME

    assert demo_client.get("/demo/enter").status_code == 403
    assert demo_client.post("/demo/enter").status_code == 403
    assert (
        demo_client.post(
            "/demo/enter", headers={"X-MyPocket-Request": "1", "Origin": "https://example.com"}
        ).status_code
        == 403
    )
    response = demo_client.post("/demo/enter", headers={"X-MyPocket-Request": "1"})
    assert response.json() == {"next": "/"}
    assert response.headers["Clear-Site-Data"] == '"cache"'
    assert "SameSite=strict" in response.headers["set-cookie"]
    assert demo_client.get("/api/summary", headers={"X-MyPocket-Mode": "live"}).status_code == 409
    response = demo_client.post("/demo/exit", headers={"X-MyPocket-Request": "1"})
    assert response.json() == {"next": "/login"}
    assert any(
        SESSION_COOKIE_NAME in value and "Max-Age=0" in value
        for value in response.headers.get_list("set-cookie")
    )
    # Apply the deletion as a real browser would, including the fixture domain cookie.
    demo_client.cookies.clear()
    assert demo_client.get("/api/summary").status_code == 401


def test_stale_demo_request_without_mode_cookie_fails_closed(demo_client):
    demo_client.cookies.delete("mp-demo")
    response = demo_client.get("/api/summary", headers={"X-MyPocket-Mode": "demo"})
    assert response.status_code == 409
