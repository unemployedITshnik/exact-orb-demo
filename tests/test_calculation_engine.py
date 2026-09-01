"""Calculation engine boundary unit tests."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timezone
import inspect
import logging
from threading import Event, get_ident
from typing import Any
from uuid import UUID

import pytest
from pydantic import ValidationError

from exact_orb.birth.types import ResolvedBirthData
from exact_orb.calculation.engine import (
    CalculationEnginePort,
    CalculationResult,
    EngineService,
    NatalTechniqueAdapter,
    TechniqueAdapter,
)
from exact_orb.calculation.errors import (
    ChartCalculationError,
    CalculationUnavailableError,
)
from exact_orb.calculation.spec import NatalChartSpec
from exact_orb.config import EphemerisStatus
from exact_orb.domain import DEFAULT_INCLUDE_BY_CHART_KIND, RulershipScheme
from exact_orb.engine.charts import natal as natal_module
from exact_orb.engine.charts.natal import NatalChart
from exact_orb.engine.ephemeris import calc as calc_module
from exact_orb.engine.ephemeris import selena as selena_module
from exact_orb.engine.ephemeris.types import CalculationWarning
from exact_orb.engine.strength.lunar_phase import calculate_lunar_phase
from exact_orb.ephemeris_runtime import ephemeris_session
from exact_orb.errors import (
    EphemerisNotInitializedError,
    EphemerisPathMismatchError,
    EphemerisSessionRequiredError,
)
from exact_orb.run_context import RunContext


pytestmark = pytest.mark.no_ephemeris_autoinit

BASE_UTC = datetime(1990, 9, 2, 10, 30, 45, tzinfo=timezone.utc)
RUN_ID = UUID("11111111-1111-4111-8111-111111111111")
SENSITIVE_MESSAGE = "source failure at 1990-09-02 55.7558 37.6173 Moscow warning text"


def test_calculation_result_is_frozen_and_normalizes_warnings_to_tuple() -> None:
    result = CalculationResult(
        chart_kind="natal",
        chart=_raw_chart(),
        warnings=[_warning("test warning")],
    )

    assert isinstance(result.warnings, tuple)
    with pytest.raises(ValidationError):
        result.chart_kind = "cosmogram"


def test_technique_adapter_is_runtime_checkable_but_engine_port_is_not() -> None:
    assert isinstance(FakeAdapter(_result()), TechniqueAdapter)
    assert not isinstance(NoTechniqueAdapter(), TechniqueAdapter)

    with pytest.raises(TypeError):
        isinstance(FakeAdapter(_result()), CalculationEnginePort)


def test_engine_service_validates_registry_on_startup() -> None:
    with ThreadPoolExecutor(max_workers=1) as executor:
        good = FakeAdapter(_result())
        EngineService(executor=executor, techniques={"natal": good}, slow_threshold_ms=3000.0)

        with pytest.raises(ValueError, match="must not be empty"):
            EngineService(executor=executor, techniques={}, slow_threshold_ms=3000.0)

        with pytest.raises(ValueError, match='contain "natal"'):
            EngineService(
                executor=executor,
                techniques={"solar": FakeAdapter(_result(), technique="solar")},
                slow_threshold_ms=3000.0,
            )

        with pytest.raises(ValueError, match="unknown"):
            EngineService(
                executor=executor,
                techniques={
                    "natal": good,
                    "solar": FakeAdapter(_result(), technique="solar"),
                },
                slow_threshold_ms=3000.0,
            )

        with pytest.raises(ValueError, match="does not match"):
            EngineService(
                executor=executor,
                techniques={"natal": FakeAdapter(_result(), technique="cosmogram")},
                slow_threshold_ms=3000.0,
            )

        with pytest.raises(ValueError, match="TechniqueAdapter"):
            EngineService(executor=executor, techniques={"natal": object()}, slow_threshold_ms=3000.0)  # type: ignore[dict-item]

        with pytest.raises(ValueError, match="synchronous"):
            EngineService(executor=executor, techniques={"natal": AsyncAdapter()}, slow_threshold_ms=3000.0)

        with pytest.raises(ValueError, match="positive"):
            EngineService(executor=executor, techniques={"natal": good}, slow_threshold_ms=0.0)


@pytest.mark.parametrize("slow_threshold_ms", (float("nan"), float("inf"), -1.0))
def test_engine_service_rejects_non_finite_or_negative_slow_threshold(
    slow_threshold_ms: float,
) -> None:
    with ThreadPoolExecutor(max_workers=1) as executor:
        with pytest.raises(ValueError, match="slow_threshold_ms"):
            EngineService(
                executor=executor,
                techniques={"natal": FakeAdapter(_result())},
                slow_threshold_ms=slow_threshold_ms,
            )


def test_natal_technique_adapter_maps_current_spec_fields_without_run_or_artifact() -> None:
    received: dict[str, Any] = {}
    chart = _raw_chart(warnings=(_warning("engine warning"),))

    def fake_calculator(*args: Any, **kwargs: Any) -> NatalChart:
        received["args"] = args
        received["kwargs"] = kwargs
        return chart

    spec = NatalChartSpec(
        chart_kind="natal",
        include=("houses", "positions"),
        house_system="p",
        rulership=RulershipScheme.MODERN,
        near_interception_threshold=2.5,
    )
    resolved = _resolved()

    result = NatalTechniqueAdapter(calculator=fake_calculator).calculate(spec, resolved)

    assert received["args"] == (
        resolved.utc_datetime,
        resolved.latitude,
        resolved.longitude,
    )
    assert received["kwargs"] == {
        "chart_kind": "natal",
        "house_system": "P",
        "rulership": RulershipScheme.MODERN,
        "include": frozenset({"houses", "positions"}),
        "near_interception_threshold": 2.5,
    }
    assert "run" not in received["kwargs"]
    assert result.chart is chart
    assert type(result.chart) is NatalChart
    assert result.warnings == chart.warnings


def test_natal_adapter_passes_cosmogram_default_include() -> None:
    received: dict[str, Any] = {}

    def fake_calculator(*args: Any, **kwargs: Any) -> NatalChart:
        received["kwargs"] = kwargs
        return _raw_chart(chart_kind="cosmogram")

    spec = NatalChartSpec(chart_kind="cosmogram")
    result = NatalTechniqueAdapter(calculator=fake_calculator).calculate(spec, _resolved())

    assert result.chart_kind == "cosmogram"
    assert received["kwargs"]["include"] == frozenset(DEFAULT_INCLUDE_BY_CHART_KIND["cosmogram"])


async def test_engine_service_runs_adapter_in_executor_and_keeps_event_loop_alive(
    caplog: pytest.LogCaptureFixture,
) -> None:
    event_loop_thread = get_ident()
    entered = Event()
    release = Event()
    worker_threads: list[int] = []
    adapter = BlockingAdapter(_result(), entered=entered, release=release, thread_ids=worker_threads)
    caplog.set_level(logging.DEBUG, logger="exact_orb.calculation.engine")

    with ThreadPoolExecutor(max_workers=1) as executor:
        service = EngineService(
            executor=executor,
            techniques={"natal": adapter},
            slow_threshold_ms=3000.0,
        )
        task = asyncio.create_task(service.calculate(_spec(), _resolved(), run=_run()))

        for _ in range(100):
            if entered.is_set():
                break
            await asyncio.sleep(0.01)

        assert entered.is_set()
        assert len(worker_threads) == 1
        assert worker_threads[0] != event_loop_thread
        assert not task.done()
        await asyncio.sleep(0)
        release.set()
        result = await task

    messages = [record.getMessage() for record in caplog.records]
    assert result.chart_kind == "natal"
    assert any(f"calculation_thread_started run_id={RUN_ID}" in message for message in messages)
    assert any("calculation_finished" in message and f"run_id={RUN_ID}" in message for message in messages)


def test_engine_module_does_not_use_asyncio_to_thread() -> None:
    from exact_orb.calculation import engine as engine_module

    assert "asyncio.to_thread" not in inspect.getsource(engine_module)


async def test_unsupported_house_system_does_not_call_executor_or_ephemeris_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captures = 0

    @contextmanager
    def counting_session() -> Any:
        nonlocal captures
        captures += 1
        yield

    monkeypatch.setattr(natal_module, "ephemeris_session", counting_session)
    spec = NatalChartSpec.model_construct(
        technique="natal",
        chart_kind="natal",
        include=DEFAULT_INCLUDE_BY_CHART_KIND["natal"],
        house_system="K",
        rulership=RulershipScheme.COMBINED,
        near_interception_threshold=1.0,
    )

    with ExplodingExecutor(max_workers=1) as executor:
        service = EngineService(
            executor=executor,
            techniques={"natal": NatalTechniqueAdapter()},
            slow_threshold_ms=3000.0,
        )
        with pytest.raises(ChartCalculationError) as exc_info:
            await service.calculate(spec, _resolved(), run=_run())

    assert exc_info.value.code == "SPEC_INVALID"
    assert exc_info.value.run_id == str(RUN_ID)
    assert captures == 0


@pytest.mark.parametrize(
    ("spec", "code"),
    (
        (
            NatalChartSpec.model_construct(
                technique="natal",
                chart_kind="natal",
                include=("positions",),
                house_system="P",
                rulership=RulershipScheme.COMBINED,
                near_interception_threshold=1.0,
            ),
            "SPEC_INVALID",
        ),
        (
            NatalChartSpec.model_construct(
                technique="natal",
                chart_kind="cosmogram",
                include=("positions", "houses"),
                house_system="P",
                rulership=RulershipScheme.COMBINED,
                near_interception_threshold=1.0,
            ),
            "SPEC_INVALID",
        ),
        (
            NatalChartSpec.model_construct(
                technique="natal",
                chart_kind="natal",
                include=("aspects", "configurations", "houses", "positions", "rulers", "strength"),
                house_system="PP",
                rulership=RulershipScheme.COMBINED,
                near_interception_threshold=1.0,
            ),
            "SPEC_INVALID",
        ),
        (
            NatalChartSpec.model_construct(
                technique="natal",
                chart_kind="natal",
                include=("aspects", "configurations", "houses", "positions", "rulers", "strength"),
                house_system="P",
                rulership="bad",
                near_interception_threshold=1.0,
            ),
            "SPEC_INVALID",
        ),
        (
            NatalChartSpec.model_construct(
                technique="natal",
                chart_kind="natal",
                include=("aspects", "configurations", "houses", "positions", "rulers", "strength"),
                house_system="P",
                rulership=RulershipScheme.COMBINED,
                near_interception_threshold=-0.1,
            ),
            "SPEC_INVALID",
        ),
        (
            NatalChartSpec.model_construct(
                technique="solar",
                chart_kind="natal",
                include=("aspects", "configurations", "houses", "positions", "rulers", "strength"),
                house_system="P",
                rulership=RulershipScheme.COMBINED,
                near_interception_threshold=1.0,
            ),
            "SPEC_INVALID",
        ),
    ),
)
async def test_prevalidation_maps_bad_specs_to_spec_invalid(
    spec: NatalChartSpec,
    code: str,
) -> None:
    with ExplodingExecutor(max_workers=1) as executor:
        service = EngineService(
            executor=executor,
            techniques={"natal": NatalTechniqueAdapter()},
            slow_threshold_ms=3000.0,
        )
        with pytest.raises(ChartCalculationError) as exc_info:
            await service.calculate(spec, _resolved(), run=_run())

    assert exc_info.value.code == code


@pytest.mark.parametrize("value", (float("nan"), float("inf"), -91.0))
async def test_prevalidation_maps_bad_latitude_to_geography_invalid(value: float) -> None:
    await _assert_bad_geography(_resolved(latitude=value))


@pytest.mark.parametrize("value", (float("nan"), float("inf"), 181.0))
async def test_prevalidation_maps_bad_longitude_to_geography_invalid(value: float) -> None:
    await _assert_bad_geography(_resolved(longitude=value))


@pytest.mark.parametrize(
    ("error", "expected_type", "code"),
    (
        (
            ValueError("could not calculate house cusps for system 'P' at latitude 78.0; Placidus degenerates"),
            ChartCalculationError,
            "HOUSES_DEGENERATE",
        ),
        (ValueError(SENSITIVE_MESSAGE), ChartCalculationError, "ENGINE_UNEXPECTED"),
        (EphemerisNotInitializedError(SENSITIVE_MESSAGE), CalculationUnavailableError, "EPHEMERIS_UNAVAILABLE"),
        (EphemerisPathMismatchError(SENSITIVE_MESSAGE), CalculationUnavailableError, "EPHEMERIS_UNAVAILABLE"),
        (EphemerisSessionRequiredError(SENSITIVE_MESSAGE), ChartCalculationError, "ENGINE_UNEXPECTED"),
        (RuntimeError(SENSITIVE_MESSAGE), ChartCalculationError, "ENGINE_UNEXPECTED"),
    ),
)
async def test_engine_error_mapping_does_not_expose_source_messages(
    error: Exception,
    expected_type: type[Exception],
    code: str,
) -> None:
    with ThreadPoolExecutor(max_workers=1) as executor:
        service = EngineService(
            executor=executor,
            techniques={"natal": FakeAdapter(error=error)},
            slow_threshold_ms=3000.0,
        )
        with pytest.raises(expected_type) as exc_info:
            await service.calculate(_spec(), _resolved(), run=_run())

    mapped = exc_info.value
    assert getattr(mapped, "code") == code
    assert getattr(mapped, "run_id") == str(RUN_ID)
    assert isinstance(getattr(mapped, "run_id"), str)
    assert mapped.__cause__ is None
    assert SENSITIVE_MESSAGE not in str(mapped)


async def test_result_invariant_mismatch_is_engine_unexpected() -> None:
    chart = _raw_chart(chart_kind="cosmogram")
    bad_result = CalculationResult(chart_kind="natal", chart=chart, warnings=chart.warnings)

    with ThreadPoolExecutor(max_workers=1) as executor:
        service = EngineService(
            executor=executor,
            techniques={"natal": FakeAdapter(bad_result)},
            slow_threshold_ms=3000.0,
        )
        with pytest.raises(ChartCalculationError) as exc_info:
            await service.calculate(_spec(), _resolved(), run=_run())

    assert exc_info.value.code == "ENGINE_UNEXPECTED"


async def test_engine_logs_failure_metadata_without_sensitive_values_or_traceback(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG, logger="exact_orb.calculation.engine")

    with ThreadPoolExecutor(max_workers=1) as executor:
        service = EngineService(
            executor=executor,
            techniques={"natal": FakeAdapter(error=ValueError(SENSITIVE_MESSAGE))},
            slow_threshold_ms=3000.0,
        )
        with pytest.raises(ChartCalculationError):
            await service.calculate(_spec(), _resolved(), run=_run())

    logs = "\n".join(record.getMessage() for record in caplog.records)
    assert f"run_id={RUN_ID}" in logs
    assert "code=ENGINE_UNEXPECTED" in logs
    assert "failure_class=ChartCalculationError" in logs
    assert "exception_type=ValueError" in logs
    assert "Traceback" not in logs
    assert "1990-09-02" not in logs
    assert "55.7558" not in logs
    assert "37.6173" not in logs
    assert "Moscow" not in logs
    assert "warning text" not in logs


def test_low_level_ephemeris_warning_log_omits_message_but_preserves_warning(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG, logger="exact_orb.engine.ephemeris.calc")
    monkeypatch.setattr(calc_module, "swiss_backend", FakeSwissBackend)

    with ephemeris_session():
        bodies, warnings = calc_module.calculate_bodies(2448136.0, {"probe": 0}, FakeSwe.FLG_SPEED, None)

    logs = "\n".join(record.getMessage() for record in caplog.records)
    assert warnings[0].message == SENSITIVE_MESSAGE
    assert bodies["probe"].longitude == 1.0
    assert "message_present=True" in logs
    assert SENSITIVE_MESSAGE not in logs
    assert all(
        "longitude" not in record.getMessage()
        and "latitude" not in record.getMessage()
        and "speed" not in record.getMessage()
        for record in caplog.records
        if "body_calculated" in record.getMessage()
    )


def test_selena_warning_log_and_runtime_error_omit_raw_warning(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING, logger="exact_orb.engine.ephemeris.selena")
    monkeypatch.setattr(selena_module, "swiss_backend", FakeSwissBackend)

    with ephemeris_session():
        with pytest.raises(RuntimeError) as exc_info:
            selena_module._calculate_perigee_selena(2448136.0, FakeSwe.FLG_SPEED, "fake", 0)

    logs = "\n".join(record.getMessage() for record in caplog.records)
    assert SENSITIVE_MESSAGE not in str(exc_info.value)
    assert "message_present=True" in logs
    assert SENSITIVE_MESSAGE not in logs


def test_lunar_phase_log_omits_numeric_longitudes(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG, logger="exact_orb.engine.strength.lunar_phase")

    phase = calculate_lunar_phase(159.340833, 7.665)

    logs = "\n".join(record.getMessage() for record in caplog.records)
    assert phase.phase_name in logs
    assert "sun_longitude" not in logs
    assert "moon_longitude" not in logs
    assert "elongation" not in logs


async def _assert_bad_geography(resolved: ResolvedBirthData) -> None:
    with ExplodingExecutor(max_workers=1) as executor:
        service = EngineService(
            executor=executor,
            techniques={"natal": NatalTechniqueAdapter()},
            slow_threshold_ms=3000.0,
        )
        with pytest.raises(ChartCalculationError) as exc_info:
            await service.calculate(_spec(), resolved, run=_run())

    assert exc_info.value.code == "GEOGRAPHY_INVALID"


def _result(chart_kind: str = "natal") -> CalculationResult:
    chart = _raw_chart(chart_kind=chart_kind)
    return CalculationResult(chart_kind=chart_kind, chart=chart, warnings=chart.warnings)


def _raw_chart(
    *,
    chart_kind: str = "natal",
    warnings: tuple[CalculationWarning, ...] = (),
) -> NatalChart:
    return NatalChart(
        chart_kind=chart_kind,
        datetime_utc=BASE_UTC,
        julian_day_ut=2448136.0,
        latitude=55.7558,
        longitude=37.6173,
        house_system="P",
        ephemeris_flags=0,
        ephemeris=EphemerisStatus(
            path=r"C:\Users\KateUser\secret\ephe",
            source="argument",
            mode="files",
            required_files=("sepl_18.se1", "semo_18.se1", "seas_18.se1"),
            found_files=("sepl_18.se1", "semo_18.se1", "seas_18.se1"),
            missing_files=(),
        ),
        selena_method="true_perigee",
        bodies={},
        cusps=None,
        angles=None,
        house_rulers=None,
        interceptions=None,
        aspects=None,
        configurations=None,
        strength=None,
        warnings=warnings,
    )


def _warning(message: str) -> CalculationWarning:
    return CalculationWarning(source="fixture", message=message, retflags=None)


def _resolved(
    *,
    latitude: float = 55.7558,
    longitude: float = 37.6173,
) -> ResolvedBirthData:
    return ResolvedBirthData.model_construct(
        utc_datetime=BASE_UTC,
        latitude=latitude,
        longitude=longitude,
        tz_id="Europe/Moscow",
        utc_offset_seconds=10800,
        canonical_place="Moscow",
        time_unknown=False,
        warnings=(),
    )


def _spec() -> NatalChartSpec:
    return NatalChartSpec(chart_kind="natal")


def _run() -> RunContext:
    return RunContext(run_id=RUN_ID, started_at=BASE_UTC)


class FakeAdapter:
    def __init__(
        self,
        result: CalculationResult | None = None,
        *,
        technique: str = "natal",
        error: Exception | None = None,
    ) -> None:
        self.technique = technique
        self._result = result or _result()
        self._error = error
        self.calls = 0

    def calculate(
        self,
        spec: NatalChartSpec,
        resolved: ResolvedBirthData,
    ) -> CalculationResult:
        self.calls += 1
        if self._error is not None:
            raise self._error
        return self._result


class BlockingAdapter:
    technique = "natal"

    def __init__(
        self,
        result: CalculationResult,
        *,
        entered: Event,
        release: Event,
        thread_ids: list[int],
    ) -> None:
        self._result = result
        self._entered = entered
        self._release = release
        self._thread_ids = thread_ids

    def calculate(
        self,
        spec: NatalChartSpec,
        resolved: ResolvedBirthData,
    ) -> CalculationResult:
        self._thread_ids.append(get_ident())
        self._entered.set()
        assert self._release.wait(2.0)
        return self._result


class NoTechniqueAdapter:
    def calculate(
        self,
        spec: NatalChartSpec,
        resolved: ResolvedBirthData,
    ) -> CalculationResult:
        return _result()


class AsyncAdapter:
    technique = "natal"

    async def calculate(
        self,
        spec: NatalChartSpec,
        resolved: ResolvedBirthData,
    ) -> CalculationResult:
        return _result()


class ExplodingExecutor(ThreadPoolExecutor):
    def submit(self, fn: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError("executor must not be used")


class FakeSwe:
    FLG_SPEED = 256

    class Error(Exception):
        pass

    @staticmethod
    def calc_ut(jd: float, body_id: int, flags: int) -> tuple[tuple[float, ...], int, str]:
        return ((1.0, 2.0, 3.0, 4.0, 5.0, 6.0), FakeSwe.FLG_SPEED, SENSITIVE_MESSAGE)


class FakeSwissBackend:
    swe = FakeSwe
