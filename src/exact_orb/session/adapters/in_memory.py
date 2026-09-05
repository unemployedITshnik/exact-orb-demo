"""Process-local implementation of the session persistence contracts."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime

from exact_orb.session.adapters._time import validate_now
from exact_orb.session.dialog import DialogTurn, append_dialog_turn
from exact_orb.session.outcomes import (
    SessionAbsent,
    SessionCreated,
    SessionIdConflict,
    VersionConflict,
)
from exact_orb.session.persistence import SessionSnapshot
from exact_orb.session.state import (
    RESET_DELTA,
    SessionState,
    StateDelta,
    apply_delta,
    is_expired,
    new_session,
    touched,
)


@dataclass(frozen=True, slots=True)
class _DialogRecord:
    turns: tuple[DialogTurn, ...]
    expires_at: datetime


class _InMemoryBackend:
    def __init__(self) -> None:
        self.lock = asyncio.Lock()
        self.states: dict[str, SessionState] = {}
        self.dialogs: dict[str, _DialogRecord] = {}


def _live_state(
    backend: _InMemoryBackend,
    session_id: str,
    *,
    now: datetime,
) -> SessionState | SessionAbsent:
    state = backend.states.get(session_id)
    if state is None:
        return SessionAbsent(reason="not_found")
    if is_expired(state, now=now):
        return SessionAbsent(reason="expired")
    return state


class InMemorySessionStore:
    """State facet backed by one aggregate-owned process-local backend."""

    def __init__(self, backend: _InMemoryBackend, /) -> None:
        self._backend = backend

    async def create(
        self,
        session_id: str,
        *,
        now: datetime,
    ) -> SessionCreated | SessionIdConflict:
        validate_now(now)
        async with self._backend.lock:
            if session_id in self._backend.states:
                return SessionIdConflict(session_id=session_id)

            state = new_session(session_id, now=now)
            self._backend.states[session_id] = state
            return SessionCreated(state=state)

    async def get(
        self,
        session_id: str,
        *,
        now: datetime,
    ) -> SessionState | SessionAbsent:
        validate_now(now)
        async with self._backend.lock:
            return _live_state(self._backend, session_id, now=now)

    async def compare_and_set(
        self,
        session_id: str,
        expected_state_version: int,
        delta: StateDelta,
        *,
        now: datetime,
    ) -> int | VersionConflict | SessionAbsent:
        validate_now(now)
        async with self._backend.lock:
            actual = _live_state(self._backend, session_id, now=now)
            if isinstance(actual, SessionAbsent):
                return actual
            if actual.state_version != expected_state_version:
                return VersionConflict(actual=actual)

            next_state = apply_delta(actual, delta, now=now)
            if delta == RESET_DELTA:
                next_dialog = None
            else:
                dialog = self._backend.dialogs.get(session_id)
                next_dialog = (
                    _DialogRecord(
                        turns=dialog.turns,
                        expires_at=next_state.expires_at,
                    )
                    if dialog is not None
                    else None
                )

            self._backend.states[session_id] = next_state
            if delta == RESET_DELTA:
                self._backend.dialogs.pop(session_id, None)
            elif next_dialog is not None:
                self._backend.dialogs[session_id] = next_dialog
            return next_state.state_version


class InMemoryDialogStore:
    """Dialog facet backed by the aggregate's state lifecycle and lock."""

    def __init__(self, backend: _InMemoryBackend, /) -> None:
        self._backend = backend

    async def append(
        self,
        session_id: str,
        turn: DialogTurn,
        *,
        now: datetime,
    ) -> None | SessionAbsent:
        validate_now(now)
        async with self._backend.lock:
            actual = _live_state(self._backend, session_id, now=now)
            if isinstance(actual, SessionAbsent):
                return actual

            dialog = self._backend.dialogs.get(session_id)
            previous = dialog.turns if dialog is not None else ()
            bounded_turns = append_dialog_turn(previous, turn)
            next_state = touched(actual, now=now)
            next_dialog = _DialogRecord(
                turns=bounded_turns,
                expires_at=next_state.expires_at,
            )

            self._backend.states[session_id] = next_state
            self._backend.dialogs[session_id] = next_dialog
            return None

    async def read(
        self,
        session_id: str,
        *,
        now: datetime,
    ) -> tuple[DialogTurn, ...] | SessionAbsent:
        validate_now(now)
        async with self._backend.lock:
            state = _live_state(self._backend, session_id, now=now)
            if isinstance(state, SessionAbsent):
                return state
            dialog = self._backend.dialogs.get(session_id)
            return dialog.turns if dialog is not None else ()

    async def clear(
        self,
        session_id: str,
        *,
        now: datetime,
    ) -> None | SessionAbsent:
        validate_now(now)
        async with self._backend.lock:
            actual = _live_state(self._backend, session_id, now=now)
            if isinstance(actual, SessionAbsent):
                return actual

            next_state = touched(actual, now=now)
            self._backend.states[session_id] = next_state
            self._backend.dialogs.pop(session_id, None)
            return None


class InMemorySessionPersistence:
    """Aggregate owning coherent state and dialog facets."""

    sessions: InMemorySessionStore
    dialogs: InMemoryDialogStore

    def __init__(self) -> None:
        backend = _InMemoryBackend()
        self._backend = backend
        self.sessions = InMemorySessionStore(backend)
        self.dialogs = InMemoryDialogStore(backend)

    async def touch(
        self,
        session_id: str,
        *,
        now: datetime,
    ) -> SessionSnapshot | SessionAbsent:
        validate_now(now)
        async with self._backend.lock:
            actual = _live_state(self._backend, session_id, now=now)
            if isinstance(actual, SessionAbsent):
                return actual

            dialog = self._backend.dialogs.get(session_id)
            turns = dialog.turns if dialog is not None else ()
            next_state = touched(actual, now=now)
            next_dialog = (
                _DialogRecord(turns=turns, expires_at=next_state.expires_at)
                if dialog is not None
                else None
            )

            self._backend.states[session_id] = next_state
            if next_dialog is not None:
                self._backend.dialogs[session_id] = next_dialog
            return SessionSnapshot(state=next_state, dialog=turns)

    async def reset(
        self,
        session_id: str,
        expected_state_version: int,
        *,
        now: datetime,
    ) -> int | VersionConflict | SessionAbsent:
        validate_now(now)
        return await self.sessions.compare_and_set(
            session_id,
            expected_state_version,
            RESET_DELTA,
            now=now,
        )

    async def delete(self, session_id: str) -> None:
        async with self._backend.lock:
            self._backend.dialogs.pop(session_id, None)
            self._backend.states.pop(session_id, None)


__all__ = [
    "InMemoryDialogStore",
    "InMemorySessionPersistence",
    "InMemorySessionStore",
]
