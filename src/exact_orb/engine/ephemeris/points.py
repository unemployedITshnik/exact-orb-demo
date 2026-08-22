"""Derived deterministic points for natal charts."""

from __future__ import annotations

from .calc import house_for_longitude, normalize_degrees
from .types import FULL_CIRCLE, HouseCusp


def is_diurnal(sun_longitude: float, cusps: tuple[HouseCusp, ...]) -> bool:
    """Return True when the Sun is above the ASC/DSC horizon.

    In house terms this means the Sun is in houses 7 through 12. The function
    uses the calculated house placement, not a clock-time shortcut.
    """

    return house_for_longitude(sun_longitude, cusps) in {7, 8, 9, 10, 11, 12}


def part_of_fortune(
    ascendant_longitude: float,
    sun_longitude: float,
    moon_longitude: float,
    cusps: tuple[HouseCusp, ...],
) -> float:
    """Calculate Pars Fortunae.

    Day formula: ASC + Moon - Sun.
    Night formula: ASC + Sun - Moon.
    """

    if is_diurnal(sun_longitude, cusps):
        return normalize_degrees(ascendant_longitude + moon_longitude - sun_longitude)
    return normalize_degrees(ascendant_longitude + sun_longitude - moon_longitude)


def opposite_point(longitude: float) -> float:
    """Return the point exactly opposite a longitude."""

    return normalize_degrees(longitude + FULL_CIRCLE / 2.0)
