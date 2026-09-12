"""Behavior and boundary tests for ``BuildNatalHandler``."""

from __future__ import annotations

import ast
import asyncio
from datetime import date, time, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

import exact_orb.application.handlers.build_natal as build_natal_module
from exact_orb.application.commands import BuildNatalCommand
from exact_orb.application.handlers.build_natal import BuildNatalHandler
from exact_orb.application.results import BuildNatalSuccess
from exact_orb.birth.places import LocalPlaceCatalog
from exact_orb.birth.resolver import BirthDataResolver
from exact_orb.birth.types import (
    BirthInput,
    BirthTimeDomain,
    ResolvedBirthData,
    UtcMinuteRange,
)
from exact_orb.calculation.errors import (
    CalculationUnavailableError,
    ChartCalculationError,
)
from exact_orb.calculation.spec import NatalChartSpec
from exact_orb.outcomes import (
    CalculationFailed,
    InputRequired,
    Issue,
    ResolutionUnavailable,
)
from exact_orb.session.state import new_session
from tests.application.stubs import StubBirthDataResolver, StubChartArtifactPort
from tests.fixtures.calculation import (
    BASE_UTC,
    artifact,
    resolved_birth_data,
    run_context,
)


PLACES_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "places.jsonl"
FIXED_TODAY = date(2026, 9, 8)


