"""Package-level helpers used across modules."""

from __future__ import annotations

import math
from typing import Any


def to_float(v: Any) -> float | None:
    """Parse a value to float, tolerating $, commas, %, NaN, and empty strings.
    Returns None when the input doesn't represent a real number."""
    if v is None:
        return None
    if isinstance(v, float) and math.isnan(v):
        return None
    if isinstance(v, str):
        s = v.replace("$", "").replace(",", "").replace("%", "").strip()
        if s in ("", "-", "--", "N/A"):
            return None
        try:
            return float(s)
        except ValueError:
            return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None
