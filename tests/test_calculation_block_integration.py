"""Calculation block integration tests without Swiss Ephemeris calls."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
import logging
from typing import Any

import pytest

from exact_orb.calculation.artifacts import ChartArtifactResolver
from exact_orb.calculation.cache import InMemoryCalculationCache
from exact_orb.calculation.codec import decode_chart_artifact, encode_chart_artifact
from exact_orb.calculation.engine import EngineService, NatalTechniqueAdapter
from exact_orb.calculation.errors import (
    CalculationUnavailableError,
    ChartCalculationError,
)
from exact_orb.errors import EphemerisNotInitializedError
from exact_orb.engine.ephemeris.types import CalculationWarning
from tests.fixtures.calculation import (
    OTHER_VERSION,
    RUN_ID,
    SENSITIVE_WARNING,
    VERSION,
    artifact as fixture_artifact,
    calculation_key_for,
    calculation_warning,
    chart_spec,
    raw_chart,
    resolved_birth_data,
    run_context,
)


pytestmark = pytest.mark.no_ephemeris_autoinit


async def test_calculation_block_miss_stores_bytes_and_second_call_hits() -> None:
    spec = chart_spec()
    resolved = resolved_birth_data()
    cache = InMemoryCalculationCache(max_entries=10, ttl_seconds=None)
    calculator = FakeCalculator()

    with _resolver(cache, calculator) as resolver:
        first = await resolver.ensure_chart(spec, resolved, run=run_context())
        second = await resolver.ensure_chart(spec, resolved, run=run_context())

    payload = await cache.get(first.calculation_key)

    assert calculator.calls == 1
    assert payload is not None
    assert isinstance(payload, bytes)
    assert decode_chart_artifact(payload) == first
    assert second == first


async def test_calculation_block_prepared_hit_skips_fake_calculator() -> None:
    spec = chart_spec()
    resolved = resolved_birth_data()
    prepared = fixture_artifact(spec=spec, resolved=resolved)
    cache = InMemoryCalculationCache(max_entries=10, ttl_seconds=None)
    await cache.put(prepared.calculation_key, encode_chart_artifact(prepared))
    calculator = FakeCalculator()

    with _resolver(cache, calculator) as resolver:
        artifact = await resolver.ensure_chart(spec, resolved, run=run_context())

    assert artifact == prepared
    assert calculator.calls == 0


async def test_calculation_block_stale_payload_recalculates_and_replaces_bytes(
    caplog: pytest.LogCaptureFixture,
) -> None:
    spec = chart_spec()
    resolved = resolved_birth_data()
    current_key = calculation_key_for(spec, resolved)
    stale = fixture_artifact(spec=spec, resolved=resolved, version=OTHER_VERSION)
    cache = InMemoryCalculationCache(max_entries=10, ttl_seconds=None)
    await cache.put(current_key, encode_chart_artifact(stale))
    calculator = FakeCalculator()
    caplog.set_level(logging.DEBUG, logger="exact_orb.calculation")

    with _resolver(cache, calculator) as resolver:
        fresh = await resolver.ensure_chart(spec, resolved, run=run_context())

    payload = await cache.get(current_key)

    assert calculator.calls == 1
    assert payload is not None
    assert decode_chart_artifact(payload) == fresh
    assert fresh.calculation_version == VERSION
    assert "cache_stale" in _logs(caplog)


async def test_calculation_block_corrupt_payload_recalculates_and_replaces_bytes(
    caplog: pytest.LogCaptureFixture,
) -> None:
    spec = chart_spec()
    resolved = resolved_birth_data()
    current_key = calculation_key_for(spec, resolved)
    cache = InMemoryCalculationCache(max_entries=10, ttl_seconds=None)
    await cache.put(current_key, b"not gzip")
    calculator = FakeCalculator()
    caplog.set_level(logging.DEBUG, logger="exact_orb.calculation")

    with _resolver(cache, calculator) as resolver:
        fresh = await resolver.ensure_chart(spec, resolved, run=run_context())

    payload = await cache.get(current_key)

    assert calculator.calls == 1
    assert payload is not None
    assert decode_chart_artifact(payload) == fresh
    assert "cache_corrupt" in _logs(caplog)


async def test_calculation_block_generic_value_error_is_not_cached_and_retries() -> None:
    spec = chart_spec()
    resolved = resolved_birth_data()
    cache = InMemoryCalculationCache(max_entries=10, ttl_seconds=None)
    calculator = FakeCalculator(
        outcomes=[
            ValueError("generic fake calculator failure"),
            raw_chart(),
        ]
    )

    with _resolver(cache, calculator) as resolver:
        with pytest.raises(ChartCalculationError) as exc_info:
            await resolver.ensure_chart(spec, resolved, run=run_context())

        assert exc_info.value.code == "ENGINE_UNEXPECTED"
        assert await cache.get(calculation_key_for(spec, resolved)) is None

        artifact = await resolver.ensure_chart(spec, resolved, run=run_context())

    assert artifact.calculation_version == VERSION
    assert calculator.calls == 2
    assert await cache.get(artifact.calculation_key) is not None


async def test_calculation_block_ephemeris_unavailable_is_not_cached() -> None:
    spec = chart_spec()
    resolved = resolved_birth_data()
    cache = InMemoryCalculationCache(max_entries=10, ttl_seconds=None)
    calculator = FakeCalculator(outcomes=[EphemerisNotInitializedError("missing runtime")])

    with _resolver(cache, calculator) as resolver:
        with pytest.raises(CalculationUnavailableError) as exc_info:
            await resolver.ensure_chart(spec, resolved, run=run_context())

    assert exc_info.value.code == "EPHEMERIS_UNAVAILABLE"
    assert calculator.calls == 1
    assert await cache.get(calculation_key_for(spec, resolved)) is None


async def test_calculation_block_logs_share_run_id_across_resolver_and_engine(
    caplog: pytest.LogCaptureFixture,
) -> None:
    spec = chart_spec()
    resolved = resolved_birth_data()
    caplog.set_level(logging.DEBUG, logger="exact_orb.calculation")

    with _resolver(
        InMemoryCalculationCache(max_entries=10, ttl_seconds=None),
        FakeCalculator(),
    ) as resolver:
        await resolver.ensure_chart(spec, resolved, run=run_context())

    messages = [record.getMessage() for record in caplog.records]
    resolver_logs = [message for message in messages if "cache_miss" in message]
    engine_logs = [message for message in messages if "calculation_started" in message]

    assert resolver_logs
    assert engine_logs
    assert all(str(RUN_ID) in message for message in resolver_logs + engine_logs)


async def test_calculation_block_privacy_logs_have_short_key_but_no_sensitive_values(
    caplog: pytest.LogCaptureFixture,
) -> None:
    spec = chart_spec()
    resolved = resolved_birth_data()
    key = calculation_key_for(spec, resolved)
    short_key = key.removeprefix("eo:calc:v1:")[:12]
    caplog.set_level(logging.DEBUG, logger="exact_orb.calculation")

    with _resolver(
        InMemoryCalculationCache(max_entries=10, ttl_seconds=None),
        FakeCalculator(warnings=(calculation_warning(),)),
    ) as resolver:
        await resolver.ensure_chart(spec, resolved, run=run_context())

    logs = _logs(caplog)

    assert str(RUN_ID) in logs
    assert short_key in logs
    assert key not in logs
    for sensitive in (
        "1990-09-02",
        "55.7558",
        "37.6173",
        "Moscow",
        SENSITIVE_WARNING,
        "Traceback",
    ):
        assert sensitive not in logs


@contextmanager
def _resolver(
    cache: InMemoryCalculationCache,
    calculator: "FakeCalculator",
) -> Iterator[ChartArtifactResolver]:
    with ThreadPoolExecutor(max_workers=2) as executor:
        engine = EngineService(
            executor=executor,
            techniques={"natal": NatalTechniqueAdapter(calculator=calculator)},
            slow_threshold_ms=3000.0,
        )
        yield ChartArtifactResolver(
            cache=cache,
            engine=engine,
            version=VERSION,
            degraded_log_interval_s=60.0,
        )


def _logs(caplog: pytest.LogCaptureFixture) -> str:
    return "\n".join(
        record.getMessage()
        for record in caplog.records
        if record.name.startswith("exact_orb.calculation")
    )


class FakeCalculator:
    def __init__(
        self,
        *,
        outcomes: list[Any] | None = None,
        warnings: tuple[CalculationWarning, ...] = (),
    ) -> None:
        self.outcomes = list(outcomes or [])
        self.warnings = warnings
        self.calls = 0
        self.received: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        self.calls += 1
        self.received.append((args, kwargs))
        if self.outcomes:
            outcome = self.outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome
        return raw_chart(
            chart_kind=kwargs["chart_kind"],
            latitude=args[1],
            longitude=args[2],
            warnings=self.warnings,
        )
