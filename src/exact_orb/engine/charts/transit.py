"""Transit chart calculations."""

from __future__ import annotations

from calendar import monthrange
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta, timezone
from math import sqrt
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
import swisseph as swe

from exact_orb.engine.aspects import AspectConfig, PositionedPoint, find_aspects
from exact_orb.engine.aspects.types import AspectType
from exact_orb.config import EphemerisStatus, configure_ephemeris
from exact_orb.engine.charts.natal import NatalChart
from exact_orb.engine.ephemeris.calc import (
    calculate_houses,
    house_for_longitude,
    julian_day_ut,
    normalize_degrees,
    normalize_house_system,
    to_utc,
    validate_geography,
    zodiac_position,
)
from exact_orb.engine.ephemeris.types import (
    AnglePosition,
    CalculationWarning,
    HouseCusp,
    ZodiacPosition,
)


FULL_CIRCLE = 360.0
HALF_CIRCLE = 180.0
DEFAULT_EXACT_WINDOW_MONTHS = 12
DEFAULT_ASPECT_ORB = 2.0
DEFAULT_STATION_ASPECT_ORB = 1.0
APPLYING_DT = timedelta(hours=1)
ROOT_TOLERANCE_DEGREES = 1e-7
ROOT_DEDUP_TOLERANCE = timedelta(hours=1)

TRANSIT_BODY_IDS: dict[str, int] = {
    "sun": swe.SUN,
    "moon": swe.MOON,
    "mercury": swe.MERCURY,
    "venus": swe.VENUS,
    "mars": swe.MARS,
    "jupiter": swe.JUPITER,
    "saturn": swe.SATURN,
    "uranus": swe.URANUS,
    "neptune": swe.NEPTUNE,
    "pluto": swe.PLUTO,
}

SLOW_STATION_BODY_IDS: dict[str, int] = {
    "jupiter": swe.JUPITER,
    "saturn": swe.SATURN,
    "uranus": swe.URANUS,
    "neptune": swe.NEPTUNE,
    "pluto": swe.PLUTO,
}

NATAL_ANGLE_ASPECTS = ("asc", "mc", "dsc", "ic", "vertex")


AspectName = AspectType


class TransitLocation(BaseModel):
    """Location used only for transit houses and angles."""

    latitude: float
    longitude: float
    house_system: str | bytes = "P"


class TransitDateRange(BaseModel):
    """Explicit range for exact transit searches."""

    start: datetime
    end: datetime


class TransitPointRef(BaseModel):
    """Reference to a point from the transit chart."""

    chart: Literal["transit"] = "transit"
    body: str


class NatalPointRef(BaseModel):
    """Reference to a point from the natal chart."""

    chart: Literal["natal"] = "natal"
    body: str


class TransitBodyPosition(BaseModel):
    """Transit body position and its placement in the natal houses."""

    chart: Literal["transit"] = "transit"
    name: str
    swe_id: int
    longitude: float = Field(..., ge=0.0, lt=360.0)
    longitude_speed: float
    retrograde: bool
    natal_house: int = Field(..., ge=1, le=12)
    zodiac: ZodiacPosition
    retflags: int


class ExactApproach(BaseModel):
    """Closest approach of a transit aspect in the search window."""

    datetime_utc: datetime
    orb: float = Field(..., ge=0.0)


class TransitAspect(BaseModel):
    """Aspect from one transit point to one natal point."""

    model_config = ConfigDict(populate_by_name=True)

    from_point: TransitPointRef = Field(alias="from")
    to: NatalPointRef
    aspect: AspectName
    aspect_angle: float
    orb: float = Field(..., ge=0.0)
    applying: bool
    exact_dates: tuple[datetime, ...]
    closest_approach: ExactApproach


class StationAspect(BaseModel):
    """Natal aspect formed by a station longitude."""

    to: NatalPointRef
    aspect: AspectName
    aspect_angle: float
    orb: float = Field(..., ge=0.0)


class TransitStation(BaseModel):
    """A station of a slow transit body."""

    chart: Literal["transit"] = "transit"
    body: str
    type: Literal["retrograde", "direct"]
    datetime_utc: datetime
    longitude: float = Field(..., ge=0.0, lt=360.0)
    longitude_speed: float
    zodiac: ZodiacPosition
    natal_aspects: tuple[StationAspect, ...]


