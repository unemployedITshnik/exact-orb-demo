"""Calculation cache contract tests."""

from __future__ import annotations

from collections import OrderedDict

import pytest

from exact_orb.calculation import cache as cache_module
from exact_orb.calculation.cache import CalculationCache, InMemoryCalculationCache


pytestmark = pytest.mark.no_ephemeris_autoinit


class FakeClock:
    def __init__(self, value: float = 0.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


async def test_empty_cache_misses() -> None:
    cache = InMemoryCalculationCache(max_entries=2, ttl_seconds=None)

    assert await cache.get("missing") is None


async def test_put_then_get_returns_exact_bytes() -> None:
    cache = InMemoryCalculationCache(max_entries=2, ttl_seconds=None)
    payload = b"payload"

    await cache.put("key", payload)

    assert await cache.get("key") == payload


async def test_put_accepts_only_bytes() -> None:
    cache = InMemoryCalculationCache(max_entries=2, ttl_seconds=None)

    for payload in (bytearray(b"x"), memoryview(b"x"), "x", object()):
        with pytest.raises(TypeError, match="payload must be bytes"):
            await cache.put("key", payload)  # type: ignore[arg-type]


async def test_repeated_put_updates_payload_expiry_and_recency() -> None:
    clock = FakeClock()
    cache = InMemoryCalculationCache(max_entries=2, ttl_seconds=10.0, clock=clock)

    await cache.put("a", b"old")
    clock.advance(1.0)
    await cache.put("b", b"b")
    await cache.put("a", b"new")
    await cache.put("c", b"c")

    assert len(cache) == 2
    assert await cache.get("a") == b"new"
    assert await cache.get("b") is None
    assert await cache.get("c") == b"c"


async def test_get_updates_lru_recency() -> None:
    cache = InMemoryCalculationCache(max_entries=2, ttl_seconds=None)

    await cache.put("a", b"a")
    await cache.put("b", b"b")
    assert await cache.get("a") == b"a"
    await cache.put("c", b"c")

    assert await cache.get("b") is None
    assert await cache.get("a") == b"a"
    assert await cache.get("c") == b"c"


async def test_put_purges_expired_entries_before_lru_eviction() -> None:
    clock = FakeClock()
    cache = InMemoryCalculationCache(max_entries=2, ttl_seconds=5.0, clock=clock)

    await cache.put("expired-a", b"a")
    await cache.put("expired-b", b"b")
    clock.advance(6.0)
    await cache.put("fresh", b"fresh")

    assert len(cache) == 1
    assert await cache.get("fresh") == b"fresh"
    assert await cache.get("expired-a") is None
    assert await cache.get("expired-b") is None


async def test_put_evicts_until_size_is_within_max_entries() -> None:
    cache = InMemoryCalculationCache(max_entries=2, ttl_seconds=None)
    cache._entries = OrderedDict(
        (
            ("a", cache_module._CacheEntry(payload=b"a", expires_at=None)),
            ("b", cache_module._CacheEntry(payload=b"b", expires_at=None)),
            ("c", cache_module._CacheEntry(payload=b"c", expires_at=None)),
        )
    )

    await cache.put("d", b"d")

    assert len(cache) == 2
    assert await cache.get("a") is None
    assert await cache.get("b") is None
    assert await cache.get("c") == b"c"
    assert await cache.get("d") == b"d"


async def test_expired_entry_misses_and_is_removed_from_physical_size() -> None:
    clock = FakeClock()
    cache = InMemoryCalculationCache(max_entries=2, ttl_seconds=5.0, clock=clock)

    await cache.put("a", b"a")
    clock.advance(5.0)

    assert await cache.get("a") is None
    assert len(cache) == 0


async def test_ttl_none_disables_expiration() -> None:
    clock = FakeClock()
    cache = InMemoryCalculationCache(max_entries=2, ttl_seconds=None, clock=clock)

    await cache.put("a", b"a")
    clock.advance(10_000.0)

    assert await cache.get("a") == b"a"


async def test_clear_removes_entries_and_cache_remains_usable() -> None:
    cache = InMemoryCalculationCache(max_entries=2, ttl_seconds=None)

    await cache.put("a", b"a")
    cache.clear()
    await cache.put("b", b"b")

    assert len(cache) == 1
    assert await cache.get("a") is None
    assert await cache.get("b") == b"b"


@pytest.mark.parametrize(
    "max_entries",
    (0, -1, 1.5, float("nan"), float("inf"), float("-inf"), True),
)
def test_invalid_max_entries_is_rejected(max_entries: object) -> None:
    with pytest.raises(ValueError, match="max_entries"):
        InMemoryCalculationCache(
            max_entries=max_entries,  # type: ignore[arg-type]
            ttl_seconds=None,
        )


@pytest.mark.parametrize(
    "ttl_seconds",
    (0.0, -1.0, float("nan"), float("inf"), float("-inf"), True),
)
def test_invalid_ttl_seconds_is_rejected(ttl_seconds: object) -> None:
    with pytest.raises(ValueError, match="ttl_seconds"):
        InMemoryCalculationCache(
            max_entries=1,
            ttl_seconds=ttl_seconds,  # type: ignore[arg-type]
        )


def test_protocol_is_runtime_checkable_and_implemented_structurally() -> None:
    cache = InMemoryCalculationCache(max_entries=1, ttl_seconds=None)

    assert isinstance(cache, CalculationCache)


def test_default_clock_is_time_monotonic() -> None:
    cache = InMemoryCalculationCache(max_entries=1, ttl_seconds=None)

    assert cache._clock is cache_module.time.monotonic
