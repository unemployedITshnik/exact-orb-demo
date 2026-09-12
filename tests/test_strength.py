"""Natal strength and structure tests."""

from __future__ import annotations

from datetime import datetime, timezone

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
import pytest
from pydantic import ValidationError

from exact_orb.engine.charts.natal import calculate_natal
from exact_orb.engine.strength import StrengthConfig
from exact_orb.engine.strength.dispositors import calculate_dispositor_chains
from exact_orb.engine.strength.lunar_phase import PHASE_NAMES, calculate_lunar_phase
from tests.fixtures.natal_1985 import REFERENCE


PLANETS = (
    "sun",
    "moon",
    "mercury",
    "venus",
    "mars",
    "jupiter",
    "saturn",
    "uranus",
    "neptune",
    "pluto",
)


def test_essential_dignities_traditional_and_modern() -> None:
    traditional = _reference_chart(StrengthConfig(dignity_system="traditional"))
    modern = _reference_chart(StrengthConfig(dignity_system="modern"))

    assert {
        body: traditional.strength.planets[body].dignity.status.value
        for body in PLANETS
    } == {body: "peregrine" for body in PLANETS}

    assert modern.strength.planets["pluto"].dignity.status.value == "domicile"
    assert modern.strength.planets["pluto"].essential_score == 5
    for body in PLANETS:
        if body != "pluto":
            assert modern.strength.planets[body].dignity.status.value == "peregrine"


def test_accidental_strength_matches_reference() -> None:
    chart = _reference_chart(StrengthConfig(dignity_system="modern"))
    expected = {
        "sun": ("angular", 4, (), 4, "moderate"),
        "moon": ("succedent", 2, (), 2, "moderate"),
        "mercury": ("cadent", 0, (), 0, "moderate"),
        "venus": ("succedent", 2, (), 2, "moderate"),
        "mars": ("cadent", 0, (), 0, "moderate"),
        "jupiter": ("cadent", 0, ("retrograde",), -2, "weak"),
        "saturn": ("cadent", 0, (), 0, "moderate"),
        "uranus": ("cadent", 0, (), 0, "moderate"),
        "neptune": ("cadent", 0, ("retrograde",), -2, "weak"),
        "pluto": ("succedent", 2, (), 7, "strong"),
    }

    for body, (house_type, house_score, modifiers, total, category) in expected.items():
        item = chart.strength.planets[body]
        assert item.accidental.house_type.value == house_type
        assert item.accidental.house_score == house_score
        assert tuple(modifier.name for modifier in item.accidental.modifiers) == modifiers
        assert item.total == total
        assert item.category.value == category


def test_modern_dispositor_chains_and_mutual_receptions_match_reference() -> None:
    chart = _reference_chart(StrengthConfig(dignity_system="modern"))
    chains = chart.strength.dispositors

    assert chart.strength.dignity_system == "modern"
    assert chart.strength.dispositor_system == "modern"
    assert chains["moon"].chain == ("moon", "mars", "sun", "mercury", "sun")
    assert chains["moon"].steps_to_cycle == 2
    assert set(chains["moon"].cycle) == {"sun", "mercury"}
    assert chains["uranus"].chain == ("uranus", "jupiter", "uranus")
    assert chains["pluto"].chain == ("pluto", "pluto")
    assert chains["pluto"].steps_to_cycle == 0
    assert chains["pluto"].cycle == ("pluto",)
    assert {(item.body_1, item.body_2) for item in chart.strength.mutual_receptions} == {
        ("sun", "mercury"),
        ("jupiter", "uranus"),
    }


def test_traditional_dispositor_chains_preserve_reference_method() -> None:
    chart = _reference_chart(StrengthConfig(dignity_system="traditional"))
    chains = chart.strength.dispositors

    assert chart.strength.dignity_system == "traditional"
    assert chart.strength.dispositor_system == "traditional"
    assert chains["uranus"].chain == (
        "uranus",
        "jupiter",
        "saturn",
        "mars",
        "sun",
        "mercury",
        "sun",
    )
    assert chains["pluto"].chain == ("pluto", "mars", "sun", "mercury", "sun")
    assert {(item.body_1, item.body_2) for item in chart.strength.mutual_receptions} == {
        ("sun", "mercury")
    }


