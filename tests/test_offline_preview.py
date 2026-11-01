"""Fictional fixture and cache boundaries, without browsers or live storage."""

import ast
import json
from datetime import UTC, datetime
from html.parser import HTMLParser

from fastapi.testclient import TestClient

from tests.offline_preview import ASSETS, DEMO, ROOT, SCOPE, create_app, fictional_snapshot


def test_fixture_contains_only_minimal_fictional_snapshot():
    snapshot = fictional_snapshot(datetime(2026, 11, 1, 12, tzinfo=UTC))
    assert set(snapshot) == {"version", "fictional", "owner_scope", "captured_at", "currency", "accounts", "sources"}
    assert snapshot["fictional"] is True and snapshot["owner_scope"] == SCOPE
    assert snapshot["captured_at"] == "2026-11-01T12:00:00Z"
    assert all(set(row) == {"label", "type", "balance"} for row in snapshot["accounts"])
    assert all(set(row) == {"label", "last_successful_check_at", "provider_as_of"} for row in snapshot["sources"])
    assert all(row["provider_as_of"] is None for row in snapshot["sources"])
    assert snapshot["sources"][0]["last_successful_check_at"] == "2026-11-01T11:33:00Z"
    assert snapshot["sources"][1]["last_successful_check_at"] == "2026-11-01T08:00:00Z"


def test_fixture_is_self_contained_and_does_not_mount_real_routes():
    with TestClient(create_app()) as client:
        for name, (_, mime) in ASSETS.items():
            response = client.get("/offline-demo/" + name)
            assert response.status_code == 200
            assert response.headers["content-type"].startswith(mime)
            assert response.headers["cache-control"] == "no-store"
            assert response.headers["x-content-type-options"] == "nosniff"
            assert "'unsafe-inline'" not in response.headers["content-security-policy"]
            assert "'unsafe-eval'" not in response.headers["content-security-policy"]
        for path in ["/", "/login", "/logout", "/api/sync", "/offline-demo/.env", "/offline-demo/static/../missing"]:
            assert client.get(path).status_code == 404
        assert client.post("/offline-demo/snapshot").status_code == 405
        assert client.get("/offline-demo/sw.js").headers["service-worker-allowed"] == "/offline-demo/"


def test_sensitive_responses_are_uncached_and_revoked_access_denies_snapshot():
    with TestClient(create_app()) as client:
        for path in ["access", "snapshot"]:
            response = client.get("/offline-demo/" + path)
            assert response.headers["cache-control"] == "no-store"
            assert response.json()["fictional"] is True
            assert not response.cookies
    with TestClient(create_app(revoked=True)) as client:
        assert client.get("/offline-demo/access").json()["active"] is False
        assert client.get("/offline-demo/snapshot").status_code == 403


def test_shell_has_no_snapshot_values_and_manifest_stays_scoped():
    shell = (DEMO / "index.html").read_text()
    assert 'id="snapshot" hidden' in shell
    assert '<script type="module" src="/offline-demo/app.mjs">' in shell
    assert "4800" not in shell and "Everyday checking" not in shell
    assert "Last synced" not in shell  # There is no invented global/provider freshness timestamp.
    manifest = json.loads((DEMO / "manifest.webmanifest").read_text())
    assert manifest["start_url"] == manifest["scope"] == "/offline-demo/"
    assert not manifest.get("share_target")


def test_fixture_imports_no_app_database_credentials_or_provider_code():
    tree = ast.parse((ROOT / "tests/offline_preview.py").read_text())
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(item.name for item in node.names)
        elif isinstance(node, ast.ImportFrom):
            imports.append(node.module)
    assert all(not module.startswith("mypocket") for module in imports)
    assert not {"sqlite3", "sqlmodel", "keyring", "httpx", "requests"}.intersection(imports)
    # The connected application has no route, registration or export into this prototype.
    assert "offline-demo" not in (ROOT / "mypocket/main.py").read_text()


def test_static_review_artifact_has_no_active_network_or_storage_code():
    from scripts.preview_offline import render_preview

    class Tags(HTMLParser):
        def __init__(self):
            super().__init__()
            self.tags = []

        def handle_starttag(self, tag, attrs):
            self.tags.append((tag, dict(attrs)))

    html = render_preview()
    parsed = Tags()
    parsed.feed(html)
    assert not {tag for tag, _ in parsed.tags}.intersection({"script", "link", "form", "iframe"})
    for tag, attrs in parsed.tags:
        if tag in {"button", "input"}:
            assert "disabled" in attrs
        for name in {"href", "src", "action"}.intersection(attrs):
            assert attrs[name].startswith("data:")
    assert "$85,900" in html and "2026-11-01T11:33:00Z" in html
    assert "Provider data as of: unknown" in html
    assert "url(/static/" not in html
