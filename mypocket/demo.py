"""Authenticated fictional app, isolated from the configured database/providers.

The middleware routes the entire request to a separate, read-only app. Unknown
routes fail closed rather than falling back to the real application.
"""

import asyncio
from datetime import UTC, date, datetime, timedelta
from functools import lru_cache

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine
from starlette.datastructures import MutableHeaders

from mypocket.core.db import get_session
from mypocket.core.templating import templates
from mypocket.domain.models import Account, Holding, Transaction
from mypocket.routes import api, pages, planning, planning_accounts
from mypocket.security.session import SESSION_COOKIE_NAME

DEMO_COOKIE = "mp-demo"
MODE_HEADER = "X-MyPocket-Mode"
STATIC_PATHS = {"/favicon.ico", "/manifest.webmanifest", "/healthz", "/robots.txt"}


@lru_cache(maxsize=1)
def fixture_engine(anchor: date):
    """Deterministic examples relative to today; never loaded from disk or a bank."""
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine, tables=[Account.__table__, Transaction.__table__, Holding.__table__])
    stamp = datetime.combine(anchor, datetime.min.time(), UTC)
    with Session(engine) as session:
        for ident, name, kind, balance in [
            (1, "Demo everyday checking", "checking", 4800),
            (2, "Demo rainy-day savings", "savings", 12000),
            (3, "Demo rewards card", "credit", 900),
            (4, "Demo investment account", "brokerage", 54000),
            (5, "Demo retirement account", "ira", 16000),
        ]:
            session.add(
                Account(
                    id=ident,
                    source="demo",
                    name=name,
                    type=kind,
                    institution="Fictional Bank" if ident < 4 else "Fictional Brokerage",
                    balance=balance,
                    last_synced=stamp,
                    created_at=stamp,
                )
            )
        # Posted purchases count as spending; the paired card payment is a transfer.
        for age in range(1096):
            day = anchor - timedelta(days=age)
            rows = []
            if day.day in (1, 15):
                rows.append((1, 2800, "Demo salary", "Income"))
            if day.day == 2:
                rows.append((1, -1250, "Demo apartment", "Housing"))
            if day.day == 5:
                rows += [
                    (1, -700, "Demo card payment", "Transfers"),
                    (3, 700, "Demo card payment", "Transfers"),
                ]
            if age % 7 == 0:
                rows.append((3, -82, "Demo market", "Groceries"))
            if age % 9 == 0:
                rows.append((3, -34, "Demo cafe", "Food & Dining"))
            if age % 13 == 0:
                rows.append((1, -45, "Demo transit", "Transport"))
            if day.day == 20:
                rows.append((4, 72, "Demo fund dividend", "Investment Income"))
            for account_id, amount, description, category in rows:
                session.add(
                    Transaction(
                        account_id=account_id,
                        tx_date=day,
                        amount=amount,
                        description=description,
                        category=category,
                        subcategory="Dividends" if account_id == 4 else None,
                        created_at=stamp,
                    )
                )
        session.add(
            Transaction(
                account_id=3,
                tx_date=anchor,
                amount=-29,
                description="Demo pending purchase",
                category="Food & Dining",
                excluded=True,
                created_at=stamp,
            )
        )
        for age in (0, 1, 7, 30):
            for account_id, symbol, name, value in [
                (4, "DEMO-EQ", "Fictional equity fund", 25000),
                (4, "DEMO-BD", "Fictional bond fund", 15000),
                (4, "DEMO-GL", "Fictional global fund", 10000),
                (5, "DEMO-RT", "Fictional retirement fund", 14000),
            ]:
                market = value * (1 - age * 0.0004)
                session.add(
                    Holding(
                        account_id=account_id,
                        symbol=symbol,
                        name=name,
                        quantity=100,
                        market_value=market,
                        cost_basis=value * 0.8,
                        last_price=market / 100,
                        total_gain=market - value * 0.8,
                        total_gain_pct=(market / (value * 0.8) - 1) * 100,
                        as_of=anchor - timedelta(days=age),
                        created_at=stamp,
                    )
                )
        session.commit()
    return engine


def fictional_session():
    with Session(fixture_engine(date.today())) as session:
        yield session


def create_demo_app():
    demo = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    demo.dependency_overrides[get_session] = fictional_session

    @demo.post("/api/sync")
    def simulated_sync():
        return {
            "demo": True,
            "teller": [],
            "etrade": [{"accounts": 2, "errors": []}],
            "plaid": [{"accounts": 3, "errors": []}],
        }

    @demo.get("/connect")
    @demo.get("/plaid")
    def connections(request: Request):
        return templates.TemplateResponse(
            request=request, name="demo_connections.html", context={"active": "connect"}
        )

    demo.include_router(pages.router)
    demo.include_router(planning.router)
    demo.include_router(planning_accounts.router)
    demo.include_router(api.router, prefix="/api")
    return demo


class DemoMiddleware:
    """Must sit inside AuthMiddleware; a mode cookie is never authentication."""

    def __init__(self, app):
        self.app = app
        self.demo = create_demo_app()
        self.lock = asyncio.Lock()

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        request = Request(scope)
        path = request.url.path
        if path.startswith(("/static/", "/icons/")) or path in STATIC_PATHS:
            return await self.app(scope, receive, send)
        enabled = request.cookies.get(DEMO_COOKIE) == "1"
        mode = "demo" if enabled else "live"
        scope.setdefault("state", {})["demo"] = enabled

        async def private_send(message):
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["Cache-Control"] = "no-store, private"
                headers["Pragma"] = "no-cache"
                headers["Vary"] = "Cookie"
                headers[MODE_HEADER] = mode
            await send(message)

        if path in {"/demo/enter", "/demo/exit"}:
            origin = request.headers.get("origin")
            if (
                request.method != "POST"
                or request.headers.get("X-MyPocket-Request") != "1"
                or request.headers.get("Sec-Fetch-Site") == "cross-site"
                or (origin and origin != str(request.base_url).rstrip("/"))
            ):
                return await JSONResponse({"detail": "same-origin POST required"}, status_code=403)(
                    scope, receive, private_send
                )
            entering = path.endswith("enter")
            response = JSONResponse({"next": "/" if entering else "/login"})
            response.headers["Clear-Site-Data"] = '"cache"'
            if entering:
                response.set_cookie(
                    DEMO_COOKIE,
                    "1",
                    max_age=60 * 60 * 24 * 30,
                    secure=request.url.scheme == "https",
                    samesite="strict",
                )
            else:
                response.delete_cookie(DEMO_COOKIE)
                # Showing the real app again is deliberate and requires its usual unlock.
                response.delete_cookie(SESSION_COOKIE_NAME)
            return await response(scope, receive, private_send)

        claimed = request.headers.get(MODE_HEADER)
        if claimed and claimed != mode:
            return await JSONResponse({"detail": "mode changed; reload required"}, status_code=409)(
                scope, receive, private_send
            )
        if not enabled or path in {"/login", "/logout"}:
            return await self.app(scope, receive, private_send)
        if path == "/setup":
            return await RedirectResponse("/login", status_code=303)(scope, receive, private_send)
        if request.method not in {"GET", "HEAD"} and not (path == "/api/sync" and request.method == "POST"):
            return await JSONResponse(
                {"detail": "Fictional demo: changes and connections are disabled."}, status_code=403
            )(scope, receive, private_send)
        # No fallback: even unknown/read-only provider URLs stay in the demo app.
        async with self.lock:
            await self.demo(scope, receive, private_send)
