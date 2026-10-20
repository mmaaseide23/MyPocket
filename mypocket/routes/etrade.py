from datetime import UTC, datetime, timedelta
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from mypocket.core.config import settings
from mypocket.core.db import get_session
from mypocket.domain.models import Enrollment
from mypocket.integrations import etrade, etrade_sync
from mypocket.integrations._errors import safe_error

router = APIRouter(prefix="/api/etrade")


class ETradeCompletePayload(BaseModel):
    enrollment_id: int
    verifier: str = Field(..., min_length=1, max_length=64)


class ETradeStartPayload(BaseModel):
    enrollment_id: int | None = None


@router.post("/start_oauth")
def start_oauth(payload: ETradeStartPayload | None = None, session: Session = Depends(get_session)):
    if not settings.etrade_consumer_key or not settings.etrade_consumer_secret:
        raise HTTPException(
            400, "E*TRADE not configured: set ETRADE_CONSUMER_KEY / ETRADE_CONSUMER_SECRET in .env"
        )
    env = settings.etrade_environment or "sandbox"
    pending = None
    if payload and payload.enrollment_id is not None:
        pending = session.get(Enrollment, payload.enrollment_id)
        if not pending or pending.provider != "etrade":
            raise HTTPException(404, "E*TRADE enrollment not found")
        if pending.status not in {"needs_reauth", "pending_verifier"}:
            raise HTTPException(400, "This connection is still active; sync it before reconnecting.")
        if pending.environment != env:
            raise HTTPException(400, "Restore the matching E*TRADE environment before reconnecting.")
    if (
        session.exec(
            select(Enrollment.id).where(
                Enrollment.provider == "etrade", Enrollment.environment == env, Enrollment.status == "active"
            )
        ).first()
        is not None
    ):
        raise HTTPException(
            409, "E*TRADE already has an active authorization. Use Sync; reconnect only if it expires."
        )
    if pending is None:
        # Reuse pending/expired metadata instead of creating another link per retry.
        row_id = session.exec(
            select(Enrollment.id)
            .where(
                Enrollment.provider == "etrade",
                Enrollment.environment == env,
                Enrollment.status.in_(["pending_verifier", "needs_reauth"]),
            )
            .order_by(Enrollment.id.desc())
        ).first()
        if row_id is not None:
            pending = session.get(Enrollment, row_id)
    try:
        request_token, request_token_secret = etrade.get_request_token(environment=env)
    except etrade.ETradeError as e:
        raise HTTPException(500, str(e)) from e

    if pending is None:
        pending = Enrollment(provider="etrade", access_token=request_token, institution="E*TRADE")
    pending.access_token = request_token
    pending.access_token_secret = request_token_secret
    pending.status = "pending_verifier"
    pending.environment = env
    pending.last_error = None
    pending.request_token_created_at = datetime.now(UTC)
    session.add(pending)
    session.commit()
    session.refresh(pending)

    return {
        "enrollment_id": pending.id,
        "authorize_url": etrade.authorize_url(request_token),
        "environment": env,
        "expires_at": (
            pending.request_token_created_at.replace(tzinfo=UTC) + timedelta(minutes=5)
        ).isoformat(),
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

    issued = enrollment.request_token_created_at
    if issued is None or datetime.now(UTC) - issued.replace(tzinfo=UTC) >= timedelta(minutes=5):
        raise HTTPException(
            400, "This E*TRADE request expired. Use Reconnect and open the new link immediately."
        )

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

    enrollment.request_token_created_at = None
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


@router.post("/diagnose")
def diagnose(request: Request, session: Session = Depends(get_session)):
    """Use the latest existing grant for status-only reads; never renew or persist."""
    if request.headers.get("x-mypocket-diagnostic") != "1":
        raise HTTPException(403, "Use the read-only check on the Connect page.")
    origin = request.headers.get("origin")
    if origin and origin != str(request.base_url).rstrip("/"):
        raise HTTPException(403, "Use the read-only check on the Connect page.")
    enrollment_id = session.exec(
        select(Enrollment.id)
        .where(
            Enrollment.provider == "etrade",
            Enrollment.status == "active",
            Enrollment.environment == settings.etrade_environment,
        )
        .order_by(Enrollment.id.desc())
    ).first()
    if enrollment_id is None:
        raise HTTPException(409, "No active E*TRADE authorization is available to check.")
    checks = []

    def check(operation, action):
        try:
            data = action()
            checks.append({"operation": operation, "status": "ok"})
            return data
        except Exception as error:
            checks.append(
                {
                    "operation": operation,
                    "status": "error",
                    "detail": safe_error(error),
                    "http_status": error.status_code if isinstance(error, etrade.ETradeError) else None,
                    "provider_code": error.provider_code if isinstance(error, etrade.ETradeError) else None,
                }
            )
            return None

    # Loading the grant uses the app's existing encryption machinery. No token
    # or provider response body leaves this function, and there is no DB write.
    enrollment = session.get(Enrollment, enrollment_id)
    accounts = check(
        "account list",
        lambda: etrade.list_accounts(
            enrollment.access_token, enrollment.access_token_secret, enrollment.environment
        ),
    )
    if accounts is not None:
        for index, account in enumerate(accounts[:10], 1):
            key = account.get("accountIdKey")
            if not isinstance(key, str) or not key:
                checks.append({"operation": "account schema", "status": "error"})
                continue
            key = quote(key, safe="")
            endpoints = (
                (
                    "balances",
                    "balance",
                    {"instType": account.get("institutionType") or "BROKERAGE", "realTimeNAV": "true"},
                ),
                ("portfolio", "portfolio", {"count": 50, "view": "COMPLETE"}),
                ("transactions", "transactions", {"count": 50}),
            )
            for label, path, params in endpoints:
                check(
                    f"account {index}: {label}",
                    lambda path=path, params=params, key=key: etrade._api(
                        f"/v1/accounts/{key}/{path}.json",
                        token=enrollment.access_token,
                        token_secret=enrollment.access_token_secret,
                        environment=enrollment.environment,
                        params=params,
                    ),
                )
        if len(accounts) > 10:
            checks.append({"operation": "remaining accounts", "status": "not checked"})
    response = {"read_only": True, "checks": checks}
    return JSONResponse(response, headers={"Cache-Control": "no-store"})
