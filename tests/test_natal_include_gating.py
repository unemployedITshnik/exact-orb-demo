"""Include gating tests for honest natal/cosmogram calculation."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from exact_orb.engine.charts.natal import calculate_natal
from tests.fixtures.natal_1985 import EXPECTED_BODY_LONGITUDES, REFERENCE


ANGLE_DERIVED_POINTS = {"asc", "mc", "dsc", "ic", "vertex", "pars", "pars_fortune"}


def _reference_natal():
    return calculate_natal(
        REFERENCE["datetime_utc"],
        REFERENCE["latitude"],
        REFERENCE["longitude"],
        chart_kind="natal",
        house_system=REFERENCE["house_system"],
    )


def _reference_cosmogram(include: set[str] | None = None):
    return calculate_natal(
        REFERENCE["datetime_utc"],
        REFERENCE["latitude"],
        REFERENCE["longitude"],
        chart_kind="cosmogram",
        house_system=REFERENCE["house_system"],
        include=include or {"positions"},
    )


def test_default_include_still_computes_houses() -> None:
    chart = _reference_natal()

    assert chart.chart_kind == "natal"
    assert chart.cusps is not None
    assert len(chart.cusps) == 12
    assert chart.angles is not None
    assert {"asc", "mc"}.issubset(chart.angles)
    assert chart.house_rulers is not None
    assert chart.interceptions is not None
    assert chart.strength is not None
    assert chart.warnings == ()


def test_default_include_matches_golden_sun() -> None:
    chart = _reference_natal()

    assert chart.bodies["sun"].longitude == pytest.approx(
        EXPECTED_BODY_LONGITUDES["sun"],
        abs=1e-3,
    )


def test_positions_only_drops_houses_and_angles() -> None:
    chart = _reference_cosmogram()

    assert chart.chart_kind == "cosmogram"
    assert chart.cusps is None
    assert chart.angles is None
    assert chart.house_rulers is None
    assert chart.interceptions is None


def test_positions_only_nulls_body_houses() -> None:
    chart = _reference_cosmogram()

    assert all(body.house is None for body in chart.bodies.values())


def test_positions_only_omits_pars_fortune() -> None:
    chart = _reference_cosmogram()

    assert "pars_fortune" not in chart.bodies
    assert "south_node" in chart.bodies
    assert "selena" in chart.bodies
    assert chart.bodies["south_node"].house is None
    assert chart.bodies["selena"].house is None


def test_positions_only_keeps_body_longitudes_intact() -> None:
    natal = _reference_natal()
    cosmogram = _reference_cosmogram()

    for name in set(natal.bodies) & set(cosmogram.bodies):
        assert cosmogram.bodies[name].longitude == pytest.approx(natal.bodies[name].longitude)


def test_cosmogram_include_has_no_angle_aspects() -> None:
    natal = _reference_natal()
    cosmogram = _reference_cosmogram({"positions", "aspects", "configurations"})

    assert any(
        {aspect.from_point.body, aspect.to_point.body} & ANGLE_DERIVED_POINTS
        for aspect in natal.aspects or ()
    )
    assert not any(
        {aspect.from_point.body, aspect.to_point.body} & ANGLE_DERIVED_POINTS
        for aspect in cosmogram.aspects or ()
    )


def test_cosmogram_is_stable_across_time_of_day() -> None:
    """Unknown birth time must not leak through house-derived values."""

    morning = calculate_natal(
        datetime(1985, 9, 1, 9, 0, tzinfo=timezone.utc),
        REFERENCE["latitude"],
        REFERENCE["longitude"],
        chart_kind="cosmogram",
        house_system=REFERENCE["house_system"],
        include={"positions", "aspects", "configurations"},
    )
    evening = calculate_natal(
        datetime(1985, 9, 1, 20, 45, tzinfo=timezone.utc),
        REFERENCE["latitude"],
        REFERENCE["longitude"],
        chart_kind="cosmogram",
        house_system=REFERENCE["house_system"],
        include={"positions", "aspects", "configurations"},
    )

    assert all(body.house is None for body in morning.bodies.values())
    assert all(body.house is None for body in evening.bodies.values())
    assert "pars_fortune" not in morning.bodies
    assert "pars_fortune" not in evening.bodies


def test_rulers_without_houses_raises() -> None:
    with pytest.raises(ValueError, match=r"(?=.*rulers)(?=.*houses)"):
        calculate_natal(
            REFERENCE["datetime_utc"],
            REFERENCE["latitude"],
            REFERENCE["longitude"],
            chart_kind="cosmogram",
            house_system=REFERENCE["house_system"],
            include={"positions", "rulers"},
        )


def test_strength_without_houses_raises() -> None:
    with pytest.raises(ValueError, match=r"(?=.*strength)(?=.*houses)"):
        calculate_natal(
            REFERENCE["datetime_utc"],
            REFERENCE["latitude"],
            REFERENCE["longitude"],
            chart_kind="cosmogram",
            house_system=REFERENCE["house_system"],
            include={"positions", "strength"},
        )


def test_natal_chart_kind_requires_houses() -> None:
    with pytest.raises(ValueError, match=r"(?=.*chart_kind)(?=.*houses)"):
        calculate_natal(
            REFERENCE["datetime_utc"],
            REFERENCE["latitude"],
            REFERENCE["longitude"],
            chart_kind="natal",
            house_system=REFERENCE["house_system"],
            include={"positions"},
        )


def test_cosmogram_chart_kind_rejects_houses() -> None:
    with pytest.raises(ValueError, match=r"(?=.*chart_kind)(?=.*houses)"):
        calculate_natal(
            REFERENCE["datetime_utc"],
            REFERENCE["latitude"],
            REFERENCE["longitude"],
            chart_kind="cosmogram",
            house_system=REFERENCE["house_system"],
            include={"positions", "houses"},
        )


def test_cosmogram_default_include_omits_house_dependent_blocks() -> None:
    chart = calculate_natal(
        REFERENCE["datetime_utc"],
        REFERENCE["latitude"],
        REFERENCE["longitude"],
        chart_kind="cosmogram",
        house_system=REFERENCE["house_system"],
    )

    assert chart.chart_kind == "cosmogram"
    assert chart.bodies is not None
    assert chart.cusps is None
    assert chart.angles is None
    assert chart.house_rulers is None
    assert chart.interceptions is None
    assert chart.strength is None
    assert chart.aspects is not None
    assert chart.configurations is not None


def test_positions_only_works_beyond_polar_circle() -> None:
    chart = calculate_natal(
        REFERENCE["datetime_utc"],
        78.0,
        REFERENCE["longitude"],
        chart_kind="cosmogram",
        house_system=REFERENCE["house_system"],
        include={"positions"},
    )

    assert chart.bodies
    assert chart.cusps is None


def test_default_include_still_fails_beyond_polar_circle() -> None:
    with pytest.raises(ValueError, match="Placidus|latitude"):
        calculate_natal(
            REFERENCE["datetime_utc"],
            78.0,
            REFERENCE["longitude"],
            chart_kind="natal",
            house_system=REFERENCE["house_system"],
        )


def test_missing_houses_adds_warning() -> None:
    chart = _reference_cosmogram()

    assert len(chart.warnings) == 1
    assert chart.warnings[0].source == "include"
    assert "houses" in chart.warnings[0].message