class TransitChart(BaseModel):
    """Complete deterministic transit calculation result."""

    moment_utc: datetime
    window_start_utc: datetime
    window_end_utc: datetime
    natal_datetime_utc: datetime
    ephemeris_flags: int
    ephemeris: EphemerisStatus
    positions: dict[str, TransitBodyPosition]
    aspects: tuple[TransitAspect, ...]
    stations: tuple[TransitStation, ...]
    houses: tuple[HouseCusp, ...] | None = None
    angles: dict[str, AnglePosition] | None = None
    warnings: tuple[CalculationWarning, ...] = ()


def calculate_transits(
    natal: NatalChart,
    moment: datetime | tuple[datetime, datetime] | TransitDateRange,
    location: TransitLocation | Mapping[str, object] | Sequence[object] | None = None,
    *,
    body_ids: Mapping[str, int] | None = None,
    ephemeris_flags: int = swe.FLG_SWIEPH,
    max_orb: float = DEFAULT_ASPECT_ORB,
    exact_window_months: int = DEFAULT_EXACT_WINDOW_MONTHS,
    station_body_ids: Mapping[str, int] | None = None,
    station_aspect_orb: float = DEFAULT_STATION_ASPECT_ORB,
    ephemeris_path: str | None = None,
) -> TransitChart:
    """Calculate transits to a natal chart.

    If ``moment`` is a datetime, positions are calculated at that instant and
    exact aspects/stations are searched in a +/- ``exact_window_months`` window.
    If ``moment`` is a range, positions are calculated at the range start and
    exact aspects/stations are searched inside that explicit range.
    """

    ephemeris = configure_ephemeris(ephemeris_path)
    moment_utc, window_start, window_end = _normalize_moment(moment, exact_window_months)
    if max_orb < 0.0:
        raise ValueError("max_orb must be non-negative")
    if station_aspect_orb < 0.0:
        raise ValueError("station_aspect_orb must be non-negative")
    if natal.cusps is None:
        raise ValueError("natal chart must include houses for transit house placement")

    flags = ephemeris_flags | swe.FLG_SPEED
    transit_body_ids = dict(body_ids or TRANSIT_BODY_IDS)
    positions, warnings = _calculate_transit_positions(moment_utc, transit_body_ids, natal, flags)
    natal_points = _natal_points(natal)
    aspects = _calculate_transit_aspects(
        moment_utc,
        positions,
        natal_points,
        window_start,
        window_end,
        flags,
        max_orb,
    )
    stations = _calculate_stations(
        window_start,
        window_end,
        dict(station_body_ids or SLOW_STATION_BODY_IDS),
        natal_points,
        flags,
        station_aspect_orb,
    )

    transit_houses: tuple[HouseCusp, ...] | None = None
    transit_angles: dict[str, AnglePosition] | None = None
    if location is not None:
        transit_houses, transit_angles = _calculate_transit_houses(moment_utc, location)

    return TransitChart(
        moment_utc=moment_utc,
        window_start_utc=window_start,
        window_end_utc=window_end,
        natal_datetime_utc=natal.datetime_utc,
        ephemeris_flags=flags,
        ephemeris=ephemeris,
        positions=positions,
        aspects=aspects,
        stations=stations,
        houses=transit_houses,
        angles=transit_angles,
        warnings=tuple(warnings),
    )


def _normalize_moment(
    moment: datetime | tuple[datetime, datetime] | TransitDateRange,
    exact_window_months: int,
) -> tuple[datetime, datetime, datetime]:
    if exact_window_months < 0:
        raise ValueError("exact_window_months must be non-negative")

    if isinstance(moment, TransitDateRange):
        start = to_utc(moment.start)
        end = to_utc(moment.end)
        if end <= start:
            raise ValueError("transit date range end must be after start")
        return start, start, end

    if isinstance(moment, tuple):
        if len(moment) != 2:
            raise ValueError("moment range must contain exactly two datetimes")
        start = to_utc(moment[0])
        end = to_utc(moment[1])
        if end <= start:
            raise ValueError("transit date range end must be after start")
        return start, start, end

    moment_utc = to_utc(moment)
    return (
        moment_utc,
        _add_months(moment_utc, -exact_window_months),
        _add_months(moment_utc, exact_window_months),
    )


