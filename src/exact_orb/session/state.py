"""Immutable session-state contracts and pure transition rules."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Final, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from exact_orb.birth.types import BirthInput, ResolvedBirthData
from exact_orb.calculation.spec import ChartSpec
from exact_orb.session.errors import ExpiredSessionTransitionError


SLIDING_TTL: Final = timedelta(days=7)
HARD_TTL: Final = timedelta(days=30)


def require_utc(value: datetime, *, name: str = "timestamp") -> datetime:
    """Return the same aware UTC value or reject it with a named error."""

    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError(f"{name} must be timezone-aware UTC")
    return value


class ChartRef(BaseModel):
    """A reproducible chart reference tied to one state version."""

    model_config = ConfigDict(frozen=True)

    state_version: int = Field(ge=1)
    spec: ChartSpec


class SessionState(BaseModel):
    """The complete immutable state of one anonymous session."""

    model_config = ConfigDict(frozen=True)

    session_id: str = Field(min_length=1)
    birth_input: BirthInput | None
    birth_resolved: ResolvedBirthData | None
    state_version: int = Field(ge=0)
    base_chart: ChartRef | None
    created_at: datetime
    expires_at: datetime
    hard_expires_at: datetime

    @field_validator("created_at", "expires_at", "hard_expires_at")
    @classmethod
    def _timestamps_must_be_utc(cls, value: datetime) -> datetime:
        return require_utc(value, name="session timestamp")

    @model_validator(mode="after")
    def _state_must_be_consistent(self) -> Self:
        content = (self.birth_input, self.birth_resolved, self.base_chart)
        present = tuple(value is not None for value in content)
        if any(present) and not all(present):
            raise ValueError(
                "birth_input, birth_resolved, and base_chart must be all set or all None"
            )
        if self.base_chart is not None:
            if self.base_chart.state_version != self.state_version:
                raise ValueError("base_chart.state_version must equal state_version")
        if not self.created_at <= self.expires_at <= self.hard_expires_at:
            raise ValueError(
                "session timestamps must satisfy created_at <= expires_at "
                "<= hard_expires_at"
            )
        return self


class StateDelta(BaseModel):
    """A full replacement of the mutable state fields."""

    model_config = ConfigDict(frozen=True)

    # These fields are nullable but intentionally required. Callers must choose
    # explicitly between a populated replacement and RESET_DELTA.
    birth_input: BirthInput | None
    birth_resolved: ResolvedBirthData | None
    base_chart_spec: ChartSpec | None

    @model_validator(mode="after")
    def _replacement_must_be_complete(self) -> Self:
        values = (self.birth_input, self.birth_resolved, self.base_chart_spec)
        present = tuple(value is not None for value in values)
        if any(present) and not all(present):
            raise ValueError(
                "birth_input, birth_resolved, and base_chart_spec must be all set "
                "or all None"
            )
        return self


RESET_DELTA: Final[StateDelta] = StateDelta(
    birth_input=None,
    birth_resolved=None,
    base_chart_spec=None,
)


def new_session(session_id: str, *, now: datetime) -> SessionState:
    """Create an empty state with a fresh sliding and absolute lifetime."""

    require_utc(now, name="now")
    return SessionState(
        session_id=session_id,
        birth_input=None,
        birth_resolved=None,
        state_version=0,
        base_chart=None,
        created_at=now,
        expires_at=now + SLIDING_TTL,
        hard_expires_at=now + HARD_TTL,
    )


def is_expired(state: SessionState, *, now: datetime) -> bool:
    """Return whether either lifetime boundary has been reached."""

    require_utc(now, name="now")
    return now >= state.expires_at or now >= state.hard_expires_at


def _renewed_expiration(state: SessionState, *, now: datetime) -> datetime:
    return min(
        state.hard_expires_at,
        max(state.expires_at, now + SLIDING_TTL),
    )


def _reject_expired(state: SessionState, *, now: datetime) -> None:
    if is_expired(state, now=now):
        raise ExpiredSessionTransitionError()


def apply_delta(
    state: SessionState,
    delta: StateDelta,
    *,
    now: datetime,
) -> SessionState:
    """Build the next-version candidate without performing persistence I/O."""

    _reject_expired(state, now=now)
    next_version = state.state_version + 1
    base_chart = (
        ChartRef(state_version=next_version, spec=delta.base_chart_spec)
        if delta.base_chart_spec is not None
        else None
    )
    return SessionState(
        session_id=state.session_id,
        birth_input=delta.birth_input,
        birth_resolved=delta.birth_resolved,
        state_version=next_version,
        base_chart=base_chart,
        created_at=state.created_at,
        expires_at=_renewed_expiration(state, now=now),
        hard_expires_at=state.hard_expires_at,
    )


def touched(state: SessionState, *, now: datetime) -> SessionState:
    """Return an otherwise-identical candidate with its sliding TTL renewed."""

    _reject_expired(state, now=now)
    return SessionState(
        session_id=state.session_id,
        birth_input=state.birth_input,
        birth_resolved=state.birth_resolved,
        state_version=state.state_version,
        base_chart=state.base_chart,
        created_at=state.created_at,
        expires_at=_renewed_expiration(state, now=now),
        hard_expires_at=state.hard_expires_at,
    )


def matches_intent(actual: SessionState, delta: StateDelta) -> bool:
    """Compare calculation intent, excluding presentation-only birth input."""

    actual_spec = actual.base_chart.spec if actual.base_chart is not None else None
    return (
        actual.birth_resolved == delta.birth_resolved
        and actual_spec == delta.base_chart_spec
    )


__all__ = [
    "HARD_TTL",
    "RESET_DELTA",
    "SLIDING_TTL",
    "ChartRef",
    "SessionState",
    "StateDelta",
    "apply_delta",
    "is_expired",
    "matches_intent",
    "new_session",
    "require_utc",
    "touched",
]