def test_modern_and_traditional_standard_rulers_differ_for_outer_signs() -> None:
    body_signs = {
        "scorpio_body": 7,
        "aquarius_body": 10,
        "pisces_body": 11,
        "mars": 0,
        "saturn": 9,
        "jupiter": 8,
        "pluto": 7,
        "uranus": 10,
        "neptune": 11,
    }

    traditional, _ = calculate_dispositor_chains(
        body_signs,
        bodies=("scorpio_body", "aquarius_body", "pisces_body"),
        system="traditional",
    )
    modern, _ = calculate_dispositor_chains(
        body_signs,
        bodies=("scorpio_body", "aquarius_body", "pisces_body"),
        system="modern",
    )

    assert tuple(chain.chain[1] for chain in traditional.values()) == (
        "mars",
        "saturn",
        "jupiter",
    )
    assert tuple(chain.chain[1] for chain in modern.values()) == (
        "pluto",
        "uranus",
        "neptune",
    )


def test_dispositor_cycle_length_three_is_finite() -> None:
    body_signs = {"a": 0, "b": 1, "c": 2}
    chains, receptions = calculate_dispositor_chains(
        body_signs,
        bodies=("a", "b", "c"),
        ruler_map={0: "b", 1: "c", 2: "a"},
    )

    assert chains["a"].chain == ("a", "b", "c", "a")
    assert chains["a"].cycle == ("a", "b", "c")
    assert receptions == ()


def test_custom_dispositor_cycle_length_one_is_finite() -> None:
    chains, receptions = calculate_dispositor_chains(
        {"a": 0},
        bodies=("a",),
        ruler_map={0: "a"},
    )

    assert chains["a"].chain == ("a", "a")
    assert chains["a"].cycle == ("a",)
    assert receptions == ()


def test_custom_dispositor_cycle_length_two_is_mutual_reception() -> None:
    chains, receptions = calculate_dispositor_chains(
        {"a": 0, "b": 1},
        bodies=("a", "b"),
        ruler_map={0: "b", 1: "a"},
    )

    assert chains["a"].chain == ("a", "b", "a")
    assert chains["a"].cycle == ("a", "b")
    assert tuple((item.body_1, item.body_2) for item in receptions) == (("a", "b"),)


def test_custom_dispositor_missing_endpoint_becomes_terminal_cycle() -> None:
    chains, receptions = calculate_dispositor_chains(
        {"a": 0},
        bodies=("a",),
        ruler_map={0: "missing"},
    )

    assert chains["a"].chain == ("a", "missing")
    assert chains["a"].steps_to_cycle == 1
    assert chains["a"].cycle == ("missing",)
    assert receptions == ()


@pytest.mark.parametrize("system", ("combined", "unknown"))
def test_dispositor_chains_reject_non_linear_standard_system(system: str) -> None:
    with pytest.raises(ValueError, match="traditional.*modern"):
        calculate_dispositor_chains(
            {"sun": 4},
            bodies=("sun",),
            system=system,  # type: ignore[arg-type]
        )


def test_dispositor_chains_require_exactly_one_explicit_ruler_source() -> None:
    with pytest.raises(ValueError, match="requires system or ruler_map"):
        calculate_dispositor_chains({"sun": 4}, bodies=("sun",))

    with pytest.raises(ValueError, match="either system or ruler_map"):
        calculate_dispositor_chains(
            {"sun": 4},
            bodies=("sun",),
            system="modern",
            ruler_map={4: "sun"},
        )


def test_natal_strength_rejects_mismatched_dignity_and_dispositor_systems() -> None:
    strength = _reference_chart(StrengthConfig()).strength
    payload = strength.model_dump(mode="python")
    payload["dispositor_system"] = "traditional"

    with pytest.raises(
        ValidationError,
        match="dispositor_system must equal dignity_system",
    ):
        type(strength).model_validate(payload)


