"""Logging contracts for ``BuildNatalHandler``."""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, time, timedelta
import json
import logging
import re

import pytest
from pydantic import ValidationError

import exact_orb.application.handlers.build_natal as build_natal_module
from exact_orb.application.commands import BuildNatalCommand
from exact_orb.application.handlers.build_natal import BuildNatalHandler
from exact_orb.birth.types import BirthInput, ResolvedBirthData
from exact_orb.calculation.errors import ChartCalculationError
from exact_orb.calculation.spec import NatalChartSpec
from exact_orb.outcomes import InputRequired, Issue, ResolutionUnavailable
from exact_orb.session.state import new_session
from tests.application.stubs import StubBirthDataResolver, StubChartArtifactPort
from tests.fixtures.calculation import (
    BASE_UTC,
    RUN_ID_B,
    SENSITIVE_WARNING,
    artifact,
    calculation_key_for,
    raw_chart,
    resolved_birth_data,
    run_context,
)


HANDLER_LOGGER = build_natal_module.__name__
DURATION_FIELD = re.compile(r"(?:^| )duration_ms=\d+\.\d{3}(?: |$)")


def _birth_input() -> BirthInput:
    return BirthInput(
        birth_date=date(1990, 9, 2),
        birth_time=time(14, 30),
        place_id="524901",
    )


def _handler_records(
    caplog: pytest.LogCaptureFixture,
) -> list[logging.LogRecord]:
    return [record for record in caplog.records if record.name == HANDLER_LOGGER]


def _event_records(
    caplog: pytest.LogCaptureFixture,
    event: str,
) -> list[logging.LogRecord]:
    return [
        record
        for record in _handler_records(caplog)
        if record.getMessage().partition(" ")[0] == event
    ]


def _single_event(
    caplog: pytest.LogCaptureFixture,
    event: str,
) -> logging.LogRecord:
    records = _event_records(caplog, event)
    assert len(records) == 1
    return records[0]


def _handler_for_typed_outcome(
    scenario: str,
    *,
    run_id: str,
) -> BuildNatalHandler:
    resolved = resolved_birth_data()
    if scenario == "success":
        return BuildNatalHandler(
            resolver=StubBirthDataResolver(resolved),
            artifacts=StubChartArtifactPort(),
        )
    if scenario == "input_required":
        return BuildNatalHandler(
            resolver=StubBirthDataResolver(
                InputRequired(
                    issues=(Issue(field="birth.place", code="INVALID"),)
                )
            ),
            artifacts=StubChartArtifactPort(),
        )
    if scenario == "resolution_unavailable":
        return BuildNatalHandler(
            resolver=StubBirthDataResolver(
                ResolutionUnavailable(
                    error_code="UNKNOWN_TIMEZONE",
                    retryable=False,
                )
            ),
            artifacts=StubChartArtifactPort(),
        )
    if scenario == "calculation_failed":
        return BuildNatalHandler(
            resolver=StubBirthDataResolver(resolved),
            artifacts=StubChartArtifactPort(
                ChartCalculationError("HOUSES_DEGENERATE", run_id=run_id)
            ),
        )
    raise ValueError(f"unknown scenario: {scenario}")


@pytest.mark.parametrize(
    (
        "scenario",
        "expected_level",
        "expected_outcome",
        "expected_chart_kind",
        "expected_error_code",
    ),
    [
        ("success", logging.INFO, "success", "natal", None),
        ("input_required", logging.INFO, "input_required", None, None),
        (
            "resolution_unavailable",
            logging.WARNING,
            "resolution_unavailable",
            None,
            "UNKNOWN_TIMEZONE",
        ),
        (
            "calculation_failed",
            logging.WARNING,
            "calculation_failed",
            "natal",
            "HOUSES_DEGENERATE",
        ),
    ],
)
async def test_completed_event_fields_and_levels_for_typed_outcomes(
    scenario: str,
    expected_level: int,
    expected_outcome: str,
    expected_chart_kind: str | None,
    expected_error_code: str | None,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG, logger=HANDLER_LOGGER)
    run = run_context()
    handler = _handler_for_typed_outcome(scenario, run_id=str(run.run_id))

    await handler.handle(
        BuildNatalCommand(birth_input=_birth_input()),
        new_session("session-1", now=BASE_UTC),
        run,
    )

    completed = _single_event(caplog, "build_natal_completed")
    message = completed.getMessage()
    assert completed.levelno == expected_level
    assert f"run_id={run.run_id}" in message
    assert f"outcome={expected_outcome}" in message
    assert DURATION_FIELD.search(message)
    if expected_chart_kind is None:
        assert "chart_kind=" not in message
    else:
        assert f"chart_kind={expected_chart_kind}" in message
    if expected_error_code is None:
        assert "error_code=" not in message
    else:
        assert f"error_code={expected_error_code}" in message