def _calculate_transit_positions(
    moment_utc: datetime,
    body_ids: Mapping[str, int],
    natal: NatalChart,
    flags: int,
) -> tuple[dict[str, TransitBodyPosition], list[CalculationWarning]]:
    positions: dict[str, TransitBodyPosition] = {}
    warnings: list[CalculationWarning] = []

    for name, body_id in body_ids.items():
        longitude, speed, retflags, warning = _body_longitude_speed(moment_utc, body_id, flags)
        if warning:
            warnings.append(CalculationWarning(source=name, message=warning, retflags=retflags))
        positions[name] = TransitBodyPosition(
            name=name,
            swe_id=body_id,
            longitude=longitude,
            longitude_speed=speed,
            retrograde=speed < 0.0,
            natal_house=house_for_longitude(longitude, natal.cusps),
            zodiac=zodiac_position(longitude),
            retflags=retflags,
        )

    return positions, warnings


def _calculate_transit_houses(
    moment_utc: datetime,
    location: TransitLocation | Mapping[str, object] | Sequence[object],
) -> tuple[tuple[HouseCusp, ...], dict[str, AnglePosition]]:
    transit_location = _normalize_location(location)
    validate_geography(transit_location.latitude, transit_location.longitude)
    house_system = normalize_house_system(transit_location.house_system)
    return calculate_houses(
        julian_day_ut(moment_utc),
        transit_location.latitude,
        transit_location.longitude,
        house_system,
    )


def _calculate_transit_aspects(
    moment_utc: datetime,
    positions: Mapping[str, TransitBodyPosition],
    natal_points: Mapping[str, float],
    window_start: datetime,
    window_end: datetime,
    flags: int,
    max_orb: float,
) -> tuple[TransitAspect, ...]:
    aspects: list[TransitAspect] = []
    config = AspectConfig.transit(max_orb=max_orb)
    for transit_name, position in positions.items():
        transit_point = PositionedPoint(
            chart="transit",
            body=transit_name,
            longitude=position.longitude,
        )
        for natal_name, natal_longitude in natal_points.items():
            natal_point = PositionedPoint(
                chart="natal",
                body=_configured_body_name(natal_name, config),
                longitude=natal_longitude,
            )
            current = find_aspects([transit_point], [natal_point], config)
            if not current:
                continue

            hit = current[0]
            aspect = hit.aspect_type
            aspect_angle = hit.exact_angle
            orb = hit.orb
            next_longitude, _, _, _ = _body_longitude_speed(
                moment_utc + APPLYING_DT,
                position.swe_id,
                flags,
            )
            next_orb = _aspect_orb(next_longitude, natal_longitude, aspect_angle)
            exact_dates = _exact_dates_for_aspect(
                position.swe_id,
                natal_longitude,
                aspect_angle,
                window_start,
                window_end,
                flags,
                transit_name,
            )
            closest_approach = _closest_approach_for_aspect(
                position.swe_id,
                natal_longitude,
                aspect_angle,
                window_start,
                window_end,
                flags,
                transit_name,
            )

            aspects.append(
                TransitAspect(
                    from_point=TransitPointRef(body=transit_name),
                    to=NatalPointRef(body=hit.to_point.body),
                    aspect=aspect,
                    aspect_angle=aspect_angle,
                    orb=orb,
                    applying=next_orb < orb,
                    exact_dates=exact_dates,
                    closest_approach=closest_approach,
                )
            )

    return tuple(sorted(aspects, key=lambda item: (item.orb, item.from_point.body, item.to.body)))


def _calculate_stations(
    window_start: datetime,
    window_end: datetime,
    body_ids: Mapping[str, int],
    natal_points: Mapping[str, float],
    flags: int,
    station_aspect_orb: float,
) -> tuple[TransitStation, ...]:
    stations: list[TransitStation] = []

    for body_name, body_id in body_ids.items():
        for station_dt, before_speed, after_speed in _station_dates(
            body_id,
            window_start,
            window_end,
            flags,
            body_name,
        ):
            longitude, speed, _, _ = _body_longitude_speed(station_dt, body_id, flags)
            station_type: Literal["retrograde", "direct"]
            station_type = "retrograde" if before_speed > after_speed else "direct"
            station_aspects = _station_aspects(longitude, natal_points, station_aspect_orb)
            stations.append(
                TransitStation(
                    body=body_name,
                    type=station_type,
                    datetime_utc=station_dt,
                    longitude=longitude,
                    longitude_speed=speed,
                    zodiac=zodiac_position(longitude),
                    natal_aspects=station_aspects,
                )
            )

    return tuple(sorted(stations, key=lambda item: (item.datetime_utc, item.body)))


