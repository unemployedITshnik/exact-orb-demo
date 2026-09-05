"""Shape tests for public session outcomes, errors, and persistence ports."""

from __future__ import annotations

from datetime import UTC, datetime
from inspect import Parameter, iscoroutinefunction, signature

import pytest
from pydantic import ValidationError

import exact_orb.session as session_contracts
import exact_orb.session.outcomes as outcomes_module
from exact_orb.session.dialog import DialogStore, DialogTurn, Selection
from exact_orb.session.errors import (
    ExpiredSessionTransitionError,
    SessionPersistenceError,
    StateReadError,
    StateWriteError,
)
from exact_orb.session.outcomes import (
    AlreadyApplied,
    Committed,
    SessionAbsent,
    SessionCreated,
    SessionIdConflict,
    StateCommitFailed,
    StateReadFailed,
    Superseded,
    VersionConflict,
)
from exact_orb.session.persistence import SessionPersistence, SessionSnapshot
from exact_orb.session.state import RESET_DELTA, SessionState, new_session
from exact_orb.session.store import SessionStore


NOW = datetime(2026, 9, 5, 12, tzinfo=UTC)


def _new_state() -> SessionState:
    return new_session("session-1", now=NOW)


def _turn() -> DialogTurn:
    return DialogTurn(
        turn_id="turn-1",
        created_at=NOW,
        selection=Selection(topic="natal", focus="relationships"),
        state_version_at_answer=1,
        status="complete",
        text="answer",
    )


def test_persistence_errors_preserve_one_error_code_contract() -> None:
    for error_type in (SessionPersistenceError, StateReadError, StateWriteError):
        error = error_type("STORAGE_UNAVAILABLE")
        assert error.error_code == "STORAGE_UNAVAILABLE"
        assert str(error) == "STORAGE_UNAVAILABLE"

    expired = ExpiredSessionTransitionError()
    assert expired.error_code == "SESSION_EXPIRED"


def test_error_hierarchy_separates_transitions_from_persistence() -> None:
    assert issubclass(StateReadError, SessionPersistenceError)
    assert issubclass(StateWriteError, SessionPersistenceError)
    assert issubclass(ExpiredSessionTransitionError, ValueError)
    assert not issubclass(ExpiredSessionTransitionError, SessionPersistenceError)


def test_failed_outcomes_use_the_same_error_code_name() -> None:
    read = StateReadFailed(error_code="READ_FAILED")
    commit = StateCommitFailed(error_code="WRITE_FAILED")

    assert read.error_code == "READ_FAILED"
    assert commit.error_code == "WRITE_FAILED"
    assert "code" not in type(read).model_fields
    assert "code" not in type(commit).model_fields


@pytest.mark.parametrize(
    "outcome",
    [
        SessionCreated(state=_new_state()),
        SessionIdConflict(session_id="session-1"),
        Committed(state_version=1),
        AlreadyApplied(state_version=1),
        Superseded(actual=_new_state()),
        VersionConflict(actual=_new_state()),
        SessionAbsent(reason="expired"),
        StateReadFailed(error_code="READ_FAILED"),
        StateCommitFailed(error_code="WRITE_FAILED"),
    ],
)
def test_outcomes_are_frozen(outcome: object) -> None:
    field_name = next(iter(type(outcome).model_fields))

    with pytest.raises(ValidationError):
        setattr(outcome, field_name, getattr(outcome, field_name))


def test_session_snapshot_is_frozen_and_uses_an_immutable_dialog_tuple() -> None:
    snapshot = SessionSnapshot(state=_new_state(), dialog=(_turn(),))

    assert isinstance(snapshot.dialog, tuple)
    with pytest.raises(ValidationError):
        snapshot.dialog = ()  # type: ignore[misc]


@pytest.mark.parametrize("reason", ["expired", "not_found"])
def test_session_absent_accepts_only_closed_reason_values(reason: str) -> None:
    assert SessionAbsent(reason=reason).reason == reason


def test_session_absent_rejects_unknown_reason() -> None:
    with pytest.raises(ValidationError):
        SessionAbsent(reason="storage_error")


def test_session_created_requires_version_zero() -> None:
    state = _new_state().model_copy(update={"state_version": 1})

    with pytest.raises(ValidationError):
        SessionCreated(state=state)


def test_session_id_conflict_requires_a_non_empty_id() -> None:
    with pytest.raises(ValidationError):
        SessionIdConflict(session_id="")


