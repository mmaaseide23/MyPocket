"""Compatibility entry point for the current isolated fictional planner preview.

Run only in a source-only copy without .env, never in the configured live repo:
uvicorn tests.planning_preview:app --host 127.0.0.1 --port 8765 --no-access-log
"""

from fastapi.responses import RedirectResponse

from tests.planning_accounts_preview import app


@app.get("/")
def root():
    return RedirectResponse("/planning")
