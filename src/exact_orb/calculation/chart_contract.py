"""Pure consistency checks shared by calculation and artifact boundaries."""

from __future__ import annotations

from exact_orb.birth.types import ResolvedBirthData, birth_time_domain_digest
from exact_orb.domain import normalize_natal_house_system_code
from exact_orb.engine.charts.natal import NatalChart

from .keys import CalculationInput, calculation_input_from
from .spec import ChartSpec


def calculation_input_from_chart(chart: NatalChart) -> CalculationInput:
    """Project the key-participating calculation input from a chart payload."""

    return CalculationInput(
        utc_datetime=chart.datetime_utc,
        latitude=chart.latitude,
        longitude=chart.longitude,
        birth_time_domain_digest=(
            birth_time_domain_digest(chart.time_uncertainty.domain)
            if chart.time_uncertainty is not None
            else None
        ),
    )


def validate_chart_against_spec(chart: NatalChart, spec: ChartSpec) -> None:
    """Require the calculated payload shape to match its requested spec."""

    if chart.chart_kind != spec.chart_kind:
        raise ValueError("chart.chart_kind must match spec.chart_kind")
    if normalize_natal_house_system_code(chart.house_system) != (
        normalize_natal_house_system_code(spec.house_system)
    ):
        raise ValueError("chart.house_system must match spec.house_system")

    expected = frozenset(spec.include)
    block_fields = {
        "positions": ("bodies",),
        "houses": ("cusps", "angles"),
        "rulers": ("house_rulers", "interceptions"),
        "aspects": ("aspects",),
        "configurations": ("configurations",),
        "strength": ("strength",),
    }
    for block, fields in block_fields.items():
        present = tuple(getattr(chart, field) is not None for field in fields)
        if (block in expected and not all(present)) or (
            block not in expected and any(present)
        ):
            raise ValueError(f"chart block {block!r} does not match spec.include")


def validate_chart_against_calculation_input(
    chart: NatalChart,
    calc_input: CalculationInput,
) -> None:
    """Require a chart to project to the expected normalized key input."""

    if calculation_input_from_chart(chart) != calc_input:
        raise ValueError("chart calculation input does not match expected input")


def validate_chart_against_resolved(
    chart: NatalChart,
    resolved: ResolvedBirthData,
) -> None:
    """Preserve the engine boundary's exact datetime identity contract."""

    if chart.datetime_utc != resolved.utc_datetime:
        raise ValueError("chart.datetime_utc must match resolved.utc_datetime")
    chart_domain = (
        chart.time_uncertainty.domain
        if chart.time_uncertainty is not None
        else None
    )
    if chart_domain != resolved.birth_time_domain:
        raise ValueError("chart time domain must match resolved birth time domain")
    validate_chart_against_calculation_input(chart, calculation_input_from(resolved))


__all__ = [
    "calculation_input_from_chart",
    "validate_chart_against_calculation_input",
    "validate_chart_against_resolved",
    "validate_chart_against_spec",
]
