"""Integration tests for the complete build-natal calculation path."""

from __future__ import annotations

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, time
import json
import logging
from pathlib import Path

import pytest

from exact_orb import component_logging
from exact_orb.application.commands import BuildNatalCommand
from exact_orb.application.handlers.build_natal import BuildNatalHandler
from exact_orb.application.results import BuildNatalSuccess
from exact_orb.birth.places import LocalPlaceCatalog, ResolvedPlace
from exact_orb.birth.resolver import BirthDataResolver
from exact_orb.birth.types import BirthInput
from exact_orb.calculation.artifacts import ChartArtifactResolver
from exact_orb.calculation.cache import InMemoryCalculationCache
from exact_orb.calculation.engine import EngineService, NatalTechniqueAdapter
from exact_orb.outcomes import CalculationFailed, InputRequired
from exact_orb.session.state import new_session
from tests.fixtures.calculation import BASE_UTC, RUN_ID_B, VERSION, run_context


PLACES_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "places.jsonl"
FIXED_TODAY = date(2026, 9, 8)
HANDLER_LOGGER = "exact_orb.application.handlers.build_natal"
BIRTH_LOGGER = "exact_orb.birth.resolver"
ARTIFACT_LOGGER = "exact_orb.calculation.artifacts"
ENGINE_LOGGER = "exact_orb.calculation.engine"
NATAL_LOGGER = "exact_orb.engine.charts.natal"


@dataclass(frozen=True)
class IntegrationStand:
    handler: BuildNatalHandler
    artifacts: ChartArtifactResolver
    cache: InMemoryCalculationCache


@contextmanager
def _stand(
    *,
    places: LocalPlaceCatalog | None = None,
) -> Iterator[IntegrationStand]:
    catalog = places or LocalPlaceCatalog.from_file(PLACES_PATH)
    birth_resolver = BirthDataResolver(
        places=catalog,
        min_birth_date=date(1900, 1, 1),
        max_birth_date=FIXED_TODAY,
        today_provider=lambda: FIXED_TODAY,
    )
    cache = InMemoryCalculationCache(max_entries=10, ttl_seconds=None)
    with ThreadPoolExecutor(max_workers=2) as executor:
        engine = EngineService(
            executor=executor,
            techniques={"natal": NatalTechniqueAdapter()},
            slow_threshold_ms=3000.0,
        )
        artifacts = ChartArtifactResolver(
            cache=cache,
            engine=engine,
            version=VERSION,
            degraded_log_interval_s=60.0,
        )
        yield IntegrationStand(
            handler=BuildNatalHandler(
                resolver=birth_resolver,
                artifacts=artifacts,
            ),
            artifacts=artifacts,
            cache=cache,
        )


def _command(
    *,
    birth_date: date = date(1990, 9, 2),
    birth_time: time | None = time(14, 30),
    place_id: str = "524901",
) -> BuildNatalCommand:
    return BuildNatalCommand(
        birth_input=BirthInput(
            birth_date=birth_date,
            birth_time=birth_time,
            place_id=place_id,
        )
    )


def _event_records(
    caplog: pytest.LogCaptureFixture,
    *,
    logger: str,
    event: str,
) -> list[logging.LogRecord]:
    return [
        record
        for record in caplog.records
        if record.name == logger
        and record.getMessage().partition(" ")[0] == event
    ]