async def test_engine_unexpected_is_error_with_isolated_warning_control(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG, logger=HANDLER_LOGGER)
    command = BuildNatalCommand(birth_input=_birth_input())
    state = new_session("session-1", now=BASE_UTC)
    error_run = run_context()
    error_handler = BuildNatalHandler(
        resolver=StubBirthDataResolver(resolved_birth_data()),
        artifacts=StubChartArtifactPort(
            ChartCalculationError(
                "ENGINE_UNEXPECTED",
                run_id=str(error_run.run_id),
            )
        ),
    )

    await error_handler.handle(command, state, error_run)

    error_record = _single_event(caplog, "build_natal_completed")
    assert error_record.levelno == logging.ERROR
    assert f"run_id={error_run.run_id}" in error_record.getMessage()
    assert "error_code=ENGINE_UNEXPECTED" in error_record.getMessage()

    caplog.clear()
    warning_run = run_context(RUN_ID_B)
    warning_handler = BuildNatalHandler(
        resolver=StubBirthDataResolver(resolved_birth_data()),
        artifacts=StubChartArtifactPort(
            ChartCalculationError(
                "HOUSES_DEGENERATE",
                run_id=str(warning_run.run_id),
            )
        ),
    )

    await warning_handler.handle(command, state, warning_run)

    warning_record = _single_event(caplog, "build_natal_completed")
    assert warning_record.levelno == logging.WARNING
    assert f"run_id={warning_run.run_id}" in warning_record.getMessage()
    assert "error_code=HOUSES_DEGENERATE" in warning_record.getMessage()


@pytest.mark.parametrize(
    ("scenario", "terminal_event"),
    [
        ("success", "build_natal_completed"),
        ("input_required", "build_natal_completed"),
        ("resolution_unavailable", "build_natal_completed"),
        ("calculation_failed", "build_natal_completed"),
        ("unexpected_exception", "build_natal_failed"),
        ("cancelled", "build_natal_failed"),
    ],
)
async def test_started_is_debug_and_precedes_exactly_one_terminal_event(
    scenario: str,
    terminal_event: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG, logger=HANDLER_LOGGER)
    run = run_context()
    if scenario in {
        "success",
        "input_required",
        "resolution_unavailable",
        "calculation_failed",
    }:
        handler = _handler_for_typed_outcome(scenario, run_id=str(run.run_id))
    elif scenario == "unexpected_exception":
        handler = BuildNatalHandler(
            resolver=StubBirthDataResolver(RuntimeError("unexpected resolver error")),
            artifacts=StubChartArtifactPort(),
        )
    else:
        handler = BuildNatalHandler(
            resolver=StubBirthDataResolver(resolved_birth_data()),
            artifacts=StubChartArtifactPort(asyncio.CancelledError()),
        )

    if scenario == "unexpected_exception":
        with pytest.raises(RuntimeError):
            await handler.handle(
                BuildNatalCommand(birth_input=_birth_input()),
                new_session("session-1", now=BASE_UTC),
                run,
            )
    elif scenario == "cancelled":
        with pytest.raises(asyncio.CancelledError):
            await handler.handle(
                BuildNatalCommand(birth_input=_birth_input()),
                new_session("session-1", now=BASE_UTC),
                run,
            )
    else:
        await handler.handle(
            BuildNatalCommand(birth_input=_birth_input()),
            new_session("session-1", now=BASE_UTC),
            run,
        )

    records = _handler_records(caplog)
    event_names = [record.getMessage().partition(" ")[0] for record in records]
    assert event_names == [
        "component_message",
        "build_natal_started",
        terminal_event,
        "component_message",
    ]
    assert records[0].levelno == logging.DEBUG
    assert "direction=in" in records[0].getMessage()
    assert "message_type=BuildNatalRequest" in records[0].getMessage()
    assert records[-1].levelno == logging.DEBUG
    assert "direction=out" in records[-1].getMessage()
    expected_status = "error" if terminal_event == "build_natal_failed" else "ok"
    assert f"status={expected_status}" in records[-1].getMessage()
    expected_payload_mode = "error" if terminal_event == "build_natal_failed" else "full"
    assert f"payload_mode={expected_payload_mode}" in records[-1].getMessage()
    assert all(f"run_id={run.run_id}" in record.getMessage() for record in records)


