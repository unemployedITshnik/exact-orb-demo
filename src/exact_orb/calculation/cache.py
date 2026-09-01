"""Opaque binary calculation cache contracts and in-memory implementation."""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from math import isfinite
import time
from typing import Protocol, runtime_checkable


@runtime_checkable
class CalculationCache(Protocol):
    """Async opaque key-value cache for serialized calculation artifacts."""

    async def get(self, key: str) -> bytes | None: ...

    async def put(self, key: str, payload: bytes) -> None: ...


@dataclass(frozen=True)
class _CacheEntry:
    payload: bytes
    expires_at: float | None


class InMemoryCalculationCache:
    """Process-local LRU cache storing opaque bytes."""

    def __init__(
        self,
        *,
        max_entries: int,
        ttl_seconds: float | None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        if (
            isinstance(max_entries, bool)
            or not isinstance(max_entries, int)
            or max_entries <= 0
        ):
            raise ValueError("max_entries must be a positive integer")
        if ttl_seconds is not None and (
            isinstance(ttl_seconds, bool)
            or not isinstance(ttl_seconds, (int, float))
            or not isfinite(ttl_seconds)
            or ttl_seconds <= 0
        ):
            raise ValueError("ttl_seconds must be a finite positive number or None")

        self.max_entries = max_entries
        self.ttl_seconds = ttl_seconds
        self._clock = clock or time.monotonic
        self._entries: OrderedDict[str, _CacheEntry] = OrderedDict()

    async def get(self, key: str) -> bytes | None:
        entry = self._entries.get(key)
        if entry is None:
            return None

        if self._is_expired(entry):
            del self._entries[key]
            return None

        self._entries.move_to_end(key)
        return entry.payload

    async def put(self, key: str, payload: bytes) -> None:
        if not isinstance(payload, bytes):
            raise TypeError("payload must be bytes")

        self._purge_expired()
        if key in self._entries:
            del self._entries[key]

        self._entries[key] = _CacheEntry(
            payload=payload,
            expires_at=self._expires_at(),
        )
        while len(self._entries) > self.max_entries:
            self._entries.popitem(last=False)

    def clear(self) -> None:
        self._entries.clear()

    def __len__(self) -> int:
        return len(self._entries)

    def _expires_at(self) -> float | None:
        if self.ttl_seconds is None:
            return None
        return self._clock() + self.ttl_seconds

    def _is_expired(self, entry: _CacheEntry) -> bool:
        return entry.expires_at is not None and self._clock() >= entry.expires_at

    def _purge_expired(self) -> None:
        expired_keys = [key for key, entry in self._entries.items() if self._is_expired(entry)]
        for key in expired_keys:
            del self._entries[key]


__all__ = [
    "CalculationCache",
    "InMemoryCalculationCache",
]