async def test_real_natal_path_caches_and_correlates_run_id(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    caplog.set_level(logging.DEBUG, logger="exact_orb")
    serialized_types: list[str] = []
    original_serialize = component_logging.serialize_component_message

    def serialize_spy(message: object) -> str:
        serialized_types.append(type(message).__name__)
        return original_serialize(message)

    monkeypatch.setattr(component_logging, "serialize_component_message", serialize_spy)
    command = _command()
    first_run = run_context()
    second_run = run_context(RUN_ID_B)

    with _stand() as stand:
        first = await stand.handler.handle(
            command,
            new_session("session-1", now=BASE_UTC),
            first_run,
        )

        assert isinstance(first, BuildNatalSuccess)
        assert first.artifact.spec == first.delta.base_chart_spec
        assert first.artifact.chart.chart_kind == "natal"
        assert first.artifact.calculation_key.startswith("eo:calc:v2:")
        assert first.delta.birth_input is command.birth_input
        assert first.artifact.chart.cusps is not None
        assert first.artifact.chart.angles is not None
        assert first.artifact.chart.house_rulers is not None
        assert first.artifact.chart.strength is not None
        assert stand.artifacts.hits == 0
        assert stand.artifacts.misses == 1
        assert stand.artifacts.put_ok == 1

        second = await stand.handler.handle(
            command,
            new_session("session-2", now=BASE_UTC),
            second_run,
        )

        assert isinstance(second, BuildNatalSuccess)
        assert type(second) is type(first)
        assert second.artifact.calculation_key == first.artifact.calculation_key
        assert second.artifact.spec == first.artifact.spec
        assert stand.artifacts.hits == 1
        assert stand.artifacts.misses == 1
        assert stand.artifacts.put_ok == 1
        assert len(stand.cache) == 1

    calculation_starts = _event_records(
        caplog,
        logger=ENGINE_LOGGER,
        event="calculation_started",
    )
    assert len(calculation_starts) == 1
    assert f"run_id={first_run.run_id}" in calculation_starts[0].getMessage()

    expected_first_run_events = (
        (HANDLER_LOGGER, "build_natal_started"),
        (BIRTH_LOGGER, "birth_resolution"),
        (ARTIFACT_LOGGER, "cache_miss"),
        (ENGINE_LOGGER, "calculation_started"),
    )
    for logger, event in expected_first_run_events:
        records = _event_records(caplog, logger=logger, event=event)
        assert records
        assert any(
            f"run_id={first_run.run_id}" in record.getMessage()
            for record in records
        )

    correlated_boundaries = (
        HANDLER_LOGGER,
        BIRTH_LOGGER,
        ARTIFACT_LOGGER,
        ENGINE_LOGGER,
    )
    for logger in correlated_boundaries:
        records = [
            record
            for record in _event_records(
                caplog,
                logger=logger,
                event="component_message",
            )
            if f"run_id={first_run.run_id}" in record.getMessage()
        ]
        assert len(records) == 2
        assert "direction=in" in records[0].getMessage()
        assert "direction=out" in records[1].getMessage()

    natal_boundaries = _event_records(
        caplog,
        logger=NATAL_LOGGER,
        event="component_message",
    )
    assert len(natal_boundaries) == 2
    assert "direction=in" in natal_boundaries[0].getMessage()
    assert "direction=out" in natal_boundaries[1].getMessage()
    assert "payload_mode=full" in natal_boundaries[1].getMessage()
    assert "message_type=NatalChart" in natal_boundaries[1].getMessage()
    natal_payload = json.loads(
        natal_boundaries[1].getMessage().partition(" message=")[2]
    )
    assert "sun" in natal_payload["bodies"]
    assert "aspects" in natal_payload
    assert "configurations" in natal_payload
    assert "strength" in natal_payload
    assert "warnings" in natal_payload

    artifact_outputs = [
        record.getMessage()
        for record in _event_records(
            caplog,
            logger=ARTIFACT_LOGGER,
            event="component_message",
        )
        if "direction=out" in record.getMessage()
    ]
    assert len(artifact_outputs) == 2
    assert all("payload_mode=full" in message for message in artifact_outputs)
    assert all("message_type=ChartArtifact" in message for message in artifact_outputs)
    assert all(
        f"calculation_key={first.artifact.calculation_key}" in message
        for message in artifact_outputs
    )
    artifact_payloads = [
        json.loads(message.partition(" message=")[2]) for message in artifact_outputs
    ]
    assert all(
        payload == first.artifact.model_dump(mode="json")
        for payload in artifact_payloads
    )

    handler_output = next(
        record.getMessage()
        for record in _event_records(
            caplog,
            logger=HANDLER_LOGGER,
            event="component_message",
        )
        if f"run_id={first_run.run_id}" in record.getMessage()
        and "direction=out" in record.getMessage()
    )
    assert "message_type=BuildNatalSuccess" in handler_output
    assert "payload_mode=full" in handler_output
    assert f"calculation_key={first.artifact.calculation_key}" in handler_output
    assert '"artifact":' in handler_output
    assert '"chart":' in handler_output

    birth_output = next(
        record.getMessage()
        for record in _event_records(
            caplog,
            logger=BIRTH_LOGGER,
            event="component_message",
        )
        if f"run_id={first_run.run_id}" in record.getMessage()
        and "direction=out" in record.getMessage()
    )
    engine_output = next(
        record.getMessage()
        for record in _event_records(
            caplog,
            logger=ENGINE_LOGGER,
            event="component_message",
        )
        if f"run_id={first_run.run_id}" in record.getMessage()
        and "direction=out" in record.getMessage()
    )
    first_artifact_output = next(
        message
        for message in artifact_outputs
        if f"run_id={first_run.run_id}" in message
    )
    birth_payload = json.loads(birth_output.partition(" message=")[2])
    calculation_payload = json.loads(engine_output.partition(" message=")[2])
    artifact_payload = json.loads(first_artifact_output.partition(" message=")[2])
    handler_payload = json.loads(handler_output.partition(" message=")[2])

    assert "message_type=ResolvedBirthData" in birth_output
    assert "payload_mode=full" in birth_output
    assert "message_type=CalculationResult" in engine_output
    assert "payload_mode=full" in engine_output
    assert birth_payload == first.delta.birth_resolved.model_dump(mode="json")
    assert calculation_payload["chart"] == natal_payload
    for field in (
        "chart_kind",
        "datetime_utc",
        "latitude",
        "longitude",
        "house_system",
        "bodies",
        "aspects",
        "configurations",
        "strength",
        "warnings",
    ):
        assert artifact_payload["chart"][field] == calculation_payload["chart"][field]
    assert handler_payload["artifact"] == artifact_payload
    assert handler_payload["delta"]["birth_resolved"] == birth_payload
    assert artifact_payload["chart"]["datetime_utc"] == birth_payload["utc_datetime"]
    assert artifact_payload["chart"]["latitude"] == birth_payload["latitude"]
    assert artifact_payload["chart"]["longitude"] == birth_payload["longitude"]

    assert serialized_types.count("BuildNatalSuccess") == 2
    assert serialized_types.count("NatalChart") == 1
    assert serialized_types.count("CalculationResult") == 1
    assert serialized_types.count("ChartArtifact") == 2


async def test_real_unknown_time_path_builds_cosmogram() -> None:
    command = _command(birth_time=None)

    with _stand() as stand:
        result = await stand.handler.handle(
            command,
            new_session("session-1", now=BASE_UTC),
            run_context(),
        )

        assert isinstance(result, BuildNatalSuccess)
        assert not isinstance(result, InputRequired)
        assert result.delta.base_chart_spec is not None
        assert result.delta.base_chart_spec.chart_kind == "cosmogram"
        assert result.artifact.spec.chart_kind == "cosmogram"
        assert result.artifact.chart.chart_kind == "cosmogram"
        assert result.artifact.chart.cusps is None
        assert result.artifact.chart.angles is None
        assert result.artifact.chart.house_rulers is None
        assert result.artifact.chart.interceptions is None
        assert result.artifact.chart.strength is None
        assert result.delta.birth_input is command.birth_input
        assert result.delta.birth_input.birth_time is None
        assert stand.artifacts.hits == 0
        assert stand.artifacts.misses == 1
        assert stand.artifacts.put_ok == 1


async def test_real_polar_calculation_fails_and_is_not_cached(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG, logger=ENGINE_LOGGER)
    polar_catalog = LocalPlaceCatalog(
        {
            "polar": ResolvedPlace(
                place_id="polar",
                canonical_name="Longyearbyen",
                latitude=78.2232,
                longitude=15.6469,
                tz_id="Arctic/Longyearbyen",
            )
        }
    )
    command = _command(
        birth_date=date(1985, 9, 1),
        birth_time=time(22, 45),
        place_id="polar",
    )

    with _stand(places=polar_catalog) as stand:
        first = await stand.handler.handle(
            command,
            new_session("session-1", now=BASE_UTC),
            run_context(),
        )
        second = await stand.handler.handle(
            command,
            new_session("session-2", now=BASE_UTC),
            run_context(RUN_ID_B),
        )

        assert first == CalculationFailed(error_code="HOUSES_DEGENERATE")
        assert second == CalculationFailed(error_code="HOUSES_DEGENERATE")
        assert stand.artifacts.hits == 0
        assert stand.artifacts.misses == 2
        assert stand.artifacts.put_ok == 0
        assert len(stand.cache) == 0

    assert len(
        _event_records(
            caplog,
            logger=ENGINE_LOGGER,
            event="calculation_started",
        )
    ) == 2


@pytest.mark.no_ephemeris_autoinit
async def test_real_unconfigured_ephemeris_becomes_calculation_unavailable(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG, logger=ENGINE_LOGGER)

    with _stand() as stand:
        result = await stand.handler.handle(
            _command(),
            new_session("session-1", now=BASE_UTC),
            run_context(),
        )

        assert result == CalculationFailed(error_code="EPHEMERIS_UNAVAILABLE")
        assert stand.artifacts.hits == 0
        assert stand.artifacts.misses == 1
        assert stand.artifacts.put_ok == 0
        assert len(stand.cache) == 0

    failures = _event_records(
        caplog,
        logger=ENGINE_LOGGER,
        event="calculation_failed",
    )
    assert len(failures) == 1
    assert "code=EPHEMERIS_UNAVAILABLE" in failures[0].getMessage()
    assert "exception_type=EphemerisNotInitializedError" in failures[0].getMessage()
