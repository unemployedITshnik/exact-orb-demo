"""Historical timezone resolution using stdlib zoneinfo."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import TypeAlias
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, field_validator

from exact_orb.birth.types import (
    BirthTimeDomain,
    ResolutionWarning,
    UtcMinuteRange,
)


POSIX_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


class UnknownTimezoneError(RuntimeError):
    """zoneinfo does not know the tz_id from the place catalog."""


class TzOk(BaseModel):
    """A local datetime was resolved to UTC."""

    utc_datetime: datetime
    utc_offset_seconds: int
    warnings: tuple[ResolutionWarning, ...] = ()


class TzNonexistent(BaseModel):
    """A local datetime falls into a skipped clock interval."""

    local_datetime: datetime
    tz_id: str
    normalized: datetime

    @field_validator("local_datetime", "normalized")
    @classmethod
    def _datetime_must_be_naive(cls, value: datetime) -> datetime:
        if value.tzinfo is not None:
            raise ValueError("datetime must be naive")
        return value


class TzAmbiguous(BaseModel):
    """A local datetime can map to two UTC instants."""

    local_datetime: datetime
    tz_id: str
    offsets: tuple[int, int]

    @field_validator("local_datetime")
    @classmethod
    def _datetime_must_be_naive(cls, value: datetime) -> datetime:
        if value.tzinfo is not None:
            raise ValueError("datetime must be naive")
        return value


TzResolution: TypeAlias = TzOk | TzNonexistent | TzAmbiguous


def resolve_historical_tz(local_datetime: datetime, tz_id: str) -> TzResolution:
    """Resolve a naive local datetime in a named IANA timezone."""

    if local_datetime.tzinfo is not None:
        raise ValueError("local_datetime must be naive")

    tz = _load_zone(tz_id)
    dt = local_datetime.replace(tzinfo=tz)

    back = dt.astimezone(timezone.utc).astimezone(tz).replace(tzinfo=None)
    if back != local_datetime:
        return TzNonexistent(
            local_datetime=local_datetime,
            tz_id=tz_id,
            normalized=back,
        )

    o0 = dt.replace(fold=0).utcoffset()
    o1 = dt.replace(fold=1).utcoffset()
    if o0 != o1:
        return TzAmbiguous(
            local_datetime=local_datetime,
            tz_id=tz_id,
            offsets=(_offset_seconds(o0), _offset_seconds(o1)),
        )

    utc_datetime = dt.astimezone(timezone.utc)
    return TzOk(
        utc_datetime=utc_datetime,
        utc_offset_seconds=_offset_seconds(o0),
        warnings=_pre_1970_warnings(utc_datetime),
    )


def resolve_anomaly(anomaly: TzNonexistent | TzAmbiguous) -> TzOk:
    """Deterministically resolve a timezone anomaly for system-provided anchors."""

    if isinstance(anomaly, TzNonexistent):
        resolved = resolve_historical_tz(anomaly.normalized, anomaly.tz_id)
        if isinstance(resolved, TzOk):
            return resolved
        raise RuntimeError(
            f"normalized local time is still anomalous for {anomaly.tz_id}"
        )

    tz = _load_zone(anomaly.tz_id)
    dt = anomaly.local_datetime.replace(tzinfo=tz, fold=0)
    utc_datetime = dt.astimezone(timezone.utc)
    return TzOk(
        utc_datetime=utc_datetime,
        utc_offset_seconds=_offset_seconds(dt.utcoffset()),
        warnings=_pre_1970_warnings(utc_datetime),
    )


def local_date_exists(local_date: date, tz_id: str) -> bool:
    """Return whether a local calendar date has non-zero UTC duration."""

    start = _utc_of_local_midnight(local_date, tz_id)
    end = _utc_of_local_midnight(local_date + timedelta(days=1), tz_id)
    return end > start


def build_birth_time_domain(
    local_date: date,
    tz_id: str,
) -> BirthTimeDomain | None:
    """Resolve every supported local minute into one canonical UTC domain."""

    utc_moments: set[datetime] = set()
    local_midnight = datetime.combine(local_date, time.min)
    for minute_index in range(24 * 60):
        local_minute = local_midnight + timedelta(minutes=minute_index)
        resolution = resolve_historical_tz(local_minute, tz_id)
        if isinstance(resolution, TzOk):
            utc_moments.add(resolution.utc_datetime)
        elif isinstance(resolution, TzAmbiguous):
            for offset_seconds in resolution.offsets:
                utc_moments.add(
                    (local_minute - timedelta(seconds=offset_seconds)).replace(
                        tzinfo=timezone.utc
                    )
                )

    ordered = sorted(utc_moments)
    if not ordered:
        return None

    ranges: list[UtcMinuteRange] = []
    first = ordered[0]
    count = 1
    previous = first
    for moment in ordered[1:]:
        if moment == previous + timedelta(minutes=1):
            count += 1
        else:
            ranges.append(UtcMinuteRange(first_utc=first, count=count))
            first = moment
            count = 1
        previous = moment
    ranges.append(UtcMinuteRange(first_utc=first, count=count))
    return BirthTimeDomain(ranges=tuple(ranges))


def resolve_unknown_birth_time_for_migration(
    local_date: date,
    tz_id: str,
) -> tuple[datetime, int, BirthTimeDomain]:
    """Return current unknown-time facts for the versioned session seam."""

    domain = build_birth_time_domain(local_date, tz_id)
    if domain is None:
        raise ValueError("local date has no supported minutes")
    noon = resolve_historical_tz(datetime.combine(local_date, time(12, 0)), tz_id)
    anchor = noon if isinstance(noon, TzOk) else resolve_anomaly(noon)
    if anchor.utc_datetime not in domain:
        raise ValueError("technical noon anchor is outside birth time domain")
    return anchor.utc_datetime, anchor.utc_offset_seconds, domain


def _load_zone(tz_id: str) -> ZoneInfo:
    try:
        return ZoneInfo(tz_id)
    except (KeyError, ZoneInfoNotFoundError) as exc:
        raise UnknownTimezoneError(tz_id) from exc


def _offset_seconds(offset: timedelta | None) -> int:
    if offset is None:
        raise ValueError("utcoffset is unavailable")
    return int(offset.total_seconds())


def _utc_of_local_midnight(local_date: date, tz_id: str) -> datetime:
    local_midnight = datetime.combine(local_date, datetime.min.time())
    result = resolve_historical_tz(local_midnight, tz_id)
    if isinstance(result, TzOk):
        return result.utc_datetime
    return resolve_anomaly(result).utc_datetime


def _pre_1970_warnings(utc_datetime: datetime) -> tuple[ResolutionWarning, ...]:
    if utc_datetime < POSIX_EPOCH:
        return (
            ResolutionWarning(
                source="time",
                code="pre_1970_offset_unverified",
                message="IANA guarantees clock agreement only after 1970-01-01",
            ),
        )
    return ()


__all__ = [
    "POSIX_EPOCH",
    "TzAmbiguous",
    "TzNonexistent",
    "TzOk",
    "TzResolution",
    "UnknownTimezoneError",
    "build_birth_time_domain",
    "local_date_exists",
    "resolve_anomaly",
    "resolve_historical_tz",
    "resolve_unknown_birth_time_for_migration",
]
