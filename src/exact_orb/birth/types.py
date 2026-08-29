"""Contracts for birth-data resolution."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Literal

from pydantic import BaseModel, field_validator


class BirthInput(BaseModel):
    """Structured birth data accepted by the build path."""

    birth_date: date
    birth_time: time | None = None
    place_id: str

    @field_validator("birth_time")
    @classmethod
    def _birth_time_must_be_naive(cls, value: time | None) -> time | None:
        if value is not None and value.tzinfo is not None:
            raise ValueError("birth_time must be naive")
        return value


class ResolutionWarning(BaseModel):
    """A machine-checkable warning produced during resolution."""

    source: Literal["place", "time"]
    code: str
    message: str


class ResolvedBirthData(BaseModel):
    """Resolved facts used by calculation and later UI restoration."""

    utc_datetime: datetime
    latitude: float
    longitude: float
    tz_id: str
    utc_offset_seconds: int
    canonical_place: str
    time_unknown: bool
    warnings: tuple[ResolutionWarning, ...] = ()

    @field_validator("utc_datetime")
    @classmethod
    def _utc_datetime_must_be_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != timedelta(0):
            raise ValueError("utc_datetime must be timezone-aware UTC")
        return value


__all__ = [
    "BirthInput",
    "ResolutionWarning",
    "ResolvedBirthData",
]
