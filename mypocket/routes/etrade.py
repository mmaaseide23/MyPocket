from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlmodel import Session

from mypocket.core.config import settings
from mypocket.core.db import get_session
from mypocket.domain.models import Enrollment
from mypocket.integrations import etrade, etrade_sync

router = APIRouter(prefix="/api/etrade")


class ETradeCompletePayload(BaseModel):
    enrollment_id: int
    verifier: str = Field(..., min_length=1, max_length=64)


@router.post("/start_oauth")
def start_oauth(session: Session = Depends(get_session)):
    if not settings.etrade_consumer_key or not settings.etrade_consumer_secret:
        raise HTTPException(
            400, "E*TRADE not configured: set ETRADE_CONSUMER_KEY / ETRADE_CONSUMER_SECRET in .env"
        )
    env = settings.etrade_environment or "sandbox"
    try:
        request_token, request_token_secret = etrade.get_request_token(environment=env)
    except etrade.ETradeError as e:
        raise HTTPException(500, str(e)) from e

    pending = Enrollment(
        provider="etrade",
        access_token=request_token,
        access_token_secret=request_token_secret,
        status="pending_verifier",
        environment=env,
        institution="E*TRADE",
    )
    session.add(pending)
    session.commit()
    session.refresh(pending)

    return {
        "enrollment_id": pending.id,
        "authorize_url": etrade.authorize_url(request_token),
        "environment": env,
    }


@router.post("/complete_oauth")
def complete_oauth(
    payload: ETradeCompletePayload,
    session: Session = Depends(get_session),
):
    verifier = payload.verifier.strip()
    if not verifier:
        raise HTTPException(400, "Missing verifier")
    enrollment = session.get(Enrollment, payload.enrollment_id)
    if not enrollment or enrollment.provider != "etrade":
        raise HTTPException(404, "Pending E*TRADE enrollment not found")
    if enrollment.status != "pending_verifier":
        raise HTTPException(400, f"Enrollment already in state '{enrollment.status}'")

    try:
        access_token, access_token_secret = etrade.get_access_token(
            enrollment.access_token,
            enrollment.access_token_secret,
            verifier,
            environment=enrollment.environment,
        )
    except etrade.ETradeError as e:
        enrollment.last_error = str(e)
        session.add(enrollment)
        session.commit()
        raise HTTPException(400, f"Could not exchange tokens: {e}") from e

    enrollment.access_token = access_token
    enrollment.access_token_secret = access_token_secret
    enrollment.status = "active"
    enrollment.last_error = None
    session.add(enrollment)
    session.commit()

    # Run the first sync immediately
    sync_result = etrade_sync.sync_enrollment(session, enrollment)
    return {"ok": True, "enrollment_id": enrollment.id, "sync": sync_result}


@router.post("/sync")
def trigger_sync(session: Session = Depends(get_session)):
    if not settings.etrade_consumer_key:
        raise HTTPException(400, "E*TRADE not configured")
    results = etrade_sync.sync_all(session)
    return {"results": results}
