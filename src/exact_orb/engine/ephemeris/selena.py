"""Selena / White Moon calculation strategies.

Selena is not a physical body and is not provided by Swiss Ephemeris as a
single canonical object. exact-orb keeps the point behind a strategy interface
so project users can choose a tradition without changing calling code.
"""

from __future__ import annotations

import logging
from typing import Protocol

import swisseph as swe

from exact_orb.config import SelenaMethodName

from .calc import normalize_degrees, zodiac_position
from .types import BodyPosition


LOGGER = logging.getLogger(__name__)


class SelenaMethod(Protocol):
    """Common interface for replaceable Selena algorithms."""

    name: str

    def calculate(self, jd: float, flags: int) -> BodyPosition:
        """Calculate Selena for a Julian day UT."""


class MeanPerigeeSelena:
    """Mean-perigee Selena.

    Tradition: Moscow and Saint Petersburg schools, also part of Western
    European practice.

    Definition: Selena is the point opposite mean lunar apogee, i.e. the mean
    perigee of the lunar orbit. It uses ``swe.MEAN_APOG + 180°`` normalized to
    ``[0, 360)``. The period is the lunar apogee/perigee cycle, about
    8.85 years. Source: Swiss Ephemeris ``MEAN_APOG`` for the mean apogee,
    with the traditional opposition to obtain perigee.
    """

    name = "mean_perigee"
    apogee_id = swe.MEAN_APOG

    def calculate(self, jd: float, flags: int) -> BodyPosition:
        return _calculate_perigee_selena(jd, flags, self.name, self.apogee_id)


class TruePerigeeSelena:
    """True-perigee Selena.

    Tradition: lunar-orbit perigee Selena with the oscillating/true apogee.

    Definition: Selena is the point opposite osculating lunar apogee, i.e. the
    true perigee of the lunar orbit. It uses ``swe.OSCU_APOG + 180°``
    normalized to ``[0, 360)``. The mean period is the same lunar
    apogee/perigee cycle, about 8.85 years, with short-term oscillation from
    the osculating apogee. Source: Swiss Ephemeris ``OSCU_APOG`` for the true
    apogee, with the traditional opposition to obtain perigee.
    """

    name = "true_perigee"
    apogee_id = swe.OSCU_APOG

    def calculate(self, jd: float, flags: int) -> BodyPosition:
        return _calculate_perigee_selena(jd, flags, self.name, self.apogee_id)


SELENA_METHODS: dict[SelenaMethodName, SelenaMethod] = {
    "mean_perigee": MeanPerigeeSelena(),
    "true_perigee": TruePerigeeSelena(),
}


def get_selena_method(name: SelenaMethodName) -> SelenaMethod:
    """Return a Selena strategy by configured name."""

    return SELENA_METHODS[name]


def _calculate_perigee_selena(
    jd: float,
    flags: int,
    method_name: str,
    apogee_id: int,
) -> BodyPosition:
    xx, retflags, warning = swe.calc_ut(jd, apogee_id, flags)
    if len(xx) != 6:
        raise RuntimeError(f"swe.calc_ut returned {len(xx)} values for Selena apogee, expected 6")
    if warning:
        LOGGER.warning(
            "Swiss Ephemeris warning source=%s retflags=%s message=%s",
            "selena",
            retflags,
            warning,
        )
        raise RuntimeError(f"could not calculate Selena without ephemeris fallback: {warning}")

    longitude = normalize_degrees(xx[0] + 180.0)
    return BodyPosition(
        name="selena",
        chart="natal",
        source="selena",
        method=method_name,
        swe_id=apogee_id,
        longitude=longitude,
        latitude=xx[1],
        distance=xx[2],
        longitude_speed=xx[3],
        latitude_speed=xx[4],
        distance_speed=xx[5],
        retrograde=xx[3] < 0.0,
        house=None,
        zodiac=zodiac_position(longitude),
        retflags=retflags,
    )
