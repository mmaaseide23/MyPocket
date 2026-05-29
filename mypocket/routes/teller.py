from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlmodel import Session, select

from mypocket.core.config import settings
from mypocket.core.db import get_session
from mypocket.core.templating import templates
from mypocket.domain.models import Enrollment
from mypocket.integrations import teller_sync

router = APIRouter()


# Schemas for the Teller Connect onSuccess payload. Teller's widget posts a nested
# object whose shape has shifted between versions; we accept extra fields and treat
# accessToken-at-top-level vs nested as either-or.
class _TellerInstitution(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: str | None = None
    id: str | None = None


class _TellerEnrollmentRef(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str | None = None
    institution: _TellerInstitution | None = None
    accessToken: str | None = None


class _TellerUser(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str | None = None


class TellerEnrollmentPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")
    accessToken: str | None = Field(default=None, max_length=4096)
    enrollmentId: str | None = Field(default=None, max_length=256)
    institution: str | None = Field(default=None, max_length=256)
    enrollment: _TellerEnrollmentRef | None = None
    user: _TellerUser | None = None


@router.get("/connect", response_class=HTMLResponse)
def connect_page(request: Request, session: Session = Depends(get_session)):
    enrollments = session.exec(select(Enrollment)).all()
    ctx = {
        "request": request,
        "active": "connect",
        "teller_application_id": settings.teller_application_id,
        "teller_environment": settings.teller_environment,
        "teller_configured": bool(
            settings.teller_application_id and settings.teller_cert_path and settings.teller_key_path
        ),
        "etrade_configured": bool(settings.etrade_consumer_key and settings.etrade_consumer_secret),
        "etrade_environment": settings.etrade_environment,
        "enrollments": [
            {
                "id": e.id,
                "provider": e.provider,
                "institution": e.institution,
                "status": e.status,
                "environment": e.environment,
                "last_synced": e.last_synced.isoformat() if e.last_synced else None,
                "last_error": e.last_error,
            }
            for e in enrollments
        ],
    }
    return templates.TemplateResponse(request, "connect.html", ctx)


@router.post("/api/teller/save_enrollment")
def save_enrollment(
    payload: TellerEnrollmentPayload,
    session: Session = Depends(get_session),
):
    """Called from Teller Connect onSuccess to persist the access token."""
    enrollment_obj = payload.enrollment
    access_token = payload.accessToken
    if not access_token and enrollment_obj:
        access_token = enrollment_obj.accessToken
    if not access_token:
        raise HTTPException(400, "Missing accessToken")

    enrollment_id = (enrollment_obj.id if enrollment_obj else None) or payload.enrollmentId
    institution = (
        enrollment_obj.institution.name if enrollment_obj and enrollment_obj.institution else None
    ) or payload.institution
    user_id = payload.user.id if payload.user else None

    existing = None
    if enrollment_id:
        existing = session.exec(select(Enrollment).where(Enrollment.enrollment_id == enrollment_id)).first()

    if existing:
        existing.access_token = access_token
        existing.institution = institution or existing.institution
        existing.user_id = user_id or existing.user_id
        existing.status = "active"
        existing.last_error = None
        session.add(existing)
        session.commit()
        return {"ok": True, "id": existing.id, "updated": True}

    e = Enrollment(
        provider="teller",
        enrollment_id=enrollment_id,
        institution=institution,
        user_id=user_id,
        access_token=access_token,
        status="active",
    )
    session.add(e)
    session.commit()
    session.refresh(e)
    return {"ok": True, "id": e.id, "created": True}


@router.post("/api/teller/sync")
def trigger_sync(session: Session = Depends(get_session)):
    if not settings.teller_application_id:
        raise HTTPException(400, "Teller not configured: set TELLER_APPLICATION_ID in .env")
    results = teller_sync.sync_all(session)
    return {"results": results}


@router.delete("/api/teller/enrollment/{enrollment_id}")
def delete_enrollment(enrollment_id: int, session: Session = Depends(get_session)):
    e = session.get(Enrollment, enrollment_id)
    if not e:
        raise HTTPException(404, "Not found")
    session.delete(e)
    session.commit()
    return {"ok": True}
