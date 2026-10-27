"""Source-only, fictional browser states. Never run against production data."""

from datetime import UTC, date, datetime, timedelta
from functools import lru_cache

from fastapi import Request
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from mypocket.domain.models import Account, Holding
from tests.demo_preview import app, get_session, session


@lru_cache(maxsize=8)
def state_engine(case):
    assert case in {"one", "none", "partial", "zero", "stale", "populated"}
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    today = date.today()
    with Session(engine) as db:
        a = Account(
            id=1,
            name="Fictional preview account",
            institution="Example broker",
            source="demo",
            type="brokerage",
            balance=1000,
        )
        db.add(a)
        if case != "none":
            ages = [0] if case == "one" else [0, 1, 7, 30, 90]
            for age in ages:
                delta = age + (10 if case == "stale" else 0)
                value = 0 if case == "zero" else 1000 - age * 2
                db.add(
                    Holding(
                        account_id=1,
                        symbol="EXAMPLE",
                        quantity=0 if case == "zero" else 10,
                        market_value=value,
                        cost_basis=None if case == "partial" else 0 if case == "zero" else 800,
                        day_change=None if case == "partial" else 0 if case == "zero" else 5,
                        day_change_pct=None if case == "partial" else 0 if case == "zero" else 0.5,
                        as_of=today - timedelta(days=delta),
                        created_at=datetime.now(UTC),
                    )
                )
        db.commit()
    return engine


def case_session(request: Request):
    case = request.query_params.get("fixture")
    if case in {"one", "none", "partial", "zero", "stale", "populated"}:
        with Session(state_engine(case)) as db:
            yield db
    else:
        yield from session()


app.dependency_overrides[get_session] = case_session
