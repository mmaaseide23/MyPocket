"""Fictional-only theme preview, in a source-only copy with no .env or real DB.

Extends the memory-only positions fixture. Login is visual only and accepts no
credentials. Never run against the production application or user profiles.
"""

from fastapi import Request

from mypocket.core.templating import templates
from tests.positions_preview import app

app.router.routes[:] = [route for route in app.router.routes if getattr(route, "path", None) != "/login"]


@app.get("/login")
def login_preview(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={"manual_only": True, "next": "/", "error": None},
    )