def _birth_input(
    *,
    birth_date: date = date(1990, 9, 2),
    birth_time: time | None = time(14, 30),
    place_id: str = "524901",
) -> BirthInput:
    return BirthInput(
        birth_date=birth_date,
        birth_time=birth_time,
        place_id=place_id,
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


def _real_resolver() -> BirthDataResolver:
    return BirthDataResolver(
        places=LocalPlaceCatalog.from_file(PLACES_PATH),
        min_birth_date=date(1900, 1, 1),
        max_birth_date=FIXED_TODAY,
        today_provider=lambda: FIXED_TODAY,
    )


async def _handle_with_real_resolver(
    birth_input: BirthInput,
) -> tuple[object, StubChartArtifactPort]:
    artifacts = StubChartArtifactPort()
    handler = BuildNatalHandler(resolver=_real_resolver(), artifacts=artifacts)
    result = await handler.handle(
        BuildNatalCommand(birth_input=birth_input),
        new_session("session-1", now=BASE_UTC),
        run_context(),
    )
    return result, artifacts


async def test_known_time_builds_natal_success_without_mutating_state() -> None:
    birth_input = _birth_input()
    command = BuildNatalCommand(birth_input=birth_input)
    resolved = _resolved()
    resolver = StubBirthDataResolver(resolved)
    artifacts = StubChartArtifactPort()
    handler = BuildNatalHandler(resolver=resolver, artifacts=artifacts)
    state = new_session("session-1", now=BASE_UTC)
    original_state = state.model_copy(deep=True)
    run = run_context()
    expected_spec = NatalChartSpec(chart_kind="natal")

    result = await handler.handle(command, state, run)

    assert isinstance(result, BuildNatalSuccess)
    assert resolver.calls == 1
    assert resolver.received_birth_input is birth_input
    assert resolver.received_run is run
    assert artifacts.calls == 1
    assert artifacts.received_spec == expected_spec
    assert artifacts.received_resolved is resolved
    assert artifacts.received_run is run
    assert result.delta.birth_input is birth_input
    assert result.delta.birth_resolved is resolved
    assert result.delta.base_chart_spec == expected_spec
    assert result.artifact.spec == expected_spec
    assert state == original_state
    assert not hasattr(result.delta, "state_version")
    assert not hasattr(result, "chart_ref")


async def test_unknown_time_builds_cosmogram_without_rewriting_birth_input() -> None:
    birth_input = _birth_input(birth_time=None)
    resolved = _resolved(time_unknown=True)
    resolver = StubBirthDataResolver(resolved)
    artifacts = StubChartArtifactPort()
    handler = BuildNatalHandler(resolver=resolver, artifacts=artifacts)
    expected_spec = NatalChartSpec(chart_kind="cosmogram")

    result = await handler.handle(
        BuildNatalCommand(birth_input=birth_input),
        new_session("session-1", now=BASE_UTC),
        run_context(),
    )

    assert isinstance(result, BuildNatalSuccess)
    assert not isinstance(result, InputRequired)
    assert artifacts.received_spec == expected_spec
    assert result.delta.birth_input == birth_input
    assert result.delta.birth_input.birth_time is None


async def test_ready_artifact_produces_the_same_success_shape() -> None:
    resolved = _resolved()
    spec = NatalChartSpec(chart_kind="natal")
    ready_artifact = artifact(spec=spec, resolved=resolved)
    resolver = StubBirthDataResolver(resolved)
    artifacts = StubChartArtifactPort(ready_artifact)
    handler = BuildNatalHandler(resolver=resolver, artifacts=artifacts)

    result = await handler.handle(
        BuildNatalCommand(birth_input=_birth_input()),
        new_session("session-1", now=BASE_UTC),
        run_context(),
    )

    assert isinstance(result, BuildNatalSuccess)
    assert result.artifact is ready_artifact
    assert result.delta.base_chart_spec == spec


@pytest.mark.parametrize(
    "resolution",
    [
        InputRequired(issues=(Issue(field="birth.place", code="INVALID"),)),
        ResolutionUnavailable(error_code="PLACE_CATALOG_UNAVAILABLE"),
    ],
)
async def test_resolution_outcomes_short_circuit_with_the_same_object(
    resolution: InputRequired | ResolutionUnavailable,
) -> None:
    resolver = StubBirthDataResolver(resolution)
    artifacts = StubChartArtifactPort()
    handler = BuildNatalHandler(resolver=resolver, artifacts=artifacts)
    run = run_context()

    result = await handler.handle(
        BuildNatalCommand(birth_input=_birth_input()),
        new_session("session-1", now=BASE_UTC),
        run,
    )

    assert result is resolution
    assert resolver.received_run is run
    assert artifacts.calls == 0


async def test_empty_input_required_passes_through_unchanged() -> None:
    input_required = InputRequired(issues=())
    resolver = StubBirthDataResolver(input_required)
    artifacts = StubChartArtifactPort()
    handler = BuildNatalHandler(resolver=resolver, artifacts=artifacts)

    result = await handler.handle(
        BuildNatalCommand(birth_input=_birth_input()),
        new_session("session-1", now=BASE_UTC),
        run_context(),
    )

    assert result is input_required
    assert result.issues == ()
    assert artifacts.calls == 0


async def test_unexpected_resolver_exception_propagates_unchanged() -> None:
    error = RuntimeError("resolver failed unexpectedly")
    resolver = StubBirthDataResolver(error)
    artifacts = StubChartArtifactPort()
    handler = BuildNatalHandler(resolver=resolver, artifacts=artifacts)

    with pytest.raises(RuntimeError) as exc_info:
        await handler.handle(
            BuildNatalCommand(birth_input=_birth_input()),
            new_session("session-1", now=BASE_UTC),
            run_context(),
        )

    assert exc_info.value is error
    assert artifacts.calls == 0


@pytest.mark.parametrize(
    "error_code",
    ["SPEC_INVALID", "GEOGRAPHY_INVALID", "HOUSES_DEGENERATE", "ENGINE_UNEXPECTED"],
)
async def test_chart_calculation_errors_become_calculation_failed(
    error_code: str,
) -> None:
    run = run_context()
    error = ChartCalculationError(error_code, run_id=str(run.run_id))
    handler = BuildNatalHandler(
        resolver=StubBirthDataResolver(_resolved()),
        artifacts=StubChartArtifactPort(error),
    )

    result = await handler.handle(
        BuildNatalCommand(birth_input=_birth_input()),
        new_session("session-1", now=BASE_UTC),
        run,
    )

    assert result == CalculationFailed(error_code=error_code)


async def test_calculation_unavailable_becomes_calculation_failed() -> None:
    run = run_context()
    error = CalculationUnavailableError(
        "EPHEMERIS_UNAVAILABLE",
        run_id=str(run.run_id),
    )
    handler = BuildNatalHandler(
        resolver=StubBirthDataResolver(_resolved()),
        artifacts=StubChartArtifactPort(error),
    )

    result = await handler.handle(
        BuildNatalCommand(birth_input=_birth_input()),
        new_session("session-1", now=BASE_UTC),
        run,
    )

    assert result == CalculationFailed(error_code="EPHEMERIS_UNAVAILABLE")


async def test_unexpected_artifact_exception_propagates_with_typed_positive_control() -> None:
    resolved = _resolved()
    run = run_context()
    command = BuildNatalCommand(birth_input=_birth_input())
    state = new_session("session-1", now=BASE_UTC)
    unexpected = LookupError("artifact failed unexpectedly")
    unexpected_handler = BuildNatalHandler(
        resolver=StubBirthDataResolver(resolved),
        artifacts=StubChartArtifactPort(unexpected),
    )

    with pytest.raises(LookupError) as exc_info:
        await unexpected_handler.handle(command, state, run)

    assert exc_info.value is unexpected

    typed_handler = BuildNatalHandler(
        resolver=StubBirthDataResolver(resolved),
        artifacts=StubChartArtifactPort(
            ChartCalculationError("SPEC_INVALID", run_id=str(run.run_id))
        ),
    )
    typed_result = await typed_handler.handle(command, state, run)
    assert typed_result == CalculationFailed(error_code="SPEC_INVALID")


async def test_foreign_artifact_spec_is_rejected_by_success_validation() -> None:
    resolved = _resolved()
    requested_spec = NatalChartSpec(chart_kind="natal")
    foreign_spec = NatalChartSpec(chart_kind="cosmogram")
    foreign_artifact = artifact(spec=foreign_spec, resolved=resolved)
    artifacts = StubChartArtifactPort(foreign_artifact)
    handler = BuildNatalHandler(
        resolver=StubBirthDataResolver(resolved),
        artifacts=artifacts,
    )

    assert requested_spec != foreign_spec
    with pytest.raises(
        ValidationError,
        match=r"artifact\.spec must equal delta\.base_chart_spec",
    ):
        await handler.handle(
            BuildNatalCommand(birth_input=_birth_input()),
            new_session("session-1", now=BASE_UTC),
            run_context(),
        )

    assert artifacts.received_spec == requested_spec
    assert foreign_artifact.spec == foreign_spec


@pytest.mark.parametrize(
    ("resolved_field", "chart_field", "different_value"),
    [
        ("utc_datetime", "datetime_utc", BASE_UTC + timedelta(minutes=1)),
        ("latitude", "latitude", 56.7558),
        ("longitude", "longitude", 38.6173),
    ],
)
async def test_foreign_artifact_input_is_rejected_by_success_validation(
    resolved_field: str,
    chart_field: str,
    different_value: object,
) -> None:
    resolved = _resolved()
    foreign_resolved = resolved.model_copy(update={resolved_field: different_value})
    requested_spec = NatalChartSpec(chart_kind="natal")
    foreign_artifact = artifact(spec=requested_spec, resolved=foreign_resolved)
    artifacts = StubChartArtifactPort(foreign_artifact)
    handler = BuildNatalHandler(
        resolver=StubBirthDataResolver(resolved),
        artifacts=artifacts,
    )

    assert foreign_artifact.spec == requested_spec
    assert getattr(foreign_artifact.chart, chart_field) != getattr(
        resolved, resolved_field
    )
    with pytest.raises(
        ValidationError,
        match=(
            r"artifact\.chart calculation input must equal "
            r"delta\.birth_resolved calculation input"
        ),
    ):
        await handler.handle(
            BuildNatalCommand(birth_input=_birth_input()),
            new_session("session-1", now=BASE_UTC),
            run_context(),
        )

    assert artifacts.received_spec == requested_spec
    assert foreign_artifact.chart.datetime_utc == foreign_resolved.utc_datetime
    assert foreign_artifact.chart.latitude == foreign_resolved.latitude
    assert foreign_artifact.chart.longitude == foreign_resolved.longitude


async def test_artifact_cancellation_propagates_unchanged() -> None:
    cancellation = asyncio.CancelledError()
    handler = BuildNatalHandler(
        resolver=StubBirthDataResolver(_resolved()),
        artifacts=StubChartArtifactPort(cancellation),
    )

    with pytest.raises(asyncio.CancelledError) as exc_info:
        await handler.handle(
            BuildNatalCommand(birth_input=_birth_input()),
            new_session("session-1", now=BASE_UTC),
            run_context(),
        )

    assert exc_info.value is cancellation


def test_handler_ast_does_not_mutate_state_or_create_a_timeout() -> None:
    source_path = Path(build_natal_module.__file__ or "")
    module = ast.parse(source_path.read_text(encoding="utf-8"))
    handle = next(
        node
        for node in ast.walk(module)
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "handle"
    )
    calls = [node for node in ast.walk(handle) if isinstance(node, ast.Call)]
    called_names = {
        node.func.id if isinstance(node.func, ast.Name) else node.func.attr
        for node in calls
        if isinstance(node.func, (ast.Name, ast.Attribute))
    }
    forbidden_calls = {
        "apply_delta",
        "touched",
        "SessionState",
        "ChartRef",
        "wait_for",
        "timeout",
        "timeout_at",
        "call_later",
        "call_at",
    }
    state_attribute_reads = [
        node
        for node in ast.walk(handle)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "state"
    ]
    calls_receiving_state = [
        call
        for call in calls
        if any(
            isinstance(node, ast.Name) and node.id == "state"
            for argument in [*call.args, *(keyword.value for keyword in call.keywords)]
            for node in ast.walk(argument)
        )
    ]

    assert called_names.isdisjoint(forbidden_calls)
    assert state_attribute_reads == []
    assert calls_receiving_state == []


async def test_real_resolver_known_time_builds_natal() -> None:
    result, artifacts = await _handle_with_real_resolver(_birth_input())

    assert isinstance(result, BuildNatalSuccess)
    assert artifacts.received_spec == NatalChartSpec(chart_kind="natal")
    assert artifacts.received_resolved is not None
    assert artifacts.received_resolved.time_unknown is False


async def test_real_resolver_unknown_time_builds_cosmogram() -> None:
    result, artifacts = await _handle_with_real_resolver(_birth_input(birth_time=None))

    assert isinstance(result, BuildNatalSuccess)
    assert artifacts.received_spec == NatalChartSpec(chart_kind="cosmogram")
    assert artifacts.received_resolved is not None
    assert artifacts.received_resolved.time_unknown is True
    assert artifacts.received_resolved.birth_time_domain is not None


async def test_explicit_noon_and_missing_time_keep_distinct_chart_semantics() -> None:
    unknown_result, unknown_artifacts = await _handle_with_real_resolver(
        _birth_input(birth_time=None)
    )
    noon_result, noon_artifacts = await _handle_with_real_resolver(
        _birth_input(birth_time=time(12, 0))
    )

    assert isinstance(unknown_result, BuildNatalSuccess)
    assert isinstance(noon_result, BuildNatalSuccess)
    assert unknown_artifacts.received_resolved is not None
    assert noon_artifacts.received_resolved is not None
    assert (
        unknown_artifacts.received_resolved.utc_datetime
        == noon_artifacts.received_resolved.utc_datetime
    )
    assert unknown_artifacts.received_spec == NatalChartSpec(chart_kind="cosmogram")
    assert noon_artifacts.received_spec == NatalChartSpec(chart_kind="natal")
    assert unknown_artifacts.received_resolved.birth_time_domain is not None
    assert noon_artifacts.received_resolved.birth_time_domain is None
    assert unknown_result.artifact.chart.time_uncertainty is not None
    assert noon_result.artifact.chart.time_uncertainty is None
    assert (
        unknown_result.artifact.calculation_key
        != noon_result.artifact.calculation_key
    )


async def test_real_resolver_invalid_place_short_circuits() -> None:
    result, artifacts = await _handle_with_real_resolver(
        _birth_input(place_id="not-in-catalog")
    )

    assert isinstance(result, InputRequired)
    assert result.issues == (Issue(field="birth.place", code="INVALID"),)
    assert artifacts.calls == 0


async def test_real_resolver_unknown_timezone_stays_unavailable() -> None:
    result, artifacts = await _handle_with_real_resolver(_birth_input(place_id="9000001"))

    assert isinstance(result, ResolutionUnavailable)
    assert not isinstance(result, InputRequired)
    assert result.error_code == "UNKNOWN_TIMEZONE"
    assert artifacts.calls == 0


async def test_real_resolver_unsupported_date_returns_constraints() -> None:
    result, artifacts = await _handle_with_real_resolver(
        _birth_input(birth_date=date(1899, 12, 31))
    )

    assert isinstance(result, InputRequired)
    issue = result.issues[0]
    assert issue.field == "birth.date"
    assert issue.code == "UNSUPPORTED"
    assert issue.constraints == {"min": "1900-01-01", "max": "2026-09-08"}
    assert artifacts.calls == 0


async def test_real_resolver_ambiguous_time_returns_offset_candidates() -> None:
    result, artifacts = await _handle_with_real_resolver(
        _birth_input(
            birth_date=date(2014, 10, 26),
            birth_time=time(1, 30),
        )
    )

    assert isinstance(result, InputRequired)
    assert result.issues == (
        Issue(
            field="birth.time",
            code="AMBIGUOUS",
            candidates=(14400, 10800),
        ),
    )
    assert artifacts.calls == 0
