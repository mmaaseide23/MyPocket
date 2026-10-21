"""Source-only fictional UI preview. Refuses a configured app directory."""

# Environment must be forced before application imports.
# ruff: noqa: E402
import os
from datetime import UTC, datetime
from pathlib import Path

assert not (Path(__file__).parents[1] / ".env").exists()
os.environ["DATABASE_URL"] = "sqlite://"
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from mypocket.core.db import get_session
from mypocket.domain.models import Account
from mypocket.routes import planning, planning_accounts

engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
SQLModel.metadata.create_all(engine)
with Session(engine) as session:
    for kind, value in [("brokerage", 6000), ("ira", 4000)]:
        session.add(
            Account(
                name="Fictional account",
                institution="Example",
                source="etrade_api",
                type=kind,
                balance=value,
                last_synced=datetime.now(UTC),
            )
        )
    session.commit()


def get_synthetic_session():
    with Session(engine) as session:
        yield session


app = FastAPI()


@app.get("/planning")
def fictional_planner(request: Request):
    response = planning.planning_page(request)
    body = response.body.replace(
        b"<h1>Financial planning</h1>",
        b'<h1>Financial planning</h1><p class="plan-help">Fictional preview - no linked accounts</p>',
    )
    return HTMLResponse(body, headers={"Cache-Control": "no-store"})


app.include_router(planning.router)
app.include_router(planning_accounts.router)
app.dependency_overrides[get_session] = get_synthetic_session
app.mount("/static", StaticFiles(directory=Path(__file__).parents[1] / "mypocket/static"), name="static")
