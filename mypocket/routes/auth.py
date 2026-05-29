"""Auth routes: first-time setup, login, logout."""

from __future__ import annotations

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlmodel import Session, select

from mypocket.core.db import get_session
from mypocket.core.templating import templates
from mypocket.domain.models import AppConfig
from mypocket.security import passcode as passcode_mod
from mypocket.security import session as session_mod
from mypocket.security.rate_limit import client_ip, login_limiter
from mypocket.security.redirects import safe_next

logger = logging.getLogger(__name__)

router = APIRouter()

MIN_PASSCODE_LEN = 6


def _is_setup_complete(session: Session) -> bool:
    return session.exec(select(AppConfig).where(AppConfig.id == 1)).first() is not None


def _set_session_cookie(response: RedirectResponse) -> None:
    response.set_cookie(
        key=session_mod.SESSION_COOKIE_NAME,
        value=session_mod.issue(),
        max_age=session_mod.SESSION_TTL_SECONDS,
        httponly=True,
        samesite="lax",
        # Setting secure=True would break local HTTP. Tailscale traffic is encrypted
        # at the network layer, so we keep this False until/unless we add TLS.
        secure=False,
    )


@router.get("/setup", response_class=HTMLResponse)
def setup_page(request: Request, session: Session = Depends(get_session)):
    if _is_setup_complete(session):
        return RedirectResponse("/login", status_code=303)
    return templates.TemplateResponse(request, "setup.html", {"request": request, "error": None})


@router.post("/setup")
def setup_submit(
    request: Request,
    passcode: str = Form(...),
    confirm: str = Form(...),
    session: Session = Depends(get_session),
):
    if _is_setup_complete(session):
        return RedirectResponse("/login", status_code=303)

    if len(passcode) < MIN_PASSCODE_LEN:
        return templates.TemplateResponse(
            request,
            "setup.html",
            {"request": request, "error": f"Passcode must be at least {MIN_PASSCODE_LEN} characters."},
            status_code=400,
        )
    if passcode != confirm:
        return templates.TemplateResponse(
            request,
            "setup.html",
            {"request": request, "error": "Passcodes don't match."},
            status_code=400,
        )

    config = AppConfig(id=1, passcode_hash=passcode_mod.hash_passcode(passcode))
    session.add(config)
    session.commit()
    logger.info("auth: initial passcode set")

    response = RedirectResponse("/", status_code=303)
    _set_session_cookie(response)
    return response


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request, next: str = "/", session: Session = Depends(get_session)):
    if not _is_setup_complete(session):
        return RedirectResponse("/setup", status_code=303)
    return templates.TemplateResponse(
        request,
        "login.html",
        {"request": request, "error": None, "next": next},
    )


@router.post("/login")
def login_submit(
    request: Request,
    passcode: str = Form(...),
    next: str = Form("/"),
    session: Session = Depends(get_session),
):
    ip = client_ip(request)
    allowed, retry_after = login_limiter.check(ip)
    if not allowed:
        logger.warning("auth: rate-limited login attempt from %s", ip)
        response = templates.TemplateResponse(
            request,
            "login.html",
            {
                "request": request,
                "error": f"Too many attempts. Try again in {retry_after} seconds.",
                "next": next,
            },
            status_code=429,
        )
        response.headers["Retry-After"] = str(retry_after)
        return response

    config = session.exec(select(AppConfig).where(AppConfig.id == 1)).first()
    if not config or not passcode_mod.verify_passcode(passcode, config.passcode_hash):
        logger.info("auth: failed login attempt from %s", ip)
        return templates.TemplateResponse(
            request,
            "login.html",
            {"request": request, "error": "Wrong passcode.", "next": next},
            status_code=401,
        )

    login_limiter.reset(ip)
    response = RedirectResponse(safe_next(next), status_code=303)
    _set_session_cookie(response)
    config.updated_at = datetime.now(config.created_at.tzinfo)
    session.add(config)
    session.commit()
    return response


@router.post("/logout")
def logout():
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(session_mod.SESSION_COOKIE_NAME)
    return response
