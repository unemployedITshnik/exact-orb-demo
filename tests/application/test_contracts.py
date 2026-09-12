"""Contracts for application commands, ports, and build results."""

from __future__ import annotations

import ast
from datetime import date, time, timedelta
from inspect import Parameter, iscoroutinefunction, signature
from pathlib import Path
from typing import get_type_hints

import pytest
from pydantic import ValidationError

import exact_orb.application.commands as commands_module
from exact_orb.application.commands import BuildNatalCommand
from exact_orb.application.ports import (
    BirthDataResolverPort,
    ChartArtifactPort,
    Handler,
)
from exact_orb.application.results import BuildNatalSuccess
from exact_orb.birth.resolver import BirthDataResolver
from exact_orb.birth.types import (
    BirthInput,
    BirthTimeDomain,
    ResolvedBirthData,
    UtcMinuteRange,
)
from exact_orb.calculation.artifacts import ChartArtifactResolver
from exact_orb.calculation.chart_contract import calculation_input_from_chart
from exact_orb.calculation.engine import NatalTechniqueAdapter, TechniqueAdapter
from exact_orb.calculation.keys import calculation_input_from, calculation_key
from exact_orb.calculation.spec import NatalChartSpec
from exact_orb.session.state import RESET_DELTA, StateDelta
from tests.fixtures.calculation import (
    VERSION,
    artifact,
    chart_spec,
    resolved_birth_data,
)


def _birth_input() -> BirthInput:
    return BirthInput(
        birth_date=date(1990, 9, 2),
        birth_time=time(14, 30),
        place_id="moscow-ru",
    )


def _populated_delta(
    spec: NatalChartSpec,
    *,
    birth_input: BirthInput | None = None,
    resolved: ResolvedBirthData | None = None,
) -> StateDelta:
    return StateDelta(
        birth_input=_birth_input() if birth_input is None else birth_input,
        birth_resolved=resolved_birth_data() if resolved is None else resolved,
        base_chart_spec=spec,
    )


def _resolved(*, time_unknown: bool = False) -> ResolvedBirthData:
    resolved = resolved_birth_data()
    if not time_unknown:
        return resolved
    return ResolvedBirthData.model_validate(
        {
            **resolved.model_dump(),
            "time_unknown": True,
            "birth_time_domain": BirthTimeDomain(
                ranges=(UtcMinuteRange(first_utc=resolved.utc_datetime, count=1),)
            ),
        }
    )


def test_build_natal_command_inherits_frozen_config_from_command() -> None:
    birth_input = _birth_input()
    command = BuildNatalCommand(birth_input=birth_input)

    assert command.birth_input == birth_input
    assert BuildNatalCommand.model_config.get("frozen") is True

    source_path = Path(commands_module.__file__ or "")
    module = ast.parse(source_path.read_text(encoding="utf-8"))
    config_owners = [
        node.name
        for node in module.body
        if isinstance(node, ast.ClassDef)
        for statement in node.body
        if isinstance(statement, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "model_config"
            for target in statement.targets
        )
    ]
    assert config_owners == ["Command"]

    with pytest.raises(ValidationError) as exc_info:
        command.birth_input = birth_input  # type: ignore[misc]

    assert exc_info.value.errors()[0]["type"] == "frozen_instance"


def test_build_natal_success_accepts_an_explicit_consistent_pair() -> None:
    spec = chart_spec(chart_kind="natal")
    resolved = resolved_birth_data()
    chart_artifact = artifact(spec=spec, resolved=resolved)
    delta = _populated_delta(spec, resolved=resolved)

    result = BuildNatalSuccess(artifact=chart_artifact, delta=delta)

    assert result.artifact == chart_artifact
    assert result.delta == delta
    assert calculation_input_from(resolved) == calculation_input_from_chart(
        chart_artifact.chart
    )
    assert chart_artifact.calculation_key == calculation_key(
        calculation_input_from(resolved),
        spec,
        chart_artifact.calculation_version,
    )


def test_build_natal_success_accepts_consistent_unknown_time_cosmogram() -> None:
    spec = chart_spec(chart_kind="cosmogram")
    resolved = _resolved(time_unknown=True)
    birth_input = _birth_input().model_copy(update={"birth_time": None})
    chart_artifact = artifact(spec=spec, resolved=resolved)
    delta = _populated_delta(
        spec,
        birth_input=birth_input,
        resolved=resolved,
    )

    result = BuildNatalSuccess(artifact=chart_artifact, delta=delta)

    assert result.artifact.chart.chart_kind == "cosmogram"
    assert result.delta.birth_resolved is not None
    assert result.delta.birth_resolved.time_unknown is True


