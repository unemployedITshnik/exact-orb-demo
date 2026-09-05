"""Shared validation for adapter methods that receive an explicit clock value."""

from __future__ import annotations

from datetime import datetime

from exact_orb.session.state import _require_utc


def _validate_now(now: datetime) -> datetime:
    """Return a valid UTC value without reading or normalizing time."""

    return _require_utc(now, name="now")
