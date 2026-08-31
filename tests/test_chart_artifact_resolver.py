"""Chart artifact resolver orchestration tests."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime, timezone
import gzip
import json
import logging
from typing import Any
from uuid import UUID

import pytest

from exact_orb.birth.types import ResolvedBirthData
from exact_orb.calculation import artifacts as artifacts_module
from exact_orb.calculation.artifacts import ChartArtifactResolver
from exact_orb.calculation.codec import encode_chart_artifact
from exact_orb.calculation.engine import CalculationResult
from exact_orb.calculation.errors import ChartCalculationError
from exact_orb.calculation.keys import calculation_input_from, calculation_key
from exact_orb.calculation.spec import NatalChartSpec
from exact_orb.calculation.types import ArtifactNatalChart, ChartArtifact
from exact_orb.config import EphemerisStatus
from exact_orb.engine.charts.natal import NatalChart
from exact_orb.engine.ephemeris.types import CalculationWarning
from exact_orb.run_context import RunContext


pytestmark = pytest.mark.no_ephemeris_autoinit

BASE_UTC = datetime(1990, 9, 2, 10, 30, 45, tzinfo=timezone.utc)
RUN_ID = UUID("11111111-1111-4111-8111-111111111111")
RUN_ID_B = UUID("22222222-2222-4222-8222-222222222222")
VERSION = "test-version-1"
OTHER_VERSION = "test-version-2"
FULL_KEY_PLACEHOLDER = "eo:calc:v1:" + ("0" * 64)
SENSITIVE_WARNING = "sensitive warning for 1990-09-02 55.7558 37.6173 Moscow"


def test_constructor_validates_settings_and_initial_stats() -> None:
    resolver = ChartArtifactResolver(
        cache=FakeCache(),
        engine=FakeEngine(_result()),
        version=VERSION,
        degraded_log_interval_s=60.0,
    )

    assert resolver.hit_ratio is None
    assert resolver.hits == 0
    assert resolver.misses == 0

    with pytest.raises(ValueError, match="version"):
        ChartArtifactResolver(
            cache=FakeCache(),
            engine=FakeEngine(_result()),
            version="",
            degraded_log_interval_s=60.0,
        )

    with pytest.raises(ValueError, match="degraded_log_interval_s"):
        ChartArtifactResolver(
            cache=FakeCache(),
            engine=FakeEngine(_result()),
            version=VERSION,
            degraded_log_interval_s=0.0,
        )


async def test_cache_hit_returns_decoded_artifact_without_engine_or_put(
    caplog: pytest.LogCaptureFixture,
) -> None:
    spec = _spec()
    resolved = _resolved()
    artifact = _artifact(spec=spec, resolved=resolved)
    key = _key(spec, resolved)
    cache = FakeCache({key: encode_chart_artifact(artifact)})
    engine = FakeEngine(_result())
    resolver = _resolver(cache, engine)
    caplog.set_level(logging.DEBUG, logger="exact_orb.calculation.artifacts")

    hit = await resolver.ensure_chart(spec, resolved, run=_run())

    assert hit == artifact
    assert engine.calls == 0
    assert cache.put_calls == []
    assert resolver.hits == 1
    assert resolver.misses == 0
    assert resolver.hit_ratio == 1.0
    assert any("cache_hit" in record.getMessage() for record in caplog.records)


async def test_miss_calculates_stores_and_next_call_hits_equal_artifact() -> None:
    spec = _spec()
    resolved = _resolved()
    cache = FakeCache()
    engine = FakeEngine(_result(warnings=(_warning(SENSITIVE_WARNING),)))
    resolver = _resolver(cache, engine)
    key = _key(spec, resolved)

    first = await resolver.ensure_chart(spec, resolved, run=_run())
    second = await resolver.ensure_chart(spec, resolved, run=_run())

    assert first == second
    assert engine.calls == 1
    assert len(cache.put_calls) == 1
    assert cache.put_calls[0][0] == key
    assert first.calculation_key == key
    assert first.calculation_version == VERSION
    assert isinstance(first.chart, ArtifactNatalChart)
    assert not hasattr(first.chart.ephemeris, "path")
    assert not hasattr(first.chart.ephemeris, "source")
    assert resolver.misses == 1
    assert resolver.hits == 1
    assert resolver.put_ok == 1
    assert resolver.hit_ratio == 0.5


async def test_version_change_misses_on_same_inputs() -> None:
    spec = _spec()
    resolved = _resolved()
    old_artifact = _artifact(spec=spec, resolved=resolved, version=VERSION)
    cache = FakeCache({_key(spec, resolved, version=VERSION): encode_chart_artifact(old_artifact)})
    engine = FakeEngine(_result())
    resolver = _resolver(cache, engine, version=OTHER_VERSION)

    artifact = await resolver.ensure_chart(spec, resolved, run=_run())

    assert artifact.calculation_version == OTHER_VERSION
    assert engine.calls == 1
    assert resolver.misses == 1
    assert resolver.hits == 0


@pytest.mark.parametrize(
    "artifact_factory",
    (
        lambda spec, resolved, key: _artifact(
            spec=spec,
            resolved=resolved,
            key=FULL_KEY_PLACEHOLDER,
        ),
        lambda spec, resolved, key: _artifact(
            spec=spec,
            resolved=resolved,
            version=OTHER_VERSION,
        ),
        lambda spec, resolved, key: _artifact(
            spec=NatalChartSpec(chart_kind="natal", include=("houses", "positions")),
            resolved=resolved,
            key=key,
        ),
    ),
)
async def test_stale_hit_recalculates_and_logs_cached_version(
    artifact_factory: Callable[[NatalChartSpec, ResolvedBirthData, str], ChartArtifact],
    caplog: pytest.LogCaptureFixture,
) -> None:
    spec = _spec()
    resolved = _resolved()
    key = _key(spec, resolved)
    cache = FakeCache({key: encode_chart_artifact(artifact_factory(spec, resolved, key))})
    engine = FakeEngine(_result())
    resolver = _resolver(cache, engine)
    caplog.set_level(logging.DEBUG, logger="exact_orb.calculation.artifacts")

    artifact = await resolver.ensure_chart(spec, resolved, run=_run())

    assert artifact.calculation_key == key
    assert engine.calls == 1
    assert resolver.stale == 1
    assert resolver.misses == 1
    logs = "\n".join(record.getMessage() for record in caplog.records)
    assert "cache_stale" in logs
    assert f"cached_version={VERSION}" in logs or f"cached_version={OTHER_VERSION}" in logs


@pytest.mark.parametrize(
    ("payload", "reason"),
    (
        (b"", "gzip"),
        (b"not gzip", "gzip"),
        (gzip.compress(b"\xff", compresslevel=6, mtime=0), "utf8"),
        (gzip.compress(b"{}", compresslevel=6, mtime=0), "validation"),
    ),
)
async def test_corrupt_hit_recalculates_with_reason(
    payload: bytes,
    reason: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    spec = _spec()
    resolved = _resolved()
    cache = FakeCache({_key(spec, resolved): payload})
    engine = FakeEngine(_result())
    resolver = _resolver(cache, engine)
    caplog.set_level(logging.DEBUG, logger="exact_orb.calculation.artifacts")

    await resolver.ensure_chart(spec, resolved, run=_run())

    assert engine.calls == 1
    assert resolver.corrupt == 1
    assert resolver.misses == 1
    record = next(record for record in caplog.records if "cache_corrupt" in record.getMessage())
    assert record.levelno == logging.WARNING
    assert f"reason={reason}" in record.getMessage()


async def test_cache_get_failure_is_fail_open_miss_and_degraded(
    caplog: pytest.LogCaptureFixture,
) -> None:
    cache = FakeCache(get_errors=[TimeoutError("contains 1990-09-02 Moscow")])
    engine = FakeEngine(_result())
    resolver = _resolver(cache, engine)
    caplog.set_level(logging.DEBUG, logger="exact_orb.calculation.artifacts")

    artifact = await resolver.ensure_chart(_spec(), _resolved(), run=_run())

    assert artifact.calculation_version == VERSION
    assert engine.calls == 1
    assert resolver.misses == 1
    assert resolver.cache_errors_total == 1
    logs = "\n".join(record.getMessage() for record in caplog.records)
    assert "cache_degraded" in logs
    assert "op=get" in logs
    assert "reason=TimeoutError" in logs
    assert "contains 1990-09-02 Moscow" not in logs


async def test_cache_put_failure_returns_artifact_and_logs_put_failed(
    caplog: pytest.LogCaptureFixture,
) -> None:
    cache = FakeCache(put_errors=[ConnectionError("contains 55.7558")])
    engine = FakeEngine(_result())
    resolver = _resolver(cache, engine)
    caplog.set_level(logging.DEBUG, logger="exact_orb.calculation.artifacts")

    artifact = await resolver.ensure_chart(_spec(), _resolved(), run=_run())

    assert artifact.calculation_version == VERSION
    assert resolver.put_failed == 1
    assert resolver.cache_errors_total == 1
    logs = "\n".join(record.getMessage() for record in caplog.records)
    assert "cache_put_failed" in logs
    assert "reason=ConnectionError" in logs
    assert "contains 55.7558" not in logs


async def test_encode_failure_returns_artifact_without_put(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    def fail_encode(artifact: ChartArtifact) -> bytes:
        raise RuntimeError("contains 37.6173")

    cache = FakeCache()
    engine = FakeEngine(_result())
    resolver = _resolver(cache, engine)
    monkeypatch.setattr(artifacts_module, "encode_chart_artifact", fail_encode)
    caplog.set_level(logging.DEBUG, logger="exact_orb.calculation.artifacts")

    artifact = await resolver.ensure_chart(_spec(), _resolved(), run=_run())

    assert artifact.calculation_version == VERSION
    assert cache.put_calls == []
    assert resolver.put_failed == 1
    assert resolver.cache_errors_total == 0
    logs = "\n".join(record.getMessage() for record in caplog.records)
    assert "cache_put_failed" in logs
    assert "reason=RuntimeError" in logs
    assert "contains 37.6173" not in logs


async def test_artifact_construction_failure_maps_to_engine_unexpected() -> None:
    chart = _raw_chart(warnings=())
    result = CalculationResult(
        chart_kind="natal",
        chart=chart,
        warnings=(_warning(SENSITIVE_WARNING),),
    )
    cache = FakeCache()
    engine = FakeEngine(result)
    resolver = _resolver(cache, engine)

    with pytest.raises(ChartCalculationError) as exc_info:
        await resolver.ensure_chart(_spec(), _resolved(), run=_run())

    assert exc_info.value.code == "ENGINE_UNEXPECTED"
    assert exc_info.value.run_id == str(RUN_ID)
    assert exc_info.value.__cause__ is None
    assert cache.put_calls == []
    assert resolver._inflight == {}


async def test_engine_error_is_not_cached_and_next_call_retries() -> None:
    engine = SequenceEngine(
        [
            ChartCalculationError("ENGINE_UNEXPECTED", run_id=str(RUN_ID)),
            _result(),
        ]
    )
    cache = FakeCache()
    resolver = _resolver(cache, engine)

    with pytest.raises(ChartCalculationError):
        await resolver.ensure_chart(_spec(), _resolved(), run=_run())

    assert cache.put_calls == []
    assert resolver._inflight == {}

    artifact = await resolver.ensure_chart(_spec(), _resolved(), run=_run())

    assert artifact.calculation_version == VERSION
    assert engine.calls == 2
    assert len(cache.put_calls) == 1


async def test_concurrent_miss_singleflight_calls_engine_once() -> None:
    cache = FakeCache()
    engine = BlockingEngine()
    resolver = _resolver(cache, engine)
    spec = _spec()
    resolved = _resolved()

    tasks = [
        asyncio.create_task(resolver.ensure_chart(spec, resolved, run=_run(run_id)))
        for run_id in (RUN_ID, RUN_ID_B, UUID("33333333-3333-4333-8333-333333333333"))
    ]
    await asyncio.wait_for(engine.first_entered.wait(), timeout=1.0)
    await asyncio.sleep(0)
    engine.release.set()

    artifacts = await asyncio.gather(*tasks)

    assert engine.calls == 1
    assert artifacts[0] == artifacts[1] == artifacts[2]
    assert len(cache.put_calls) == 1
    assert resolver.misses == 3
    assert resolver._inflight == {}


async def test_singleflight_engine_error_fans_out_and_cleanup() -> None:
    error = ChartCalculationError("ENGINE_UNEXPECTED", run_id=str(RUN_ID))
    engine = BlockingEngine(error=error)
    resolver = _resolver(FakeCache(), engine)

    tasks = [
        asyncio.create_task(resolver.ensure_chart(_spec(), _resolved(), run=_run(run_id)))
        for run_id in (RUN_ID, RUN_ID_B)
    ]
    await asyncio.wait_for(engine.first_entered.wait(), timeout=1.0)
    await asyncio.sleep(0)
    engine.release.set()
    results = await asyncio.gather(*tasks, return_exceptions=True)

    assert engine.calls == 1
    assert all(result is error for result in results)
    assert resolver._inflight == {}


async def test_cancelling_joiner_does_not_cancel_shared_task() -> None:
    cache = FakeCache()
    engine = BlockingEngine()
    resolver = _resolver(cache, engine)

    leader = asyncio.create_task(resolver.ensure_chart(_spec(), _resolved(), run=_run(RUN_ID)))
    await asyncio.wait_for(engine.first_entered.wait(), timeout=1.0)
    joiner = asyncio.create_task(resolver.ensure_chart(_spec(), _resolved(), run=_run(RUN_ID_B)))
    await asyncio.sleep(0)

    joiner.cancel()
    with pytest.raises(asyncio.CancelledError):
        await joiner

    engine.release.set()
    artifact = await leader

    assert artifact.calculation_version == VERSION
    assert engine.calls == 1
    assert len(cache.put_calls) == 1


async def test_cancelling_leader_does_not_cancel_shared_task() -> None:
    cache = FakeCache()
    engine = BlockingEngine()
    resolver = _resolver(cache, engine)

    leader = asyncio.create_task(resolver.ensure_chart(_spec(), _resolved(), run=_run(RUN_ID)))
    await asyncio.wait_for(engine.first_entered.wait(), timeout=1.0)
    joiner = asyncio.create_task(resolver.ensure_chart(_spec(), _resolved(), run=_run(RUN_ID_B)))
    await asyncio.sleep(0)

    leader.cancel()
    with pytest.raises(asyncio.CancelledError):
        await leader

    engine.release.set()
    artifact = await joiner

    assert artifact.calculation_version == VERSION
    assert engine.calls == 1
    assert len(cache.put_calls) == 1


async def test_all_waiters_can_cancel_while_shared_task_still_stores_result() -> None:
    cache = FakeCache()
    engine = BlockingEngine()
    resolver = _resolver(cache, engine)
    loop = asyncio.get_running_loop()
    contexts: list[dict[str, Any]] = []
    old_handler = loop.get_exception_handler()
    loop.set_exception_handler(lambda _loop, context: contexts.append(context))

    try:
        leader = asyncio.create_task(resolver.ensure_chart(_spec(), _resolved(), run=_run(RUN_ID)))
        await asyncio.wait_for(engine.first_entered.wait(), timeout=1.0)
        joiner = asyncio.create_task(resolver.ensure_chart(_spec(), _resolved(), run=_run(RUN_ID_B)))
        await asyncio.sleep(0)
        leader.cancel()
        joiner.cancel()

        for task in (leader, joiner):
            with pytest.raises(asyncio.CancelledError):
                await task

        engine.release.set()
        await _wait_until(lambda: len(cache.put_calls) == 1 and resolver._inflight == {})
        await asyncio.sleep(0)
    finally:
        loop.set_exception_handler(old_handler)

    assert engine.calls == 1
    assert contexts == []


async def test_different_keys_are_not_serialized_by_resolver() -> None:
    cache = FakeCache()
    engine = BlockingEngine(target_entries=2)
    resolver = _resolver(cache, engine)
    task_a = asyncio.create_task(resolver.ensure_chart(_spec(), _resolved(), run=_run(RUN_ID)))
    task_b = asyncio.create_task(
        resolver.ensure_chart(
            NatalChartSpec(chart_kind="natal", house_system="K"),
            _resolved(),
            run=_run(RUN_ID_B),
        )
    )

    await asyncio.wait_for(engine.target_entered.wait(), timeout=1.0)
    assert engine.max_active == 2
    engine.release.set()
    await asyncio.gather(task_a, task_b)

    assert engine.calls == 2


async def test_done_callback_drains_unobserved_exception_without_loop_noise() -> None:
    error = ChartCalculationError("ENGINE_UNEXPECTED", run_id=str(RUN_ID))
    engine = BlockingEngine(error=error)
    resolver = _resolver(FakeCache(), engine)
    loop = asyncio.get_running_loop()
    contexts: list[dict[str, Any]] = []
    old_handler = loop.get_exception_handler()
    loop.set_exception_handler(lambda _loop, context: contexts.append(context))

    try:
        caller = asyncio.create_task(resolver.ensure_chart(_spec(), _resolved(), run=_run()))
        await asyncio.wait_for(engine.first_entered.wait(), timeout=1.0)
        caller.cancel()
        with pytest.raises(asyncio.CancelledError):
            await caller

        engine.release.set()
        await _wait_until(lambda: resolver._inflight == {})
        await asyncio.sleep(0)
    finally:
        loop.set_exception_handler(old_handler)

    assert contexts == []


async def test_done_callback_handles_cancelled_shared_task_without_loop_noise() -> None:
    engine = BlockingEngine()
    resolver = _resolver(FakeCache(), engine)
    loop = asyncio.get_running_loop()
    contexts: list[dict[str, Any]] = []
    old_handler = loop.get_exception_handler()
    loop.set_exception_handler(lambda _loop, context: contexts.append(context))

    try:
        caller = asyncio.create_task(resolver.ensure_chart(_spec(), _resolved(), run=_run()))
        await asyncio.wait_for(engine.first_entered.wait(), timeout=1.0)
        entry = next(iter(resolver._inflight.values()))
        entry.task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await caller
        await _wait_until(lambda: resolver._inflight == {})
        await asyncio.sleep(0)
    finally:
        loop.set_exception_handler(old_handler)

    assert contexts == []


async def test_degraded_logs_are_throttled_and_recovered_per_operation(
    caplog: pytest.LogCaptureFixture,
) -> None:
    clock = FakeClock()
    cache = FakeCache(
        get_errors=[
            TimeoutError("first"),
            TimeoutError("second"),
            TimeoutError("third"),
            None,
        ],
        put_errors=[ConnectionError("put failed"), None],
    )
    engine = FakeEngine(_result())
    resolver = _resolver(cache, engine, clock=clock)
    caplog.set_level(logging.DEBUG, logger="exact_orb.calculation.artifacts")

    await resolver.ensure_chart(_spec(), _resolved(longitude=37.6173), run=_run())
    clock.advance(10.0)
    await resolver.ensure_chart(_spec(), _resolved(longitude=37.6174), run=_run())
    clock.advance(51.0)
    await resolver.ensure_chart(_spec(), _resolved(longitude=37.6175), run=_run())
    clock.advance(1.0)
    await resolver.ensure_chart(_spec(), _resolved(longitude=37.6176), run=_run())

    messages = [record.getMessage() for record in caplog.records]
    degraded_get = [
        message for message in messages if "cache_degraded" in message and "op=get" in message
    ]
    degraded_put = [
        message for message in messages if "cache_degraded" in message and "op=put" in message
    ]
    recovered_get = [
        message for message in messages if "cache_recovered" in message and "op=get" in message
    ]
    recovered_put = [
        message for message in messages if "cache_recovered" in message and "op=put" in message
    ]

    assert len(degraded_get) == 2
    assert "suppressed=2" in degraded_get[1]
    assert len(degraded_put) == 1
    assert len(recovered_get) == 1
    assert len(recovered_put) == 1
    assert resolver.cache_errors_total == 4
    assert resolver.put_failed == 1
    assert resolver.put_ok == 3
    assert resolver.misses == 4


async def test_stats_count_hits_misses_stale_corrupt_and_puts() -> None:
    spec = _spec()
    resolved = _resolved()
    key = _key(spec, resolved)
    stale_artifact = _artifact(spec=spec, resolved=resolved, version=OTHER_VERSION)
    cache = FakeCache({key: encode_chart_artifact(stale_artifact)})
    engine = FakeEngine(_result())
    resolver = _resolver(cache, engine)

    await resolver.ensure_chart(spec, resolved, run=_run())
    await resolver.ensure_chart(spec, resolved, run=_run())

    assert resolver.stale == 1
    assert resolver.corrupt == 0
    assert resolver.misses == 1
    assert resolver.hits == 1
    assert resolver.put_ok == 1
    assert resolver.put_failed == 0
    assert resolver.hit_ratio == 0.5


async def test_privacy_logs_omit_sensitive_values(
    caplog: pytest.LogCaptureFixture,
) -> None:
    spec = _spec()
    resolved = _resolved()
    full_key = _key(spec, resolved)
    warning = _warning(SENSITIVE_WARNING)

    caplog.set_level(logging.DEBUG, logger="exact_orb.calculation")

    hit_cache = FakeCache({full_key: encode_chart_artifact(_artifact(warnings=(warning,)))})
    await _resolver(hit_cache, FakeEngine(_result(warnings=(warning,)))).ensure_chart(
        spec,
        resolved,
        run=_run(),
    )

    await _resolver(FakeCache(), FakeEngine(_result(warnings=(warning,)))).ensure_chart(
        spec,
        resolved,
        run=_run(),
    )

    stale_cache = FakeCache(
        {full_key: encode_chart_artifact(_artifact(version=OTHER_VERSION, warnings=(warning,)))}
    )
    await _resolver(stale_cache, FakeEngine(_result(warnings=(warning,)))).ensure_chart(
        spec,
        resolved,
        run=_run(),
    )

    corrupt_cache = FakeCache({full_key: gzip.compress(b"{}", compresslevel=6, mtime=0)})
    await _resolver(corrupt_cache, FakeEngine(_result(warnings=(warning,)))).ensure_chart(
        spec,
        resolved,
        run=_run(),
    )

    degraded_cache = FakeCache(get_errors=[TimeoutError("1990-09-02 Moscow")])
    await _resolver(degraded_cache, FakeEngine(_result(warnings=(warning,)))).ensure_chart(
        spec,
        resolved,
        run=_run(),
    )

    logs = "\n".join(record.getMessage() for record in caplog.records)

    assert str(RUN_ID) in logs
    assert full_key not in logs
    for sensitive in (
        "1990-09-02",
        "55.7558",
        "37.6173",
        "Moscow",
        SENSITIVE_WARNING,
        "ValidationError",
        "Traceback",
    ):
        assert sensitive not in logs


def _resolver(
    cache: FakeCache,
    engine: Any,
    *,
    version: str = VERSION,
    clock: Callable[[], float] | None = None,
) -> ChartArtifactResolver:
    return ChartArtifactResolver(
        cache=cache,
        engine=engine,
        version=version,
        degraded_log_interval_s=60.0,
        clock=clock,
    )


def _key(
    spec: NatalChartSpec,
    resolved: ResolvedBirthData,
    *,
    version: str = VERSION,
) -> str:
    return calculation_key(calculation_input_from(resolved), spec, version)


def _artifact(
    *,
    spec: NatalChartSpec | None = None,
    resolved: ResolvedBirthData | None = None,
    chart: NatalChart | None = None,
    warnings: tuple[CalculationWarning, ...] | None = None,
    key: str | None = None,
    version: str = VERSION,
) -> ChartArtifact:
    spec = spec or _spec()
    resolved = resolved or _resolved()
    chart = chart or _raw_chart(chart_kind=spec.chart_kind, warnings=warnings or ())
    warnings = warnings if warnings is not None else chart.warnings
    key = key or _key(spec, resolved, version=version)
    return ChartArtifact(
        calculation_key=key,
        calculation_version=version,
        spec=spec,
        chart_kind=spec.chart_kind,
        chart=chart,
        warnings=warnings,
    )


def _result(
    *,
    chart_kind: str = "natal",
    warnings: tuple[CalculationWarning, ...] = (),
) -> CalculationResult:
    chart = _raw_chart(chart_kind=chart_kind, warnings=warnings)
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


def _run(run_id: UUID = RUN_ID) -> RunContext:
    return RunContext(run_id=run_id, started_at=BASE_UTC)


def _warning(message: str) -> CalculationWarning:
    return CalculationWarning(source="fixture", message=message, retflags=None)


async def _wait_until(
    predicate: Callable[[], bool],
    *,
    timeout: float = 1.0,
) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() >= deadline:
            raise AssertionError("condition was not met before timeout")
        await asyncio.sleep(0.01)


class FakeClock:
    def __init__(self, value: float = 0.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class FakeCache:
    def __init__(
        self,
        entries: dict[str, bytes] | None = None,
        *,
        get_errors: list[Exception | None] | None = None,
        put_errors: list[Exception | None] | None = None,
    ) -> None:
        self.entries = dict(entries or {})
        self.get_errors = list(get_errors or [])
        self.put_errors = list(put_errors or [])
        self.get_calls: list[str] = []
        self.put_calls: list[tuple[str, bytes]] = []

    async def get(self, key: str) -> bytes | None:
        self.get_calls.append(key)
        if self.get_errors:
            error = self.get_errors.pop(0)
            if error is not None:
                raise error
        return self.entries.get(key)

    async def put(self, key: str, payload: bytes) -> None:
        if self.put_errors:
            error = self.put_errors.pop(0)
            if error is not None:
                raise error
        self.put_calls.append((key, payload))
        self.entries[key] = payload


class FakeEngine:
    def __init__(self, result: CalculationResult) -> None:
        self.result = result
        self.calls = 0

    async def calculate(
        self,
        spec: NatalChartSpec,
        resolved: ResolvedBirthData,
        *,
        run: RunContext,
    ) -> CalculationResult:
        self.calls += 1
        return self.result


class SequenceEngine:
    def __init__(self, outcomes: list[CalculationResult | Exception]) -> None:
        self.outcomes = list(outcomes)
        self.calls = 0

    async def calculate(
        self,
        spec: NatalChartSpec,
        resolved: ResolvedBirthData,
        *,
        run: RunContext,
    ) -> CalculationResult:
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class BlockingEngine:
    def __init__(
        self,
        *,
        error: Exception | None = None,
        target_entries: int = 1,
    ) -> None:
        self.error = error
        self.target_entries = target_entries
        self.first_entered = asyncio.Event()
        self.target_entered = asyncio.Event()
        self.release = asyncio.Event()
        self.calls = 0
        self.active = 0
        self.max_active = 0

    async def calculate(
        self,
        spec: NatalChartSpec,
        resolved: ResolvedBirthData,
        *,
        run: RunContext,
    ) -> CalculationResult:
        self.calls += 1
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        self.first_entered.set()
        if self.calls >= self.target_entries:
            self.target_entered.set()
        try:
            await self.release.wait()
            if self.error is not None:
                raise self.error
            return _result(chart_kind=spec.chart_kind)
        finally:
            self.active -= 1
