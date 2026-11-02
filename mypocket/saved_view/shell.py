"""Exact public shell allowlist. No financial values or session cookies in assets."""

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import Response

ROOT = Path(__file__).resolve().parent
STATIC = ROOT.parent / "static"
CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; "
       "connect-src 'self'; worker-src 'self'; manifest-src 'self'; base-uri 'none'; "
       "form-action 'self'; frame-ancestors 'none'; object-src 'none'")
ASSETS = {
    "": (ROOT / "index.html", "text/html"),
    "app.mjs": (ROOT / "app.mjs", "text/javascript"),
    "client.mjs": (ROOT / "client.mjs", "text/javascript"),
    "storage.mjs": (ROOT / "storage.mjs", "text/javascript"),
    "sw.js": (ROOT / "sw.js", "text/javascript"),
    "clear.mjs": (ROOT / "clear.mjs", "text/javascript"),
    "cleared": (ROOT / "cleared.html", "text/html"),
    "manifest.webmanifest": (ROOT / "manifest.webmanifest", "application/manifest+json"),
    "saved.css": (ROOT / "saved.css", "text/css"),
    "offline.css": (ROOT.parent / "offline_demo/offline.css", "text/css"),
    "styles.css": (STATIC / "styles.css", "text/css"),
    "sky-daylight.jpg": (STATIC / "images/sky-daylight.jpg", "image/jpeg"),
    "icon.svg": (STATIC / "icon.svg", "image/svg+xml"),
}
PUBLIC_PATHS = frozenset("/saved-view/" + name for name in ASSETS)


def shell_router(*, synthetic_label=False):
    router = APIRouter(include_in_schema=False)

    def endpoint(name):
        def asset():
            path, mime = ASSETS[name]
            payload = path.read_bytes()
            if name == "styles.css":
                payload = payload.replace(b"/static/images/sky-daylight.jpg", b"/saved-view/sky-daylight.jpg")
            if name == "" and synthetic_label:
                payload = payload.replace(b"<!--fixture-notice-->", b'''
<section class="saved-notice"><p>Fictional test only. No real accounts or credentials.</p>
<div class="saved-actions">
<form method="post" action="/fixture/login"><button type="submit">Start fictional test</button></form>
<form method="post" action="/fixture/check"><button type="submit">Simulate a balance check</button></form>
</div><p class="saved-small">Start here while connected, choose an offline policy, then simulate two checks if you opted into history. Every value is invented. No bank connection is made.</p></section>''')
            headers = {"Cache-Control": "no-store", "Content-Security-Policy": CSP,
                       "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer"}
            if name == "sw.js":
                headers["Service-Worker-Allowed"] = "/saved-view/"
            return Response(payload, media_type=mime, headers=headers)
        return asset

    for name in ASSETS:
        router.add_api_route("/" + name, endpoint(name), methods=["GET"])
    return router
