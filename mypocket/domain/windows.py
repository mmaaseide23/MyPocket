"""Calendar-date reporting windows. Start inclusive, stop exclusive throughout.

`today` is the server/Mac's local date. Provider date-only fields stay dates;
they are never interpreted as UTC timestamps by the browser.
"""

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal

Period = Literal["day", "week", "month"]
DAY_CHOICES = (7, 30, 90, 180, 365)
MONTH_CHOICES = (3, 6, 12, 24, 36)


@dataclass(frozen=True)
class Window:
    start: date
    stop: date

    @property
    def end(self) -> date:
        return self.stop - timedelta(days=1)

    @property
    def label(self) -> str:
        return f"{self.start:%b %d, %Y} – {self.end:%b %d, %Y}"


def month_start_offset(day: date, offset: int) -> date:
    year, month = divmod(day.year * 12 + day.month - 1 + offset, 12)
    return date(year, month + 1, 1)


def trailing_days(days: int, as_of: date | None = None) -> Window:
    if not 1 <= days <= 3660:
        raise ValueError("Days must be between 1 and 3660")
    end = as_of or date.today()
    return Window(end - timedelta(days=days - 1), end + timedelta(days=1))


def calendar_months(months: int, as_of: date | None = None) -> Window:
    if not 1 <= months <= 120:
        raise ValueError("Months must be between 1 and 120")
    end = as_of or date.today()
    return Window(month_start_offset(end, 1 - months), end + timedelta(days=1))


def buckets(window: Window, period: Period) -> list[dict]:
    """Intersect calendar buckets with the exact selected range, including zeros."""
    if period not in ("day", "week", "month"):
        raise ValueError("Unknown reporting period")
    cursor = window.start
    rows = []
    while cursor < window.stop:
        if period == "month":
            natural_start = cursor.replace(day=1)
            natural_stop = month_start_offset(cursor, 1)
        elif period == "week":
            natural_start = cursor - timedelta(days=cursor.weekday())
            natural_stop = natural_start + timedelta(days=7)
        else:
            natural_start = cursor
            natural_stop = cursor + timedelta(days=1)
        stop = min(natural_stop, window.stop)
        end = stop - timedelta(days=1)
        partial = cursor != natural_start or stop != natural_stop
        label = cursor.strftime("%b %Y" if period == "month" else "%b %d, %Y")
        if partial:
            label += " (partial)"
        rows.append(
            {
                "label": label,
                "month": natural_start.strftime("%Y-%m"),
                "start": cursor.isoformat(),
                "end": end.isoformat(),
                "partial": partial,
                "range": f"{cursor:%b %d, %Y} – {end:%b %d, %Y}",
            }
        )
        cursor = stop
    return rows
