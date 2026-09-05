"""State persistence port for session lifecycle operations."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from exact_orb.session.outcomes import (
    SessionAbsent,
    SessionCreated,
    SessionIdConflict,
    VersionConflict,
)
from exact_orb.session.state import SessionState, StateDelta


@runtime_checkable
class SessionStore(Protocol):
    """Atomic, TTL-aware state facet implemented by P2 and P4 adapters."""

    async def create(
        self,
        session_id: str,
        *,
        now: datetime,
    ) -> SessionCreated | SessionIdConflict:
        """Insert a new lifecycle without overwriting any existing row."""

        ...

    async def get(
        self,
        session_id: str,
        *,
        now: datetime,
    ) -> SessionState | SessionAbsent:
        """Read live state without touching it or raise ``StateReadError``."""

        ...

    async def compare_and_set(
        self,
        session_id: str,
        expected_state_version: int,
        delta: StateDelta,
        *,
        now: datetime,
    ) -> int | VersionConflict | SessionAbsent:
        """Atomically apply ``delta`` or raise ``StateWriteError``.

        A failed comparison returns the actual state from the same atomic
        operation and changes neither state, dialog, nor their TTL. Applying
        ``RESET_DELTA`` also clears the dialog in that operation.
        """

        ...


__all__ = ["SessionStore"]
