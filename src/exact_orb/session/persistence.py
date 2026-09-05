"""Aggregate persistence contract for one session lifecycle."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict

from exact_orb.session.dialog import DialogStore, DialogTurn
from exact_orb.session.outcomes import SessionAbsent, VersionConflict
from exact_orb.session.state import SessionState
from exact_orb.session.store import SessionStore


class SessionSnapshot(BaseModel):
    """State and dialog observed and touched in one aggregate operation."""

    model_config = ConfigDict(frozen=True)

    state: SessionState
    dialog: tuple[DialogTurn, ...]


@runtime_checkable
class SessionPersistence(Protocol):
    """Aggregate exposing co-located state and dialog persistence facets.

    Implementations of ``reset`` delegate to
    ``sessions.compare_and_set(session_id, expected_state_version,
    RESET_DELTA, now=now)``. The facet CAS owns the atomic state transition,
    dialog clear, and shared TTL update; aggregate adapters must not implement
    a second reset algorithm.
    """

    sessions: SessionStore
    dialogs: DialogStore

    async def touch(
        self,
        session_id: str,
        *,
        now: datetime,
    ) -> SessionSnapshot | SessionAbsent:
        """Atomically load and touch live state plus its existing dialog."""

        ...

    async def reset(
        self,
        session_id: str,
        expected_state_version: int,
        *,
        now: datetime,
    ) -> int | VersionConflict | SessionAbsent:
        """Delegate the canonical reset delta to the state facet CAS."""

        ...

    async def delete(self, session_id: str) -> None:
        """Idempotently delete state and dialog in one atomic operation."""

        ...


__all__ = ["SessionPersistence", "SessionSnapshot"]