def _station_aspects(
    station_longitude: float,
    natal_points: Mapping[str, float],
    max_orb: float,
) -> tuple[StationAspect, ...]:
    aspects: list[StationAspect] = []
    config = AspectConfig.transit(max_orb=max_orb)
    station_point = PositionedPoint(
        chart="transit",
        body="station",
        longitude=station_longitude,
    )
    for natal_name, natal_longitude in natal_points.items():
        natal_point = PositionedPoint(
            chart="natal",
            body=_configured_body_name(natal_name, config),
            longitude=natal_longitude,
        )
        current = find_aspects([station_point], [natal_point], config)
        if not current:
            continue
        hit = current[0]
        aspects.append(
            StationAspect(
                to=NatalPointRef(body=hit.to_point.body),
                aspect=hit.aspect_type,
                aspect_angle=hit.exact_angle,
                orb=hit.orb,
            )
        )
    return tuple(sorted(aspects, key=lambda item: (item.orb, item.to.body)))


def _configured_body_name(name: str, config: AspectConfig) -> str:
    return config.point_aliases.get(name, name)


def _station_dates(
    body_id: int,
    start: datetime,
    end: datetime,
    flags: int,
    body_name: str,
) -> tuple[tuple[datetime, float, float], ...]:
    step = timedelta(days=1)
    samples = _sample_speed(body_id, start, end, flags, body_name, step)
    stations: list[tuple[datetime, float, float]] = []

    for (left_dt, left_speed), (right_dt, right_speed) in zip(samples, samples[1:]):
        if left_speed == 0.0:
            stations.append((left_dt, left_speed, right_speed))
            continue
        if left_speed * right_speed > 0.0:
            continue

        root = _bisect_speed_zero(body_id, left_dt, right_dt, flags)
        before = _body_longitude_speed(root - timedelta(hours=6), body_id, flags)[1]
        after = _body_longitude_speed(root + timedelta(hours=6), body_id, flags)[1]
        stations.append((root, before, after))

    return tuple(_dedupe_station_dates(stations))


def _sample_speed(
    body_id: int,
    start: datetime,
    end: datetime,
    flags: int,
    body_name: str,
    step: timedelta,
) -> list[tuple[datetime, float]]:
    samples: list[tuple[datetime, float]] = []
    current = start
    while current < end:
        try:
            speed = _body_longitude_speed(current, body_id, flags)[1]
        except RuntimeError as exc:
            raise RuntimeError(f"could not scan stations for {body_name!r}: {exc}") from exc
        samples.append((current, speed))
        current += step
    samples.append((end, _body_longitude_speed(end, body_id, flags)[1]))
    return samples


def _bisect_speed_zero(body_id: int, left: datetime, right: datetime, flags: int) -> datetime:
    left_speed = _body_longitude_speed(left, body_id, flags)[1]
    right_speed = _body_longitude_speed(right, body_id, flags)[1]

    for _ in range(48):
        midpoint = left + (right - left) / 2
        midpoint_speed = _body_longitude_speed(midpoint, body_id, flags)[1]
        if abs(midpoint_speed) < 1e-10:
            return midpoint
        if left_speed * midpoint_speed <= 0.0:
            right = midpoint
            right_speed = midpoint_speed
        else:
            left = midpoint
            left_speed = midpoint_speed

    _ = right_speed
    return left + (right - left) / 2


def _dedupe_station_dates(
    stations: list[tuple[datetime, float, float]],
) -> tuple[tuple[datetime, float, float], ...]:
    deduped: list[tuple[datetime, float, float]] = []
    for station in sorted(stations, key=lambda item: item[0]):
        if deduped and abs(station[0] - deduped[-1][0]) < ROOT_DEDUP_TOLERANCE:
            continue
        deduped.append(station)
    return tuple(deduped)


