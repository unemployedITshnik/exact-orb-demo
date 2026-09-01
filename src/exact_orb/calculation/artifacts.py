"""Chart artifact resolver orchestration.

Known debt until ``CalculationVersion`` exists:
* callers must pass a ``version`` string that already covers deployment
  choices affecting numbers, including ``selena_method``;
* ``calculation_*`` events are logged by ``EngineService`` without the key
  because the engine boundary intentionally does not know cache identity;
* cache operation timeouts belong to a future Redis adapter or cache settings,
  not to this resolver;
* ``artifacts.py`` is part of the calculation package, but not the
  backend-free contract exported from ``exact_orb.calculation``.
"""

from __future__ import annotations

import asyncio
from asyncio import tasks as asyncio_tasks
from collections.abc import Callable
from dataclasses import dataclass
import logging
from math import isfinite
import time
from typing import Literal

from pydantic import ValidationError

from exact_orb.birth.types import ResolvedBirthData
from exact_orb.domain import validate_geography
from exact_orb.run_context import RunContext

from .cache import CalculationCache
from .codec import ChartArtifactDecodeError, decode_chart_artifact, encode_chart_artifact
from .engine import CalculationEnginePort
from .errors import ChartCalculationError
from .keys import KEY_PREFIX, calculation_input_from, calculation_key
from .spec import ChartSpec
from .types import ChartArtifact


LOGGER = logging.getLogger(__name__)

CacheOperation = Literal["get", "put"]
CacheOutcome = Literal["hit", "miss"]


@dataclass
class _ResolutionState:
    cache_outcome: CacheOutcome | None = None


@dataclass(frozen=True)
class _InFlight:
    task: asyncio.Task[ChartArtifact]
    leader_run_id: str
    started_at: float
    state: _ResolutionState


@dataclass
class _DegradedOperation:
    degraded: bool = False
    started_at: float = 0.0
    last_logged_at: float = 0.0
    suppressed: int = 0
    last_reason: str = ""