def test_1990_control_chart_uses_one_system_for_dignities_and_dispositors() -> None:
    calculation_args = (
        datetime(1990, 9, 2, 10, 30, tzinfo=timezone.utc),
        55.7558,
        37.6173,
    )
    traditional = calculate_natal(
        *calculation_args,
        chart_kind="natal",
        strength_config=StrengthConfig(dignity_system="traditional"),
    )
    modern = calculate_natal(
        *calculation_args,
        chart_kind="natal",
        strength_config=StrengthConfig(dignity_system="modern"),
    )

    assert traditional.strength.dispositors["moon"].chain == (
        "moon",
        "saturn",
        "saturn",
    )
    assert modern.strength.dispositors["moon"].chain == (
        "moon",
        "uranus",
        "saturn",
        "saturn",
    )
    assert traditional.strength.dispositors["pluto"].chain == (
        "pluto",
        "mars",
        "mercury",
        "mercury",
    )
    assert modern.strength.planets["pluto"].dignity.status.value == "domicile"
    assert modern.strength.dispositors["pluto"].chain == ("pluto", "pluto")
    assert _final_dispositors(traditional) == {"mercury", "saturn"}
    assert _final_dispositors(modern) == {"mercury", "saturn", "pluto"}


def test_house_rulership_scheme_does_not_change_modern_strength_system() -> None:
    combined_houses = _reference_chart(StrengthConfig(dignity_system="modern"))
    modern_houses = calculate_natal(
        REFERENCE["datetime_utc"],
        REFERENCE["latitude"],
        REFERENCE["longitude"],
        chart_kind="natal",
        house_system=REFERENCE["house_system"],
        rulership="modern",
        strength_config=StrengthConfig(dignity_system="modern"),
    )

    assert combined_houses.house_rulers != modern_houses.house_rulers
    assert combined_houses.strength.dispositors == modern_houses.strength.dispositors
    assert combined_houses.strength.dispositor_system == "modern"


def test_balance_matches_reference_and_fixed_modality_dominates() -> None:
    chart = _reference_chart(StrengthConfig())
    balance = chart.strength.balance

    assert {name: bucket.score for name, bucket in balance.elements.items()} == {
        "fire": 10.0,
        "earth": 4.0,
        "air": 5.0,
        "water": 6.0,
    }
    assert {name: bucket.score for name, bucket in balance.modalities.items()} == {
        "cardinal": 7.0,
        "fixed": 14.0,
        "mutable": 4.0,
    }
    assert balance.dominant_modalities == ("fixed",)


def test_lunar_phase_matches_reference() -> None:
    chart = _reference_chart(StrengthConfig())
    phase = chart.strength.lunar_phase

    assert phase.elongation == pytest.approx(208.324, abs=0.001)
    assert phase.phase_name == "полнолуние"
    assert phase.phase_number == 5
    assert phase.degrees_after_exact_opposition == pytest.approx(28.324, abs=0.001)


def test_special_degree_flags_match_reference_cases() -> None:
    chart = _reference_chart(StrengthConfig())
    flagged = {
        item.point: item
        for item in chart.strength.degree_flags
        if item.is_zero_degree or item.is_anaretic or item.is_critical
    }

    assert set(flagged) == {"neptune", "jupiter", "mercury", "saturn", "house_5", "house_11"}
    assert flagged["neptune"].is_zero_degree is True
    assert flagged["neptune"].is_critical is True
    assert flagged["jupiter"].is_critical is True
    assert flagged["mercury"].is_critical is True
    assert flagged["saturn"].is_critical is True
    assert flagged["house_5"].is_anaretic is True
    assert flagged["house_11"].is_anaretic is True


def test_interceptions_are_separated_for_json_consumers() -> None:
    chart = _reference_chart(StrengthConfig())
    summary = chart.strength.interceptions

    assert {(item.sign, item.house) for item in summary.intercepted} == {
        ("Libra", 5),
        ("Sagittarius", 6),
        ("Aries", 11),
        ("Gemini", 12),
    }
    assert {(item.sign, item.house) for item in summary.near_intercepted} == {
        ("Virgo", 4),
        ("Pisces", 10),
    }
    assert summary.near_intercepted[0].remaining_arc == pytest.approx(0.250715, abs=1e-6)
    payload = chart.model_dump(mode="json")
    assert payload["strength"]["interceptions"]["intercepted"]
    assert payload["strength"]["interceptions"]["near_intercepted"]


