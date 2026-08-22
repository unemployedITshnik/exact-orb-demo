"""House placement checks."""

from __future__ import annotations

import pytest

from exact_orb.engine.charts.natal import get_natal
from exact_orb.engine.ephemeris.calc import house_for_longitude
from tests.fixtures.natal_1985 import BODY_IDS, EXPECTED_BODY_HOUSES, REFERENCE


def _chart_for_body(body_name: str):
    if body_name in BODY_IDS:
        return get_natal(
            REFERENCE["datetime_utc"],
            REFERENCE["latitude"],
            REFERENCE["longitude"],
            house_system=REFERENCE["house_system"],
            body_ids={body_name: BODY_IDS[body_name]},
        )
    return get_natal(
        REFERENCE["datetime_utc"],
        REFERENCE["latitude"],
        REFERENCE["longitude"],
        house_system=REFERENCE["house_system"],
    )


@pytest.mark.parametrize(
    "body_name,expected_house",
    EXPECTED_BODY_HOUSES.items(),
    ids=list(EXPECTED_BODY_HOUSES),
)
def test_body_house_matches_geocult_reference(body_name: str, expected_house: int) -> None:
    chart = _chart_for_body(body_name)

    assert chart.bodies[body_name].house == expected_house


def test_longitude_exactly_on_cusp_belongs_to_house_starting_at_that_cusp() -> None:
    chart = get_natal(
        REFERENCE["datetime_utc"],
        REFERENCE["latitude"],
        REFERENCE["longitude"],
        house_system=REFERENCE["house_system"],
        body_ids={"sun": BODY_IDS["sun"]},
    )

    second_cusp = chart.cusps[1].longitude

    assert house_for_longitude(second_cusp - 1e-8, chart.cusps) == 1
    assert house_for_longitude(second_cusp, chart.cusps) == 2
    assert house_for_longitude(second_cusp + 1e-8, chart.cusps) == 2