def _exact_dates_for_aspect(
    body_id: int,
    natal_longitude: float,
    aspect_angle: float,
    start: datetime,
    end: datetime,
    flags: int,
    body_name: str,
) -> tuple[datetime, ...]:
    roots: list[datetime] = []
    step = timedelta(days=_scan_step_days(body_name))
    targets = _aspect_targets(natal_longitude, aspect_angle)

    for target in targets:
        previous_dt = start
        previous_value = _signed_delta_at(previous_dt, body_id, target, flags)
        current_dt = start + step

        while current_dt <= end:
            current_value = _signed_delta_at(current_dt, body_id, target, flags)
            if abs(previous_value) <= ROOT_TOLERANCE_DEGREES:
                roots.append(previous_dt)
            if _has_root_between(previous_value, current_value):
                roots.append(
                    _bisect_signed_delta(
                        body_id,
                        target,
                        previous_dt,
                        current_dt,
                        previous_value,
                        current_value,
                        flags,
                    )
                )

            previous_dt = current_dt
            previous_value = current_value
            current_dt += step

        if previous_dt < end:
            current_value = _signed_delta_at(end, body_id, target, flags)
            if _has_root_between(previous_value, current_value):
                roots.append(
                    _bisect_signed_delta(
                        body_id,
                        target,
                        previous_dt,
                        end,
                        previous_value,
                        current_value,
                        flags,
                    )
                )

    return tuple(_dedupe_datetimes(roots))


def _closest_approach_for_aspect(
    body_id: int,
    natal_longitude: float,
    aspect_angle: float,
    start: datetime,
    end: datetime,
    flags: int,
    body_name: str,
) -> ExactApproach:
    step = timedelta(days=_scan_step_days(body_name))
    samples: list[tuple[datetime, float]] = []

    current = start
    while current < end:
        samples.append((current, _orb_at(current, body_id, natal_longitude, aspect_angle, flags)))
        current += step
    samples.append((end, _orb_at(end, body_id, natal_longitude, aspect_angle, flags)))

    best_dt, best_orb = min(samples, key=lambda item: item[1])
    for index in range(1, len(samples) - 1):
        previous_orb = samples[index - 1][1]
        current_orb = samples[index][1]
        next_orb = samples[index + 1][1]
        if current_orb <= previous_orb and current_orb <= next_orb:
            refined_dt, refined_orb = _golden_section_minimize_orb(
                body_id,
                natal_longitude,
                aspect_angle,
                samples[index - 1][0],
                samples[index + 1][0],
                flags,
            )
            if refined_orb < best_orb:
                best_dt, best_orb = refined_dt, refined_orb

    return ExactApproach(datetime_utc=best_dt, orb=best_orb)


def _golden_section_minimize_orb(
    body_id: int,
    natal_longitude: float,
    aspect_angle: float,
    left: datetime,
    right: datetime,
    flags: int,
) -> tuple[datetime, float]:
    left_seconds = left.timestamp()
    right_seconds = right.timestamp()
    inverse_phi = (sqrt(5.0) - 1.0) / 2.0

    c = right_seconds - inverse_phi * (right_seconds - left_seconds)
    d = left_seconds + inverse_phi * (right_seconds - left_seconds)
    c_value = _orb_at(_datetime_from_timestamp(c), body_id, natal_longitude, aspect_angle, flags)
    d_value = _orb_at(_datetime_from_timestamp(d), body_id, natal_longitude, aspect_angle, flags)

    for _ in range(48):
        if c_value < d_value:
            right_seconds = d
            d = c
            d_value = c_value
            c = right_seconds - inverse_phi * (right_seconds - left_seconds)
            c_value = _orb_at(
                _datetime_from_timestamp(c),
                body_id,
                natal_longitude,
                aspect_angle,
                flags,
            )
        else:
            left_seconds = c
            c = d
            c_value = d_value
            d = left_seconds + inverse_phi * (right_seconds - left_seconds)
            d_value = _orb_at(
                _datetime_from_timestamp(d),
                body_id,
                natal_longitude,
                aspect_angle,
                flags,
            )

    best_seconds = (left_seconds + right_seconds) / 2.0
    best_dt = _datetime_from_timestamp(best_seconds)
    return best_dt, _orb_at(best_dt, body_id, natal_longitude, aspect_angle, flags)


def _has_root_between(left: float, right: float) -> bool:
    if abs(left) <= ROOT_TOLERANCE_DEGREES or abs(right) <= ROOT_TOLERANCE_DEGREES:
        return True
    if abs(left - right) > HALF_CIRCLE:
        return False
    return left * right < 0.0