def test_build_natal_success_rejects_reset_delta_as_unpopulated() -> None:
    spec = chart_spec(chart_kind="natal")
    chart_artifact = artifact(spec=spec)

    with pytest.raises(
        ValidationError,
        match="successful build requires a fully populated StateDelta",
    ):
        BuildNatalSuccess(artifact=chart_artifact, delta=RESET_DELTA)


@pytest.mark.parametrize(
    ("artifact_kind", "delta_kind"),
    [
        ("natal", "cosmogram"),
        ("cosmogram", "natal"),
    ],
)
def test_build_natal_success_rejects_explicit_mismatched_specs(
    artifact_kind: str,
    delta_kind: str,
) -> None:
    artifact_spec = chart_spec(chart_kind=artifact_kind)
    delta_spec = chart_spec(chart_kind=delta_kind)
    chart_artifact = artifact(spec=artifact_spec)
    delta = _populated_delta(delta_spec)

    assert artifact_spec != delta_spec
    with pytest.raises(
        ValidationError,
        match=r"artifact\.spec must equal delta\.base_chart_spec",
    ):
        BuildNatalSuccess(artifact=chart_artifact, delta=delta)


def test_build_natal_success_rejects_spec_parameter_mismatch_with_same_kind() -> None:
    artifact_spec = NatalChartSpec(
        chart_kind="natal",
        near_interception_threshold=1.0,
    )
    delta_spec = NatalChartSpec(
        chart_kind="natal",
        near_interception_threshold=2.0,
    )
    chart_artifact = artifact(spec=artifact_spec)

    assert artifact_spec.chart_kind == delta_spec.chart_kind
    assert artifact_spec != delta_spec
    with pytest.raises(
        ValidationError,
        match=r"artifact\.spec must equal delta\.base_chart_spec",
    ):
        BuildNatalSuccess(
            artifact=chart_artifact,
            delta=_populated_delta(delta_spec),
        )


@pytest.mark.parametrize(
    ("field", "different_value"),
    [
        ("utc_datetime", resolved_birth_data().utc_datetime + timedelta(minutes=1)),
        ("latitude", 56.7558),
        ("longitude", 38.6173),
    ],
)
def test_build_natal_success_rejects_artifact_for_different_calculation_input(
    field: str,
    different_value: object,
) -> None:
    spec = chart_spec(chart_kind="natal")
    delta_resolved = resolved_birth_data()
    artifact_resolved = delta_resolved.model_copy(update={field: different_value})
    chart_artifact = artifact(spec=spec, resolved=artifact_resolved)

    assert chart_artifact.spec == spec
    assert calculation_input_from_chart(chart_artifact.chart) == calculation_input_from(
        artifact_resolved
    )
    assert calculation_input_from_chart(chart_artifact.chart) != calculation_input_from(
        delta_resolved
    )
    assert chart_artifact.calculation_key == calculation_key(
        calculation_input_from(artifact_resolved),
        spec,
        VERSION,
    )
    with pytest.raises(
        ValidationError,
        match=(
            r"artifact\.chart calculation input must equal "
            r"delta\.birth_resolved calculation input"
        ),
    ):
        BuildNatalSuccess(
            artifact=chart_artifact,
            delta=_populated_delta(spec, resolved=delta_resolved),
        )


def test_build_natal_success_compares_normalized_calculation_inputs() -> None:
    spec = chart_spec(chart_kind="natal")
    artifact_resolved = resolved_birth_data(latitude=55.7558)
    delta_resolved = resolved_birth_data(latitude=55.7558004)
    chart_artifact = artifact(spec=spec, resolved=artifact_resolved)

    assert artifact_resolved.latitude != delta_resolved.latitude
    assert calculation_input_from(artifact_resolved) == calculation_input_from(
        delta_resolved
    )

    result = BuildNatalSuccess(
        artifact=chart_artifact,
        delta=_populated_delta(spec, resolved=delta_resolved),
    )

    assert result.artifact is chart_artifact


