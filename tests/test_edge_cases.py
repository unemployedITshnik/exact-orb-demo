"""Edge-case behavior for natal chart calculations."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from exact_orb.engine.charts import natal as natal_module
from exact_orb.engine.charts.natal import calculate_natal
from exact_orb.engine.ephemeris.calc import (
    house_for_longitude,
    normalize_degrees,
    zodiac_position,
)
from exact_orb.engine.ephemeris.types import (
    HouseCusp,
)
from tests.fixtures.natal_1985 import BODY_IDS, EXPECTED_RETROGRADE, REFERENCE


def test_high_latitude_placidus_raises_clear_error() -> None:
    with pytest.raises(ValueError, match="Placidus degenerates"):
        calculate_natal(
            datetime(1985, 9, 1, 20, 45, tzinfo=timezone.utc),
            67.0,
            37.6155,
            chart_kind="natal",
            house_system="P",
            body_ids={"sun": BODY_IDS["sun"]},
        )


@pytest.mark.parametrize("house_system", ("K", "Z", "?"))
def test_unsupported_natal_house_system_is_rejected_before_house_calculation(
    house_system: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    house_calls = 0

    def unexpected_house_calculation(*args: object, **kwargs: object) -> None:
        nonlocal house_calls
        house_calls += 1
        raise AssertionError("calculate_houses must not be called")

    monkeypatch.setattr(natal_module, "calculate_houses", unexpected_house_calculation)

    with pytest.raises(ValueError, match="Placidus"):
        calculate_natal(
            REFERENCE["datetime_utc"],
            REFERENCE["latitude"],
            REFERENCE["longitude"],
            chart_kind="natal",
            house_system=house_system,
            body_ids={"sun": BODY_IDS["sun"]},
        )

    assert house_calls == 0


def test_planet_exactly_on_cusp_uses_next_house() -> None:
    cusps = _equal_house_cusps(start=350.0)

    assert house_for_longitude(349.999999, cusps) == 12
    assert house_for_longitude(350.0, cusps) == 1
    assert house_for_longitude(0.0, cusps) == 1
    assert house_for_longitude(19.999999, cusps) == 1
    assert house_for_longitude(20.0, cusps) == 2


def test_longitude_normalization_near_zero_and_full_circle() -> None:
    assert normalize_degrees(360.0) == 0.0
    assert normalize_degrees(-0.000001) == pytest.approx(359.999999)
    assert zodiac_position(359.9999999).longitude < 360.0
    assert zodiac_position(359.9999999).sign == "Aries"
    assert zodiac_position(0.000001).sign == "Aries"


@pytest.mark.parametrize(
    "body_name,expected_retrograde",
    EXPECTED_RETROGRADE.items(),
    ids=list(EXPECTED_RETROGRADE),
)
def test_retrograde_flag_matches_reference(body_name: str, expected_retrograde: bool) -> None:
    chart = calculate_natal(
        REFERENCE["datetime_utc"],
        REFERENCE["latitude"],
        REFERENCE["longitude"],
        chart_kind="natal",
        house_system=REFERENCE["house_system"],
        body_ids={body_name: BODY_IDS[body_name]},
    )

    body = chart.bodies[body_name]

    assert body.retrograde is expected_retrograde
    assert (body.longitude_speed < 0.0) is expected_retrograde


def _equal_house_cusps(start: float) -> tuple[HouseCusp, ...]:
    return tuple(
        HouseCusp(
            house=house,
            longitude=(start + (house - 1) * 30.0) % 360.0,
            zodiac=zodiac_position(start + (house - 1) * 30.0),
        )
        for house in range(1, 13)
    )