def _bisect_signed_delta(
    body_id: int,
    target_longitude: float,
    left: datetime,
    right: datetime,
    left_value: float,
    right_value: float,
    flags: int,
) -> datetime:
    for _ in range(48):
        midpoint = left + (right - left) / 2
        midpoint_value = _signed_delta_at(midpoint, body_id, target_longitude, flags)
        if abs(midpoint_value) <= ROOT_TOLERANCE_DEGREES:
            return midpoint
        if _has_root_between(left_value, midpoint_value):
            right = midpoint
            right_value = midpoint_value
        else:
            left = midpoint
            left_value = midpoint_value

    _ = right_value
    return left + (right - left) / 2


def _dedupe_datetimes(values: list[datetime]) -> list[datetime]:
    deduped: list[datetime] = []
    for value in sorted(values):
        if deduped and abs(value - deduped[-1]) <= ROOT_DEDUP_TOLERANCE:
            continue
        deduped.append(value)
    return deduped


def _natal_points(natal: NatalChart) -> dict[str, float]:
    if natal.bodies is None or natal.angles is None:
        raise ValueError("natal chart must include positions and houses for transit aspects")

    points = {name: body.longitude for name, body in natal.bodies.items()}
    for angle_name in NATAL_ANGLE_ASPECTS:
        angle = natal.angles.get(angle_name)
        if angle is not None:
            points[angle_name] = angle.longitude
    return points


def _aspect_orb(left_longitude: float, right_longitude: float, aspect_angle: float) -> float:
    return min(
        abs(_signed_delta(left_longitude, target))
        for target in _aspect_targets(right_longitude, aspect_angle)
    )


def _orb_at(
    dt: datetime,
    body_id: int,
    natal_longitude: float,
    aspect_angle: float,
    flags: int,
) -> float:
    longitude, _, _, _ = _body_longitude_speed(dt, body_id, flags)
    return _aspect_orb(longitude, natal_longitude, aspect_angle)


def _signed_delta_at(
    dt: datetime,
    body_id: int,
    target_longitude: float,
    flags: int,
) -> float:
    longitude, _, _, _ = _body_longitude_speed(dt, body_id, flags)
    return _signed_delta(longitude, target_longitude)


def _signed_delta(left_longitude: float, target_longitude: float) -> float:
    return ((left_longitude - target_longitude + 180.0) % 360.0) - 180.0


def _aspect_targets(natal_longitude: float, aspect_angle: float) -> tuple[float, ...]:
    if aspect_angle == 0.0:
        return (normalize_degrees(natal_longitude),)
    if aspect_angle == 180.0:
        return (normalize_degrees(natal_longitude + 180.0),)
    return (
        normalize_degrees(natal_longitude + aspect_angle),
        normalize_degrees(natal_longitude - aspect_angle),
    )


def _body_longitude_speed(
    dt: datetime,
    body_id: int,
    flags: int,
) -> tuple[float, float, int, str]:
    try:
        xx, retflags, warning = swe.calc_ut(julian_day_ut(dt), body_id, flags)
    except swe.Error as exc:
        raise RuntimeError(
            f"could not calculate transit body swe id {body_id} at {dt.isoformat()}: {exc}"
        ) from exc
    if len(xx) != 6:
        raise RuntimeError(f"swe.calc_ut returned {len(xx)} values, expected 6")
    if not retflags & swe.FLG_SPEED:
        raise RuntimeError("swe.calc_ut did not return speed values")
    return normalize_degrees(xx[0]), xx[3], retflags, warning


def _normalize_location(
    location: TransitLocation | Mapping[str, object] | Sequence[object],
) -> TransitLocation:
    if isinstance(location, TransitLocation):
        return location
    if isinstance(location, Mapping):
        return TransitLocation(**location)
    if len(location) == 2:
        latitude, longitude = location
        return TransitLocation(latitude=latitude, longitude=longitude)
    if len(location) == 3:
        latitude, longitude, house_system = location
        return TransitLocation(latitude=latitude, longitude=longitude, house_system=house_system)
    raise ValueError("location must be TransitLocation, mapping, (lat, lon), or (lat, lon, hsys)")


def _scan_step_days(body_name: str) -> float:
    if body_name == "moon":
        return 0.25
    if body_name in {"sun", "mercury", "venus", "mars"}:
        return 0.5
    return 2.0


def _add_months(value: datetime, months: int) -> datetime:
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    day = min(value.day, monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)


def _datetime_from_timestamp(value: float) -> datetime:
    return datetime.fromtimestamp(value, tz=timezone.utc)
