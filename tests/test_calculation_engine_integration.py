"""Calculation engine boundary integration smoke tests."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import logging
from uuid import UUID

import pytest

from exact_orb.birth.types import ResolvedBirthData
from exact_orb.calculation.engine import EngineService, NatalTechniqueAdapter
from exact_orb.calculation.errors import ChartCalculationError
from exact_orb.calculation.spec import NatalChartSpec
from exact_orb.engine.charts.natal import calculate_natal
from exact_orb.run_context import RunContext
from tests.fixtures.natal_1985 import REFERENCE


RUN_ID = UUID("22222222-2222-4222-8222-222222222222")


async def test_engine_service_natal_matches_direct_calculate_natal() -> None:
    spec = NatalChartSpec(chart_kind="natal")
    resolved = _resolved()
    direct = _direct_chart(spec, resolved)

    with ThreadPoolExecutor(max_workers=1) as executor:
        service = _service(executor)
        result = await service.calculate(spec, resolved, run=_run())

    assert result.chart_kind == "natal"
    assert result.chart.chart_kind == "natal"
    assert result.chart.house_system == "P"
    assert direct.house_system == "P"
    assert result.chart.bodies is not None
    assert direct.bodies is not None
    assert result.chart.bodies["sun"].longitude == pytest.approx(direct.bodies["sun"].longitude)
    assert result.chart.cusps is not None
    assert result.warnings == result.chart.warnings


async def test_engine_service_cosmogram_matches_direct_calculate_natal() -> None:
    spec = NatalChartSpec(chart_kind="cosmogram")
    resolved = _resolved()
    direct = _direct_chart(spec, resolved)

    with ThreadPoolExecutor(max_workers=1) as executor:
        service = _service(executor)
        result = await service.calculate(spec, resolved, run=_run())

    assert result.chart_kind == "cosmogram"
    assert result.chart.chart_kind == "cosmogram"
    assert result.chart.bodies is not None
    assert direct.bodies is not None
    assert result.chart.bodies["sun"].longitude == pytest.approx(direct.bodies["sun"].longitude)
    assert result.chart.cusps is None
    assert result.chart.house_rulers is None
    assert result.chart.strength is None
    assert result.warnings == result.chart.warnings


async def test_engine_service_maps_real_high_latitude_placidus_to_houses_degenerate() -> None:
    spec = NatalChartSpec(chart_kind="natal")
    resolved = _resolved(latitude=78.0)

    with ThreadPoolExecutor(max_workers=1) as executor:
        service = _service(executor)
        with pytest.raises(ChartCalculationError) as exc_info:
            await service.calculate(spec, resolved, run=_run())

    assert exc_info.value.code == "HOUSES_DEGENERATE"
    assert exc_info.value.__cause__ is None
    assert "78.0" not in str(exc_info.value)


def test_calculation_runtime_logs_omit_sensitive_input_and_position_fields(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG, logger="exact_orb.engine")

    chart = calculate_natal(
        REFERENCE["datetime_utc"],
        REFERENCE["latitude"],
        REFERENCE["longitude"],
        chart_kind="natal",
        house_system=REFERENCE["house_system"],
    )

    start_logs = _messages(caplog, "calculate_natal start")
    ephemeris_logs = _messages(caplog, "ephemeris configured")
    body_logs = _messages(caplog, "body_calculated")
    derived_logs = _messages(caplog, "derived_point")
    lunar_logs = _messages(caplog, "calculate_lunar_phase")

    assert start_logs
    assert "1985-09-01" not in "\n".join(start_logs)
    assert "55.7522" not in "\n".join(start_logs)
    assert "37.6155" not in "\n".join(start_logs)

    assert ephemeris_logs
    assert chart.ephemeris.path not in "\n".join(ephemeris_logs)
    assert "path=" not in "\n".join(ephemeris_logs)

    assert body_logs
    assert all("longitude" not in message for message in body_logs)
    assert all("latitude" not in message for message in body_logs)
    assert all("speed" not in message for message in body_logs)

    assert derived_logs
    assert all("longitude" not in message for message in derived_logs)

    assert lunar_logs
    assert all("sun_longitude" not in message for message in lunar_logs)
    assert all("moon_longitude" not in message for message in lunar_logs)
    assert all("elongation" not in message for message in lunar_logs)


def _service(executor: ThreadPoolExecutor) -> EngineService:
    return EngineService(
        executor=executor,
        techniques={"natal": NatalTechniqueAdapter()},
        slow_threshold_ms=3000.0,
    )


def _direct_chart(spec: NatalChartSpec, resolved: ResolvedBirthData):
    return calculate_natal(
        resolved.utc_datetime,
        resolved.latitude,
        resolved.longitude,
        chart_kind=spec.chart_kind,
        house_system=spec.house_system,
        rulership=spec.rulership,
        include=frozenset(spec.include),
        near_interception_threshold=spec.near_interception_threshold,
    )


def _resolved(
    *,
    latitude: float | None = None,
    longitude: float | None = None,
) -> ResolvedBirthData:
    return ResolvedBirthData(
        utc_datetime=REFERENCE["datetime_utc"],
        latitude=REFERENCE["latitude"] if latitude is None else latitude,
        longitude=REFERENCE["longitude"] if longitude is None else longitude,
        tz_id="Europe/Moscow",
        utc_offset_seconds=14400,
        canonical_place="Moscow",
        time_unknown=False,
        warnings=(),
    )


def _run() -> RunContext:
    return RunContext(
        run_id=RUN_ID,
        started_at=datetime(2026, 8, 31, 8, 0, tzinfo=timezone.utc),
    )


def _messages(caplog: pytest.LogCaptureFixture, marker: str) -> list[str]:
    return [
        record.getMessage()
        for record in caplog.records
        if record.name.startswith("exact_orb.engine") and marker in record.getMessage()
    ]
