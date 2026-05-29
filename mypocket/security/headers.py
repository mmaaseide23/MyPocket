"""Response-header middleware adding Content-Security-Policy and friends.

CSP allows `'unsafe-inline'` for scripts + styles because the templates use
inline Tailwind config and Chart.js init, and `'unsafe-eval'` because Alpine.js
evaluates `x-data` / `x-show` / `@click` expressions via `new Function()`.
Without `unsafe-eval`, Alpine silently fails after removing `x-cloak`, which
leaves toggleable elements stuck visible. Restricting `script-src` to known
hosts is still a meaningful narrowing vs no policy.
"""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

CSP = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline' 'unsafe-eval' https://cdn.teller.io; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; "
    "connect-src 'self'; "
    "font-src 'self' data:; "
    "frame-src https://teller.io https://*.teller.io; "
    "object-src 'none'; "
    "base-uri 'self'; "
    "form-action 'self'"
)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request, call_next):
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = CSP
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        return response


class StaticCacheMiddleware(BaseHTTPMiddleware):
    """Long cache for vendor JS/CSS (which never change in place — re-download
    requires a deploy or `uv sync`). Shorter cache for icons/manifest. App
    HTML stays uncached so a passcode change or sync immediately shows up.
    """

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request, call_next):
        response = await call_next(request)
        path = request.url.path
        if path.startswith("/static/vendor/"):
            # Vendor scripts are pinned to a downloaded version; safe to cache hard.
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        elif path in ("/static/icon.svg", "/favicon.ico", "/manifest.webmanifest"):
            response.headers["Cache-Control"] = "public, max-age=86400"
        elif path.startswith("/static/"):
            response.headers["Cache-Control"] = "public, max-age=3600"
        return response
