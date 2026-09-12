"""File-backed SQLite implementation of the session persistence ports."""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Final, Generic, Self, TypeVar, cast

from pydantic import TypeAdapter, ValidationError
from pydantic_core import PydanticSerializationError

from exact_orb.session.adapters._time import validate_now
from exact_orb.session.dialog import (
    MAX_DIALOG_CHARS,
    MAX_DIALOG_TURN_CHARS,
    MAX_DIALOG_TURNS,
    DialogTurn,
    append_dialog_turn,
)
from exact_orb.session.errors import StateReadError, StateWriteError
from exact_orb.session.outcomes import (
    SessionAbsent,
    SessionCreated,
    SessionIdConflict,
    VersionConflict,
)
from exact_orb.session.persistence import SessionSnapshot, UnknownTimeStateMigrator
from exact_orb.session.state import (
    RESET_DELTA,
    SessionState,
    StateDelta,
    apply_delta,
    is_expired,
    new_session,
    touched,
)


_BUSY: Final = "SESSION_SQLITE_BUSY"
_OPEN_FAILED: Final = "SESSION_SQLITE_OPEN_FAILED"
_READ_FAILED: Final = "SESSION_SQLITE_READ_FAILED"
_WRITE_FAILED: Final = "SESSION_SQLITE_WRITE_FAILED"
_DATA_CORRUPT: Final = "SESSION_SQLITE_DATA_CORRUPT"
_PAYLOAD_UNSUPPORTED: Final = "SESSION_SQLITE_PAYLOAD_UNSUPPORTED"
_SCHEMA_INCOMPATIBLE: Final = "SESSION_SQLITE_SCHEMA_INCOMPATIBLE"
_MIGRATION_FAILED: Final = "SESSION_SQLITE_MIGRATION_FAILED"
_INVARIANT_VIOLATION: Final = "SESSION_SQLITE_INVARIANT_VIOLATION"
_COMMIT_UNKNOWN: Final = "SESSION_SQLITE_COMMIT_UNKNOWN"

_SESSION_COMPONENT: Final = "session"
_STATE_PAYLOAD_VERSION: Final = 2
_DIALOG_PAYLOAD_VERSION: Final = 1
_EPOCH: Final = datetime(1970, 1, 1, tzinfo=UTC)
_LOGGER = logging.getLogger(__name__)

_STATE_COLUMNS: Final = """
    session_id,
    state_version,
    created_at_us,
    expires_at_us,
    hard_expires_at_us,
    payload_version,
    state_json
"""

