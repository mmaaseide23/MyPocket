"""Saved account-total endpoint protected by the existing app authentication gate."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlmodel import Session

from mypocket.core.db import get_session
from mypocket.domain.planning_accounts import saved_accounts

router = APIRouter()


@router.get("/api/planning/accounts")
def accounts(request: Request, session: Annotated[Session, Depends(get_session)]) -> JSONResponse:
    headers = {"Cache-Control": "no-store", "Pragma": "no-cache"}
    if (
        request.headers.get("X-MyPocket-Request") != "1"
        or request.headers.get("Sec-Fetch-Site") == "cross-site"
        or (request.headers.get("Origin") and request.headers["Origin"] != str(request.base_url).rstrip("/"))
    ):
        return JSONResponse({"detail": "same-origin request required"}, status_code=403, headers=headers)
    return JSONResponse(saved_accounts(session), headers=headers)
