"""Authenticated-mode browser fixture. Both modes use fictional memory-only data.

Run only in a source-only copy, on localhost, with no .env or real credentials.
The headless test supplies the synthetic-session cookie, never a real session.
"""

import os
from pathlib import Path

assert not (Path(__file__).parents[1] / ".env").exists()
os.environ["DATABASE_URL"] = "sqlite://"

import httpx  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.responses import HTMLResponse, RedirectResponse, Response  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

from mypocket.demo import DemoMiddleware  # noqa: E402
from mypocket.routes import planning, planning_accounts  # noqa: E402
from mypocket.security import session as auth_session  # noqa: E402
from mypocket.security.headers import SecurityHeadersMiddleware  # noqa: E402
from mypocket.security.middleware import AuthMiddleware  # noqa: E402
from tests.chart_preview import ROOT, api, forbidden, get_session, pages, preview_lock, session  # noqa: E402

httpx.Client = forbidden
httpx.AsyncClient = forbidden
auth_session.verify = lambda token: token == "synthetic-session"


class FixtureAuth(AuthMiddleware):
    def _setup_complete(self):
        return True


app = FastAPI()


@app.get("/login")
def login():
    return HTMLResponse("<h1>Fictional login fixture</h1><p>No passcode requested or accepted.</p>")


@app.post("/logout")
def logout():
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(auth_session.SESSION_COOKIE_NAME)
    return response


@app.post("/api/sync")
def sync():
    return {"teller": [], "plaid": [], "etrade": []}


app.include_router(pages.router)
app.include_router(planning.router)
app.include_router(planning_accounts.router)
app.include_router(api.router, prefix="/api")
app.dependency_overrides[get_session] = session
app.mount("/static", StaticFiles(directory=ROOT / "mypocket/static"))
app.add_middleware(DemoMiddleware)
app.add_middleware(FixtureAuth)
app.add_middleware(SecurityHeadersMiddleware)


@app.middleware("http")
async def forbid_other_writes(request, call_next):
    if request.method not in {"GET", "HEAD"} and request.url.path not in {
        "/demo/enter",
        "/demo/exit",
        "/api/sync",
        "/logout",
    }:
        return Response(status_code=403)
    # Both normal-mode fixture tabs share one in-memory SQLite connection.
    # Serialize the full response lifetime, preserving duplicate Set-Cookie fields.
    async with preview_lock:
        response = await call_next(request)
        body = b"".join([part async for part in response.body_iterator])
        result = Response(body, status_code=response.status_code)
        result.raw_headers = response.raw_headers
        return result
