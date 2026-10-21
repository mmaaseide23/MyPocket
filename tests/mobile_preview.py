"""Isolated fictional release QA, with a simulated Sync button response.

No main lifespan, real configuration, provider calls, credentials or disk DB.
"""

import os
import re
from pathlib import Path

# ruff: noqa: E402
assert not (Path(__file__).parents[1] / ".env").exists()
os.environ["DATABASE_URL"] = "sqlite://"
from datetime import UTC, datetime

import httpx
from fastapi import FastAPI
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from sqlmodel import Session, select

from mypocket.domain.models import Account
from mypocket.routes import planning, planning_accounts, teller
from tests.chart_preview import ROOT, api, engine, forbidden, get_session, pages, preview_lock, session

httpx.Client = forbidden
httpx.AsyncClient = forbidden
with Session(engine) as fixture:
    for account in fixture.exec(select(Account)).all():
        if account.type == "brokerage":
            account.source = "etrade_api"
            account.last_synced = datetime.now(UTC)
            fixture.add(account)
    fixture.commit()

app = FastAPI()
sync_calls = 0


@app.post("/api/sync")
def simulated_sync():
    global sync_calls
    sync_calls += 1
    return {
        "teller": [],
        "etrade": [{"accounts": 1, "errors": []}],
        "plaid": [
            {"accounts": 1, "errors": [] if sync_calls % 2 else ["Fictional connection needs review."]}
        ],
    }


app.include_router(pages.router)
app.include_router(planning.router)
app.include_router(planning_accounts.router)
app.include_router(teller.router)
app.include_router(api.router, prefix="/api")
app.dependency_overrides[get_session] = session
app.mount("/static", StaticFiles(directory=ROOT / "mypocket/static"))


@app.middleware("http")
async def isolate(request, call_next):
    if request.method != "GET" and (request.method != "POST" or request.url.path != "/api/sync"):
        return Response(status_code=405)
    async with preview_lock:
        response = await call_next(request)
        body = b"".join([chunk async for chunk in response.body_iterator])
        headers = dict(response.headers)
        if headers.get("content-type", "").startswith("text/html"):
            body = re.sub(
                rb"(<main\b[^>]*>)",
                rb'\1<p style="padding:.5rem;border:1px solid #b58900;color:#b58900;font-size:13px">Fictional preview - no linked accounts or provider requests</p>',
                body,
                count=1,
            )
            headers.pop("content-length", None)
        return Response(body, status_code=response.status_code, headers=headers)