class _Sessions:
    async def create(self, session_id: str, *, now: datetime):
        return SessionCreated(state=new_session(session_id, now=now))

    async def get(self, session_id: str, *, now: datetime):
        return SessionAbsent(reason="not_found")

    async def compare_and_set(
        self,
        session_id: str,
        expected_state_version: int,
        delta: object,
        *,
        now: datetime,
    ):
        return SessionAbsent(reason="not_found")


class _Dialogs:
    async def append(
        self,
        session_id: str,
        turn: DialogTurn,
        *,
        now: datetime,
    ):
        return None

    async def read(self, session_id: str, *, now: datetime):
        return ()

    async def clear(self, session_id: str, *, now: datetime):
        return None


class _Persistence:
    def __init__(self) -> None:
        self.sessions = _Sessions()
        self.dialogs = _Dialogs()

    async def touch(self, session_id: str, *, now: datetime):
        return SessionSnapshot(state=new_session(session_id, now=now), dialog=())

    async def reset(
        self,
        session_id: str,
        expected_state_version: int,
        *,
        now: datetime,
    ):
        return await self.sessions.compare_and_set(
            session_id,
            expected_state_version,
            RESET_DELTA,
            now=now,
        )

    async def delete(self, session_id: str) -> None:
        return None


def test_all_ports_are_runtime_checkable() -> None:
    assert isinstance(_Sessions(), SessionStore)
    assert isinstance(_Dialogs(), DialogStore)
    assert isinstance(_Persistence(), SessionPersistence)


@pytest.mark.parametrize(
    ("method", "parameter_names", "keyword_only"),
    [
        (SessionStore.create, ("self", "session_id", "now"), ("now",)),
        (SessionStore.get, ("self", "session_id", "now"), ("now",)),
        (
            SessionStore.compare_and_set,
            ("self", "session_id", "expected_state_version", "delta", "now"),
            ("now",),
        ),
        (
            DialogStore.append,
            ("self", "session_id", "turn", "now"),
            ("now",),
        ),
        (DialogStore.read, ("self", "session_id", "now"), ("now",)),
        (DialogStore.clear, ("self", "session_id", "now"), ("now",)),
        (
            SessionPersistence.touch,
            ("self", "session_id", "now"),
            ("now",),
        ),
        (
            SessionPersistence.reset,
            ("self", "session_id", "expected_state_version", "now"),
            ("now",),
        ),
        (SessionPersistence.delete, ("self", "session_id"), ()),
    ],
)
def test_port_methods_have_the_exact_async_shape(
    method: object,
    parameter_names: tuple[str, ...],
    keyword_only: tuple[str, ...],
) -> None:
    parameters = signature(method).parameters

    assert iscoroutinefunction(method)
    assert tuple(parameters) == parameter_names
    assert tuple(
        name
        for name, parameter in parameters.items()
        if parameter.kind is Parameter.KEYWORD_ONLY
    ) == keyword_only


def test_snapshot_belongs_to_persistence_and_not_outcomes() -> None:
    assert SessionSnapshot.__module__ == "exact_orb.session.persistence"
    assert not hasattr(outcomes_module, "SessionSnapshot")
    assert session_contracts.SessionSnapshot is SessionSnapshot


def test_session_package_exports_the_complete_public_contract() -> None:
    expected = {
        "AlreadyApplied",
        "ChartRef",
        "Committed",
        "DialogStore",
        "DialogTurn",
        "ExpiredSessionTransitionError",
        "HARD_TTL",
        "MAX_DIALOG_CHARS",
        "MAX_DIALOG_TURN_CHARS",
        "MAX_DIALOG_TURNS",
        "RESET_DELTA",
        "SLIDING_TTL",
        "Selection",
        "SessionAbsent",
        "SessionCreated",
        "SessionIdConflict",
        "SessionPersistence",
        "SessionPersistenceError",
        "SessionSnapshot",
        "SessionState",
        "SessionStore",
        "StateCommitFailed",
        "StateDelta",
        "StateReadError",
        "StateReadFailed",
        "StateWriteError",
        "Superseded",
        "VersionConflict",
        "append_dialog_turn",
        "apply_delta",
        "is_expired",
        "matches_intent",
        "new_session",
        "touched",
    }

    assert set(session_contracts.__all__) == expected
    assert all(hasattr(session_contracts, name) for name in expected)
