"""Public contracts for immutable session state and persistence."""

from exact_orb.session.dialog import (
    MAX_DIALOG_CHARS,
    MAX_DIALOG_TURN_CHARS,
    MAX_DIALOG_TURNS,
    DialogStore,
    DialogTurn,
    Selection,
    append_dialog_turn,
)
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
from exact_orb.session.state import (
    HARD_TTL,
    RESET_DELTA,
    SLIDING_TTL,
    ChartRef,
    SessionState,
    StateDelta,
    apply_delta,
    is_expired,
    matches_intent,
    new_session,
    require_utc,
    touched,
)
from exact_orb.session.store import SessionStore


__all__ = [
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
    "require_utc",
    "touched",
]
