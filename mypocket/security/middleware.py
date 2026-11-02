"""HTTP middleware enforcing app-wide authentication."""

from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlmodel import Session, select
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from mypocket.core.config import settings
from mypocket.core.db import engine
from mypocket.domain.models import AppConfig
from mypocket.security import session as session_mod

PUBLIC_PATHS = frozenset(
    {
        "/login",
        "/setup",
        "/favicon.ico",
        "/manifest.webmanifest",
        "/robots.txt",
        "/healthz",
    }
)
PUBLIC_PREFIXES: tuple[str, ...] = ("/static/", "/icons/")


class AuthMiddleware(BaseHTTPMiddleware):
    """Redirect HTML requests to /login (or /setup) when no valid session is present.

    JSON requests get a 401 instead so the JS in the templates can react cleanly.
    """

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)
        self._setup_cache: bool | None = None

    def _setup_complete(self) -> bool:
        if self._setup_cache is True:
            return True
        with Session(engine) as session:
            row = session.exec(select(AppConfig).where(AppConfig.id == 1)).first()
        self._setup_cache = row is not None
        return self._setup_cache

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        request.state.saved_view_enabled = settings.saved_view_enabled
        if settings.saved_view_enabled:
            from mypocket.saved_view.policy import SavedViewOptions
            from mypocket.saved_view.shell import PUBLIC_PATHS as SAVED_PATHS

            options = SavedViewOptions(enabled=True, origin=settings.saved_view_origin)
            if f"{request.url.scheme}://{request.url.netloc}" != options.origin:
                return JSONResponse({"detail": "HTTPS origin required"}, status_code=400)
            if path in SAVED_PATHS:
                return await call_next(request)
        if path in PUBLIC_PATHS or path.startswith(PUBLIC_PREFIXES):
            return await call_next(request)

        if not self._setup_complete():
            return RedirectResponse("/setup", status_code=303)

        token = request.cookies.get(session_mod.SESSION_COOKIE_NAME)
        if session_mod.verify(token):
            return await call_next(request)

        accept = request.headers.get("accept", "")
        if "application/json" in accept or path.startswith("/api/"):
            return JSONResponse({"detail": "authentication required"}, status_code=401)
        return RedirectResponse(f"/login?next={path}", status_code=303)

    def invalidate_setup_cache(self) -> None:
        self._setup_cache = None