class ChartArtifactResolver:
    """Single entry point for cache-backed chart artifact retrieval."""

    def __init__(
        self,
        *,
        cache: CalculationCache,
        engine: CalculationEnginePort,
        version: str,
        degraded_log_interval_s: float,
        clock: Callable[[], float] | None = None,
    ) -> None:
        if not version:
            raise ValueError("version must be non-empty")
        if (
            isinstance(degraded_log_interval_s, bool)
            or not isinstance(degraded_log_interval_s, (int, float))
            or not isfinite(degraded_log_interval_s)
            or degraded_log_interval_s <= 0
        ):
            raise ValueError("degraded_log_interval_s must be a finite positive number")

        self.cache = cache
        self.engine = engine
        self.version = version
        self.degraded_log_interval_s = degraded_log_interval_s
        self._clock = clock or time.monotonic
        self._inflight: dict[str, _InFlight] = {}
        self._degraded: dict[CacheOperation, _DegradedOperation] = {
            "get": _DegradedOperation(),
            "put": _DegradedOperation(),
        }

        self.hits = 0
        self.misses = 0
        self.stale = 0
        self.corrupt = 0
        self.put_ok = 0
        self.put_failed = 0
        self.cache_errors_total = 0

    @property
    def hit_ratio(self) -> float | None:
        total = self.hits + self.misses
        if total == 0:
            return None
        return self.hits / total

    async def ensure_chart(
        self,
        spec: ChartSpec,
        resolved: ResolvedBirthData,
        *,
        run: RunContext,
    ) -> ChartArtifact:
        run_id = str(run.run_id)
        try:
            validate_geography(resolved.latitude, resolved.longitude)
        except (TypeError, ValueError):
            raise ChartCalculationError("GEOGRAPHY_INVALID", run_id=run_id) from None

        calc_input = calculation_input_from(resolved)
        key = calculation_key(calc_input, spec, self.version)
        return await self._ensure_singleflight(key, spec, resolved, run)

    async def _get_valid_hit(
        self,
        key: str,
        spec: ChartSpec,
        run_id: str,
    ) -> ChartArtifact | None:
        key_prefix = _short_key(key)
        try:
            payload = await self.cache.get(key)
        except Exception as exc:
            self.cache_errors_total += 1
            self._record_cache_degraded("get", _reason(exc), run_id)
            return None

        self._record_cache_recovered("get", run_id)
        if payload is None:
            LOGGER.debug(
                "cache_miss run_id=%s key=%s chart_kind=%s",
                run_id,
                key_prefix,
                spec.chart_kind,
            )
            return None

        try:
            artifact = decode_chart_artifact(payload)
        except ChartArtifactDecodeError as exc:
            self.corrupt += 1
            LOGGER.warning(
                "cache_corrupt run_id=%s key=%s reason=%s",
                run_id,
                key_prefix,
                exc.reason,
            )
            return None

        if (
            artifact.calculation_key != key
            or artifact.calculation_version != self.version
            or artifact.spec != spec
        ):
            self.stale += 1
            LOGGER.warning(
                "cache_stale run_id=%s key=%s cached_version=%s",
                run_id,
                key_prefix,
                artifact.calculation_version,
            )
            return None

        LOGGER.debug(
            "cache_hit run_id=%s key=%s chart_kind=%s",
            run_id,
            key_prefix,
            artifact.chart_kind,
        )
        return artifact

    async def _ensure_singleflight(
        self,
        key: str,
        spec: ChartSpec,
        resolved: ResolvedBirthData,
        run: RunContext,
    ) -> ChartArtifact:
        # No await between lookup and task insertion: this is the process-local
        # single-flight critical section in one event loop.
        entry = self._inflight.get(key)
        if entry is None:
            state = _ResolutionState()
            task = asyncio.create_task(
                self._resolve_and_store(key, spec, resolved, run, state)
            )
            entry = _InFlight(
                task=task,
                leader_run_id=str(run.run_id),
                started_at=self._clock(),
                state=state,
            )
            self._inflight[key] = entry
            task.add_done_callback(_drain_task)
        else:
            LOGGER.debug(
                "singleflight_join run_id=%s leader_run_id=%s key=%s waited_ms=%.3f",
                str(run.run_id),
                entry.leader_run_id,
                _short_key(key),
                (self._clock() - entry.started_at) * 1000,
            )

        waiter = asyncio.shield(entry.task)
        try:
            artifact = await waiter
            return artifact.model_copy(deep=True)
        except asyncio.CancelledError:
            _remove_shield_exception_logger(entry.task)
            raise
        finally:
            self._record_cache_outcome(entry.state.cache_outcome)
            waiter.add_done_callback(_drain_future)

    async def _resolve_and_store(
        self,
        key: str,
        spec: ChartSpec,
        resolved: ResolvedBirthData,
        run: RunContext,
        state: _ResolutionState,
    ) -> ChartArtifact:
        try:
            artifact = await self._get_valid_hit(key, spec, str(run.run_id))
            if artifact is not None:
                state.cache_outcome = "hit"
                return artifact

            state.cache_outcome = "miss"
            return await self._calculate_and_store(key, spec, resolved, run)
        finally:
            current = asyncio.current_task()
            entry = self._inflight.get(key)
            if entry is not None and entry.task is current:
                del self._inflight[key]

    async def _calculate_and_store(
        self,
        key: str,
        spec: ChartSpec,
        resolved: ResolvedBirthData,
        run: RunContext,
    ) -> ChartArtifact:
        result = await self.engine.calculate(spec, resolved, run=run)
        run_id = str(run.run_id)
        try:
            artifact = ChartArtifact(
                calculation_key=key,
                calculation_version=self.version,
                spec=spec,
                chart_kind=result.chart_kind,
                chart=result.chart,
                warnings=result.warnings,
            )
        except ValidationError:
            raise ChartCalculationError("ENGINE_UNEXPECTED", run_id=run_id) from None

        await self._try_store(key, artifact, run_id)
        return artifact

    async def _try_store(self, key: str, artifact: ChartArtifact, run_id: str) -> None:
        key_prefix = _short_key(key)
        try:
            payload = encode_chart_artifact(artifact)
        except Exception as exc:
            self.put_failed += 1
            LOGGER.warning(
                "cache_put_failed run_id=%s key=%s op=put reason=%s",
                run_id,
                key_prefix,
                _reason(exc),
            )
            return

        try:
            await self.cache.put(key, payload)
        except Exception as exc:
            reason = _reason(exc)
            self.put_failed += 1
            self.cache_errors_total += 1
            LOGGER.warning(
                "cache_put_failed run_id=%s key=%s op=put reason=%s",
                run_id,
                key_prefix,
                reason,
            )
            self._record_cache_degraded("put", reason, run_id)
            return

        self.put_ok += 1
        self._record_cache_recovered("put", run_id)
        LOGGER.debug("cache_put_ok run_id=%s key=%s", run_id, key_prefix)

    def _record_cache_degraded(
        self,
        op: CacheOperation,
        reason: str,
        run_id: str,
    ) -> None:
        state = self._degraded[op]
        now = self._clock()
        state.last_reason = reason

        if not state.degraded:
            state.degraded = True
            state.started_at = now
            state.last_logged_at = now
            state.suppressed = 0
            LOGGER.warning("cache_degraded run_id=%s op=%s reason=%s", run_id, op, reason)
            return

        state.suppressed += 1
        if now - state.last_logged_at >= self.degraded_log_interval_s:
            LOGGER.warning(
                "cache_degraded run_id=%s op=%s reason=%s suppressed=%d",
                run_id,
                op,
                reason,
                state.suppressed,
            )
            state.last_logged_at = now
            state.suppressed = 0

    def _record_cache_recovered(self, op: CacheOperation, run_id: str) -> None:
        state = self._degraded[op]
        if not state.degraded:
            return

        now = self._clock()
        LOGGER.info(
            "cache_recovered run_id=%s op=%s degraded_ms=%.3f suppressed=%d",
            run_id,
            op,
            (now - state.started_at) * 1000,
            state.suppressed,
        )
        self._degraded[op] = _DegradedOperation()

    def _record_cache_outcome(self, outcome: CacheOutcome | None) -> None:
        if outcome == "hit":
            self.hits += 1
        elif outcome == "miss":
            self.misses += 1


def _short_key(key: str) -> str:
    if key.startswith(KEY_PREFIX):
        return key[len(KEY_PREFIX) : len(KEY_PREFIX) + 12]
    return key[:12]


def _reason(exc: Exception) -> str:
    return type(exc).__name__


def _drain_task(task: asyncio.Task[ChartArtifact]) -> None:
    if task.cancelled():
        return
    task.exception()


def _drain_future(future: asyncio.Future[ChartArtifact]) -> None:
    if future.cancelled():
        return
    future.exception()


def _remove_shield_exception_logger(task: asyncio.Task[ChartArtifact]) -> None:
    callback = getattr(asyncio_tasks, "_log_on_exception", None)
    if callback is None:
        return
    task.remove_done_callback(callback)
    task.get_loop().call_soon(task.remove_done_callback, callback)


__all__ = ["ChartArtifactResolver"]
