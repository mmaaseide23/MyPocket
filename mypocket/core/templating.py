"""Shared Jinja2 templates instance + small UI helpers exposed to templates."""

from datetime import UTC, datetime
from pathlib import Path

from fastapi.templating import Jinja2Templates
from sqlmodel import Session, select

from mypocket.core.db import engine
from mypocket.domain.models import Enrollment

# Templates live at mypocket/templates/, one level up from this file (which is
# at mypocket/core/templating.py). Resolve relative to the package root so a
# future move of this module doesn't silently break template discovery.
TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def _last_sync_at() -> datetime | None:
    """Most-recent successful sync across all enrollments, or None if never synced."""
    with Session(engine) as s:
        rows = s.exec(select(Enrollment.last_synced)).all()
    candidates = [r for r in rows if r]
    return max(candidates, default=None)


def _humanize_time_ago(dt: datetime | None) -> str:
    """Return a short human-readable string like '5m ago' or 'just now'."""
    if dt is None:
        return "never"
    now = datetime.now(UTC)
    dt_aware = dt if dt.tzinfo else dt.replace(tzinfo=UTC)
    seconds = int((now - dt_aware).total_seconds())
    if seconds < 30:
        return "just now"
    if seconds < 3600:
        return f"{max(seconds // 60, 1)}m ago"
    if seconds < 86400:
        return f"{seconds // 3600}h ago"
    return f"{seconds // 86400}d ago"


# Expose to every template under `last_sync_at()` and `humanize_time_ago()`.
templates.env.globals["last_sync_at"] = _last_sync_at
templates.env.globals["humanize_time_ago"] = _humanize_time_ago