@pytest.mark.parametrize(
    ("time_unknown", "chart_kind"),
    [(True, "natal"), (False, "cosmogram")],
)
def test_build_natal_success_rejects_chart_kind_inconsistent_with_time_unknown(
    time_unknown: bool,
    chart_kind: str,
) -> None:
    resolved = _resolved(time_unknown=time_unknown)
    birth_input = _birth_input().model_copy(
        update={"birth_time": None if time_unknown else time(14, 30)}
    )
    spec = chart_spec(chart_kind=chart_kind)

    with pytest.raises(
        ValidationError,
        match=(
            r"delta\.base_chart_spec\.chart_kind must match "
            r"delta\.birth_resolved\.time_unknown"
        ),
    ):
        BuildNatalSuccess(
            artifact=artifact(spec=spec, resolved=resolved),
            delta=_populated_delta(
                spec,
                birth_input=birth_input,
                resolved=resolved,
            ),
        )


@pytest.mark.parametrize(
    ("birth_time", "time_unknown", "chart_kind"),
    [(None, False, "natal"), (time(14, 30), True, "cosmogram")],
)
def test_build_natal_success_rejects_birth_time_presence_inconsistent_with_resolution(
    birth_time: time | None,
    time_unknown: bool,
    chart_kind: str,
) -> None:
    birth_input = _birth_input().model_copy(update={"birth_time": birth_time})
    resolved = _resolved(time_unknown=time_unknown)
    spec = chart_spec(chart_kind=chart_kind)

    with pytest.raises(
        ValidationError,
        match=(
            r"delta\.birth_input\.birth_time must match "
            r"delta\.birth_resolved\.time_unknown"
        ),
    ):
        BuildNatalSuccess(
            artifact=artifact(spec=spec, resolved=resolved),
            delta=_populated_delta(
                spec,
                birth_input=birth_input,
                resolved=resolved,
            ),
        )


def test_build_natal_success_rejects_tampered_artifact_key() -> None:
    spec = chart_spec(chart_kind="natal")
    resolved = resolved_birth_data()
    valid_artifact = artifact(spec=spec, resolved=resolved)
    tampered_artifact = valid_artifact.model_copy(
        update={"calculation_key": f"{valid_artifact.calculation_key}-foreign"}
    )
    valid_result = BuildNatalSuccess(
        artifact=valid_artifact,
        delta=_populated_delta(spec, resolved=resolved),
    )
    tampered_result = valid_result.model_copy(update={"artifact": tampered_artifact})

    assert valid_artifact.calculation_key == calculation_key(
        calculation_input_from(resolved),
        spec,
        valid_artifact.calculation_version,
    )
    assert tampered_artifact.calculation_key != valid_artifact.calculation_key
    with pytest.raises(
        ValidationError,
        match=(
            r"artifact\.calculation_key must match delta birth data, spec, "
            r"and calculation version"
        ),
    ):
        BuildNatalSuccess.model_validate(tampered_result)


def test_application_ports_are_not_runtime_checkable() -> None:
    assert isinstance(NatalTechniqueAdapter(), TechniqueAdapter)

    for port in (Handler, BirthDataResolverPort, ChartArtifactPort):
        with pytest.raises(TypeError):
            isinstance(object(), port)


@pytest.mark.parametrize(
    ("implementation_method", "port_method", "parameter_names", "run_default"),
    [
        (
            BirthDataResolver.resolve,
            BirthDataResolverPort.resolve,
            ("self", "birth_input", "run"),
            None,
        ),
        (
            ChartArtifactResolver.ensure_chart,
            ChartArtifactPort.ensure_chart,
            ("self", "spec", "resolved", "run"),
            Parameter.empty,
        ),
    ],
)
def test_implemented_dependencies_match_port_signatures(
    implementation_method: object,
    port_method: object,
    parameter_names: tuple[str, ...],
    run_default: object,
) -> None:
    implementation_parameters = signature(implementation_method).parameters
    port_parameters = signature(port_method).parameters

    assert iscoroutinefunction(implementation_method)
    assert iscoroutinefunction(port_method)
    assert tuple(implementation_parameters) == parameter_names
    assert tuple(port_parameters) == parameter_names

    for name, implementation_parameter in implementation_parameters.items():
        port_parameter = port_parameters[name]
        assert implementation_parameter.kind is port_parameter.kind
        assert implementation_parameter.default == port_parameter.default

    assert implementation_parameters["run"].kind is Parameter.KEYWORD_ONLY
    assert implementation_parameters["run"].default is run_default
    assert port_parameters["run"].default is run_default
    assert get_type_hints(implementation_method) == get_type_hints(port_method)
