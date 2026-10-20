"""Authenticated local Link and explicit reconciliation review endpoints."""

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field
from sqlmodel import Session, select

from mypocket.core.config import settings
from mypocket.core.db import get_session
from mypocket.core.templating import templates
from mypocket.domain.models import (
    Account,
    Enrollment,
    PlaidAccount,
    PlaidConnection,
    PlaidLinkFlow,
    Transaction,
)
from mypocket.integrations import plaid, plaid_sync
from mypocket.integrations._bank_lock import bank_lock
from mypocket.integrations._errors import safe_error
from mypocket.security.session import SESSION_COOKIE_NAME


class SafeRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def safe(request):
            try:
                return await handler(request)
            except RequestValidationError:
                # FastAPI's normal error detail can echo a rejected public token.
                raise HTTPException(422, "Invalid Plaid request. No data was changed.") from None
            except plaid.PlaidError as exc:
                raise HTTPException(400, str(exc)) from None
            except HTTPException:
                raise
            except Exception as exc:
                raise HTTPException(500, safe_error(exc)) from None

        return safe


def guard(request: Request):
    if request.method == "GET":
        return
    origin = request.headers.get("origin")
    if (
        request.headers.get("x-mypocket-request") != "1"
        or request.headers.get("sec-fetch-site") == "cross-site"
        or origin
        and origin != str(request.base_url).rstrip("/")
        or request.headers.get("content-type", "").split(";")[0] != "application/json"
    ):
        raise HTTPException(403, "Use the MyPocket page to make this request.")


router = APIRouter(route_class=SafeRoute, dependencies=[Depends(guard)])


