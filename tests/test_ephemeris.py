"""Reference checks for planetary positions and house cusps."""

from __future__ import annotations

import pytest

from exact_orb.engine.charts.natal import get_natal
from tests.fixtures.natal_1985 import (
    ARCSECOND_DEGREES,
    BODY_IDS,
    EXPECTED_ANGLE_LONGITUDES,
    EXPECTED_BODY_LONGITUDES,
    EXPECTED_BODY_TOLERANCES,
    EXPECTED_CUSPS,
    EXPECTED_DERIVED_LONGITUDES,
    REFERENCE,
)
from tests.helpers import assert_longitude_close


def _chart_for_body(body_name: str):
    return get_natal(
        REFERENCE["datetime_utc"],
        REFERENCE["latitude"],
        REFERENCE["longitude"],
        chart_kind="natal",
        house_system=REFERENCE["house_system"],
        body_ids={body_name: BODY_IDS[body_name]},
    )


@pytest.mark.parametrize(
    "body_name,expected_longitude",
    EXPECTED_BODY_LONGITUDES.items(),
    ids=list(EXPECTED_BODY_LONGITUDES),
)
def test_body_longitude_matches_geocult_reference(body_name: str, expected_longitude: float) -> None:
    chart = _chart_for_body(body_name)

    assert_longitude_close(
        chart.bodies[body_name].longitude,
        expected_longitude,
        tolerance_degrees=EXPECTED_BODY_TOLERANCES.get(body_name, ARCSECOND_DEGREES),
        label=body_name,
    )


@pytest.mark.parametrize(
    "point_name,expected_longitude",
    EXPECTED_DERIVED_LONGITUDES.items(),
    ids=list(EXPECTED_DERIVED_LONGITUDES),
)
def test_derived_point_longitude_matches_reference(
    point_name: str,
    expected_longitude: float,
) -> None:
    chart = get_natal(
        REFERENCE["datetime_utc"],
        REFERENCE["latitude"],
        REFERENCE["longitude"],
        chart_kind="natal",
        house_system=REFERENCE["house_system"],
    )

    assert_longitude_close(
        chart.bodies[point_name].longitude,
        expected_longitude,
        tolerance_degrees=ARCSECOND_DEGREES,
        label=point_name,
    )


@pytest.mark.parametrize(
    "angle_name,expected_longitude",
    EXPECTED_ANGLE_LONGITUDES.items(),
    ids=list(EXPECTED_ANGLE_LONGITUDES),
)
def test_angle_longitude_matches_reference(angle_name: str, expected_longitude: float) -> None:
    chart = get_natal(
        REFERENCE["datetime_utc"],
        REFERENCE["latitude"],
        REFERENCE["longitude"],
        chart_kind="natal",
        house_system=REFERENCE["house_system"],
    )

    assert_longitude_close(
        chart.angles[angle_name].longitude,
        expected_longitude,
        tolerance_degrees=ARCSECOND_DEGREES,
        label=angle_name,
    )


@pytest.mark.parametrize(
    "house,expected_longitude",
    EXPECTED_CUSPS.items(),
    ids=[f"house_{house}" for house in EXPECTED_CUSPS],
)
def test_house_cusp_longitude_matches_geocult_reference(
    house: int,
    expected_longitude: float,
) -> None:
    chart = get_natal(
        REFERENCE["datetime_utc"],
        REFERENCE["latitude"],
        REFERENCE["longitude"],
        chart_kind="natal",
        house_system=REFERENCE["house_system"],
        body_ids={"sun": BODY_IDS["sun"]},
    )

    assert_longitude_close(
        chart.cusps[house - 1].longitude,
        expected_longitude,
        tolerance_degrees=ARCSECOND_DEGREES,
        label=f"house {house}",
    )
