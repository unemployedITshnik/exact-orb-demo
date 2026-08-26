"""Low-level deterministic ephemeris calculation primitives."""

from __future__ import annotations

from datetime import datetime, timezone
import logging
from math import isfinite
from typing import Mapping

from exact_orb import swiss_backend

from .runtime import require_ephemeris_session
from .types import (
    ANGLE_INDICES,
    COMBINED_RULERS,
    EPSILON,
    FULL_CIRCLE,
    MODERN_RULERS,
    SECONDS_PER_SIGN,
    TRADITIONAL_RULERS,
    ZODIAC_SIGNS,
    AnglePosition,
    BodyPosition,
    CalculationWarning,
    HouseCusp,
    RulershipScheme,
    ZodiacPosition,
)


LOGGER = logging.getLogger(__name__)


def validate_geography(latitude: float, longitude: float) -> None:
    if not isfinite(latitude) or not -90.0 <= latitude <= 90.0:
        raise ValueError("latitude must be a finite value in [-90, 90]")
    if not isfinite(longitude) or not -180.0 <= longitude <= 180.0:
        raise ValueError("longitude must be a finite value in [-180, 180]")


def normalize_house_system(house_system: str | bytes) -> bytes:
    if isinstance(house_system, str):
        house_system_bytes = house_system.encode("ascii")
    else:
        house_system_bytes = bytes(house_system)

    if len(house_system_bytes) != 1:
        raise ValueError("house_system must be a single ASCII character or one byte")
    return house_system_bytes


def to_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("birth_datetime must be timezone-aware; pass UTC explicitly")
    return value.astimezone(timezone.utc)


def julian_day_ut(value: datetime) -> float:
    require_ephemeris_session()
    hour = (
        value.hour
        + value.minute / 60.0
        + value.second / 3600.0
        + value.microsecond / 3_600_000_000.0
    )
    return swiss_backend.swe.julday(
        value.year,
        value.month,
        value.day,
        hour,
        swiss_backend.swe.GREG_CAL,
    )


def calculate_bodies(
    julian_day_ut: float,
    body_ids: Mapping[str, int],
    flags: int,
    cusps: tuple[HouseCusp, ...] | None,
) -> tuple[dict[str, BodyPosition], list[CalculationWarning]]:
    bodies: dict[str, BodyPosition] = {}
    warnings: list[CalculationWarning] = []

    for name, body_id in body_ids.items():
        require_ephemeris_session()
        try:
            xx, retflags, warning = swiss_backend.swe.calc_ut(julian_day_ut, body_id, flags)
        except swiss_backend.swe.Error as exc:
            raise RuntimeError(
                f"could not calculate body {name!r} (swe id {body_id}) "
                f"for Julian day UT {julian_day_ut}: {exc}"
            ) from exc
        if len(xx) != 6:
            raise RuntimeError(f"swe.calc_ut returned {len(xx)} values for {name}, expected 6")
        if not retflags & swiss_backend.swe.FLG_SPEED:
            raise RuntimeError(f"swe.calc_ut did not return speed values for {name}")

        longitude_value = normalize_degrees(xx[0])
        if warning:
            warnings.append(CalculationWarning(source=name, message=warning, retflags=retflags))
            LOGGER.warning(
                "Swiss Ephemeris warning source=%s retflags=%s message=%s",
                name,
                retflags,
                warning,
            )

        bodies[name] = BodyPosition(
            name=name,
            chart="natal",
            source="swisseph",
            swe_id=body_id,
            longitude=longitude_value,
            latitude=xx[1],
            distance=xx[2],
            longitude_speed=xx[3],
            latitude_speed=xx[4],
            distance_speed=xx[5],
            retrograde=xx[3] < 0.0,
            house=house_for_longitude(longitude_value, cusps) if cusps is not None else None,
            zodiac=zodiac_position(longitude_value),
            retflags=retflags,
        )
        LOGGER.debug(
            "body_calculated name=%s swe_id=%s longitude=%.6f latitude=%.6f speed=%.6f house=%s retflags=%s",
            name,
            body_id,
            longitude_value,
            xx[1],
            xx[3],
            bodies[name].house,
            retflags,
        )

    return bodies, warnings


