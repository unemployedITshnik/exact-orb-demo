"""Process-wide Swiss Ephemeris runtime lock."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import threading

from exact_orb.errors import EphemerisSessionRequiredError


_LOCK = threading.RLock()
_THREAD_STATE = threading.local()


@contextmanager
def ephemeris_session() -> Iterator[None]:
    """Serialize Swiss Ephemeris calls in this process."""

    _LOCK.acquire()
    depth = getattr(_THREAD_STATE, "depth", 0)
    _THREAD_STATE.depth = depth + 1
    try:
        yield
    finally:
        current_depth = getattr(_THREAD_STATE, "depth", 0)
        if current_depth <= 1:
            _THREAD_STATE.depth = 0
        else:
            _THREAD_STATE.depth = current_depth - 1
        _LOCK.release()


def require_ephemeris_session() -> None:
    """Require the current thread to be inside an ephemeris session."""

    if getattr(_THREAD_STATE, "depth", 0) <= 0:
        raise EphemerisSessionRequiredError(
            "Swiss Ephemeris calls require an active ephemeris_session()"
        )

