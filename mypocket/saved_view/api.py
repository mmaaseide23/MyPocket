"""Optional API and public offline shell, absent while the feature is disabled."""

from typing import Literal

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, ValidationError

from mypocket.saved_view import service
from mypocket.saved_view.changes import recorded_changes
from mypocket.saved_view.identity import sign_out_everywhere
from mypocket.saved_view.policy import SavedViewOptions, SavedViewUnavailable
from mypocket.saved_view.shell import shell_router
from mypocket.security.session import SESSION_COOKIE_NAME

HEADERS = {"Cache-Control": "no-store, private", "Pragma": "no-cache",
           "Vary": "Cookie", "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer"}


class Approval(BaseModel):
    model_config = ConfigDict(extra="forbid")
    account_ids: list[StrictInt] = Field(min_length=1, max_length=20)
    field_set: Literal["generic-balances-v1"]
    retention_seconds: StrictInt = Field(ge=3600, le=604800)
    history_enabled: StrictBool
    confirm_fields: StrictBool
    confirm_offline_limit: StrictBool
    confirm_phrase: StrictBool


def create_router(*, options=SavedViewOptions(), session_factory=None, verify_session=None,
                  history_available=False, synthetic_label=False, session_cookie_name=SESSION_COOKIE_NAME):
    """Auth and sessions are explicit dependencies; disabled means zero routes."""
    router = APIRouter(prefix="/saved-view", include_in_schema=False)
    if not options.enabled:
        return router
    if not callable(session_factory) or not callable(verify_session):
        raise ValueError("Explicit session and authentication dependencies are required")

    def respond(body, status=200):
        return JSONResponse(body, status_code=status, headers=HEADERS)

    def authorized(request):
        if request.headers.get("sec-fetch-site") == "cross-site":
            return False
        origin = request.headers.get("origin")
        if origin is not None and origin != options.origin:
            return False
        if f"{request.url.scheme}://{request.url.netloc}" != options.origin:
            return False
        # Deployments must trust forwarded headers only from their HTTPS proxy.
        token = request.cookies.get(session_cookie_name)
        return bool(token) and verify_session(token)

    def read(request, callback):
        if not authorized(request):
            return respond({"error": "access_denied"}, 401)
        try:
            with session_factory() as session:
                return respond(callback(session))
        except SavedViewUnavailable as error:
            return respond({"error": str(error)}, 403)

    @router.get("/options")
    def choices(request: Request):
        return read(request, lambda session: {**service.approval_options(session), "history_available": history_available})

    @router.post("/approve")
    async def approve(request: Request):
        if request.headers.get("origin") != options.origin or not authorized(request):
            return respond({"error": "access_denied"}, 403)
        if request.headers.get("content-type", "").split(";")[0] != "application/json":
            return respond({"error": "invalid_approval"}, 400)
        try:
            # Small bounded policy input, never a phrase, passcode or provider payload.
            raw = bytearray()
            async for chunk in request.stream():
                if len(raw) + len(chunk) > 8192:
                    return respond({"error": "invalid_approval"}, 400)
                raw.extend(chunk)
            approval = Approval.model_validate_json(raw)
            if not (approval.confirm_fields and approval.confirm_offline_limit and approval.confirm_phrase):
                return respond({"error": "approval_required"}, 400)
            if approval.history_enabled and not history_available:
                return respond({"error": "history_disabled"}, 400)
        except (ValidationError, ValueError):
            return respond({"error": "invalid_approval"}, 400)
        return read(request, lambda session: service.record_approval(
            session, account_ids=approval.account_ids, field_set=approval.field_set,
            retention_seconds=approval.retention_seconds, history_enabled=approval.history_enabled,
        ))

    @router.get("/access")
    def access(request: Request):
        return read(request, service.access_state)

    @router.get("/snapshot")
    def export(request: Request):
        return read(request, service.snapshot)

    @router.get("/changes")
    def changes(request: Request):
        return read(request, recorded_changes)

    @router.post("/revoke")
    def revoke(request: Request):
        # Cookie-authenticated mutation: require an exact Origin, never Referer fallback.
        if request.headers.get("origin") != options.origin:
            return respond({"error": "origin_required"}, 403)
        return read(request, lambda session: (service.revoke(session), {"active": False})[1])

    @router.post("/logout")
    def logout(request: Request):
        if request.headers.get("origin") != options.origin:
            return respond({"error": "origin_required"}, 403)
        response = read(request, lambda session: (sign_out_everywhere(session), {"signed_out": True})[1])
        if response.status_code == 200:
            response.delete_cookie(session_cookie_name, secure=options.origin.startswith("https://"), httponly=True, samesite="lax")
        return response

    # Public files are registered after API routes and contain no user-specific data.
    # Their paths remain exact; there is no catch-all navigation/auth fallback.
    router.include_router(shell_router(synthetic_label=synthetic_label), prefix="")
    return router
