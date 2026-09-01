"""Calculation engine boundary for chart artifacts.

Known debt: the current ``NatalChartSpec`` owns 7 of 15 ``calculate_natal``
parameters. Remaining defaults, including ``selena_method``, are covered by
the architecture debt until ``CalculationVersion`` and fuller specs land.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
import inspect
import logging
from math import isfinite
from time import perf_counter
from types import MappingProxyType
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict

from exact_orb.birth.types import ResolvedBirthData
from exact_orb.calculation.errors import (
    ChartCalculationError,
    CalculationUnavailableError,
)
from exact_orb.calculation.spec import ChartSpec
from exact_orb.domain import (
    ChartKind,
    RulershipScheme,
    normalize_include,
    normalize_natal_house_system_code,
    validate_geography,
)
from exact_orb.engine.charts.natal import NatalChart, calculate_natal
from exact_orb.engine.ephemeris.types import CalculationWarning
from exact_orb.errors import (
    EphemerisConfigurationError,
    EphemerisSessionRequiredError,
)
from exact_orb.run_context import RunContext


LOGGER = logging.getLogger(__name__)
SUPPORTED_TECHNIQUES = frozenset({"natal"})


class CalculationResult(BaseModel):
    """Raw engine result returned before artifact construction."""

    model_config = ConfigDict(frozen=True)

    chart_kind: ChartKind
    chart: NatalChart
    warnings: tuple[CalculationWarning, ...]


class CalculationEnginePort(Protocol):
    """Async calculation boundary used by artifact orchestration."""

    async def calculate(
        self,
        spec: ChartSpec,
        resolved: ResolvedBirthData,
        *,
        run: RunContext,
    ) -> CalculationResult: ...


@runtime_checkable
class TechniqueAdapter(Protocol):
    """Synchronous adapter from a chart spec to one engine technique."""

    technique: str

    def calculate(
        self,
        spec: ChartSpec,
        resolved: ResolvedBirthData,
    ) -> CalculationResult: ...


class NatalTechniqueAdapter:
    """Map ``NatalChartSpec`` and resolved birth data into ``calculate_natal``."""

    technique = "natal"

    def __init__(self, *, calculator: Callable[..., NatalChart] = calculate_natal) -> None:
        self._calculator = calculator

    def calculate(
        self,
        spec: ChartSpec,
        resolved: ResolvedBirthData,
    ) -> CalculationResult:
        chart = self._calculator(
            resolved.utc_datetime,
            resolved.latitude,
            resolved.longitude,
            chart_kind=spec.chart_kind,
            house_system=normalize_natal_house_system_code(spec.house_system),
            rulership=spec.rulership,
            include=frozenset(spec.include),
            near_interception_threshold=spec.near_interception_threshold,
        )
        return CalculationResult(
            chart_kind=chart.chart_kind,
            chart=chart,
            warnings=chart.warnings,
        )


class EngineService:
    """Async service boundary around synchronous calculation techniques."""

    def __init__(
        self,
        *,
        executor: ThreadPoolExecutor,
        techniques: Mapping[str, TechniqueAdapter],
        slow_threshold_ms: float,
    ) -> None:
        if slow_threshold_ms <= 0.0 or not isfinite(slow_threshold_ms):
            raise ValueError("slow_threshold_ms must be positive")
        if not techniques:
            raise ValueError("techniques registry must not be empty")

        registry = dict(techniques)
        registered = frozenset(registry)
        if "natal" not in registered:
            raise ValueError('techniques registry must contain "natal"')

        unknown = registered - SUPPORTED_TECHNIQUES
        if unknown:
            raise ValueError(f"unknown calculation technique(s): {', '.join(sorted(unknown))}")

        for key, adapter in registry.items():
            if not isinstance(adapter, TechniqueAdapter):
                raise ValueError(f"technique {key!r} does not satisfy TechniqueAdapter")
            if key != adapter.technique:
                raise ValueError(f"technique key {key!r} does not match adapter.technique")
            if inspect.iscoroutinefunction(adapter.calculate):
                raise ValueError(f"technique {key!r} must be synchronous")

        self._executor = executor
        self._techniques = MappingProxyType(registry)
        self._slow_threshold_ms = slow_threshold_ms

    async def calculate(
        self,
        spec: ChartSpec,
        resolved: ResolvedBirthData,
        *,
        run: RunContext,
    ) -> CalculationResult:
        run_id = str(run.run_id)
        started_at = perf_counter()
        exception_type = "None"

        try:
            _prevalidate(spec, resolved, run_id)
            adapter = self._adapter_for(spec, run_id)
            LOGGER.info(
                "calculation_started run_id=%s technique=%s chart_kind=%s",
                run_id,
                spec.technique,
                spec.chart_kind,
            )

            loop = asyncio.get_running_loop()
            # The Swiss Ephemeris runtime is guarded by a process-wide lock;
            # the executor keeps the event loop responsive, not faster.
            result = await loop.run_in_executor(
                self._executor,
                _calculate_sync,
                adapter,
                spec,
                resolved,
                run_id,
            )
            _validate_result(result, spec, run_id)
        except (ChartCalculationError, CalculationUnavailableError) as exc:
            exception_type = type(exc).__name__
            self._log_failure(exc, started_at, exception_type)
            raise
        except Exception as exc:
            exception_type = type(exc).__name__
            mapped = _map_engine_error(exc, run_id)
            self._log_failure(mapped, started_at, exception_type)
            raise mapped from None

        duration_ms = _elapsed_ms(started_at)
        LOGGER.info(
            "calculation_finished run_id=%s technique=%s chart_kind=%s duration_ms=%.3f slow=%s",
            run_id,
            spec.technique,
            spec.chart_kind,
            duration_ms,
            duration_ms > self._slow_threshold_ms,
        )
        return result

    def _adapter_for(self, spec: ChartSpec, run_id: str) -> TechniqueAdapter:
        adapter = self._techniques.get(spec.technique)
        if adapter is None:
            raise ChartCalculationError("SPEC_INVALID", run_id=run_id)
        return adapter

    def _log_failure(
        self,
        error: ChartCalculationError | CalculationUnavailableError,
        started_at: float,
        exception_type: str,
    ) -> None:
        duration_ms = _elapsed_ms(started_at)
        LOGGER.warning(
            "calculation_failed run_id=%s code=%s failure_class=%s exception_type=%s "
            "duration_ms=%.3f slow=%s",
            error.run_id,
            error.code,
            type(error).__name__,
            exception_type,
            duration_ms,
            duration_ms > self._slow_threshold_ms,
        )


def _calculate_sync(
    adapter: TechniqueAdapter,
    spec: ChartSpec,
    resolved: ResolvedBirthData,
    run_id: str,
) -> CalculationResult:
    started_at = perf_counter()
    LOGGER.debug(
        "calculation_thread_started run_id=%s technique=%s chart_kind=%s",
        run_id,
        spec.technique,
        spec.chart_kind,
    )
    try:
        return adapter.calculate(spec, resolved)
    finally:
        LOGGER.debug(
            "calculation_thread_finished run_id=%s technique=%s chart_kind=%s duration_ms=%.3f",
            run_id,
            spec.technique,
            spec.chart_kind,
            _elapsed_ms(started_at),
        )


def _prevalidate(spec: ChartSpec, resolved: ResolvedBirthData, run_id: str) -> None:
    if getattr(spec, "technique", None) != "natal":
        raise ChartCalculationError("SPEC_INVALID", run_id=run_id)

    try:
        include = getattr(spec, "include", None)
        if include is None:
            raise ValueError("include must not be None")
        normalize_include(spec.chart_kind, include)
        normalize_natal_house_system_code(spec.house_system)
        RulershipScheme(spec.rulership)
        threshold = float(spec.near_interception_threshold)
        if not isfinite(threshold) or threshold < 0.0:
            raise ValueError("near_interception_threshold must be finite and non-negative")
    except (TypeError, ValueError):
        raise ChartCalculationError("SPEC_INVALID", run_id=run_id) from None

    try:
        validate_geography(resolved.latitude, resolved.longitude)
    except (TypeError, ValueError):
        raise ChartCalculationError("GEOGRAPHY_INVALID", run_id=run_id) from None


def _validate_result(result: CalculationResult, spec: ChartSpec, run_id: str) -> None:
    try:
        if result.chart_kind != spec.chart_kind or result.chart.chart_kind != spec.chart_kind:
            raise ChartCalculationError("ENGINE_UNEXPECTED", run_id=run_id)
    except AttributeError:
        raise ChartCalculationError("ENGINE_UNEXPECTED", run_id=run_id) from None


def _map_engine_error(exc: Exception, run_id: str) -> ChartCalculationError | CalculationUnavailableError:
    if isinstance(exc, EphemerisSessionRequiredError):
        return ChartCalculationError("ENGINE_UNEXPECTED", run_id=run_id)
    if isinstance(exc, EphemerisConfigurationError):
        return CalculationUnavailableError("EPHEMERIS_UNAVAILABLE", run_id=run_id)
    if isinstance(exc, ValueError) and _is_degenerate_houses_error(exc):
        return ChartCalculationError("HOUSES_DEGENERATE", run_id=run_id)
    return ChartCalculationError("ENGINE_UNEXPECTED", run_id=run_id)


def _is_degenerate_houses_error(exc: ValueError) -> bool:
    message = str(exc)
    return "could not calculate house cusps" in message or "Placidus degenerates" in message


def _elapsed_ms(started_at: float) -> float:
    return (perf_counter() - started_at) * 1000.0


__all__ = [
    "CalculationEnginePort",
    "CalculationResult",
    "EngineService",
    "NatalTechniqueAdapter",
    "TechniqueAdapter",
]