_CREATE_SCHEMA_MIGRATIONS: Final = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    component TEXT,
    version INTEGER,
    PRIMARY KEY (component, version)
)
"""

_CREATE_SESSION_STATES: Final = """
CREATE TABLE IF NOT EXISTS session_states (
    session_id TEXT PRIMARY KEY CHECK (length(session_id) > 0),
    state_version INTEGER NOT NULL CHECK (state_version >= 0),
    created_at_us INTEGER NOT NULL,
    expires_at_us INTEGER NOT NULL,
    hard_expires_at_us INTEGER NOT NULL,
    payload_version INTEGER NOT NULL CHECK (payload_version >= 1),
    state_json TEXT NOT NULL,
    CHECK (created_at_us <= expires_at_us),
    CHECK (expires_at_us <= hard_expires_at_us)
)
"""

_CREATE_SESSION_DIALOGS: Final = """
CREATE TABLE IF NOT EXISTS session_dialogs (
    session_id TEXT PRIMARY KEY
        REFERENCES session_states(session_id) ON DELETE CASCADE,
    expires_at_us INTEGER NOT NULL,
    payload_version INTEGER NOT NULL CHECK (payload_version >= 1),
    turns_json TEXT NOT NULL
)
"""

_CREATE_SESSION_EXPIRY_INDEX: Final = """
CREATE INDEX IF NOT EXISTS idx_session_states_expires_at_us
ON session_states(expires_at_us)
"""


@dataclass(frozen=True, slots=True)
class _Migration:
    version: int
    statements: tuple[str, ...]


_SESSION_MIGRATIONS: tuple[_Migration, ...] = (
    _Migration(
        version=1,
        statements=(
            _CREATE_SESSION_STATES,
            _CREATE_SESSION_DIALOGS,
            _CREATE_SESSION_EXPIRY_INDEX,
        ),
    ),
)


# These two leaf seams are deliberately private. Tests may replace the factory
# to observe connection ownership/failures and pause the two-statement dialog
# read after its parent snapshot has been established.
_CONNECTION_FACTORY: Callable[..., sqlite3.Connection] = sqlite3.connect
_DIALOG_READ_AFTER_PARENT_HOOK: Callable[[], None] | None = None


class _AdapterFailure(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class _CommitFailure(Exception):
    """A commit was invoked but did not return confirmation."""


class _WalNotConfirmed(Exception):
    """Initialization could not confirm the persistent WAL journal mode."""


@dataclass(frozen=True, slots=True)
class _SqliteBackend:
    db_path: Path
    executor: ThreadPoolExecutor
    busy_timeout_ms: int
    unknown_time_migrator: UnknownTimeStateMigrator | None

    async def run(self, operation: Callable[..., Any], /, *args: Any) -> Any:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self.executor, operation, self, *args)


_T = TypeVar("_T")


@dataclass(frozen=True, slots=True)
class _TransactionResult(Generic[_T]):
    value: _T
    commit: bool = True


def _validate_db_path(db_path: str | Path) -> Path:
    if not isinstance(db_path, (str, Path)):
        raise TypeError("db_path must be str or Path")
    raw_path = str(db_path)
    if raw_path == "" or raw_path == ":memory:" or raw_path.startswith("file:"):
        raise ValueError("db_path must identify a file-backed SQLite database")
    return Path(db_path).resolve()


def _validate_busy_timeout(busy_timeout_ms: int) -> int:
    if type(busy_timeout_ms) is not int or busy_timeout_ms < 0:
        raise ValueError("busy_timeout_ms must be a non-negative int")
    return busy_timeout_ms


def _sqlite_primary_code(exc: sqlite3.Error) -> int | None:
    code = getattr(exc, "sqlite_errorcode", None)
    # Read the diagnostic name as part of the stable numeric-classification
    # seam, but never expose it or use exception text for classification.
    getattr(exc, "sqlite_errorname", None)
    return code & 0xFF if isinstance(code, int) else None


def _sqlite_code(exc: sqlite3.Error, *, default: str) -> str:
    primary = _sqlite_primary_code(exc)
    if primary in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
        return _BUSY
    if primary in (sqlite3.SQLITE_CORRUPT, sqlite3.SQLITE_NOTADB):
        return _DATA_CORRUPT
    return default


def _typed_code(exc: sqlite3.Error | OSError, *, default: str) -> str:
    if isinstance(exc, sqlite3.ProgrammingError):
        raise exc
    if isinstance(exc, sqlite3.Error):
        return _sqlite_code(exc, default=default)
    return default


def _close_connection(
    connection: sqlite3.Connection,
) -> sqlite3.Error | OSError | None:
    try:
        connection.close()
    except sqlite3.ProgrammingError:
        raise
    except (sqlite3.Error, OSError) as exc:
        return exc
    return None


def _warn_cleanup_failure(action: str) -> None:
    _LOGGER.warning(
        "session SQLite connection cleanup failed during %s; "
        "the exception detail is suppressed",
        action,
    )


def _execute_no_result(
    connection: sqlite3.Connection,
    sql: str,
    parameters: tuple[Any, ...] = (),
) -> None:
    cursor = connection.execute(sql, parameters)
    cursor.close()


def _fetch_one(
    connection: sqlite3.Connection,
    sql: str,
    parameters: tuple[Any, ...] = (),
) -> tuple[Any, ...] | None:
    cursor = connection.execute(sql, parameters)
    try:
        row = cursor.fetchone()
    finally:
        cursor.close()
    return cast(tuple[Any, ...] | None, row)


def _fetch_all(
    connection: sqlite3.Connection,
    sql: str,
    parameters: tuple[Any, ...] = (),
) -> list[tuple[Any, ...]]:
    cursor = connection.execute(sql, parameters)
    try:
        rows = cursor.fetchall()
    finally:
        cursor.close()
    return cast(list[tuple[Any, ...]], rows)


def _pragma_scalar(
    connection: sqlite3.Connection,
    sql: str,
    *,
    mismatch_code: str,
) -> Any:
    row = _fetch_one(connection, sql)
    if row is None or len(row) != 1:
        raise _AdapterFailure(mismatch_code)
    return row[0]


def _configure_connection(
    connection: sqlite3.Connection,
    *,
    busy_timeout_ms: int,
    initialization: bool,
    mismatch_code: str,
) -> None:
    _execute_no_result(connection, "PRAGMA foreign_keys = ON")
    if (
        _pragma_scalar(
            connection,
            "PRAGMA foreign_keys",
            mismatch_code=mismatch_code,
        )
        != 1
    ):
        raise _AdapterFailure(mismatch_code)

    _execute_no_result(connection, f"PRAGMA busy_timeout = {busy_timeout_ms}")
    if (
        _pragma_scalar(
            connection,
            "PRAGMA busy_timeout",
            mismatch_code=mismatch_code,
        )
        != busy_timeout_ms
    ):
        raise _AdapterFailure(mismatch_code)

    if initialization:
        journal_mode = _pragma_scalar(
            connection,
            "PRAGMA journal_mode",
            mismatch_code=mismatch_code,
        )
        if not isinstance(journal_mode, str) or journal_mode.casefold() != "wal":
            journal_mode = _pragma_scalar(
                connection,
                "PRAGMA journal_mode = WAL",
                mismatch_code=mismatch_code,
            )
            if not isinstance(journal_mode, str) or journal_mode.casefold() != "wal":
                raise _WalNotConfirmed()

    _execute_no_result(connection, "PRAGMA synchronous = NORMAL")
    if (
        _pragma_scalar(
            connection,
            "PRAGMA synchronous",
            mismatch_code=mismatch_code,
        )
        != 1
    ):
        raise _AdapterFailure(mismatch_code)


def _open_configured_connection(
    backend: _SqliteBackend,
    *,
    initialization: bool,
    mismatch_code: str,
) -> sqlite3.Connection:
    connection = _CONNECTION_FACTORY(
        str(backend.db_path),
        timeout=0,
        isolation_level=None,
        check_same_thread=True,
    )
    configured = False
    try:
        _configure_connection(
            connection,
            busy_timeout_ms=backend.busy_timeout_ms,
            initialization=initialization,
            mismatch_code=mismatch_code,
        )
        configured = True
        return connection
    finally:
        if not configured:
            close_error = _close_connection(connection)
            if close_error is not None:
                _warn_cleanup_failure("failed connection configuration")


def _rollback_best_effort(
    connection: sqlite3.Connection,
) -> sqlite3.Error | OSError | None:
    try:
        connection.rollback()
    except sqlite3.ProgrammingError:
        raise
    except (sqlite3.Error, OSError) as exc:
        return exc
    return None


def _commit(connection: sqlite3.Connection) -> None:
    try:
        connection.commit()
    except sqlite3.ProgrammingError:
        raise
    except (sqlite3.Error, OSError) as exc:
        raise _CommitFailure() from exc


def _run_connection(
    backend: _SqliteBackend,
    operation: Callable[[sqlite3.Connection], _T],
    *,
    error_type: type[StateReadError] | type[StateWriteError],
    default_code: str,
) -> _T:
    connection: sqlite3.Connection | None = None
    try:
        connection = _open_configured_connection(
            backend,
            initialization=False,
            mismatch_code=default_code,
        )
        return operation(connection)
    except _AdapterFailure as exc:
        raise error_type(exc.code) from None
    except (sqlite3.Error, OSError) as exc:
        raise error_type(_typed_code(exc, default=default_code)) from None
    finally:
        if connection is not None:
            close_error = _close_connection(connection)
            if close_error is not None:
                _warn_cleanup_failure("operation close")


def _run_immediate(
    backend: _SqliteBackend,
    operation: Callable[[sqlite3.Connection], _TransactionResult[_T]],
    *,
    error_type: type[StateReadError] | type[StateWriteError],
    default_code: str,
) -> _T:
    return _run_transaction(
        backend,
        operation,
        begin_statement="BEGIN IMMEDIATE",
        error_type=error_type,
        default_code=default_code,
    )


def _run_transaction(
    backend: _SqliteBackend,
    operation: Callable[[sqlite3.Connection], _TransactionResult[_T]],
    *,
    begin_statement: str,
    error_type: type[StateReadError] | type[StateWriteError],
    default_code: str,
) -> _T:
    connection: sqlite3.Connection | None = None
    transaction_open = False
    commit_invoked = False
    rollback_error: sqlite3.Error | OSError | None = None
    try:
        connection = _open_configured_connection(
            backend,
            initialization=False,
            mismatch_code=default_code,
        )
        _execute_no_result(connection, begin_statement)
        transaction_open = True
        result = operation(connection)
        if not result.commit:
            rollback_error = _rollback_best_effort(connection)
            if rollback_error is None:
                transaction_open = False
            return result.value

        commit_invoked = True
        _commit(connection)
        transaction_open = False
        return result.value
    except _CommitFailure:
        raise error_type(_COMMIT_UNKNOWN) from None
    except _AdapterFailure as exc:
        if connection is not None and transaction_open and not commit_invoked:
            cleanup_error = _rollback_best_effort(connection)
            if cleanup_error is not None:
                _warn_cleanup_failure("rollback after operation failure")
        raise error_type(exc.code) from None
    except sqlite3.ProgrammingError:
        raise
    except (sqlite3.Error, OSError) as exc:
        if connection is not None and transaction_open and not commit_invoked:
            cleanup_error = _rollback_best_effort(connection)
            if cleanup_error is not None:
                _warn_cleanup_failure("rollback after operation failure")
        raise error_type(_typed_code(exc, default=default_code)) from None
    finally:
        if connection is not None:
            close_error = _close_connection(connection)
            if close_error is not None:
                _warn_cleanup_failure("transaction close")
                if rollback_error is not None:
                    raise error_type(
                        _typed_code(rollback_error, default=default_code)
                    ) from None
            elif rollback_error is not None:
                _warn_cleanup_failure("normal-outcome rollback")


def _datetime_to_micros(value: datetime) -> int:
    delta = value - _EPOCH
    return (
        (delta.days * 86_400 + delta.seconds) * 1_000_000
        + delta.microseconds
    )


def _micros_to_datetime(value: Any) -> datetime:
    if type(value) is not int:
        raise _AdapterFailure(_DATA_CORRUPT)
    try:
        return _EPOCH + timedelta(microseconds=value)
    except OverflowError as exc:
        raise _AdapterFailure(_DATA_CORRUPT) from exc


def _encode_state(state: SessionState) -> tuple[int, int, int, int, int, str]:
    try:
        payload = state.model_dump_json()
    except PydanticSerializationError as exc:
        raise _AdapterFailure(_WRITE_FAILED) from exc
    return (
        state.state_version,
        _datetime_to_micros(state.created_at),
        _datetime_to_micros(state.expires_at),
        _datetime_to_micros(state.hard_expires_at),
        _STATE_PAYLOAD_VERSION,
        payload,
    )


def _require_payload_version(value: Any, *, supported: int) -> None:
    if type(value) is not int:
        raise _AdapterFailure(_DATA_CORRUPT)
    if value != supported:
        raise _AdapterFailure(_PAYLOAD_UNSUPPORTED)


def _decode_state_row(
    row: tuple[Any, ...],
    unknown_time_migrator: UnknownTimeStateMigrator | None,
) -> SessionState:
    if len(row) != 7:
        raise _AdapterFailure(_DATA_CORRUPT)
    (
        session_id,
        state_version,
        created_at_us,
        expires_at_us,
        hard_expires_at_us,
        payload_version,
        state_json,
    ) = row

    if type(payload_version) is not int:
        raise _AdapterFailure(_DATA_CORRUPT)
    if payload_version not in {1, _STATE_PAYLOAD_VERSION}:
        raise _AdapterFailure(_PAYLOAD_UNSUPPORTED)
    if type(session_id) is not str or type(state_version) is not int:
        raise _AdapterFailure(_DATA_CORRUPT)
    if type(state_json) is not str:
        raise _AdapterFailure(_DATA_CORRUPT)

    created_at = _micros_to_datetime(created_at_us)
    expires_at = _micros_to_datetime(expires_at_us)
    hard_expires_at = _micros_to_datetime(hard_expires_at_us)
    if payload_version == 1:
        state = _migrate_state_v1(state_json, unknown_time_migrator)
    else:
        try:
            state = SessionState.model_validate_json(state_json)
        except ValidationError as exc:
            raise _AdapterFailure(_DATA_CORRUPT) from exc

    if (
        state.session_id != session_id
        or state.state_version != state_version
        or state.created_at != created_at
        or state.expires_at != expires_at
        or state.hard_expires_at != hard_expires_at
    ):
        raise _AdapterFailure(_DATA_CORRUPT)
    return state


def _migrate_state_v1(
    state_json: str,
    unknown_time_migrator: UnknownTimeStateMigrator | None,
) -> SessionState:
    try:
        payload = json.loads(state_json)
    except (json.JSONDecodeError, TypeError) as exc:
        raise _AdapterFailure(_DATA_CORRUPT) from exc
    if not isinstance(payload, dict):
        raise _AdapterFailure(_DATA_CORRUPT)

    resolved = payload.get("birth_resolved")
    birth_input = payload.get("birth_input")
    if resolved is None:
        try:
            return SessionState.model_validate(payload)
        except ValidationError as exc:
            raise _AdapterFailure(_DATA_CORRUPT) from exc
    if not isinstance(resolved, dict) or not isinstance(birth_input, dict):
        raise _AdapterFailure(_DATA_CORRUPT)
    if type(resolved.get("time_unknown")) is not bool:
        raise _AdapterFailure(_DATA_CORRUPT)

    migrated_resolved = dict(resolved)
    if resolved["time_unknown"]:
        birth_date = birth_input.get("birth_date")
        tz_id = resolved.get("tz_id")
        if not isinstance(birth_date, str) or not isinstance(tz_id, str):
            raise _AdapterFailure(_DATA_CORRUPT)
        if unknown_time_migrator is None:
            raise _AdapterFailure(_MIGRATION_FAILED)
        try:
            parsed_birth_date = datetime.strptime(birth_date, "%Y-%m-%d").date()
            anchor, offset_seconds, domain = unknown_time_migrator(
                parsed_birth_date,
                tz_id,
            )
            migrated_resolved["utc_datetime"] = anchor
            migrated_resolved["utc_offset_seconds"] = offset_seconds
            migrated_resolved["birth_time_domain"] = domain.model_dump(mode="json")
        except Exception as exc:
            raise _AdapterFailure(_MIGRATION_FAILED) from exc
    else:
        migrated_resolved["birth_time_domain"] = None

    migrated = dict(payload)
    migrated["birth_resolved"] = migrated_resolved
    try:
        return SessionState.model_validate(migrated)
    except ValidationError as exc:
        raise _AdapterFailure(_MIGRATION_FAILED) from exc


_DIALOG_ADAPTER: Final = TypeAdapter(tuple[DialogTurn, ...])


def _encode_dialog(turns: tuple[DialogTurn, ...]) -> str:
    try:
        return _DIALOG_ADAPTER.dump_json(turns).decode("utf-8")
    except PydanticSerializationError as exc:
        raise _AdapterFailure(_WRITE_FAILED) from exc


def _decode_dialog_row(row: tuple[Any, ...]) -> tuple[DialogTurn, ...]:
    if len(row) != 3:
        raise _AdapterFailure(_DATA_CORRUPT)
    expires_at_us, payload_version, turns_json = row
    _require_payload_version(payload_version, supported=_DIALOG_PAYLOAD_VERSION)
    _micros_to_datetime(expires_at_us)
    if type(turns_json) is not str:
        raise _AdapterFailure(_DATA_CORRUPT)
    try:
        turns = _DIALOG_ADAPTER.validate_json(turns_json)
    except ValidationError as exc:
        raise _AdapterFailure(_DATA_CORRUPT) from exc

    if len(turns) > MAX_DIALOG_TURNS:
        raise _AdapterFailure(_DATA_CORRUPT)
    if any(len(turn.text) > MAX_DIALOG_TURN_CHARS for turn in turns):
        raise _AdapterFailure(_DATA_CORRUPT)
    if sum(len(turn.text) for turn in turns) > MAX_DIALOG_CHARS:
        raise _AdapterFailure(_DATA_CORRUPT)
    return turns


def _select_state_row(
    connection: sqlite3.Connection,
    session_id: str,
) -> tuple[Any, ...] | None:
    return _fetch_one(
        connection,
        f"SELECT {_STATE_COLUMNS} FROM session_states WHERE session_id = ?",
        (session_id,),
    )


def _select_live_state(
    backend: _SqliteBackend,
    connection: sqlite3.Connection,
    session_id: str,
    *,
    now: datetime,
) -> SessionState | SessionAbsent:
    row = _select_state_row(connection, session_id)
    if row is None:
        return SessionAbsent(reason="not_found")
    state = _decode_state_row(row, backend.unknown_time_migrator)
    if is_expired(state, now=now):
        return SessionAbsent(reason="expired")
    return state


def _select_dialog_row(
    connection: sqlite3.Connection,
    session_id: str,
) -> tuple[Any, ...] | None:
    return _fetch_one(
        connection,
        """
        SELECT expires_at_us, payload_version, turns_json
        FROM session_dialogs
        WHERE session_id = ?
        """,
        (session_id,),
    )


def _guarded_update_state(
    connection: sqlite3.Connection,
    state: SessionState,
    *,
    expected_state_version: int,
) -> None:
    (
        state_version,
        created_at_us,
        expires_at_us,
        hard_expires_at_us,
        payload_version,
        state_json,
    ) = _encode_state(state)
    cursor = connection.execute(
        """
        UPDATE session_states
        SET state_version = ?,
            created_at_us = ?,
            expires_at_us = ?,
            hard_expires_at_us = ?,
            payload_version = ?,
            state_json = ?
        WHERE session_id = ? AND state_version = ?
        """,
        (
            state_version,
            created_at_us,
            expires_at_us,
            hard_expires_at_us,
            payload_version,
            state_json,
            state.session_id,
            expected_state_version,
        ),
    )
    try:
        rowcount = cursor.rowcount
    finally:
        cursor.close()
    if rowcount != 1:
        raise _AdapterFailure(_INVARIANT_VIOLATION)


def _sync_create(
    backend: _SqliteBackend,
    session_id: str,
    now: datetime,
) -> SessionCreated | SessionIdConflict:
    def operation(
        connection: sqlite3.Connection,
    ) -> _TransactionResult[SessionCreated | SessionIdConflict]:
        state = new_session(session_id, now=now)
        (
            state_version,
            created_at_us,
            expires_at_us,
            hard_expires_at_us,
            payload_version,
            state_json,
        ) = _encode_state(state)
        try:
            cursor = connection.execute(
                """
                INSERT INTO session_states (
                    session_id,
                    state_version,
                    created_at_us,
                    expires_at_us,
                    hard_expires_at_us,
                    payload_version,
                    state_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id) DO NOTHING
                """,
                (
                    session_id,
                    state_version,
                    created_at_us,
                    expires_at_us,
                    hard_expires_at_us,
                    payload_version,
                    state_json,
                ),
            )
        except sqlite3.IntegrityError as exc:
            if (
                getattr(exc, "sqlite_errorcode", None)
                == sqlite3.SQLITE_CONSTRAINT_PRIMARYKEY
            ):
                return _TransactionResult(
                    SessionIdConflict(session_id=session_id),
                    commit=False,
                )
            raise
        try:
            rowcount = cursor.rowcount
        finally:
            cursor.close()

        if rowcount == 1:
            return _TransactionResult(SessionCreated(state=state))
        if rowcount == 0:
            return _TransactionResult(
                SessionIdConflict(session_id=session_id),
                commit=False,
            )
        raise _AdapterFailure(_INVARIANT_VIOLATION)

    return _run_immediate(
        backend,
        operation,
        error_type=StateWriteError,
        default_code=_WRITE_FAILED,
    )


def _sync_get(
    backend: _SqliteBackend,
    session_id: str,
    now: datetime,
) -> SessionState | SessionAbsent:
    return _run_connection(
        backend,
        lambda connection: _select_live_state(
            backend,
            connection,
            session_id,
            now=now,
        ),
        error_type=StateReadError,
        default_code=_READ_FAILED,
    )


def _sync_compare_and_set(
    backend: _SqliteBackend,
    session_id: str,
    expected_state_version: int,
    delta: StateDelta,
    now: datetime,
) -> int | VersionConflict | SessionAbsent:
    def operation(
        connection: sqlite3.Connection,
    ) -> _TransactionResult[int | VersionConflict | SessionAbsent]:
        actual = _select_live_state(backend, connection, session_id, now=now)
        if isinstance(actual, SessionAbsent):
            return _TransactionResult(actual, commit=False)
        if actual.state_version != expected_state_version:
            return _TransactionResult(VersionConflict(actual=actual), commit=False)

        next_state = apply_delta(actual, delta, now=now)
        _guarded_update_state(
            connection,
            next_state,
            expected_state_version=actual.state_version,
        )
        if delta == RESET_DELTA:
            _execute_no_result(
                connection,
                "DELETE FROM session_dialogs WHERE session_id = ?",
                (session_id,),
            )
        else:
            _execute_no_result(
                connection,
                """
                UPDATE session_dialogs
                SET expires_at_us = ?
                WHERE session_id = ?
                """,
                (_datetime_to_micros(next_state.expires_at), session_id),
            )
        return _TransactionResult(next_state.state_version)

    return _run_immediate(
        backend,
        operation,
        error_type=StateWriteError,
        default_code=_WRITE_FAILED,
    )


def _sync_dialog_read(
    backend: _SqliteBackend,
    session_id: str,
    now: datetime,
) -> tuple[DialogTurn, ...] | SessionAbsent:
    def operation(
        connection: sqlite3.Connection,
    ) -> _TransactionResult[tuple[DialogTurn, ...] | SessionAbsent]:
        state = _select_live_state(backend, connection, session_id, now=now)
        if isinstance(state, SessionAbsent):
            return _TransactionResult(state, commit=False)

        hook = _DIALOG_READ_AFTER_PARENT_HOOK
        if hook is not None:
            hook()

        row = _select_dialog_row(connection, session_id)
        turns = () if row is None else _decode_dialog_row(row)
        return _TransactionResult(turns, commit=False)

    return _run_transaction(
        backend,
        operation,
        begin_statement="BEGIN DEFERRED",
        error_type=StateReadError,
        default_code=_READ_FAILED,
    )


def _sync_dialog_append(
    backend: _SqliteBackend,
    session_id: str,
    turn: DialogTurn,
    now: datetime,
) -> None | SessionAbsent:
    def operation(
        connection: sqlite3.Connection,
    ) -> _TransactionResult[None | SessionAbsent]:
        actual = _select_live_state(backend, connection, session_id, now=now)
        if isinstance(actual, SessionAbsent):
            return _TransactionResult(actual, commit=False)

        row = _select_dialog_row(connection, session_id)
        previous = () if row is None else _decode_dialog_row(row)
        next_turns = append_dialog_turn(previous, turn)
        next_state = touched(actual, now=now)
        _guarded_update_state(
            connection,
            next_state,
            expected_state_version=actual.state_version,
        )
        cursor = connection.execute(
            """
            INSERT INTO session_dialogs (
                session_id,
                expires_at_us,
                payload_version,
                turns_json
            ) VALUES (?, ?, ?, ?)
            ON CONFLICT(session_id) DO UPDATE SET
                expires_at_us = excluded.expires_at_us,
                payload_version = excluded.payload_version,
                turns_json = excluded.turns_json
            """,
            (
                session_id,
                _datetime_to_micros(next_state.expires_at),
                _DIALOG_PAYLOAD_VERSION,
                _encode_dialog(next_turns),
            ),
        )
        cursor.close()
        return _TransactionResult(None)

    return _run_immediate(
        backend,
        operation,
        error_type=StateWriteError,
        default_code=_WRITE_FAILED,
    )


def _sync_dialog_clear(
    backend: _SqliteBackend,
    session_id: str,
    now: datetime,
) -> None | SessionAbsent:
    def operation(
        connection: sqlite3.Connection,
    ) -> _TransactionResult[None | SessionAbsent]:
        actual = _select_live_state(backend, connection, session_id, now=now)
        if isinstance(actual, SessionAbsent):
            return _TransactionResult(actual, commit=False)

        next_state = touched(actual, now=now)
        _guarded_update_state(
            connection,
            next_state,
            expected_state_version=actual.state_version,
        )
        _execute_no_result(
            connection,
            "DELETE FROM session_dialogs WHERE session_id = ?",
            (session_id,),
        )
        return _TransactionResult(None)

    return _run_immediate(
        backend,
        operation,
        error_type=StateWriteError,
        default_code=_WRITE_FAILED,
    )


def _sync_touch(
    backend: _SqliteBackend,
    session_id: str,
    now: datetime,
) -> SessionSnapshot | SessionAbsent:
    def operation(
        connection: sqlite3.Connection,
    ) -> _TransactionResult[SessionSnapshot | SessionAbsent]:
        actual = _select_live_state(backend, connection, session_id, now=now)
        if isinstance(actual, SessionAbsent):
            return _TransactionResult(actual, commit=False)

        row = _select_dialog_row(connection, session_id)
        turns = () if row is None else _decode_dialog_row(row)
        next_state = touched(actual, now=now)
        _guarded_update_state(
            connection,
            next_state,
            expected_state_version=actual.state_version,
        )
        if row is not None:
            _execute_no_result(
                connection,
                """
                UPDATE session_dialogs
                SET expires_at_us = ?
                WHERE session_id = ?
                """,
                (_datetime_to_micros(next_state.expires_at), session_id),
            )
        return _TransactionResult(SessionSnapshot(state=next_state, dialog=turns))

    return _run_immediate(
        backend,
        operation,
        error_type=StateReadError,
        default_code=_READ_FAILED,
    )


def _sync_delete(backend: _SqliteBackend, session_id: str) -> None:
    def operation(connection: sqlite3.Connection) -> _TransactionResult[None]:
        _execute_no_result(
            connection,
            "DELETE FROM session_states WHERE session_id = ?",
            (session_id,),
        )
        return _TransactionResult(None)

    return _run_immediate(
        backend,
        operation,
        error_type=StateWriteError,
        default_code=_WRITE_FAILED,
    )


def _sync_reap_expired(backend: _SqliteBackend, now: datetime) -> int:
    def operation(connection: sqlite3.Connection) -> _TransactionResult[int]:
        cursor = connection.execute(
            "DELETE FROM session_states WHERE expires_at_us <= ?",
            (_datetime_to_micros(now),),
        )
        try:
            rowcount = cursor.rowcount
        finally:
            cursor.close()
        if rowcount < 0:
            raise _AdapterFailure(_INVARIANT_VIOLATION)
        return _TransactionResult(rowcount)

    return _run_immediate(
        backend,
        operation,
        error_type=StateWriteError,
        default_code=_WRITE_FAILED,
    )


def _normalized_schema_sql(value: str) -> str:
    return "".join(value.casefold().split())


def _schema_sql(
    connection: sqlite3.Connection,
    *,
    object_type: str,
    name: str,
) -> str:
    row = _fetch_one(
        connection,
        "SELECT sql FROM sqlite_schema WHERE type = ? AND name = ?",
        (object_type, name),
    )
    if row is None or len(row) != 1 or type(row[0]) is not str:
        raise _AdapterFailure(_SCHEMA_INCOMPATIBLE)
    return row[0]


def _schema_object_exists(
    connection: sqlite3.Connection,
    *,
    object_type: str,
    name: str,
) -> bool:
    row = _fetch_one(
        connection,
        "SELECT 1 FROM sqlite_schema WHERE type = ? AND name = ?",
        (object_type, name),
    )
    return row is not None


def _table_shape(
    connection: sqlite3.Connection,
    table: str,
) -> tuple[tuple[str, str, int, int], ...]:
    rows = _fetch_all(connection, f"PRAGMA table_info({table})")
    shape: list[tuple[str, str, int, int]] = []
    for row in rows:
        if len(row) < 6:
            raise _AdapterFailure(_SCHEMA_INCOMPATIBLE)
        _, name, declared_type, not_null, _, primary_key = row[:6]
        if (
            type(name) is not str
            or type(declared_type) is not str
            or type(not_null) is not int
            or type(primary_key) is not int
        ):
            raise _AdapterFailure(_SCHEMA_INCOMPATIBLE)
        shape.append((name, declared_type.upper(), not_null, primary_key))
    return tuple(shape)


def _validate_ledger_schema(connection: sqlite3.Connection) -> None:
    expected = (
        ("component", "TEXT", 0, 1),
        ("version", "INTEGER", 0, 2),
    )
    if _table_shape(connection, "schema_migrations") != expected:
        raise _AdapterFailure(_SCHEMA_INCOMPATIBLE)


def _index_columns(
    connection: sqlite3.Connection,
    index_name: str,
) -> tuple[str, ...]:
    rows = _fetch_all(connection, f"PRAGMA index_info({index_name})")
    columns: list[str] = []
    for row in rows:
        if len(row) < 3 or type(row[2]) is not str:
            raise _AdapterFailure(_SCHEMA_INCOMPATIBLE)
        columns.append(row[2])
    return tuple(columns)


def _validate_v1_schema(connection: sqlite3.Connection) -> None:
    expected_states = (
        ("session_id", "TEXT", 0, 1),
        ("state_version", "INTEGER", 1, 0),
        ("created_at_us", "INTEGER", 1, 0),
        ("expires_at_us", "INTEGER", 1, 0),
        ("hard_expires_at_us", "INTEGER", 1, 0),
        ("payload_version", "INTEGER", 1, 0),
        ("state_json", "TEXT", 1, 0),
    )
    expected_dialogs = (
        ("session_id", "TEXT", 0, 1),
        ("expires_at_us", "INTEGER", 1, 0),
        ("payload_version", "INTEGER", 1, 0),
        ("turns_json", "TEXT", 1, 0),
    )
    if _table_shape(connection, "session_states") != expected_states:
        raise _AdapterFailure(_SCHEMA_INCOMPATIBLE)
    if _table_shape(connection, "session_dialogs") != expected_dialogs:
        raise _AdapterFailure(_SCHEMA_INCOMPATIBLE)

    states_sql = _normalized_schema_sql(
        _schema_sql(connection, object_type="table", name="session_states")
    )
    for required in (
        "check(length(session_id)>0)",
        "check(state_version>=0)",
        "check(payload_version>=1)",
        "check(created_at_us<=expires_at_us)",
        "check(expires_at_us<=hard_expires_at_us)",
    ):
        if required not in states_sql:
            raise _AdapterFailure(_SCHEMA_INCOMPATIBLE)

    dialogs_sql = _normalized_schema_sql(
        _schema_sql(connection, object_type="table", name="session_dialogs")
    )
    if "check(payload_version>=1)" not in dialogs_sql:
        raise _AdapterFailure(_SCHEMA_INCOMPATIBLE)

    foreign_keys = _fetch_all(connection, "PRAGMA foreign_key_list(session_dialogs)")
    expected_foreign_key = (
        "session_states",
        "session_id",
        "session_id",
        "NO ACTION",
        "CASCADE",
    )
    if len(foreign_keys) != 1 or len(foreign_keys[0]) < 7:
        raise _AdapterFailure(_SCHEMA_INCOMPATIBLE)
    foreign_key = foreign_keys[0]
    observed_foreign_key = (
        foreign_key[2],
        foreign_key[3],
        foreign_key[4],
        foreign_key[5],
        foreign_key[6],
    )
    if observed_foreign_key != expected_foreign_key:
        raise _AdapterFailure(_SCHEMA_INCOMPATIBLE)

    state_indexes = _fetch_all(connection, "PRAGMA index_list(session_states)")
    expiry_indexes = [
        row
        for row in state_indexes
        if len(row) >= 5
        and type(row[1]) is str
        and _index_columns(connection, row[1]) == ("expires_at_us",)
    ]
    if len(expiry_indexes) != 1:
        raise _AdapterFailure(_SCHEMA_INCOMPATIBLE)
    expiry_index = expiry_indexes[0]
    if expiry_index[2] != 0 or expiry_index[4] != 0:
        raise _AdapterFailure(_SCHEMA_INCOMPATIBLE)

    dialog_indexes = _fetch_all(connection, "PRAGMA index_list(session_dialogs)")
    if any(
        len(row) >= 2
        and type(row[1]) is str
        and "expires_at_us" in _index_columns(connection, row[1])
        for row in dialog_indexes
    ):
        raise _AdapterFailure(_SCHEMA_INCOMPATIBLE)


def _known_migration_versions() -> list[int]:
    versions = [migration.version for migration in _SESSION_MIGRATIONS]
    if versions != list(range(1, len(versions) + 1)):
        raise RuntimeError("session migrations must be a contiguous ordered sequence")
    return versions


def _apply_migrations(connection: sqlite3.Connection) -> None:
    _execute_no_result(connection, _CREATE_SCHEMA_MIGRATIONS)
    _validate_ledger_schema(connection)

    rows = _fetch_all(
        connection,
        """
        SELECT version
        FROM schema_migrations
        WHERE component = ?
        ORDER BY version
        """,
        (_SESSION_COMPONENT,),
    )
    observed_versions: list[int] = []
    for row in rows:
        if len(row) != 1 or type(row[0]) is not int:
            raise _AdapterFailure(_SCHEMA_INCOMPATIBLE)
        observed_versions.append(row[0])

    known_versions = _known_migration_versions()
    if observed_versions != known_versions[: len(observed_versions)]:
        raise _AdapterFailure(_SCHEMA_INCOMPATIBLE)

    # Owned objects without their ledger entry cannot be adopted safely. This
    # also keeps a pre-existing incompatible table from being misreported as a
    # failure of the known v1 migration that merely encountered it.
    if not observed_versions and any(
        _schema_object_exists(connection, object_type=object_type, name=name)
        for object_type, name in (
            ("table", "session_states"),
            ("table", "session_dialogs"),
            ("index", "idx_session_states_expires_at_us"),
        )
    ):
        raise _AdapterFailure(_SCHEMA_INCOMPATIBLE)

    for migration in _SESSION_MIGRATIONS[len(observed_versions) :]:
        for statement in migration.statements:
            _execute_no_result(connection, statement)
        cursor = connection.execute(
            "INSERT INTO schema_migrations(component, version) VALUES (?, ?)",
            (_SESSION_COMPONENT, migration.version),
        )
        try:
            rowcount = cursor.rowcount
        finally:
            cursor.close()
        if rowcount != 1:
            raise _AdapterFailure(_INVARIANT_VIOLATION)

    if 1 in known_versions:
        _validate_v1_schema(connection)


def _sync_initialize(backend: _SqliteBackend) -> None:
    connection: sqlite3.Connection | None = None
    transaction_open = False
    commit_invoked = False
    phase_code = _OPEN_FAILED
    try:
        try:
            connection = _open_configured_connection(
                backend,
                initialization=True,
                mismatch_code=_OPEN_FAILED,
            )
        except (_WalNotConfirmed, sqlite3.Error) as exc:
            if isinstance(exc, sqlite3.Error) and _sqlite_primary_code(exc) not in (
                sqlite3.SQLITE_BUSY,
                sqlite3.SQLITE_LOCKED,
            ):
                raise
            # Two first-open connections can deadlock while upgrading the
            # rollback-journal mode to WAL; SQLite then bypasses busy_timeout
            # for one participant. The failed helper has already closed that
            # connection, so one fresh, bounded configuration attempt releases
            # the upgrade deadlock without a process-global lock or sleep loop.
            connection = _open_configured_connection(
                backend,
                initialization=True,
                mismatch_code=_OPEN_FAILED,
            )
        phase_code = _MIGRATION_FAILED
        _execute_no_result(connection, "BEGIN IMMEDIATE")
        transaction_open = True
        _apply_migrations(connection)
        commit_invoked = True
        _commit(connection)
        transaction_open = False
    except _CommitFailure:
        raise StateWriteError(_COMMIT_UNKNOWN) from None
    except _WalNotConfirmed:
        raise StateWriteError(_OPEN_FAILED) from None
    except _AdapterFailure as exc:
        if connection is not None and transaction_open and not commit_invoked:
            cleanup_error = _rollback_best_effort(connection)
            if cleanup_error is not None:
                _warn_cleanup_failure("initialization rollback")
        raise StateWriteError(exc.code) from None
    except (sqlite3.Error, OSError) as exc:
        if connection is not None and transaction_open and not commit_invoked:
            cleanup_error = _rollback_best_effort(connection)
            if cleanup_error is not None:
                _warn_cleanup_failure("initialization rollback")
        raise StateWriteError(_typed_code(exc, default=phase_code)) from None
    finally:
        if connection is not None:
            close_error = _close_connection(connection)
            if close_error is not None:
                _warn_cleanup_failure("initialization close")


class SqliteSessionStore:
    """SQLite state facet backed by an aggregate-owned backend."""

    def __init__(self, backend: _SqliteBackend, /) -> None:
        self._backend = backend

    async def create(
        self,
        session_id: str,
        *,
        now: datetime,
    ) -> SessionCreated | SessionIdConflict:
        validate_now(now)
        return cast(
            SessionCreated | SessionIdConflict,
            await self._backend.run(_sync_create, session_id, now),
        )

    async def get(
        self,
        session_id: str,
        *,
        now: datetime,
    ) -> SessionState | SessionAbsent:
        validate_now(now)
        return cast(
            SessionState | SessionAbsent,
            await self._backend.run(_sync_get, session_id, now),
        )

    async def compare_and_set(
        self,
        session_id: str,
        expected_state_version: int,
        delta: StateDelta,
        *,
        now: datetime,
    ) -> int | VersionConflict | SessionAbsent:
        validate_now(now)
        return cast(
            int | VersionConflict | SessionAbsent,
            await self._backend.run(
                _sync_compare_and_set,
                session_id,
                expected_state_version,
                delta,
                now,
            ),
        )


class SqliteDialogStore:
    """SQLite dialog facet sharing its aggregate's state lifecycle."""

    def __init__(self, backend: _SqliteBackend, /) -> None:
        self._backend = backend

    async def append(
        self,
        session_id: str,
        turn: DialogTurn,
        *,
        now: datetime,
    ) -> None | SessionAbsent:
        validate_now(now)
        return cast(
            None | SessionAbsent,
            await self._backend.run(_sync_dialog_append, session_id, turn, now),
        )

    async def read(
        self,
        session_id: str,
        *,
        now: datetime,
    ) -> tuple[DialogTurn, ...] | SessionAbsent:
        validate_now(now)
        return cast(
            tuple[DialogTurn, ...] | SessionAbsent,
            await self._backend.run(_sync_dialog_read, session_id, now),
        )

    async def clear(
        self,
        session_id: str,
        *,
        now: datetime,
    ) -> None | SessionAbsent:
        validate_now(now)
        return cast(
            None | SessionAbsent,
            await self._backend.run(_sync_dialog_clear, session_id, now),
        )