def calculate_houses(
    julian_day_ut: float,
    latitude: float,
    longitude: float,
    house_system: bytes,
) -> tuple[tuple[HouseCusp, ...], dict[str, AnglePosition]]:
    require_ephemeris_session()
    try:
        raw_cusps, raw_ascmc = swiss_backend.swe.houses_ex(
            julian_day_ut,
            latitude,
            longitude,
            house_system,
            0,
        )
    except swiss_backend.swe.Error as exc:
        house_system_name = house_system.decode("ascii", errors="replace")
        raise ValueError(
            f"could not calculate house cusps for system {house_system_name!r} "
            f"at latitude {latitude}; this can happen when Placidus degenerates "
            "at high latitudes"
        ) from exc

    if len(raw_cusps) != 13:
        raise ValueError("only 12-house systems with a 13-item cusps tuple are supported")
    if len(raw_ascmc) != 8:
        raise RuntimeError(f"swe.houses_ex returned {len(raw_ascmc)} ascmc values, expected 8")

    cusps = tuple(
        HouseCusp(
            house=house,
            longitude=normalize_degrees(raw_cusps[house]),
            zodiac=zodiac_position(raw_cusps[house]),
        )
        for house in range(1, 13)
    )

    # ASC and MC are intentionally taken from ascmc, not cusps[1] and cusps[10].
    angles = {
        name: AnglePosition(
            name=name,
            longitude=normalize_degrees(raw_ascmc[index]),
            zodiac=zodiac_position(raw_ascmc[index]),
        )
        for name, index in ANGLE_INDICES
    }
    angles["dsc"] = AnglePosition(
        name="dsc",
        longitude=normalize_degrees(angles["asc"].longitude + 180.0),
        zodiac=zodiac_position(angles["asc"].longitude + 180.0),
    )
    angles["ic"] = AnglePosition(
        name="ic",
        longitude=normalize_degrees(angles["mc"].longitude + 180.0),
        zodiac=zodiac_position(angles["mc"].longitude + 180.0),
    )

    return cusps, angles


def rulers_for_sign(sign_index: int, scheme: RulershipScheme) -> tuple[str, ...]:
    if scheme is RulershipScheme.COMBINED:
        rulers = COMBINED_RULERS
    elif scheme is RulershipScheme.MODERN:
        rulers = MODERN_RULERS
    else:
        rulers = TRADITIONAL_RULERS
    return rulers[sign_index]


def house_for_longitude(longitude: float, cusps: tuple[HouseCusp, ...]) -> int:
    point = normalize_degrees(longitude)
    for index, cusp in enumerate(cusps):
        next_cusp = cusps[(index + 1) % len(cusps)]
        span = (normalize_degrees(next_cusp.longitude) - cusp.longitude) % FULL_CIRCLE
        if span <= EPSILON:
            span = FULL_CIRCLE

        distance_from_cusp = (point - cusp.longitude) % FULL_CIRCLE
        if distance_from_cusp < span - EPSILON or distance_from_cusp <= EPSILON:
            return cusp.house

    raise RuntimeError(f"could not assign longitude {longitude} to a house")


def zodiac_position(longitude: float) -> ZodiacPosition:
    normalized = normalize_degrees(longitude)

    sign_index = int(normalized // 30.0)
    exact_degree_in_sign = normalized - sign_index * 30.0
    seconds_in_sign = int(round(exact_degree_in_sign * 3600.0))
    if seconds_in_sign >= SECONDS_PER_SIGN:
        sign_index = (sign_index + 1) % 12
        seconds_in_sign = 0
        exact_degree_in_sign = 0.0

    degree = seconds_in_sign // 3600
    minute = (seconds_in_sign % 3600) // 60
    second = seconds_in_sign % 60

    return ZodiacPosition(
        longitude=normalized,
        sign_index=sign_index,
        sign=ZODIAC_SIGNS[sign_index],
        degree_in_sign=exact_degree_in_sign,
        degree=degree,
        minute=minute,
        second=second,
    )


def normalize_degrees(value: float) -> float:
    if not isfinite(value):
        raise ValueError("longitude values must be finite")
    normalized = value % FULL_CIRCLE
    return normalized
