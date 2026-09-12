"""Contracts for birth-data resolution."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date, datetime, time, timedelta, timezone
import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator, model_validator


class UtcMinuteRange(BaseModel):
    """One canonical run of UTC instants separated by exactly one minute."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    first_utc: datetime
    count: int

    @field_validator("first_utc")
    @classmethod
    def _first_utc_must_be_exact_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != timedelta(0):
            raise ValueError("first_utc must be timezone-aware UTC")
        if value.microsecond != 0:
            raise ValueError("first_utc microseconds must be zero")
        return value.astimezone(timezone.utc)

    @field_validator("count", mode="before")
    @classmethod
    def _count_must_be_a_positive_int(cls, value: object) -> int:
        if type(value) is not int or value <= 0:
            raise ValueError("count must be a positive int")
        return value

    @model_validator(mode="after")
    def _last_minute_must_be_representable(self) -> "UtcMinuteRange":
        try:
            self.first_utc + timedelta(minutes=self.count - 1)
        except OverflowError as exc:
            raise ValueError("UTC minute range overflows datetime") from exc
        return self

    @property
    def last_utc(self) -> datetime:
        return self.first_utc + timedelta(minutes=self.count - 1)

    def iter_utc(self) -> Iterator[datetime]:
        for index in range(self.count):
            yield self.first_utc + timedelta(minutes=index)


class BirthTimeDomain(BaseModel):
    """Canonical finite set of supported UTC birth-time minutes."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    ranges: tuple[UtcMinuteRange, ...]

    @model_validator(mode="after")
    def _ranges_must_be_canonical(self) -> "BirthTimeDomain":
        if not self.ranges:
            raise ValueError("ranges must not be empty")

        for index, current in enumerate(self.ranges[1:], start=1):
            previous = self.ranges[index - 1]
            if current.first_utc <= previous.last_utc:
                raise ValueError("ranges must be strictly sorted and non-overlapping")
            if current.first_utc == previous.last_utc + timedelta(minutes=1):
                raise ValueError("adjacent UTC minute ranges must be merged")
        return self

    def iter_utc(self) -> Iterator[datetime]:
        for minute_range in self.ranges:
            yield from minute_range.iter_utc()

    def __contains__(self, moment: object) -> bool:
        if not isinstance(moment, datetime):
            return False
        return any(
            minute_range.first_utc <= moment <= minute_range.last_utc
            and (moment - minute_range.first_utc) % timedelta(minutes=1)
            == timedelta(0)
            for minute_range in self.ranges
        )

    @property
    def minute_count(self) -> int:
        return sum(minute_range.count for minute_range in self.ranges)


def birth_time_domain_digest(domain: BirthTimeDomain) -> str:
    """Return the canonical lowercase SHA-256 identity of one UTC domain."""

    payload = {
        "format": "utc-minute-domain-v1",
        "ranges": [
            {
                "first_utc": minute_range.first_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "count": minute_range.count,
            }
            for minute_range in domain.ranges
        ],
    }
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class BirthInput(BaseModel):
    """Structured birth data accepted by the build path."""

    model_config = ConfigDict(frozen=True)

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

    model_config = ConfigDict(frozen=True)

    source: Literal["place", "time"]
    code: str
    message: str


class ResolvedBirthData(BaseModel):
    """Resolved facts used by calculation and later UI restoration."""

    model_config = ConfigDict(frozen=True)

    utc_datetime: datetime
    latitude: float
    longitude: float
    tz_id: str
    utc_offset_seconds: int
    canonical_place: str
    time_unknown: bool
    birth_time_domain: BirthTimeDomain | None
    warnings: tuple[ResolutionWarning, ...] = ()

    @field_validator("utc_datetime")
    @classmethod
    def _utc_datetime_must_be_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != timedelta(0):
            raise ValueError("utc_datetime must be timezone-aware UTC")
        return value

    @model_validator(mode="after")
    def _time_domain_must_match_resolution(self) -> "ResolvedBirthData":
        if self.time_unknown:
            if self.birth_time_domain is None:
                raise ValueError("unknown birth time requires birth_time_domain")
            if self.utc_datetime not in self.birth_time_domain:
                raise ValueError("utc_datetime must belong to birth_time_domain")
        elif self.birth_time_domain is not None:
            raise ValueError("known birth time must not have birth_time_domain")
        return self


__all__ = [
    "BirthTimeDomain",
    "BirthInput",
    "ResolutionWarning",
    "ResolvedBirthData",
    "UtcMinuteRange",
    "birth_time_domain_digest",
]
