"""Selena strategy tests."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
import swisseph as swe

from exact_orb.engine.charts.natal import calculate_natal
from exact_orb.engine.ephemeris.runtime import ephemeris_session
from exact_orb.engine.ephemeris.selena import SELENA_METHODS, get_selena_method
from tests.fixtures.natal_1985 import BODY_IDS, REFERENCE
from tests.fixtures.selena_1985 import DATETIME_UTC, EXPECTED_SELENA, JULIAN_DAY_UT
from tests.helpers import angular_delta_degrees, assert_longitude_close


FLAGS = swe.FLG_SWIEPH | swe.FLG_SPEED


@pytest.mark.parametrize("method_name", EXPECTED_SELENA, ids=list(EXPECTED_SELENA))
def test_selena_strategy_is_reproducible(method_name: str) -> None:
    method = get_selena_method(method_name)

    with ephemeris_session():
        first = method.calculate(JULIAN_DAY_UT, FLAGS)
        second = method.calculate(JULIAN_DAY_UT, FLAGS)

    assert first.longitude == second.longitude
    assert first.longitude_speed == second.longitude_speed
    assert first.retrograde is second.retrograde


@pytest.mark.parametrize(
    "method_name,expected_longitude",
    EXPECTED_SELENA.items(),
    ids=list(EXPECTED_SELENA),
)
def test_selena_strategy_matches_golden_value(method_name: str, expected_longitude: float) -> None:
    method = get_selena_method(method_name)

    with ephemeris_session():
        result = method.calculate(JULIAN_DAY_UT, FLAGS)

    assert_longitude_close(
        result.longitude,
        expected_longitude,
        tolerance_degrees=1e-9,
        label=method_name,
    )


def test_mean_perigee_is_exactly_opposite_lilith() -> None:
    chart = calculate_natal(
        DATETIME_UTC,
        REFERENCE["latitude"],
        REFERENCE["longitude"],
        chart_kind="natal",
        house_system=REFERENCE["house_system"],
        selena_method="mean_perigee",
    )

    assert angular_delta_degrees(
        chart.bodies["selena"].longitude,
        chart.bodies["mean_apog"].longitude + 180.0,
    ) <= 1e-9


@pytest.mark.parametrize("method_name", EXPECTED_SELENA, ids=list(EXPECTED_SELENA))
def test_selena_result_is_normalized(method_name: str) -> None:
    method = get_selena_method(method_name)

    with ephemeris_session():
        result = method.calculate(JULIAN_DAY_UT, FLAGS)

    assert 0.0 <= result.longitude < 360.0


def test_selena_strategy_switch_changes_result() -> None:
    mean_chart = calculate_natal(
        DATETIME_UTC,
        REFERENCE["latitude"],
        REFERENCE["longitude"],
        chart_kind="natal",
        house_system=REFERENCE["house_system"],
        selena_method="mean_perigee",
    )
    true_chart = calculate_natal(
        DATETIME_UTC,
        REFERENCE["latitude"],
        REFERENCE["longitude"],
        chart_kind="natal",
        house_system=REFERENCE["house_system"],
        selena_method="true_perigee",
    )

    assert mean_chart.selena_method == "mean_perigee"
    assert true_chart.selena_method == "true_perigee"
    assert mean_chart.bodies["selena"].longitude != true_chart.bodies["selena"].longitude


def test_project_config_selects_true_perigee() -> None:
    chart = calculate_natal(
        datetime(1985, 9, 1, 20, 45, tzinfo=timezone.utc),
        REFERENCE["latitude"],
        REFERENCE["longitude"],
        chart_kind="natal",
        house_system=REFERENCE["house_system"],
    )

    assert chart.selena_method == "true_perigee"
    assert chart.bodies["selena"].longitude == pytest.approx(EXPECTED_SELENA["true_perigee"])
