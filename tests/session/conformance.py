"""Reusable black-box conformance suite for session persistence adapters."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from exact_orb.birth.types import BirthInput, ResolutionWarning, ResolvedBirthData
from exact_orb.calculation.spec import NatalChartSpec
from exact_orb.session.dialog import (
    MAX_DIALOG_CHARS,
    MAX_DIALOG_TURN_CHARS,
    MAX_DIALOG_TURNS,
    DialogStore,
    DialogTurn,
    Selection,
)
from exact_orb.session.errors import (
    ExpiredSessionTransitionError,
    StateReadError,
    StateWriteError,
)
from exact_orb.session.outcomes import (
    SessionAbsent,
    SessionCreated,
    SessionIdConflict,
    VersionConflict,
)
from exact_orb.session.persistence import SessionPersistence, SessionSnapshot
from exact_orb.session.state import (
    HARD_TTL,
    RESET_DELTA,
    SLIDING_TTL,
    ChartRef,
    SessionState,
    StateDelta,
)
from exact_orb.session.store import SessionStore


NOW = datetime(2026, 9, 5, 12, tzinfo=UTC)
BIRTH_INPUT = BirthInput(
    birth_date=date(1990, 9, 2),
    birth_time=time(12, 30),
    place_id="moscow",
)
OTHER_BIRTH_INPUT = BirthInput(
    birth_date=date(1991, 10, 3),
    birth_time=time(7, 45),
    place_id="saint-petersburg",
)
RESOLVED = ResolvedBirthData(
    utc_datetime=datetime(1990, 9, 2, 8, 30, tzinfo=UTC),
    latitude=55.75,
    longitude=37.62,
    tz_id="Europe/Moscow",
    utc_offset_seconds=14_400,
    canonical_place="Moscow",
    time_unknown=False,
    warnings=(
        ResolutionWarning(
            source="place",
            code="NORMALIZED",
            message="normalized",
        ),
    ),
)
OTHER_RESOLVED = ResolvedBirthData(
    utc_datetime=datetime(1991, 10, 3, 4, 45, tzinfo=UTC),
    latitude=59.93,
    longitude=30.32,
    tz_id="Europe/Moscow",
    utc_offset_seconds=10_800,
    canonical_place="Saint Petersburg",
    time_unknown=False,
)
SPEC = NatalChartSpec(chart_kind="natal")
OTHER_SPEC = NatalChartSpec(chart_kind="cosmogram")
DELTA = StateDelta(
    birth_input=BIRTH_INPUT,
    birth_resolved=RESOLVED,
    base_chart_spec=SPEC,
)
OTHER_DELTA = StateDelta(
    birth_input=OTHER_BIRTH_INPUT,
    birth_resolved=OTHER_RESOLVED,
    base_chart_spec=OTHER_SPEC,
)
SELECTION = Selection(topic="natal", focus="relationships")


@dataclass(frozen=True)
class PersistenceHandles:
    primary: SessionPersistence
    peer: SessionPersistence


PersistenceFactory = Callable[[], AbstractAsyncContextManager[PersistenceHandles]]


def make_turn(
    index: int = 0,
    *,
    marker: int = 1,
    text: str = "answer",
    status: str = "complete",
    truncated: bool = False,
    created_at: datetime | None = None,
    turn_id: str | None = None,
) -> DialogTurn:
    return DialogTurn(
        turn_id=turn_id or f"turn-{index}",
        created_at=created_at or NOW + timedelta(minutes=index),
        selection=SELECTION,
        state_version_at_answer=marker,
        status=status,
        truncated=truncated,
        text=text,
    )


async def create_session(
    persistence: SessionPersistence,
    session_id: str = "session-1",
    *,
    now: datetime = NOW,
) -> SessionState:
    created = await persistence.sessions.create(session_id, now=now)
    assert isinstance(created, SessionCreated)
    return created.state


async def populate_session(
    persistence: SessionPersistence,
    session_id: str = "session-1",
    *,
    now: datetime = NOW,
    delta: StateDelta = DELTA,
) -> SessionState:
    await create_session(persistence, session_id, now=now)
    committed = await persistence.sessions.compare_and_set(
        session_id,
        0,
        delta,
        now=now,
    )
    assert committed == 1
    state = await persistence.sessions.get(session_id, now=now)
    assert isinstance(state, SessionState)
    return state


async def populate_with_dialog(
    persistence: SessionPersistence,
    session_id: str = "session-1",
    *,
    now: datetime = NOW,
) -> tuple[SessionState, DialogTurn]:
    state = await populate_session(persistence, session_id, now=now)
    turn = make_turn()
    appended = await persistence.dialogs.append(session_id, turn, now=now)
    assert appended is None
    return state, turn


def _pair(
    handles: PersistenceHandles,
    pair_kind: str,
) -> tuple[SessionPersistence, SessionPersistence]:
    if pair_kind == "same-handle":
        return handles.primary, handles.primary
    assert pair_kind == "cross-handle"
    return handles.primary, handles.peer


async def _race(
    left: Callable[[], Coroutine[Any, Any, object]],
    right: Callable[[], Coroutine[Any, Any, object]],
) -> tuple[object, object]:
    barrier = asyncio.Barrier(2)

    async def run(operation: Callable[[], Coroutine[Any, Any, object]]) -> object:
        await barrier.wait()
        return await operation()

    left_result, right_result = await asyncio.wait_for(
        asyncio.gather(run(left), run(right)),
        timeout=5,
    )
    return left_result, right_result


async def _observable(
    persistence: SessionPersistence,
    session_id: str,
    *,
    now: datetime,
) -> tuple[SessionState | SessionAbsent, tuple[DialogTurn, ...] | SessionAbsent]:
    return (
        await persistence.sessions.get(session_id, now=now),
        await persistence.dialogs.read(session_id, now=now),
    )


async def _invoke_now_method(
    persistence: SessionPersistence,
    method_name: str,
    session_id: str,
    *,
    now: datetime,
) -> object:
    if method_name == "create":
        return await persistence.sessions.create(session_id, now=now)
    if method_name == "get":
        return await persistence.sessions.get(session_id, now=now)
    if method_name == "compare_and_set":
        return await persistence.sessions.compare_and_set(
            session_id,
            0,
            DELTA,
            now=now,
        )
    if method_name == "append":
        return await persistence.dialogs.append(
            session_id,
            make_turn(),
            now=now,
        )
    if method_name == "read":
        return await persistence.dialogs.read(session_id, now=now)
    if method_name == "clear":
        return await persistence.dialogs.clear(session_id, now=now)
    if method_name == "touch":
        return await persistence.touch(session_id, now=now)
    if method_name == "reset":
        return await persistence.reset(session_id, 0, now=now)
    raise AssertionError(f"unknown now-bearing method: {method_name}")


async def _renew(
    persistence: SessionPersistence,
    session_id: str,
    renewer: str,
    *,
    now: datetime,
) -> object:
    if renewer == "compare_and_set":
        state = await persistence.sessions.get(session_id, now=now)
        assert isinstance(state, SessionState)
        return await persistence.sessions.compare_and_set(
            session_id,
            state.state_version,
            DELTA,
            now=now,
        )
    if renewer == "touch":
        return await persistence.touch(session_id, now=now)
    if renewer == "append":
        return await persistence.dialogs.append(
            session_id,
            make_turn(int(now.timestamp()), created_at=now),
            now=now,
        )
    if renewer == "clear":
        return await persistence.dialogs.clear(session_id, now=now)
    raise AssertionError(f"unknown renewer: {renewer}")


class SessionPersistenceConformance:
    """Inherited public-contract tests shared by every persistence adapter."""

    def make_factory(self, tmp_path: Path) -> PersistenceFactory:
        raise NotImplementedError

    @pytest.fixture
    def persistence_factory(self, tmp_path: Path) -> PersistenceFactory:
        return self.make_factory(tmp_path)

    async def test_aggregate_facets_are_runtime_protocols_and_stable(
        self,
        persistence_factory: PersistenceFactory,
    ) -> None:
        async with persistence_factory() as handles:
            persistence = handles.primary
            assert isinstance(persistence, SessionPersistence)
            assert isinstance(persistence.sessions, SessionStore)
            assert isinstance(persistence.dialogs, DialogStore)
            assert persistence.sessions is persistence.sessions
            assert persistence.dialogs is persistence.dialogs

    async def test_factory_contexts_are_isolated(
        self,
        persistence_factory: PersistenceFactory,
    ) -> None:
        async with persistence_factory() as first_handles:
            await create_session(first_handles.primary, "isolated")
            async with persistence_factory() as second_handles:
                missing = await second_handles.primary.sessions.get(
                    "isolated",
                    now=NOW,
                )
                assert missing == SessionAbsent(reason="not_found")

    @pytest.mark.parametrize(
        "method_name",
        [
            "create",
            "get",
            "compare_and_set",
            "append",
            "read",
            "clear",
            "touch",
            "reset",
        ],
    )
    @pytest.mark.parametrize(
        "invalid_now",
        [
            datetime(2026, 9, 5, 12),
            datetime(2026, 9, 5, 15, tzinfo=timezone(timedelta(hours=3))),
        ],
        ids=["naive", "non-zero-offset"],
    )
    @pytest.mark.parametrize("lifecycle_kind", ["live", "missing", "expired"])
    async def test_invalid_now_precedes_lookup_and_mutation(
        self,
        persistence_factory: PersistenceFactory,
        method_name: str,
        invalid_now: datetime,
        lifecycle_kind: str,
    ) -> None:
        async with persistence_factory() as handles:
            persistence = handles.primary
            session_id = f"invalid-{method_name}-{lifecycle_kind}"
            if lifecycle_kind == "live":
                created_at = NOW
                await create_session(persistence, session_id, now=created_at)
                await persistence.dialogs.append(
                    session_id,
                    make_turn(),
                    now=created_at,
                )
                inspection_time = created_at
            elif lifecycle_kind == "expired":
                created_at = NOW - SLIDING_TTL
                await create_session(persistence, session_id, now=created_at)
                await persistence.dialogs.append(
                    session_id,
                    make_turn(created_at=created_at),
                    now=created_at,
                )
                inspection_time = created_at
            else:
                inspection_time = NOW

            before = await _observable(
                persistence,
                session_id,
                now=inspection_time,
            )
            with pytest.raises(ValueError) as caught:
                await _invoke_now_method(
                    persistence,
                    method_name,
                    session_id,
                    now=invalid_now,
                )

            assert type(caught.value) is ValueError
            assert not isinstance(
                caught.value,
                (StateReadError, StateWriteError, ExpiredSessionTransitionError),
            )
            after = await _observable(
                persistence,
                session_id,
                now=inspection_time,
            )
            assert after == before

    async def test_create_returns_exact_version_zero_state(
        self,
        persistence_factory: PersistenceFactory,
    ) -> None:
        async with persistence_factory() as handles:
            result = await handles.primary.sessions.create("created", now=NOW)

            assert result == SessionCreated(
                state=SessionState(
                    session_id="created",
                    birth_input=None,
                    birth_resolved=None,
                    state_version=0,
                    base_chart=None,
                    created_at=NOW,
                    expires_at=NOW + SLIDING_TTL,
                    hard_expires_at=NOW + HARD_TTL,
                )
            )
            assert await handles.primary.dialogs.read("created", now=NOW) == ()

    async def test_repeated_create_conflicts_without_overwrite(
        self,
        persistence_factory: PersistenceFactory,
    ) -> None:
        async with persistence_factory() as handles:
            persistence = handles.primary
            state = await populate_session(persistence, "existing")
            result = await persistence.sessions.create("existing", now=NOW)

            assert result == SessionIdConflict(session_id="existing")
            assert await persistence.sessions.get("existing", now=NOW) == state

    async def test_create_conflicts_with_logically_expired_row(
        self,
        persistence_factory: PersistenceFactory,
    ) -> None:
        async with persistence_factory() as handles:
            persistence = handles.primary
            await create_session(persistence, "expired-create")

            assert await persistence.sessions.get(
                "expired-create",
                now=NOW + SLIDING_TTL,
            ) == SessionAbsent(reason="expired")
            assert await persistence.sessions.create(
                "expired-create",
                now=NOW + SLIDING_TTL,
            ) == SessionIdConflict(session_id="expired-create")

    @pytest.mark.parametrize("pair_kind", ["same-handle", "cross-handle"])
    async def test_concurrent_create_has_one_winner(
        self,
        persistence_factory: PersistenceFactory,
        pair_kind: str,
    ) -> None:
        async with persistence_factory() as handles:
            left, right = _pair(handles, pair_kind)
            results = await _race(
                lambda: left.sessions.create("create-race", now=NOW),
                lambda: right.sessions.create("create-race", now=NOW),
            )

            assert sum(isinstance(item, SessionCreated) for item in results) == 1
            assert sum(isinstance(item, SessionIdConflict) for item in results) == 1

    @pytest.mark.parametrize(
        ("session_id", "read_now", "expected"),
        [
            ("live", NOW, "live"),
            ("missing", NOW, "not_found"),
            ("expired", NOW + SLIDING_TTL, "expired"),
        ],
    )
    async def test_get_distinguishes_lifecycle_and_is_read_only(
        self,
        persistence_factory: PersistenceFactory,
        session_id: str,
        read_now: datetime,
        expected: str,
    ) -> None:
        async with persistence_factory() as handles:
            persistence = handles.primary
            if session_id != "missing":
                original = await create_session(persistence, session_id)

            first = await persistence.sessions.get(session_id, now=read_now)
            second = await persistence.sessions.get(session_id, now=read_now)

            if expected == "live":
                assert first == second == original
                assert first.expires_at == NOW + SLIDING_TTL
            else:
                assert first == second == SessionAbsent(reason=expected)

    async def test_exact_expiry_boundary_is_not_purged(
        self,
        persistence_factory: PersistenceFactory,
    ) -> None:
        async with persistence_factory() as handles:
            persistence = handles.primary
            state = await create_session(persistence, "boundary")

            assert await persistence.sessions.get(
                "boundary",
                now=state.expires_at,
            ) == SessionAbsent(reason="expired")
            assert await persistence.sessions.create(
                "boundary",
                now=state.expires_at,
            ) == SessionIdConflict(session_id="boundary")

    async def test_populated_cas_commits_version_and_chart_reference(
        self,
        persistence_factory: PersistenceFactory,
    ) -> None:
        async with persistence_factory() as handles:
            persistence = handles.primary
            await create_session(persistence, "cas")

            result = await persistence.sessions.compare_and_set(
                "cas",
                0,
                DELTA,
                now=NOW,
            )
            stored = await persistence.sessions.get("cas", now=NOW)

            assert result == 1
            assert isinstance(stored, SessionState)
            assert stored.state_version == 1
            assert stored.base_chart == ChartRef(state_version=1, spec=SPEC)

    @pytest.mark.parametrize("lifecycle_kind", ["missing", "expired"])
    async def test_cas_absence_does_not_create_or_revive(
        self,
        persistence_factory: PersistenceFactory,
        lifecycle_kind: str,
    ) -> None:
        async with persistence_factory() as handles:
            persistence = handles.primary
            session_id = f"cas-{lifecycle_kind}"
            now = NOW
            if lifecycle_kind == "expired":
                await create_session(persistence, session_id)
                now = NOW + SLIDING_TTL

            result = await persistence.sessions.compare_and_set(
                session_id,
                0,
                DELTA,
                now=now,
            )

            expected_reason = "not_found" if lifecycle_kind == "missing" else "expired"
            assert result == SessionAbsent(reason=expected_reason)
            assert not isinstance(result, ExpiredSessionTransitionError)
            assert await persistence.sessions.get(session_id, now=now) == SessionAbsent(
                reason=expected_reason
            )

    @pytest.mark.parametrize(
        ("actual_version", "expected_version"),
        [(1, 0), (0, 1)],
        ids=["expected-behind", "expected-ahead-version-zero"],
    )
    async def test_cas_conflict_returns_atomic_actual_without_mutation(
        self,
        persistence_factory: PersistenceFactory,
        actual_version: int,
        expected_version: int,
    ) -> None:
        async with persistence_factory() as handles:
            persistence = handles.primary
            await create_session(persistence, "conflict")
            if actual_version == 1:
                assert await persistence.sessions.compare_and_set(
                    "conflict",
                    0,
                    DELTA,
                    now=NOW,
                ) == 1
            await persistence.dialogs.append("conflict", make_turn(), now=NOW)
            before = await _observable(persistence, "conflict", now=NOW)

            result = await persistence.sessions.compare_and_set(
                "conflict",
                expected_version,
                OTHER_DELTA,
                now=NOW + timedelta(days=1),
            )

            assert isinstance(result, VersionConflict)
            assert result.actual == before[0]
            assert result.actual.state_version == actual_version
            assert await _observable(persistence, "conflict", now=NOW) == before

    @pytest.mark.parametrize("pair_kind", ["same-handle", "cross-handle"])
    async def test_concurrent_cas_commits_once_and_reports_winner(
        self,
        persistence_factory: PersistenceFactory,
        pair_kind: str,
    ) -> None:
        async with persistence_factory() as handles:
            await create_session(handles.primary, "cas-race")
            left, right = _pair(handles, pair_kind)

            results = await _race(
                lambda: left.sessions.compare_and_set(
                    "cas-race", 0, DELTA, now=NOW
                ),
                lambda: right.sessions.compare_and_set(
                    "cas-race", 0, OTHER_DELTA, now=NOW
                ),
            )
            stored = await handles.primary.sessions.get("cas-race", now=NOW)

            assert isinstance(stored, SessionState)
            assert stored.state_version == 1
            assert sum(isinstance(item, int) for item in results) == 1
            conflicts = [item for item in results if isinstance(item, VersionConflict)]
            assert len(conflicts) == 1
            assert conflicts[0].actual == stored

    async def test_successful_cas_renews_state_and_preserves_dialog(
        self,
        persistence_factory: PersistenceFactory,
    ) -> None:
        async with persistence_factory() as handles:
            persistence = handles.primary
            await create_session(persistence, "cas-renew")
            turn = make_turn()
            assert await persistence.dialogs.append("cas-renew", turn, now=NOW) is None
            old_deadline = NOW + SLIDING_TTL

            assert await persistence.sessions.compare_and_set(
                "cas-renew",
                0,
                DELTA,
                now=NOW + timedelta(days=6),
            ) == 1
            live_time = old_deadline + timedelta(days=1)
            state, dialog = await _observable(
                persistence,
                "cas-renew",
                now=live_time,
            )

            assert isinstance(state, SessionState)
            assert state.expires_at == NOW + timedelta(days=13)
            assert dialog == (turn,)

    async def test_dialog_read_handles_empty_missing_expired_without_touch(
        self,
        persistence_factory: PersistenceFactory,
    ) -> None:
        async with persistence_factory() as handles:
            persistence = handles.primary
            state = await create_session(persistence, "empty-dialog")

            assert await persistence.dialogs.read("empty-dialog", now=NOW) == ()
            assert await persistence.dialogs.read("empty-dialog", now=NOW) == ()
            assert await persistence.sessions.get("empty-dialog", now=NOW) == state
            assert await persistence.dialogs.read("missing-dialog", now=NOW) == SessionAbsent(
                reason="not_found"
            )
            assert await persistence.dialogs.read(
                "empty-dialog",
                now=state.expires_at,
            ) == SessionAbsent(reason="expired")

    async def test_append_is_write_and_renew_without_state_version_change(
        self,
        persistence_factory: PersistenceFactory,
    ) -> None:
        async with persistence_factory() as handles:
            persistence = handles.primary
            original = await populate_session(persistence, "append-renew")
            turn = make_turn()
            old_deadline = original.expires_at

            result = await persistence.dialogs.append(
                "append-renew",
                turn,
                now=NOW + timedelta(days=6),
            )
            state = await persistence.sessions.get(
                "append-renew",
                now=old_deadline + timedelta(days=1),
            )
            dialog = await persistence.dialogs.read(
                "append-renew",
                now=old_deadline + timedelta(days=1),
            )

            assert result is None
            assert isinstance(state, SessionState)
            assert state.model_copy(update={"expires_at": original.expires_at}) == original
            assert state.expires_at == NOW + timedelta(days=13)
            assert dialog == (turn,)

    @pytest.mark.parametrize("lifecycle_kind", ["missing", "expired"])
    async def test_append_absence_creates_no_observable_orphan(
        self,
        persistence_factory: PersistenceFactory,
        lifecycle_kind: str,
    ) -> None:
        async with persistence_factory() as handles:
            persistence = handles.primary
            session_id = f"append-{lifecycle_kind}"
            now = NOW
            if lifecycle_kind == "expired":
                await create_session(persistence, session_id)
                now = NOW + SLIDING_TTL

            result = await persistence.dialogs.append(
                session_id,
                make_turn(),
                now=now,
            )

            expected_reason = "not_found" if lifecycle_kind == "missing" else "expired"
            assert result == SessionAbsent(reason=expected_reason)
            assert not isinstance(result, ExpiredSessionTransitionError)
            assert await persistence.sessions.get(session_id, now=now) == SessionAbsent(
                reason=expected_reason
            )
            if lifecycle_kind == "missing":
                await create_session(persistence, session_id)
                assert await persistence.dialogs.read(session_id, now=NOW) == ()

    @pytest.mark.parametrize("pair_kind", ["same-handle", "cross-handle"])
    async def test_concurrent_append_preserves_both_turns(
        self,
        persistence_factory: PersistenceFactory,
        pair_kind: str,
    ) -> None:
        async with persistence_factory() as handles:
            await create_session(handles.primary, "append-race")
            left, right = _pair(handles, pair_kind)
            first = make_turn(1)
            second = make_turn(2)

            results = await _race(
                lambda: left.dialogs.append("append-race", first, now=NOW),
                lambda: right.dialogs.append("append-race", second, now=NOW),
            )
            dialog = await handles.primary.dialogs.read("append-race", now=NOW)

            assert results == (None, None)
            assert isinstance(dialog, tuple)
            assert len(dialog) == 2
            assert set(dialog) == {first, second}

    async def test_sequential_append_uses_append_order_not_created_at(
        self,
        persistence_factory: PersistenceFactory,
    ) -> None:
        async with persistence_factory() as handles:
            persistence = handles.primary
            await create_session(persistence, "order")
            first = make_turn(1, created_at=NOW + timedelta(hours=1))
            second = make_turn(2, created_at=NOW - timedelta(hours=1))
            await persistence.dialogs.append("order", first, now=NOW)
            await persistence.dialogs.append("order", second, now=NOW)

            assert await persistence.dialogs.read("order", now=NOW) == (first, second)

    async def test_duplicate_turn_id_is_appended_twice(
        self,
        persistence_factory: PersistenceFactory,
    ) -> None:
        async with persistence_factory() as handles:
            persistence = handles.primary
            await create_session(persistence, "duplicates")
            first = make_turn(1, turn_id="duplicate")
            second = make_turn(2, turn_id="duplicate")
            await persistence.dialogs.append("duplicates", first, now=NOW)
            await persistence.dialogs.append("duplicates", second, now=NOW)

            assert await persistence.dialogs.read("duplicates", now=NOW) == (
                first,
                second,
            )

    @pytest.mark.parametrize(
        ("current_version", "marker"),
        [(2, 1), (2, 3), (0, 1)],
        ids=["marker-behind", "marker-ahead", "fresh-version-zero"],
    )
    async def test_append_preserves_nonmatching_version_marker(
        self,
        persistence_factory: PersistenceFactory,
        current_version: int,
        marker: int,
    ) -> None:
        async with persistence_factory() as handles:
            persistence = handles.primary
            await create_session(persistence, "marker")
            for expected in range(current_version):
                assert await persistence.sessions.compare_and_set(
                    "marker",
                    expected,
                    DELTA,
                    now=NOW,
                ) == expected + 1
            turn = make_turn(marker=marker)

            assert await persistence.dialogs.append("marker", turn, now=NOW) is None
            assert await persistence.dialogs.read("marker", now=NOW) == (turn,)

    @pytest.mark.parametrize("status", ["complete", "partial"])
    async def test_append_truncates_by_code_points_without_changing_status(
        self,
        persistence_factory: PersistenceFactory,
        status: str,
    ) -> None:
        async with persistence_factory() as handles:
            persistence = handles.primary
            await create_session(persistence, "truncate")
            source = make_turn(
                text="🌙" * (MAX_DIALOG_TURN_CHARS + 1),
                status=status,
            )
            await persistence.dialogs.append("truncate", source, now=NOW)
            dialog = await persistence.dialogs.read("truncate", now=NOW)

            assert isinstance(dialog, tuple)
            assert len(dialog[0].text) == MAX_DIALOG_TURN_CHARS
            assert dialog[0].status == status
            assert dialog[0].truncated is True
            assert source.truncated is False

    async def test_append_evicts_oldest_at_turn_limit(
        self,
        persistence_factory: PersistenceFactory,
    ) -> None:
        async with persistence_factory() as handles:
            persistence = handles.primary
            await create_session(persistence, "turn-limit")
            for index in range(MAX_DIALOG_TURNS + 1):
                await persistence.dialogs.append(
                    "turn-limit",
                    make_turn(index),
                    now=NOW,
                )
            dialog = await persistence.dialogs.read("turn-limit", now=NOW)

            assert isinstance(dialog, tuple)
            assert len(dialog) == MAX_DIALOG_TURNS
            assert dialog[0].turn_id == "turn-1"
            assert dialog[-1].turn_id == f"turn-{MAX_DIALOG_TURNS}"

    async def test_append_evicts_oldest_at_total_character_limit(
        self,
        persistence_factory: PersistenceFactory,
    ) -> None:
        async with persistence_factory() as handles:
            persistence = handles.primary
            await create_session(persistence, "char-limit")
            text = "x" * MAX_DIALOG_TURN_CHARS
            for index in range(16):
                await persistence.dialogs.append(
                    "char-limit",
                    make_turn(index, text=text),
                    now=NOW,
                )
            dialog = await persistence.dialogs.read("char-limit", now=NOW)

            assert isinstance(dialog, tuple)
            assert len(dialog) == MAX_DIALOG_CHARS // MAX_DIALOG_TURN_CHARS
            assert dialog[0].turn_id == "turn-1"
            assert dialog[-1].turn_id == "turn-15"
            assert sum(len(turn.text) for turn in dialog) == MAX_DIALOG_CHARS

    @pytest.mark.parametrize("has_dialog", [False, True])
    async def test_clear_is_write_and_renew_while_preserving_state_content(
        self,
        persistence_factory: PersistenceFactory,
        has_dialog: bool,
    ) -> None:
        async with persistence_factory() as handles:
            persistence = handles.primary
            original = await populate_session(persistence, "clear")
            if has_dialog:
                await persistence.dialogs.append("clear", make_turn(), now=NOW)

            result = await persistence.dialogs.clear(
                "clear",
                now=NOW + timedelta(days=6),
            )
            state = await persistence.sessions.get(
                "clear",
                now=NOW + timedelta(days=8),
            )

            assert result is None
            assert await persistence.dialogs.read(
                "clear",
                now=NOW + timedelta(days=8),
            ) == ()
            assert isinstance(state, SessionState)
            assert state.model_copy(update={"expires_at": original.expires_at}) == original
            assert state.expires_at == NOW + timedelta(days=13)

    @pytest.mark.parametrize("lifecycle_kind", ["missing", "expired"])
    async def test_clear_absence_does_not_create_or_revive(
        self,
        persistence_factory: PersistenceFactory,
        lifecycle_kind: str,
    ) -> None:
        async with persistence_factory() as handles:
            persistence = handles.primary
            session_id = f"clear-{lifecycle_kind}"
            now = NOW
            if lifecycle_kind == "expired":
                await create_session(persistence, session_id)
                now = NOW + SLIDING_TTL

            result = await persistence.dialogs.clear(session_id, now=now)

            expected_reason = "not_found" if lifecycle_kind == "missing" else "expired"
            assert result == SessionAbsent(reason=expected_reason)
            assert not isinstance(result, ExpiredSessionTransitionError)
            assert await persistence.sessions.get(session_id, now=now) == SessionAbsent(
                reason=expected_reason
            )

    @pytest.mark.parametrize("lifecycle_kind", ["missing", "expired"])
    async def test_touch_absence_writes_nothing(
        self,
        persistence_factory: PersistenceFactory,
        lifecycle_kind: str,
    ) -> None:
        async with persistence_factory() as handles:
            persistence = handles.primary
            session_id = f"touch-{lifecycle_kind}"
            now = NOW
            if lifecycle_kind == "expired":
                await create_session(persistence, session_id)
                now = NOW + SLIDING_TTL

            result = await persistence.touch(session_id, now=now)

            expected_reason = "not_found" if lifecycle_kind == "missing" else "expired"
            assert result == SessionAbsent(reason=expected_reason)
            assert not isinstance(result, ExpiredSessionTransitionError)
            assert await persistence.sessions.get(session_id, now=now) == SessionAbsent(
                reason=expected_reason
            )

    async def test_touch_without_dialog_returns_frozen_empty_snapshot(
        self,
        persistence_factory: PersistenceFactory,
    ) -> None:
        async with persistence_factory() as handles:
            persistence = handles.primary
            original = await populate_session(persistence, "touch-empty")

            result = await persistence.touch(
                "touch-empty",
                now=NOW + timedelta(days=6),
            )

            assert isinstance(result, SessionSnapshot)
            assert result.dialog == ()
            assert result.state.expires_at == NOW + timedelta(days=13)
            assert result.state.model_copy(update={"expires_at": original.expires_at}) == original
            with pytest.raises(ValidationError):
                result.dialog = ()  # type: ignore[misc]

    async def test_touch_returns_consistent_renewed_snapshot(
        self,
        persistence_factory: PersistenceFactory,
    ) -> None:
        async with persistence_factory() as handles:
            persistence = handles.primary
            original, turn = await populate_with_dialog(persistence, "touch-dialog")
            old_deadline = original.expires_at

            result = await persistence.touch(
                "touch-dialog",
                now=NOW + timedelta(days=6),
            )
            assert isinstance(result, SessionSnapshot)
            state, dialog = await _observable(
                persistence,
                "touch-dialog",
                now=old_deadline + timedelta(days=1),
            )

            assert result.dialog == (turn,)
            assert result.state.expires_at == NOW + timedelta(days=13)
            assert result.state.model_copy(update={"expires_at": original.expires_at}) == original
            assert state == result.state
            assert dialog == result.dialog

    @pytest.mark.parametrize(
        "renewer",
        ["compare_and_set", "touch", "append", "clear"],
    )
    async def test_renewers_never_shorten_ttl(
        self,
        persistence_factory: PersistenceFactory,
        renewer: str,
    ) -> None:
        async with persistence_factory() as handles:
            persistence = handles.primary
            await create_session(persistence, "non-regression")
            first = await persistence.touch(
                "non-regression",
                now=NOW + timedelta(days=5),
            )
            assert isinstance(first, SessionSnapshot)
            assert first.state.expires_at == NOW + timedelta(days=12)

            await _renew(
                persistence,
                "non-regression",
                renewer,
                now=NOW + timedelta(days=1),
            )
            state = await persistence.sessions.get(
                "non-regression",
                now=NOW + timedelta(days=1),
            )

            assert isinstance(state, SessionState)
            assert state.expires_at == NOW + timedelta(days=12)

    @pytest.mark.parametrize(
        "renewer",
        ["compare_and_set", "touch", "append", "clear"],
    )
    async def test_renewers_clip_at_hard_expiration(
        self,
        persistence_factory: PersistenceFactory,
        renewer: str,
    ) -> None:
        async with persistence_factory() as handles:
            persistence = handles.primary
            await create_session(persistence, "hard-cap")
            for day, expected_day in [(6, 13), (12, 19), (18, 25)]:
                snapshot = await persistence.touch(
                    "hard-cap",
                    now=NOW + timedelta(days=day),
                )
                assert isinstance(snapshot, SessionSnapshot)
                assert snapshot.state.expires_at == NOW + timedelta(days=expected_day)

            await _renew(
                persistence,
                "hard-cap",
                renewer,
                now=NOW + timedelta(days=24),
            )
            capped = await persistence.sessions.get(
                "hard-cap",
                now=NOW + timedelta(days=24),
            )

            assert isinstance(capped, SessionState)
            assert NOW + timedelta(days=24) + SLIDING_TTL == NOW + timedelta(days=31)
            assert capped.hard_expires_at == NOW + HARD_TTL
            assert capped.expires_at == NOW + HARD_TTL
            assert await persistence.sessions.get(
                "hard-cap",
                now=NOW + HARD_TTL,
            ) == SessionAbsent(reason="expired")

    async def test_direct_reset_delta_clears_state_and_dialog(
        self,
        persistence_factory: PersistenceFactory,
    ) -> None:
        async with persistence_factory() as handles:
            persistence = handles.primary
            original, _ = await populate_with_dialog(persistence, "reset-direct")

            result = await persistence.sessions.compare_and_set(
                "reset-direct",
                original.state_version,
                RESET_DELTA,
                now=NOW + timedelta(days=1),
            )
            state, dialog = await _observable(
                persistence,
                "reset-direct",
                now=NOW + timedelta(days=1),
            )

            assert result == 2
            assert isinstance(state, SessionState)
            assert state.state_version == 2
            assert state.birth_input is state.birth_resolved is state.base_chart is None
            assert state.expires_at == NOW + timedelta(days=8)
            assert dialog == ()

    async def test_value_equivalent_reset_delta_uses_reset_semantics(
        self,
        persistence_factory: PersistenceFactory,
    ) -> None:
        async with persistence_factory() as handles:
            persistence = handles.primary
            await populate_with_dialog(persistence, "reset-value")
            equivalent = StateDelta(
                birth_input=None,
                birth_resolved=None,
                base_chart_spec=None,
            )

            assert await persistence.sessions.compare_and_set(
                "reset-value",
                1,
                equivalent,
                now=NOW,
            ) == 2
            assert await persistence.dialogs.read("reset-value", now=NOW) == ()

    async def test_aggregate_reset_matches_direct_cas_reset(
        self,
        persistence_factory: PersistenceFactory,
    ) -> None:
        async with persistence_factory() as handles:
            persistence = handles.primary
            await populate_with_dialog(persistence, "reset-aggregate")
            await populate_with_dialog(persistence, "reset-cas")

            aggregate_result = await persistence.reset(
                "reset-aggregate",
                1,
                now=NOW + timedelta(days=1),
            )
            cas_result = await persistence.sessions.compare_and_set(
                "reset-cas",
                1,
                RESET_DELTA,
                now=NOW + timedelta(days=1),
            )
            aggregate_state, aggregate_dialog = await _observable(
                persistence,
                "reset-aggregate",
                now=NOW + timedelta(days=1),
            )
            cas_state, cas_dialog = await _observable(
                persistence,
                "reset-cas",
                now=NOW + timedelta(days=1),
            )

            assert aggregate_result == cas_result == 2
            assert isinstance(aggregate_state, SessionState)
            assert isinstance(cas_state, SessionState)
            assert aggregate_state.model_copy(update={"session_id": "reset-cas"}) == cas_state
            assert aggregate_dialog == cas_dialog == ()

    async def test_reset_conflict_and_repeated_old_expected_do_not_mutate(
        self,
        persistence_factory: PersistenceFactory,
    ) -> None:
        async with persistence_factory() as handles:
            persistence = handles.primary
            await populate_with_dialog(persistence, "reset-conflict")
            before = await _observable(persistence, "reset-conflict", now=NOW)

            conflict = await persistence.reset(
                "reset-conflict",
                0,
                now=NOW + timedelta(days=1),
            )
            assert isinstance(conflict, VersionConflict)
            assert await _observable(persistence, "reset-conflict", now=NOW) == before

            assert await persistence.reset("reset-conflict", 1, now=NOW) == 2
            repeated = await persistence.reset("reset-conflict", 1, now=NOW)
            assert isinstance(repeated, VersionConflict)
            assert repeated.actual.state_version == 2
            assert repeated.actual.birth_input is None
            assert await persistence.dialogs.read("reset-conflict", now=NOW) == ()

    @pytest.mark.parametrize("lifecycle_kind", ["missing", "expired"])
    async def test_reset_absence_does_not_create_or_revive(
        self,
        persistence_factory: PersistenceFactory,
        lifecycle_kind: str,
    ) -> None:
        async with persistence_factory() as handles:
            persistence = handles.primary
            session_id = f"reset-{lifecycle_kind}"
            now = NOW
            if lifecycle_kind == "expired":
                await create_session(persistence, session_id)
                now = NOW + SLIDING_TTL

            result = await persistence.reset(session_id, 0, now=now)

            expected_reason = "not_found" if lifecycle_kind == "missing" else "expired"
            assert result == SessionAbsent(reason=expected_reason)
            assert not isinstance(result, ExpiredSessionTransitionError)
            assert await persistence.sessions.get(session_id, now=now) == SessionAbsent(
                reason=expected_reason
            )

    @pytest.mark.parametrize("pair_kind", ["same-handle", "cross-handle"])
    async def test_touch_reset_race_has_only_complete_serial_outcomes(
        self,
        persistence_factory: PersistenceFactory,
        pair_kind: str,
    ) -> None:
        async with persistence_factory() as handles:
            persistence = handles.primary
            _, turn = await populate_with_dialog(persistence, "touch-reset-race")
            left, right = _pair(handles, pair_kind)

            touch_result, reset_result = await _race(
                lambda: left.touch("touch-reset-race", now=NOW),
                lambda: right.reset("touch-reset-race", 1, now=NOW),
            )
            final_state, final_dialog = await _observable(
                persistence,
                "touch-reset-race",
                now=NOW,
            )

            assert reset_result == 2
            assert isinstance(touch_result, SessionSnapshot)
            assert (
                touch_result.state.state_version,
                touch_result.dialog,
            ) in {(1, (turn,)), (2, ())}
            assert isinstance(final_state, SessionState)
            assert final_state.state_version == 2
            assert final_state.birth_input is None
            assert final_dialog == ()

    async def test_delete_is_idempotent_for_missing_and_live_or_expired(
        self,
        persistence_factory: PersistenceFactory,
    ) -> None:
        async with persistence_factory() as handles:
            persistence = handles.primary
            await persistence.delete("missing-delete")
            await persistence.delete("missing-delete")

            for session_id, delete_time in [
                ("live-delete", NOW),
                ("expired-delete", NOW + SLIDING_TTL),
            ]:
                await populate_with_dialog(persistence, session_id)
                if delete_time > NOW:
                    assert await persistence.sessions.get(
                        session_id,
                        now=delete_time,
                    ) == SessionAbsent(reason="expired")
                await persistence.delete(session_id)
                assert await persistence.sessions.get(
                    session_id,
                    now=delete_time,
                ) == SessionAbsent(reason="not_found")
                assert await persistence.dialogs.read(
                    session_id,
                    now=delete_time,
                ) == SessionAbsent(reason="not_found")

    @pytest.mark.parametrize("pair_kind", ["same-handle", "cross-handle"])
    async def test_append_delete_race_leaves_no_orphan(
        self,
        persistence_factory: PersistenceFactory,
        pair_kind: str,
    ) -> None:
        async with persistence_factory() as handles:
            persistence = handles.primary
            await create_session(persistence, "append-delete-race")
            left, right = _pair(handles, pair_kind)

            append_result, delete_result = await _race(
                lambda: left.dialogs.append(
                    "append-delete-race",
                    make_turn(),
                    now=NOW,
                ),
                lambda: right.delete("append-delete-race"),
            )

            assert delete_result is None
            assert append_result is None or append_result == SessionAbsent(
                reason="not_found"
            )
            assert await persistence.sessions.get(
                "append-delete-race",
                now=NOW,
            ) == SessionAbsent(reason="not_found")
            assert await persistence.dialogs.read(
                "append-delete-race",
                now=NOW,
            ) == SessionAbsent(reason="not_found")

            await create_session(persistence, "append-delete-race")
            assert await persistence.dialogs.read(
                "append-delete-race",
                now=NOW,
            ) == ()
