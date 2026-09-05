"""Typed outcomes shared by session persistence and context services."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from exact_orb.session.state import SessionState


class _FrozenOutcome(BaseModel):
    model_config = ConfigDict(frozen=True)


class SessionCreated(_FrozenOutcome):
    """A new empty lifecycle session was inserted."""

    state: SessionState

    @field_validator("state")
    @classmethod
    def _state_must_be_new(cls, value: SessionState) -> SessionState:
        if value.state_version != 0:
            raise ValueError("created session state_version must be 0")
        return value


class SessionIdConflict(_FrozenOutcome):
    """The requested opaque identifier already belongs to a lifecycle."""

    session_id: str = Field(min_length=1)


class Committed(_FrozenOutcome):
    """A state transition was committed at the returned version."""

    state_version: int = Field(ge=1)


class AlreadyApplied(_FrozenOutcome):
    """The same logical intent was committed previously."""

    state_version: int = Field(ge=1)


class Superseded(_FrozenOutcome):
    """A different intent won the version race."""

    actual: SessionState


class VersionConflict(_FrozenOutcome):
    """Store-level CAS rejection with an atomic snapshot of actual state."""

    actual: SessionState


class SessionAbsent(_FrozenOutcome):
    """The current operation observed no live session."""

    reason: Literal["expired", "not_found"]


class StateReadFailed(_FrozenOutcome):
    """A session read could not be completed by persistence."""

    error_code: str = Field(min_length=1)


class StateCommitFailed(_FrozenOutcome):
    """A session write has an unknown or failed persistence outcome."""

    error_code: str = Field(min_length=1)


__all__ = [
    "AlreadyApplied",
    "Committed",
    "SessionAbsent",
    "SessionCreated",
    "SessionIdConflict",
    "StateCommitFailed",
    "StateReadFailed",
    "Superseded",
    "VersionConflict",
]
