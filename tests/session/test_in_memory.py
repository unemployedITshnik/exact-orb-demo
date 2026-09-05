"""InMemory session persistence conformance and structural invariants."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from exact_orb.session.adapters import (
    InMemoryDialogStore,
    InMemorySessionPersistence,
    InMemorySessionStore,
)
from exact_orb.session.persistence import SessionSnapshot
from exact_orb.session.state import RESET_DELTA, SessionState
from tests.session.conformance import (
    DELTA,
    NOW,
    PersistenceFactory,
    PersistenceHandles,
    SessionPersistenceConformance,
    create_session,
    make_turn,
)


pytestmark = pytest.mark.no_ephemeris_autoinit


class TestInMemorySessionPersistence(SessionPersistenceConformance):
    def make_factory(self, tmp_path: Path) -> PersistenceFactory:
        @asynccontextmanager
        async def factory():
            persistence = InMemorySessionPersistence()
            yield PersistenceHandles(primary=persistence, peer=persistence)

        return factory

    def test_concrete_suite_collects_inherited_conformance_cases(self) -> None:
        base_tests = {
            name
            for name in dir(SessionPersistenceConformance)
            if name.startswith("test_")
        }

        assert type(self).__name__.startswith("Test")
        assert getattr(type(self), "__test__", True) is not False
        assert base_tests
        assert base_tests <= set(dir(type(self)))
        assert "make_factory" in type(self).__dict__

    def test_facets_require_an_explicit_backend(self) -> None:
        with pytest.raises(TypeError):
            InMemorySessionStore()  # type: ignore[call-arg]
        with pytest.raises(TypeError):
            InMemoryDialogStore()  # type: ignore[call-arg]

    def test_aggregate_facets_share_one_backend_and_lock(self) -> None:
        persistence = InMemorySessionPersistence()

        assert persistence.sessions._backend is persistence.dialogs._backend
        assert persistence.sessions._backend is persistence._backend
        assert persistence.sessions._backend.lock is persistence.dialogs._backend.lock
        assert persistence.sessions is persistence.sessions
        assert persistence.dialogs is persistence.dialogs

    def test_aggregates_do_not_share_backends(self) -> None:
        first = InMemorySessionPersistence()
        second = InMemorySessionPersistence()

        assert first._backend is not second._backend
        assert first._backend.lock is not second._backend.lock

    async def test_touch_without_dialog_does_not_create_private_record(self) -> None:
        persistence = InMemorySessionPersistence()
        await create_session(persistence, "touch-no-dialog")

        result = await persistence.touch("touch-no-dialog", now=NOW)

        assert isinstance(result, SessionSnapshot)
        assert "touch-no-dialog" not in persistence._backend.dialogs

    async def test_clear_removes_private_dialog_record(self) -> None:
        persistence = InMemorySessionPersistence()
        await create_session(persistence, "clear-private")
        await persistence.dialogs.append("clear-private", make_turn(), now=NOW)
        assert "clear-private" in persistence._backend.dialogs

        assert await persistence.dialogs.clear("clear-private", now=NOW) is None

        assert "clear-private" not in persistence._backend.dialogs

    async def test_append_synchronizes_private_deadline(self) -> None:
        persistence = InMemorySessionPersistence()
        await create_session(persistence, "append-deadline")

        await persistence.dialogs.append("append-deadline", make_turn(), now=NOW)
        state = await persistence.sessions.get("append-deadline", now=NOW)

        assert isinstance(state, SessionState)
        assert persistence._backend.dialogs["append-deadline"].expires_at == state.expires_at

    async def test_cas_preserves_turns_and_synchronizes_private_deadline(self) -> None:
        persistence = InMemorySessionPersistence()
        await create_session(persistence, "cas-deadline")
        turn = make_turn()
        await persistence.dialogs.append("cas-deadline", turn, now=NOW)

        assert await persistence.sessions.compare_and_set(
            "cas-deadline",
            0,
            DELTA,
            now=NOW,
        ) == 1
        state = await persistence.sessions.get("cas-deadline", now=NOW)
        record = persistence._backend.dialogs["cas-deadline"]

        assert isinstance(state, SessionState)
        assert record.turns == (turn,)
        assert record.expires_at == state.expires_at

    async def test_touch_preserves_turns_and_synchronizes_private_deadline(self) -> None:
        persistence = InMemorySessionPersistence()
        await create_session(persistence, "touch-deadline")
        turn = make_turn()
        await persistence.dialogs.append("touch-deadline", turn, now=NOW)

        result = await persistence.touch("touch-deadline", now=NOW)
        record = persistence._backend.dialogs["touch-deadline"]

        assert isinstance(result, SessionSnapshot)
        assert record.turns == (turn,)
        assert record.expires_at == result.state.expires_at

    async def test_reset_delegates_once_to_facet_cas(self) -> None:
        persistence = InMemorySessionPersistence()
        spy = AsyncMock(return_value=4)
        persistence.sessions.compare_and_set = spy  # type: ignore[method-assign]

        result = await persistence.reset("delegated", 3, now=NOW)

        assert result == 4
        spy.assert_awaited_once_with("delegated", 3, RESET_DELTA, now=NOW)
        assert spy.await_args.args[2] is RESET_DELTA
