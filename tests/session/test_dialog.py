"""Tests for immutable dialog models and bounded append policy."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from exact_orb.session.dialog import (
    MAX_DIALOG_CHARS,
    MAX_DIALOG_TURN_CHARS,
    MAX_DIALOG_TURNS,
    DialogTurn,
    Selection,
    append_dialog_turn,
)


NOW = datetime(2026, 9, 5, 12, tzinfo=UTC)
SELECTION = Selection(topic="natal", focus="relationships")


def _turn(
    index: int = 0,
    *,
    text: str = "answer",
    status: str = "complete",
    truncated: bool = False,
    created_at: datetime | None = None,
) -> DialogTurn:
    return DialogTurn(
        turn_id=f"turn-{index}",
        created_at=created_at or NOW + timedelta(minutes=index),
        selection=SELECTION,
        state_version_at_answer=1,
        status=status,
        truncated=truncated,
        text=text,
    )


def test_selection_and_dialog_turn_are_frozen() -> None:
    turn = _turn()

    with pytest.raises(ValidationError):
        SELECTION.topic = "transits"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        turn.text = "changed"  # type: ignore[misc]


@pytest.mark.parametrize("status", ["complete", "partial"])
@pytest.mark.parametrize("truncated", [False, True])
def test_status_and_truncated_are_independent(
    status: str,
    truncated: bool,
) -> None:
    turn = _turn(status=status, truncated=truncated)

    assert turn.status == status
    assert turn.truncated is truncated


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("turn_id", ""),
        ("state_version_at_answer", 0),
        ("status", "cancelled"),
    ],
)
def test_dialog_turn_rejects_invalid_contract_fields(field: str, value: object) -> None:
    values = _turn().model_dump()
    values[field] = value

    with pytest.raises(ValidationError):
        DialogTurn.model_validate(values)


@pytest.mark.parametrize(
    "created_at",
    [
        NOW.replace(tzinfo=None),
        NOW.astimezone(timezone(timedelta(hours=3))),
    ],
)
def test_dialog_turn_requires_aware_utc(created_at: datetime) -> None:
    with pytest.raises(ValidationError):
        _turn(created_at=created_at)


@pytest.mark.parametrize("field", ["topic", "focus"])
def test_selection_fields_are_non_empty(field: str) -> None:
    values = {"topic": "natal", "focus": "relationships"}
    values[field] = ""

    with pytest.raises(ValidationError):
        Selection(**values)


def test_append_trims_unicode_code_points_and_preserves_status() -> None:
    source = _turn(
        text="🌙" * (MAX_DIALOG_TURN_CHARS + 1),
        status="partial",
    )

    result = append_dialog_turn((), source)

    assert len(result) == 1
    assert result[0].text == "🌙" * MAX_DIALOG_TURN_CHARS
    assert result[0].status == "partial"
    assert result[0].truncated is True
    assert source.truncated is False
    assert len(source.text) == MAX_DIALOG_TURN_CHARS + 1


def test_append_does_not_mark_text_at_exact_turn_limit_as_truncated() -> None:
    source = _turn(text="x" * MAX_DIALOG_TURN_CHARS)

    result = append_dialog_turn((), source)

    assert result == (source,)
    assert result[0].truncated is False


def test_append_preserves_existing_truncated_marker() -> None:
    source = _turn(text="short", truncated=True)

    assert append_dialog_turn((), source)[0].truncated is True


def test_append_evicts_oldest_by_append_order_at_turn_limit() -> None:
    turns = tuple(
        _turn(
            index,
            created_at=NOW - timedelta(minutes=index),
        )
        for index in range(MAX_DIALOG_TURNS)
    )

    result = append_dialog_turn(turns, _turn(MAX_DIALOG_TURNS))

    assert len(result) == MAX_DIALOG_TURNS
    assert tuple(turn.turn_id for turn in result) == tuple(
        f"turn-{index}" for index in range(1, MAX_DIALOG_TURNS + 1)
    )


def test_append_evicts_oldest_until_total_character_limit_is_met() -> None:
    full_turn = "x" * MAX_DIALOG_TURN_CHARS
    turns = tuple(_turn(index, text=full_turn) for index in range(15))

    result = append_dialog_turn(turns, _turn(15, text=full_turn))

    assert len(result) == 15
    assert result[0].turn_id == "turn-1"
    assert result[-1].turn_id == "turn-15"
    assert sum(len(turn.text) for turn in result) == MAX_DIALOG_CHARS


def test_one_maximum_turn_fits_and_append_does_not_mutate_input_tuple() -> None:
    prior = (_turn(0, text="old"),)
    source = _turn(1, text="x" * MAX_DIALOG_TURN_CHARS)

    result = append_dialog_turn(prior, source)

    assert prior == (_turn(0, text="old"),)
    assert result == (*prior, source)