class SqliteSessionPersistence:
    """Aggregate implementing the complete session persistence contract."""

    sessions: SqliteSessionStore
    dialogs: SqliteDialogStore

    def __init__(self, backend: _SqliteBackend, /) -> None:
        self._backend = backend
        self.sessions = SqliteSessionStore(backend)
        self.dialogs = SqliteDialogStore(backend)

    @classmethod
    async def open(
        cls,
        db_path: str | Path,
        /,
        *,
        executor: ThreadPoolExecutor,
        busy_timeout_ms: int = 5_000,
        unknown_time_migrator: UnknownTimeStateMigrator | None = None,
    ) -> Self:
        timeout = _validate_busy_timeout(busy_timeout_ms)
        path = _validate_db_path(db_path)
        if executor is None:
            raise TypeError("executor is required")
        backend = _SqliteBackend(
            db_path=path,
            executor=executor,
            busy_timeout_ms=timeout,
            unknown_time_migrator=unknown_time_migrator,
        )
        await backend.run(_sync_initialize)
        return cls(backend)

    async def touch(
        self,
        session_id: str,
        *,
        now: datetime,
    ) -> SessionSnapshot | SessionAbsent:
        validate_now(now)
        return cast(
            SessionSnapshot | SessionAbsent,
            await self._backend.run(_sync_touch, session_id, now),
        )

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
        await self._backend.run(_sync_delete, session_id)

    async def reap_expired(self, *, now: datetime) -> int:
        validate_now(now)
        return cast(int, await self._backend.run(_sync_reap_expired, now))


__all__ = [
    "SqliteDialogStore",
    "SqliteSessionPersistence",
    "SqliteSessionStore",
]