async def test_handler_logs_complete_input_and_output_component_messages(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG, logger=HANDLER_LOGGER)
    birth_input = BirthInput(
        birth_date=date(1987, 6, 5),
        birth_time=time(23, 47),
        place_id="secret-place-987654",
    )
    resolved = ResolvedBirthData(
        utc_datetime=datetime(1987, 6, 5, 19, 47, tzinfo=UTC),
        latitude=12.3456789,
        longitude=-98.7654321,
        tz_id="Secret/Timezone",
        utc_offset_seconds=14400,
        canonical_place="Private-City-Alpha",
        time_unknown=False,
        birth_time_domain=None,
        warnings=(),
    )
    spec = NatalChartSpec(chart_kind="natal")
    calculation_key = calculation_key_for(spec, resolved)
    expected_chart = raw_chart(
        latitude=resolved.latitude,
        longitude=resolved.longitude,
    ).model_copy(
        update={
            "datetime_utc": resolved.utc_datetime,
            "julian_day_ut": 2446952.324305556,
        }
    )
    expected_artifact = artifact(
        spec=spec,
        resolved=resolved,
        chart=expected_chart,
    )
    run = run_context()
    handler = BuildNatalHandler(
        resolver=StubBirthDataResolver(resolved),
        artifacts=StubChartArtifactPort(expected_artifact),
    )
    command = BuildNatalCommand(birth_input=birth_input)

    result = await handler.handle(
        command,
        new_session("session-1", now=BASE_UTC),
        run,
    )

    completed = _single_event(caplog, "build_natal_completed")
    records = _handler_records(caplog)
    component_messages = [
        record.getMessage()
        for record in records
        if record.getMessage().startswith("component_message ")
    ]
    assert f"run_id={run.run_id}" in completed.getMessage()
    assert f"calculation_key={calculation_key}" in completed.getMessage()
    assert len(component_messages) == 2
    incoming, outgoing = component_messages
    assert "direction=in" in incoming
    assert "calculation_key=-" in incoming
    assert "payload_mode=full" in incoming
    assert "message_type=BuildNatalRequest" in incoming
    assert "direction=out" in outgoing
    assert f"calculation_key={calculation_key}" in outgoing
    assert "payload_mode=full" in outgoing
    assert "message_type=BuildNatalSuccess" in outgoing

    expected_input = {
        "command": command.model_dump(mode="json"),
        "run": run.model_dump(mode="json"),
    }
    expected_output = {
        "artifact": expected_artifact.model_dump(mode="json"),
        "delta": {
            "base_chart_spec": spec.model_dump(mode="json"),
            "birth_input": birth_input.model_dump(mode="json"),
            "birth_resolved": resolved.model_dump(mode="json"),
        },
    }
    assert json.loads(incoming.partition(" message=")[2]) == expected_input
    assert json.loads(outgoing.partition(" message=")[2]) == expected_output
    assert result.model_dump(mode="json") == expected_output

    local_datetime = (
        resolved.utc_datetime + timedelta(seconds=resolved.utc_offset_seconds)
    ).replace(tzinfo=None)
    assert local_datetime == datetime.combine(birth_input.birth_date, birth_input.birth_time)
    assert expected_chart.datetime_utc == resolved.utc_datetime
    assert expected_chart.latitude == resolved.latitude
    assert expected_chart.longitude == resolved.longitude


async def test_failed_event_omits_exception_message_and_traceback(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG, logger=HANDLER_LOGGER)
    run = run_context()
    error = RuntimeError(SENSITIVE_WARNING)
    handler = BuildNatalHandler(
        resolver=StubBirthDataResolver(error),
        artifacts=StubChartArtifactPort(),
    )

    with pytest.raises(RuntimeError) as exc_info:
        await handler.handle(
            BuildNatalCommand(birth_input=_birth_input()),
            new_session("session-1", now=BASE_UTC),
            run,
        )

    failed = _single_event(caplog, "build_natal_failed")
    assert exc_info.value is error
    assert f"run_id={run.run_id}" in failed.getMessage()
    assert "exception_type=RuntimeError" in failed.getMessage()
    assert SENSITIVE_WARNING not in failed.getMessage()
    assert "Traceback" not in failed.getMessage()
    assert failed.exc_info is None
    assert failed.exc_text is None


async def test_input_required_event_omits_issue_details_with_positive_control(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG, logger=HANDLER_LOGGER)
    run = run_context()
    handler = BuildNatalHandler(
        resolver=StubBirthDataResolver(
            InputRequired(issues=(Issue(field="birth.place", code="INVALID"),))
        ),
        artifacts=StubChartArtifactPort(),
    )

    await handler.handle(
        BuildNatalCommand(birth_input=_birth_input()),
        new_session("session-1", now=BASE_UTC),
        run,
    )

    completed = _single_event(caplog, "build_natal_completed")
    message = completed.getMessage()
    assert f"run_id={run.run_id}" in message
    assert "outcome=input_required" in message
    assert "birth.place" not in message
    assert "INVALID" not in message


