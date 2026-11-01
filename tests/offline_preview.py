"""Isolated fictional preview. Does not import the live app, DB or settings.

Standalone fictional preview on loopback:
  python -m uvicorn tests.offline_preview:app --host 127.0.0.1 --port 8765
Never point a production app or a real provider connection at this fixture.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import Response

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "mypocket/offline_demo"
STATIC = ROOT / "mypocket/static"
SCOPE = "fictional-owner-v1"
ASSETS = {
    "": (DEMO / "index.html", "text/html"),
    "app.mjs": (DEMO / "app.mjs", "text/javascript"),
    "store.mjs": (DEMO / "store.mjs", "text/javascript"),
    "session.mjs": (DEMO / "session.mjs", "text/javascript"),
    "sw.js": (DEMO / "sw.js", "text/javascript"),
    "offline.css": (DEMO / "offline.css", "text/css"),
    "styles.css": (STATIC / "styles.css", "text/css"),
    "sky-daylight.jpg": (STATIC / "images/sky-daylight.jpg", "image/jpeg"),
    "icon.svg": (STATIC / "icon.svg", "image/svg+xml"),
    "manifest.webmanifest": (DEMO / "manifest.webmanifest", "application/manifest+json"),
}


def fictional_snapshot(now: datetime | None = None) -> dict:
    now = now or datetime.now(UTC)

    def stamp(value: datetime) -> str:
        return value.isoformat(timespec="seconds").replace("+00:00", "Z")

    return {
        "version": 1,
        "fictional": True,
        "owner_scope": SCOPE,
        "captured_at": stamp(now),
        "currency": "USD",
        "accounts": [
            {"label": "Everyday checking", "type": "checking", "balance": 4800},
            {"label": "Rainy-day savings", "type": "savings", "balance": 12000},
            {"label": "Investment account", "type": "brokerage", "balance": 54000},
            {"label": "Retirement account", "type": "ira", "balance": 16000},
            {"label": "Everyday card", "type": "credit", "balance": 900},
        ],
        "sources": [
            {"label": "Demo bank", "last_successful_check_at": stamp(now - timedelta(minutes=27)), "provider_as_of": None},
            {"label": "Demo investments", "last_successful_check_at": stamp(now - timedelta(hours=4)), "provider_as_of": None},
        ],
    }


def create_app(*, revoked: bool = False) -> FastAPI:
    preview = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @preview.middleware("http")
    async def isolated_headers(request, call_next):
        response = await call_next(request)
        response.headers.update({
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer",
            "Content-Security-Policy": "default-src 'none'; script-src 'self'; style-src 'self'; "
            "img-src 'self'; connect-src 'self'; worker-src 'self'; manifest-src 'self'; "
            "base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
        })
        return response

    @preview.get("/offline-demo/access")
    def access():
        return {"fictional": True, "active": not revoked, "owner_scope": SCOPE}

    @preview.get("/offline-demo/snapshot")
    def snapshot():
        if revoked:
            return Response(status_code=403)
        return fictional_snapshot()

    @preview.get("/offline-demo/{asset:path}")
    def asset_file(asset: str):
        if asset not in ASSETS:
            return Response(status_code=404)
        path, mime = ASSETS[asset]
        payload = path.read_bytes()
        if asset == "styles.css":
            payload = payload.replace(b"/static/images/sky-daylight.jpg", b"/offline-demo/sky-daylight.jpg")
        headers = {"Service-Worker-Allowed": "/offline-demo/"} if asset == "sw.js" else None
        return Response(payload, media_type=mime, headers=headers)

    return preview


app = create_app()
