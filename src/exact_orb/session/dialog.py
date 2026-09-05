"""Immutable dialog contracts and bounded append policy."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, field_validator

from exact_orb.session.outcomes import SessionAbsent


MAX_DIALOG_TURNS = 50
MAX_DIALOG_TURN_CHARS = 8_000
MAX_DIALOG_CHARS = 120_000


class Selection(BaseModel):
    """The interpretation slice selected for one dialog turn."""

    model_config = ConfigDict(frozen=True)

    topic: str = Field(min_length=1)
    focus: str = Field(min_length=1)


class DialogTurn(BaseModel):
    """One immutable answer stored in append order."""

    model_config = ConfigDict(frozen=True)

    turn_id: str = Field(min_length=1)
    created_at: datetime
    selection: Selection
    state_version_at_answer: int = Field(ge=1)
    status: Literal["complete", "partial"]
    truncated: bool = False
    text: str

    @field_validator("created_at")
    @classmethod
    def _created_at_must_be_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != timedelta(0):
            raise ValueError("created_at must be timezone-aware UTC")
        return value


def append_dialog_turn(
    turns: tuple[DialogTurn, ...],
    turn: DialogTurn,
) -> tuple[DialogTurn, ...]:
    """Append one turn, trim its text, then evict oldest appended turns."""

    if len(turn.text) > MAX_DIALOG_TURN_CHARS:
        turn = turn.model_copy(
            update={
                "text": turn.text[:MAX_DIALOG_TURN_CHARS],
                "truncated": True,
            }
        )

    bounded = (*turns, turn)
    total_chars = sum(len(item.text) for item in bounded)
    first_retained = 0
    while (
        len(bounded) - first_retained > MAX_DIALOG_TURNS
        or total_chars > MAX_DIALOG_CHARS
    ):
        total_chars -= len(bounded[first_retained].text)
        first_retained += 1

    return bounded[first_retained:]


@runtime_checkable
class DialogStore(Protocol):
    """TTL-aware dialog facet backed by the same lifecycle as state."""

    async def append(
        self,
        session_id: str,
        turn: DialogTurn,
        *,
        now: datetime,
    ) -> None | SessionAbsent:
        """Atomically append to a live dialog or raise ``StateWriteError``."""

        ...

    async def read(
        self,
        session_id: str,
        *,
        now: datetime,
    ) -> tuple[DialogTurn, ...] | SessionAbsent:
        """Read a live dialog without touching it or raise ``StateReadError``."""

        ...

    async def clear(
        self,
        session_id: str,
        *,
        now: datetime,
    ) -> None | SessionAbsent:
        """Idempotently clear a live dialog or raise ``StateWriteError``."""

        ...


__all__ = [
    "DialogStore",
    "DialogTurn",
    "MAX_DIALOG_CHARS",
    "MAX_DIALOG_TURN_CHARS",
    "MAX_DIALOG_TURNS",
    "Selection",
    "append_dialog_turn",
]