@pytest.mark.parametrize(
    ("failure_source", "expected_stage"),
    [("resolver", "resolve"), ("artifacts", "ensure_chart")],
)
async def test_unexpected_exception_logs_failed_stage_and_propagates(
    failure_source: str,
    expected_stage: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG, logger=HANDLER_LOGGER)
    run = run_context()
    error = RuntimeError("unexpected dependency error")
    resolver = StubBirthDataResolver(
        error if failure_source == "resolver" else resolved_birth_data()
    )
    artifacts = StubChartArtifactPort(
        error if failure_source == "artifacts" else None
    )
    handler = BuildNatalHandler(resolver=resolver, artifacts=artifacts)

    with pytest.raises(RuntimeError) as exc_info:
        await handler.handle(
            BuildNatalCommand(birth_input=_birth_input()),
            new_session("session-1", now=BASE_UTC),
            run,
        )

    failed = _single_event(caplog, "build_natal_failed")
    message = failed.getMessage()
    assert exc_info.value is error
    assert failed.levelno == logging.ERROR
    assert f"run_id={run.run_id}" in message
    assert f"stage={expected_stage}" in message
    assert "exception_type=RuntimeError" in message
    assert DURATION_FIELD.search(message)
    assert "cancelled=false" in message
    assert _event_records(caplog, "build_natal_completed") == []


async def test_inconsistent_artifact_logs_build_result_stage(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG, logger=HANDLER_LOGGER)
    resolved = resolved_birth_data()
    requested_spec = NatalChartSpec(chart_kind="natal")
    foreign_resolved = resolved.model_copy(
        update={
            "utc_datetime": resolved.utc_datetime + timedelta(minutes=1),
            "latitude": resolved.latitude + 1.0,
            "longitude": resolved.longitude + 1.0,
        }
    )
    foreign_artifact = artifact(spec=requested_spec, resolved=foreign_resolved)
    artifacts = StubChartArtifactPort(foreign_artifact)
    handler = BuildNatalHandler(
        resolver=StubBirthDataResolver(resolved),
        artifacts=artifacts,
    )

    assert foreign_artifact.spec == requested_spec
    assert foreign_artifact.chart.datetime_utc == foreign_resolved.utc_datetime
    assert foreign_artifact.chart.latitude == foreign_resolved.latitude
    assert foreign_artifact.chart.longitude == foreign_resolved.longitude
    with pytest.raises(ValidationError):
        await handler.handle(
            BuildNatalCommand(birth_input=_birth_input()),
            new_session("session-1", now=BASE_UTC),
            run_context(),
        )

    failed = _single_event(caplog, "build_natal_failed")
    assert failed.levelno == logging.ERROR
    assert "stage=build_result" in failed.getMessage()
    assert "exception_type=ValidationError" in failed.getMessage()
    assert "cancelled=false" in failed.getMessage()
    assert artifacts.received_spec == requested_spec
    assert _event_records(caplog, "build_natal_completed") == []


@pytest.mark.parametrize(
    ("cancel_source", "expected_stage"),
    [("resolver", "resolve"), ("artifacts", "ensure_chart")],
)
async def test_cancellation_logs_warning_and_propagates(
    cancel_source: str,
    expected_stage: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG, logger=HANDLER_LOGGER)
    cancellation = asyncio.CancelledError()
    resolver = StubBirthDataResolver(
        cancellation if cancel_source == "resolver" else resolved_birth_data()
    )
    artifacts = StubChartArtifactPort(
        cancellation if cancel_source == "artifacts" else None
    )
    handler = BuildNatalHandler(resolver=resolver, artifacts=artifacts)

    with pytest.raises(asyncio.CancelledError) as exc_info:
        await handler.handle(
            BuildNatalCommand(birth_input=_birth_input()),
            new_session("session-1", now=BASE_UTC),
            run_context(),
        )

    failed = _single_event(caplog, "build_natal_failed")
    message = failed.getMessage()
    assert exc_info.value is cancellation
    assert failed.levelno == logging.WARNING
    assert f"stage={expected_stage}" in message
    assert "exception_type=CancelledError" in message
    assert DURATION_FIELD.search(message)
    assert "cancelled=true" in message
    assert _event_records(caplog, "build_natal_completed") == []
