"""Property-based invariants for natal chart calculations."""

from __future__ import annotations

from datetime import datetime, timezone

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from exact_orb.engine.charts.natal import get_natal
from exact_orb.engine.ephemeris.types import DEFAULT_BODY_IDS, HouseCusp
from tests.helpers import angular_delta_degrees


DATES = st.datetimes(
    min_value=datetime(1900, 1, 1, 0, 0, 0),
    max_value=datetime(2099, 12, 31, 23, 59, 59),
    timezones=st.just(timezone.utc),
)
LATITUDES = st.floats(min_value=-60.0, max_value=60.0, allow_nan=False, allow_infinity=False)
LONGITUDES = st.floats(min_value=-180.0, max_value=180.0, allow_nan=False, allow_infinity=False)
SHIFTS = st.floats(min_value=0.0, max_value=360.0, allow_nan=False, allow_infinity=False)

ASPECT_ANGLES = (0.0, 60.0, 90.0, 120.0, 150.0, 180.0)
ASPECT_ORB = 1.0


@settings(max_examples=25, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(dt=DATES, latitude=LATITUDES, longitude=LONGITUDES, shift=SHIFTS)
def test_natal_chart_invariants(dt, latitude: float, longitude: float, shift: float) -> None:
    chart = get_natal(dt, latitude, longitude, chart_kind="natal", body_ids=DEFAULT_BODY_IDS)

    assert abs(sum(_house_arcs(chart.cusps)) - 360.0) < 1e-7
    assert angular_delta_degrees(chart.cusps[6].longitude, chart.cusps[0].longitude + 180.0) < 1e-7
    assert angular_delta_degrees(chart.cusps[9].longitude, chart.cusps[3].longitude + 180.0) < 1e-7

    all_longitudes = [body.longitude for body in chart.bodies.values()]
    all_longitudes.extend(cusp.longitude for cusp in chart.cusps)
    all_longitudes.extend(angle.longitude for angle in chart.angles.values())
    assert all(0.0 <= longitude < 360.0 for longitude in all_longitudes)

    for body in chart.bodies.values():
        containing_houses = _houses_containing(body.longitude, chart.cusps)
        assert containing_houses == [body.house]

    point_longitudes = tuple(body.longitude for body in chart.bodies.values())
    shifted_longitudes = tuple((longitude + shift) % 360.0 for longitude in point_longitudes)
    assert _aspect_set(point_longitudes) == _aspect_set(shifted_longitudes)


def _house_arcs(cusps: tuple[HouseCusp, ...]) -> list[float]:
    return [
        (cusps[(index + 1) % 12].longitude - cusp.longitude) % 360.0
        for index, cusp in enumerate(cusps)
    ]


def _houses_containing(longitude: float, cusps: tuple[HouseCusp, ...]) -> list[int]:
    point = longitude % 360.0
    matches: list[int] = []
    for index, cusp in enumerate(cusps):
        next_cusp = cusps[(index + 1) % 12]
        span = (next_cusp.longitude - cusp.longitude) % 360.0
        if span == 0.0:
            span = 360.0

        distance = (point - cusp.longitude) % 360.0
        if distance == 0.0 or distance < span:
            matches.append(cusp.house)

    return matches


def _aspect_set(longitudes: tuple[float, ...]) -> frozenset[tuple[int, int, float]]:
    aspects: set[tuple[int, int, float]] = set()
    for left_index, left in enumerate(longitudes):
        for right_index in range(left_index + 1, len(longitudes)):
            distance = angular_delta_degrees(left, longitudes[right_index])
            for aspect_angle in ASPECT_ANGLES:
                if abs(distance - aspect_angle) <= ASPECT_ORB:
                    aspects.add((left_index, right_index, aspect_angle))
    return frozenset(aspects)