def test_include_without_strength_sets_block_to_none() -> None:
    chart = calculate_natal(
        REFERENCE["datetime_utc"],
        REFERENCE["latitude"],
        REFERENCE["longitude"],
        chart_kind="natal",
        house_system=REFERENCE["house_system"],
        include={"positions", "houses", "rulers", "aspects", "configurations"},
    )

    assert chart.strength is None
    assert chart.aspects is not None
    assert chart.configurations is not None


@settings(max_examples=20, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    dt=st.datetimes(
        min_value=datetime(1900, 1, 1),
        max_value=datetime(2099, 12, 31, 23, 59, 59),
        timezones=st.just(timezone.utc),
    ),
    latitude=st.floats(min_value=-60.0, max_value=60.0, allow_nan=False, allow_infinity=False),
    longitude=st.floats(min_value=-180.0, max_value=180.0, allow_nan=False, allow_infinity=False),
)
def test_property_balance_element_and_modality_totals_match(
    dt,
    latitude: float,
    longitude: float,
) -> None:
    chart = calculate_natal(dt, latitude, longitude, chart_kind="natal", strength_config=StrengthConfig())
    balance = chart.strength.balance

    assert sum(item.score for item in balance.elements.values()) == pytest.approx(
        sum(item.score for item in balance.modalities.values())
    )


@settings(max_examples=30, deadline=None)
@given(
    sun=st.floats(min_value=0.0, max_value=360.0, allow_nan=False, allow_infinity=False),
    moon=st.floats(min_value=0.0, max_value=360.0, allow_nan=False, allow_infinity=False),
)
def test_property_lunar_phase_is_always_normalized(sun: float, moon: float) -> None:
    phase = calculate_lunar_phase(sun, moon)

    assert 0.0 <= phase.elongation < 360.0
    assert phase.phase_name in PHASE_NAMES


@settings(max_examples=30, deadline=None)
@given(
    signs=st.fixed_dictionaries(
        {body: st.integers(min_value=0, max_value=11) for body in PLANETS}
    )
)
def test_property_dispositor_chains_are_finite(signs: dict[str, int]) -> None:
    chains, _ = calculate_dispositor_chains(
        signs,
        bodies=PLANETS,
        system="modern",
    )

    assert all(len(chain.chain) <= len(PLANETS) + 1 for chain in chains.values())


@settings(max_examples=10, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    dt=st.datetimes(
        min_value=datetime(1900, 1, 1),
        max_value=datetime(2099, 12, 31, 23, 59, 59),
        timezones=st.just(timezone.utc),
    ),
    latitude=st.floats(min_value=-60.0, max_value=60.0, allow_nan=False, allow_infinity=False),
    longitude=st.floats(min_value=-180.0, max_value=180.0, allow_nan=False, allow_infinity=False),
)
def test_property_interceptions_are_even_oppositional_pairs(
    dt,
    latitude: float,
    longitude: float,
) -> None:
    chart = calculate_natal(dt, latitude, longitude, chart_kind="natal")
    interceptions = chart.interceptions or ()
    sign_indices = {item.sign_index for item in interceptions}

    assert len(sign_indices) % 2 == 0
    assert all((sign_index + 6) % 12 in sign_indices for sign_index in sign_indices)


def _reference_chart(config: StrengthConfig):
    return calculate_natal(
        REFERENCE["datetime_utc"],
        REFERENCE["latitude"],
        REFERENCE["longitude"],
        chart_kind="natal",
        house_system=REFERENCE["house_system"],
        strength_config=config,
    )


def _final_dispositors(chart) -> set[str]:
    return {
        chain.cycle[0]
        for chain in chart.strength.dispositors.values()
        if len(chain.cycle) == 1
    }
