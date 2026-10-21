"""Planning page shell. Saved account totals are loaded through a separate authenticated endpoint."""

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

router = APIRouter()
PACKAGE_DIR = Path(__file__).resolve().parents[1]
# Separate environment: the shared finance templates register database helpers.
templates = Jinja2Templates(directory=str(PACKAGE_DIR / "templates"))


def _static_v(name: str = "styles.css") -> int:
    return int((PACKAGE_DIR / "static" / name).stat().st_mtime)


templates.env.globals["static_v"] = _static_v


@router.get("/planning", response_class=HTMLResponse)
def planning_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="planning.html",
        context={"active": "planning", "manual_only": True},
        headers={"Cache-Control": "no-store"},
    )