class Payload(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LinkPayload(Payload):
    enrollment_id: int | None = None


class FlowPayload(Payload):
    flow_id: str = Field(min_length=1, max_length=128)


class ExchangePayload(FlowPayload):
    public_token: str = Field(min_length=1, max_length=4096, repr=False)


class EnrollmentPayload(Payload):
    enrollment_id: int


class Selection(Payload):
    external_id: str = Field(min_length=1, max_length=4096)
    mode: Literal["existing", "replace", "new", "ignore"]
    account_id: int | None = None


class PlanPayload(EnrollmentPayload):
    review_id: str = Field(min_length=1, max_length=128)
    selections: list[Selection] = Field(min_length=1, max_length=100)


class ApprovePayload(EnrollmentPayload):
    plan_id: str = Field(min_length=1, max_length=128)
    confirmed: bool


def _session_hash(request):
    cookie = request.cookies.get(SESSION_COOKIE_NAME)
    if not cookie:
        raise HTTPException(401, "Unlock MyPocket first.")
    return hashlib.sha256(cookie.encode()).hexdigest()


def _enrollment(session, eid):
    e = session.get(Enrollment, eid)
    if not e or e.provider != "plaid":
        raise HTTPException(404, "Plaid connection not found.")
    plaid.validate_configuration(e.environment)
    return e


def _flow(session, request, flow_id):
    flow = session.get(PlaidLinkFlow, flow_id)
    if (
        not flow
        or flow.session_hash != _session_hash(request)
        or flow.environment != settings.plaid_environment
        or datetime.now(UTC) - flow.created_at.replace(tzinfo=UTC) > timedelta(minutes=30)
    ):
        raise HTTPException(400, "Link session expired or belongs to another browser. Return to MyPocket.")
    return flow


@router.get("/plaid")
def page(request: Request, session: Session = Depends(get_session)):
    mapped = session.exec(
        select(Account)
        .join(PlaidAccount, PlaidAccount.account_id == Account.id)
        .where(Account.archived.is_(False))
    ).all()
    coverage = []
    for account in mapped:
        transactions = session.exec(
            select(Transaction.tx_date, Transaction.transaction_type).where(
                Transaction.account_id == account.id
            )
        ).all()
        dates = [t.tx_date for t in transactions]
        coverage.append(
            {
                "type": account.type,
                "count": len(dates),
                "first": min(dates) if dates else None,
                "last": max(dates) if dates else None,
                "pending": sum(t.transaction_type == "pending" for t in transactions),
            }
        )
    rows = session.exec(
        select(Enrollment.id, Enrollment.status, Enrollment.environment, Enrollment.last_error).where(
            Enrollment.provider == "plaid", Enrollment.environment == settings.plaid_environment
        )
    ).all()
    return templates.TemplateResponse(
        request,
        "plaid.html",
        {
            "request": request,
            "active": "connect",
            "configured": plaid.configured(),
            "environment": settings.plaid_environment,
            "trial_confirmed": settings.plaid_trial_confirmed,
            "enrollments": rows,
            "unlinked_types": sorted({a.type for a in plaid_sync._eligible_accounts(session)}),
            "coverage": coverage,
        },
    )


@router.post("/api/plaid/link_token")
def link_token(payload: LinkPayload, request: Request, session: Session = Depends(get_session)):
    with bank_lock:
        env = plaid.validate_configuration()
        session_hash = _session_hash(request)
        existing = session.exec(
            select(Enrollment.id).where(Enrollment.provider == "plaid", Enrollment.environment == env)
        ).first()
        e = _enrollment(session, payload.enrollment_id) if payload.enrollment_id else None
        if existing and e is None:
            raise HTTPException(
                409,
                "A Plaid connection already exists. Review or reconnect it instead of creating another Item.",
            )
        cutoff = datetime.now(UTC) - timedelta(minutes=30)
        flows = session.exec(
            select(PlaidLinkFlow).where(
                PlaidLinkFlow.environment == env,
                PlaidLinkFlow.created_at >= cutoff,
                PlaidLinkFlow.result_id.is_(None),
            )
        ).all()
        for flow in flows:
            if flow.enrollment_id == payload.enrollment_id:
                if flow.session_hash != session_hash:
                    raise HTTPException(
                        409, "A Link flow is open in another session. Finish it there, or wait 30 minutes."
                    )
                return {"flow_id": flow.id, "link_token": flow.link_token, "update": bool(e)}
        token = plaid.create_link_token(e.access_token, e.environment) if e else plaid.create_link_token()
        flow = PlaidLinkFlow(
            id=secrets.token_urlsafe(24),
            session_hash=session_hash,
            environment=env,
            enrollment_id=payload.enrollment_id,
            link_token=token,
        )
        session.add(flow)
        session.commit()
        return {"flow_id": flow.id, "link_token": token, "update": bool(e)}


@router.post("/api/plaid/exchange")
def exchange(payload: ExchangePayload, request: Request, session: Session = Depends(get_session)):
    with bank_lock:
        plaid.validate_configuration()
        flow = _flow(session, request, payload.flow_id)
        digest = hashlib.sha256(payload.public_token.encode()).hexdigest()
        if flow.result_id:
            if digest != flow.public_token_hash:
                raise HTTPException(400, "Link session already completed.")
            return {"enrollment_id": flow.result_id, "status": "pending_review"}
        if flow.enrollment_id is not None:
            raise HTTPException(400, "Use the reconnect completion flow.")
        if session.exec(
            select(Enrollment.id).where(
                Enrollment.provider == "plaid", Enrollment.environment == settings.plaid_environment
            )
        ).first():
            raise HTTPException(409, "A Plaid connection already exists. No second connection was saved.")
        token, item_id = plaid.exchange(payload.public_token)
        # Save immediately: a public token is one-use. Later preview failures must
        # not discard the access token and force another Trial Item to be created.
        e = Enrollment(
            provider="plaid",
            enrollment_id=item_id,
            access_token=token,
            environment=settings.plaid_environment,
            status="pending_review",
            institution="Citi (review required)",
        )
        session.add(e)
        session.flush()
        session.add(PlaidConnection(enrollment_id=e.id))
        flow.result_id = e.id
        flow.public_token_hash = digest
        flow.link_token = "completed"
        session.add(flow)
        session.commit()
        return {"enrollment_id": e.id, "status": "pending_review"}


@router.post("/api/plaid/reconnect_complete")
def reconnect(payload: FlowPayload, request: Request, session: Session = Depends(get_session)):
    with bank_lock:
        flow = _flow(session, request, payload.flow_id)
        if flow.enrollment_id is None:
            raise HTTPException(400, "This is not an update flow.")
        e = _enrollment(session, flow.enrollment_id)
        data = plaid.accounts(e.access_token, e.environment)
        if data["item"].get("item_id") != e.enrollment_id:
            raise HTTPException(400, "Plaid returned a different connection. Nothing changed.")
        state = session.get(PlaidConnection, e.id)
        e.status = "active" if state and state.approved else "pending_review"
        e.last_error = None
        flow.result_id = e.id
        flow.link_token = "completed"
        session.add(e)
        session.add(flow)
        session.commit()
        return {"enrollment_id": e.id, "status": e.status}


@router.post("/api/plaid/review")
def review(payload: EnrollmentPayload, session: Session = Depends(get_session)):
    return plaid_sync.stage_review(session, _enrollment(session, payload.enrollment_id))


@router.post("/api/plaid/preview")
def preview(payload: PlanPayload, session: Session = Depends(get_session)):
    return plaid_sync.preview_plan(
        session,
        _enrollment(session, payload.enrollment_id),
        payload.review_id,
        [x.model_dump() for x in payload.selections],
    )


@router.post("/api/plaid/approve")
def approve(payload: ApprovePayload, session: Session = Depends(get_session)):
    if not payload.confirmed:
        raise HTTPException(400, "Explicitly confirm the reviewed mapping first.")
    if settings.plaid_environment != "production":
        raise HTTPException(
            400, "Sandbox is preview-only. Synthetic data cannot be imported into your dashboard."
        )
    return plaid_sync.approve_plan(session, _enrollment(session, payload.enrollment_id), payload.plan_id)


@router.post("/api/plaid/sync")
def sync(payload: EnrollmentPayload, session: Session = Depends(get_session)):
    return plaid_sync.sync_enrollment(session, _enrollment(session, payload.enrollment_id))
