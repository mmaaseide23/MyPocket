"""Revocable single-owner sessions, used only in the opt-in saved-view mode."""

from uuid import uuid4

from sqlalchemy import delete, insert, select

from mypocket.saved_view.policy import utc
from mypocket.saved_view.schema import identities
from mypocket.saved_view.service import _owner_created_at, revoke


def session_scope(session, *, create=False):
    created = _owner_created_at(session)
    row = session.execute(select(identities).where(identities.c.id == 1)).mappings().first()
    if row and utc(row["app_created_at"]) == created:
        return row["session_epoch"]
    if not create:
        return None
    # A reset creates a new scope, invalidates prior approval and old cookies.
    revoke(session)
    session.execute(delete(identities))
    epoch = str(uuid4())
    session.execute(insert(identities).values(id=1, session_epoch=epoch, app_created_at=created))
    session.commit()
    return epoch


def sign_out_everywhere(session):
    revoke(session)
    session.execute(delete(identities))
    session.commit()


def current_scope(*, create=False):
    # Lazy import: importing the client/API or testing a disabled gate cannot
    # open the application database or reach the credential store.
    from sqlmodel import Session

    from mypocket.core.db import engine

    with Session(engine) as session:
        return session_scope(session, create=create)
