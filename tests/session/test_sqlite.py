"""SQLite session persistence conformance and adapter-specific invariants."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Iterator
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, time, timedelta, timezone
import inspect
import itertools
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import threading
from typing import Any

import pytest

from exact_orb.birth import (
    build_birth_time_domain,
    resolve_unknown_birth_time_for_migration,
)
from exact_orb.birth.types import BirthInput, ResolutionWarning, ResolvedBirthData
from exact_orb.calculation.spec import NatalChartSpec
from exact_orb.domain import RulershipScheme
from exact_orb.session.adapters import sqlite as sqlite_adapter
from exact_orb.session.adapters.sqlite import (
    SqliteDialogStore,
    SqliteSessionPersistence,
    SqliteSessionStore,
)
from exact_orb.session.dialog import (
    MAX_DIALOG_CHARS,
    MAX_DIALOG_TURN_CHARS,
    MAX_DIALOG_TURNS,
    DialogTurn,
    Selection,
)
from exact_orb.session.errors import StateReadError, StateWriteError
from exact_orb.session.outcomes import (
    SessionAbsent,
    SessionCreated,
    SessionIdConflict,
    VersionConflict,
)
from exact_orb.session.persistence import SessionSnapshot
from exact_orb.session.state import HARD_TTL, SLIDING_TTL, ChartRef, SessionState, StateDelta
from tests.session.conformance import (
    DELTA,
    NOW,
    PersistenceFactory,
    PersistenceHandles,
    SessionPersistenceConformance,
    create_session,
    make_turn,
    populate_session,
    populate_with_dialog,
)


pytestmark = pytest.mark.no_ephemeris_autoinit

SQLITE_TEST_EXECUTOR_WORKERS = 4
SRC_ROOT = Path(__file__).resolve().parents[2] / "src"
GOLDEN_PAYLOAD_V1 = Path(__file__).with_name("golden") / "session_sqlite_payload_v1.json"
GOLDEN_PAYLOAD_V2 = Path(__file__).with_name("golden") / "session_sqlite_payload_v2.json"

BUSY = "SESSION_SQLITE_BUSY"
OPEN_FAILED = "SESSION_SQLITE_OPEN_FAILED"
READ_FAILED = "SESSION_SQLITE_READ_FAILED"
WRITE_FAILED = "SESSION_SQLITE_WRITE_FAILED"
DATA_CORRUPT = "SESSION_SQLITE_DATA_CORRUPT"
PAYLOAD_UNSUPPORTED = "SESSION_SQLITE_PAYLOAD_UNSUPPORTED"
SCHEMA_INCOMPATIBLE = "SESSION_SQLITE_SCHEMA_INCOMPATIBLE"
MIGRATION_FAILED = "SESSION_SQLITE_MIGRATION_FAILED"
INVARIANT_VIOLATION = "SESSION_SQLITE_INVARIANT_VIOLATION"
COMMIT_UNKNOWN = "SESSION_SQLITE_COMMIT_UNKNOWN"


def _connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path, isolation_level=None)
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def _fetchone(path: Path, statement: str, parameters: tuple[object, ...] = ()) -> tuple[Any, ...]:
    connection = _connect(path)
    try:
        row = connection.execute(statement, parameters).fetchone()
        assert row is not None
        return row
    finally:
        connection.close()


def _fetchall(
    path: Path,
    statement: str,
    parameters: tuple[object, ...] = (),
) -> list[tuple[Any, ...]]:
    connection = _connect(path)
    try:
        return connection.execute(statement, parameters).fetchall()
    finally:
        connection.close()


def _execute(path: Path, statement: str, parameters: tuple[object, ...] = ()) -> None:
    connection = _connect(path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(statement, parameters)
        connection.commit()
    finally:
        connection.close()


def _epoch_us(value: datetime) -> int:
    delta = value - datetime(1970, 1, 1, tzinfo=UTC)
    return ((delta.days * 86_400 + delta.seconds) * 1_000_000) + delta.microseconds


def _json(value: object) -> str:
    if hasattr(value, "model_dump_json"):
        return value.model_dump_json()  # type: ignore[union-attr]
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


@asynccontextmanager
async def _opened(
    path: Path,
    *,
    busy_timeout_ms: int = 1_000,
    executor: ThreadPoolExecutor | None = None,
    unknown_time_migrator: Callable[..., object] | None = resolve_unknown_birth_time_for_migration,
) -> AsyncIterator[tuple[SqliteSessionPersistence, SqliteSessionPersistence]]:
    owned_executor = executor is None
    selected_executor = executor or ThreadPoolExecutor(max_workers=SQLITE_TEST_EXECUTOR_WORKERS)
    try:
        primary = await SqliteSessionPersistence.open(
            path,
            executor=selected_executor,
            busy_timeout_ms=busy_timeout_ms,
            unknown_time_migrator=unknown_time_migrator,
        )
        peer = await SqliteSessionPersistence.open(
            path,
            executor=selected_executor,
            busy_timeout_ms=busy_timeout_ms,
            unknown_time_migrator=unknown_time_migrator,
        )
        yield primary, peer
    finally:
        if owned_executor:
            selected_executor.shutdown(wait=True)


class RecordingExecutor(ThreadPoolExecutor):
    """Real executor which records submission and execution threads."""

    def __init__(self, *, max_workers: int) -> None:
        super().__init__(max_workers=max_workers)
        self.submit_count = 0
        self.worker_thread_ids: list[int] = []
        self._record_lock = threading.Lock()
        self.probe: threading.Barrier | None = None

    def submit(  # type: ignore[override]
        self,
        fn: Callable[..., Any],
        /,
        *args: Any,
        **kwargs: Any,
    ) -> Future[Any]:
        with self._record_lock:
            self.submit_count += 1

        def recorded() -> Any:
            with self._record_lock:
                self.worker_thread_ids.append(threading.get_ident())
                probe = self.probe
            if probe is not None:
                probe.wait(timeout=5)
            return fn(*args, **kwargs)

        return super().submit(recorded)


class RejectingExecutor(ThreadPoolExecutor):
    def __init__(self) -> None:
        super().__init__(max_workers=1)
        self.submit_count = 0

    def submit(  # type: ignore[override]
        self,
        fn: Callable[..., Any],
        /,
        *args: Any,
        **kwargs: Any,
    ) -> Future[Any]:
        self.submit_count += 1
        raise AssertionError("validation must precede executor submission")


class TestSqliteSessionPersistence(SessionPersistenceConformance):
    def make_factory(self, tmp_path: Path) -> PersistenceFactory:
        counter = itertools.count()

        @asynccontextmanager
        async def factory() -> AsyncIterator[PersistenceHandles]:
            path = tmp_path / f"conformance-{next(counter)}.sqlite3"
            executor = ThreadPoolExecutor(max_workers=SQLITE_TEST_EXECUTOR_WORKERS)
            try:
                primary = await SqliteSessionPersistence.open(
                    path,
                    executor=executor,
                    busy_timeout_ms=1_000,
                )
                peer = await SqliteSessionPersistence.open(
                    path,
                    executor=executor,
                    busy_timeout_ms=1_000,
                )
                yield PersistenceHandles(primary=primary, peer=peer)
            finally:
                executor.shutdown(wait=True)

        return factory

    def test_concrete_suite_collects_inherited_conformance_cases(self) -> None:
        inherited = {
            name
            for name in dir(SessionPersistenceConformance)
            if name.startswith("test_")
        }

        assert SQLITE_TEST_EXECUTOR_WORKERS >= 4
        assert inherited
        assert inherited <= set(dir(type(self)))
        assert "make_factory" in type(self).__dict__


def test_public_construction_signatures_are_deliberately_narrow() -> None:
    open_parameters = list(inspect.signature(SqliteSessionPersistence.open).parameters.values())
    assert [(item.name, item.kind, item.default) for item in open_parameters] == [
        ("db_path", inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.empty),
        ("executor", inspect.Parameter.KEYWORD_ONLY, inspect.Parameter.empty),
        ("busy_timeout_ms", inspect.Parameter.KEYWORD_ONLY, 5_000),
        ("unknown_time_migrator", inspect.Parameter.KEYWORD_ONLY, None),
    ]

    reaper_parameters = list(
        inspect.signature(SqliteSessionPersistence.reap_expired).parameters.values()
    )
    assert [(item.name, item.kind) for item in reaper_parameters] == [
        ("self", inspect.Parameter.POSITIONAL_OR_KEYWORD),
        ("now", inspect.Parameter.KEYWORD_ONLY),
    ]
    assert inspect.iscoroutinefunction(SqliteSessionPersistence.reap_expired)

    for concrete_type in (
        SqliteSessionPersistence,
        SqliteSessionStore,
        SqliteDialogStore,
    ):
        constructor_parameters = list(
            inspect.signature(concrete_type.__init__).parameters.values()
        )
        assert [(item.name, item.kind) for item in constructor_parameters] == [
            ("self", inspect.Parameter.POSITIONAL_ONLY),
            ("backend", inspect.Parameter.POSITIONAL_ONLY),
        ]


def test_module_public_surface_contains_only_three_concrete_types() -> None:
    assert sqlite_adapter.__all__ == [
        "SqliteDialogStore",
        "SqliteSessionPersistence",
        "SqliteSessionStore",
    ]
    assert "SqliteBackend" not in sqlite_adapter.__all__
    assert "ConnectionFactory" not in sqlite_adapter.__all__


@pytest.mark.parametrize(
    "concrete_type",
    [SqliteSessionPersistence, SqliteSessionStore, SqliteDialogStore],
)
def test_concrete_types_require_an_explicit_private_backend(concrete_type: type[object]) -> None:
    with pytest.raises(TypeError):
        concrete_type()  # type: ignore[call-arg]


async def test_aggregate_has_stable_facets_with_one_private_backend(tmp_path: Path) -> None:
    path = tmp_path / "construction.sqlite3"
    async with _opened(path) as (primary, peer):
        assert primary.sessions is primary.sessions
        assert primary.dialogs is primary.dialogs
        assert primary.sessions._backend is primary.dialogs._backend
        assert primary.sessions._backend is primary._backend
        assert peer._backend is not primary._backend
        assert peer.sessions._backend is peer.dialogs._backend

        for unsupported in ("close", "aclose", "__enter__", "__aenter__"):
            assert not hasattr(primary, unsupported)


async def test_relative_database_path_is_canonicalized_at_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_directory = tmp_path / "original-cwd"
    later_directory = tmp_path / "later-cwd"
    original_directory.mkdir()
    later_directory.mkdir()
    monkeypatch.chdir(original_directory)
    executor = ThreadPoolExecutor(max_workers=SQLITE_TEST_EXECUTOR_WORKERS)
    try:
        persistence = await SqliteSessionPersistence.open(
            Path("session.sqlite3"),
            executor=executor,
        )
        monkeypatch.chdir(later_directory)

        assert isinstance(
            await persistence.sessions.create("canonical-path", now=NOW),
            SessionCreated,
        )
        assert _fetchone(
            original_directory / "session.sqlite3",
            "SELECT COUNT(*) FROM session_states WHERE session_id = 'canonical-path'",
        ) == (1,)
        assert not (later_directory / "session.sqlite3").exists()
    finally:
        executor.shutdown(wait=True)


@pytest.mark.parametrize("invalid_timeout", [-1, True, False, 1.5, "1", None])
async def test_invalid_busy_timeout_is_rejected_before_submit(
    tmp_path: Path,
    invalid_timeout: object,
) -> None:
    executor = RejectingExecutor()
    path = tmp_path / "invalid-timeout.sqlite3"
    try:
        with pytest.raises(ValueError):
            await SqliteSessionPersistence.open(
                path,
                executor=executor,
                busy_timeout_ms=invalid_timeout,  # type: ignore[arg-type]
            )
        assert executor.submit_count == 0
        assert not path.exists()
    finally:
        executor.shutdown(wait=True)


async def test_invalid_busy_timeout_precedes_path_resolution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executor = RejectingExecutor()

    def fail_if_resolved(path: Path, *args: Any, **kwargs: Any) -> Path:
        raise AssertionError(f"path resolution must not run: {path}")

    monkeypatch.setattr(sqlite_adapter.Path, "resolve", fail_if_resolved)
    try:
        with pytest.raises(ValueError):
            await SqliteSessionPersistence.open(
                "relative.sqlite3",
                executor=executor,
                busy_timeout_ms=-1,
            )
        assert executor.submit_count == 0
    finally:
        executor.shutdown(wait=True)


@pytest.mark.parametrize("invalid_path", ["", ":memory:", "file:session.sqlite3"])
async def test_non_file_backed_paths_are_rejected_before_submit(invalid_path: str) -> None:
    executor = RejectingExecutor()
    try:
        with pytest.raises(ValueError):
            await SqliteSessionPersistence.open(invalid_path, executor=executor)
        assert executor.submit_count == 0
    finally:
        executor.shutdown(wait=True)


async def test_zero_busy_timeout_is_supported(tmp_path: Path) -> None:
    executor = ThreadPoolExecutor(max_workers=SQLITE_TEST_EXECUTOR_WORKERS)
    try:
        persistence = await SqliteSessionPersistence.open(
            tmp_path / "zero-timeout.sqlite3",
            executor=executor,
            busy_timeout_ms=0,
        )
        assert isinstance(await persistence.sessions.create("zero", now=NOW), SessionCreated)
    finally:
        executor.shutdown(wait=True)


async def test_schema_v1_ledger_columns_constraints_and_indexes(tmp_path: Path) -> None:
    path = tmp_path / "schema.sqlite3"
    async with _opened(path):
        ledger = _fetchall(
            path,
            "SELECT component, version FROM schema_migrations ORDER BY component, version",
        )
        assert ledger == [("session", 1)]

        state_columns = _fetchall(path, "PRAGMA table_info(session_states)")
        dialog_columns = _fetchall(path, "PRAGMA table_info(session_dialogs)")
        ledger_columns = _fetchall(path, "PRAGMA table_info(schema_migrations)")
        assert [(row[1], row[2].upper(), row[3], row[5]) for row in ledger_columns] == [
            ("component", "TEXT", 0, 1),
            ("version", "INTEGER", 0, 2),
        ]
        assert [(row[1], row[2].upper(), row[3], row[5]) for row in state_columns] == [
            ("session_id", "TEXT", 0, 1),
            ("state_version", "INTEGER", 1, 0),
            ("created_at_us", "INTEGER", 1, 0),
            ("expires_at_us", "INTEGER", 1, 0),
            ("hard_expires_at_us", "INTEGER", 1, 0),
            ("payload_version", "INTEGER", 1, 0),
            ("state_json", "TEXT", 1, 0),
        ]
        assert [(row[1], row[2].upper(), row[3], row[5]) for row in dialog_columns] == [
            ("session_id", "TEXT", 0, 1),
            ("expires_at_us", "INTEGER", 1, 0),
            ("payload_version", "INTEGER", 1, 0),
            ("turns_json", "TEXT", 1, 0),
        ]

        foreign_keys = _fetchall(path, "PRAGMA foreign_key_list(session_dialogs)")
        assert len(foreign_keys) == 1
        assert foreign_keys[0][2] == "session_states"
        assert foreign_keys[0][3:5] == ("session_id", "session_id")
        assert foreign_keys[0][6].upper() == "CASCADE"

        state_indexes = _fetchall(path, "PRAGMA index_list(session_states)")
        dialog_indexes = _fetchall(path, "PRAGMA index_list(session_dialogs)")
        state_index_columns = {
            tuple(row[2] for row in _fetchall(path, f"PRAGMA index_info({index[1]})"))
            for index in state_indexes
            if index[2] == 0
        }
        dialog_index_columns = {
            tuple(row[2] for row in _fetchall(path, f"PRAGMA index_info({index[1]})"))
            for index in dialog_indexes
            if index[2] == 0
        }
        assert state_index_columns == {("expires_at_us",)}
        assert ("expires_at_us",) not in dialog_index_columns


@pytest.mark.parametrize(
    ("columns", "values"),
    [
        (
            "session_id,state_version,created_at_us,expires_at_us,hard_expires_at_us,payload_version,state_json",
            ("", 0, 0, 1, 2, 1, "{}"),
        ),
        (
            "session_id,state_version,created_at_us,expires_at_us,hard_expires_at_us,payload_version,state_json",
            ("negative-version", -1, 0, 1, 2, 1, "{}"),
        ),
        (
            "session_id,state_version,created_at_us,expires_at_us,hard_expires_at_us,payload_version,state_json",
            ("bad-payload", 0, 0, 1, 2, 0, "{}"),
        ),
        (
            "session_id,state_version,created_at_us,expires_at_us,hard_expires_at_us,payload_version,state_json",
            ("bad-order-a", 0, 2, 1, 3, 1, "{}"),
        ),
        (
            "session_id,state_version,created_at_us,expires_at_us,hard_expires_at_us,payload_version,state_json",
            ("bad-order-b", 0, 0, 3, 2, 1, "{}"),
        ),
    ],
)
async def test_database_constraints_reject_invalid_state_rows(
    tmp_path: Path,
    columns: str,
    values: tuple[object, ...],
) -> None:
    path = tmp_path / f"constraint-{values[0] or 'empty'}.sqlite3"
    async with _opened(path):
        placeholders = ",".join("?" for _ in values)
        connection = _connect(path)
        try:
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(
                    f"INSERT INTO session_states({columns}) VALUES ({placeholders})",
                    values,
                )
        finally:
            connection.close()


async def test_initialization_is_idempotent_and_preserves_data(tmp_path: Path) -> None:
    path = tmp_path / "idempotent.sqlite3"
    executor = ThreadPoolExecutor(max_workers=SQLITE_TEST_EXECUTOR_WORKERS)
    try:
        first = await SqliteSessionPersistence.open(path, executor=executor)
        expected = await populate_session(first, "preserved")

        second = await SqliteSessionPersistence.open(path, executor=executor)

        assert await second.sessions.get("preserved", now=NOW) == expected
        assert _fetchall(path, "SELECT component, version FROM schema_migrations") == [
            ("session", 1)
        ]
    finally:
        executor.shutdown(wait=True)


async def test_concurrent_initialization_of_new_file_is_serializable(tmp_path: Path) -> None:
    path = tmp_path / "concurrent-open.sqlite3"
    executor = ThreadPoolExecutor(max_workers=SQLITE_TEST_EXECUTOR_WORKERS)
    try:
        left, right = await asyncio.wait_for(
            asyncio.gather(
                SqliteSessionPersistence.open(path, executor=executor, busy_timeout_ms=1_000),
                SqliteSessionPersistence.open(path, executor=executor, busy_timeout_ms=1_000),
            ),
            timeout=5,
        )
        assert isinstance(await left.sessions.create("left", now=NOW), SessionCreated)
        assert isinstance(await right.sessions.create("right", now=NOW), SessionCreated)
        assert _fetchall(path, "SELECT component, version FROM schema_migrations") == [
            ("session", 1)
        ]
    finally:
        executor.shutdown(wait=True)


class BusyWalTransitionConnection(sqlite3.Connection):
    raised_count = 0

    def execute(
        self,
        sql: str,
        parameters: tuple[object, ...] = (),
        /,
    ) -> sqlite3.Cursor:
        normalized = " ".join(sql.strip().upper().split())
        if normalized == "PRAGMA JOURNAL_MODE = WAL" and not type(self).raised_count:
            type(self).raised_count += 1
            cursor = super().execute(sql, parameters)
            cursor.close()
            error = sqlite3.OperationalError("synthetic WAL transition race")
            error.sqlite_errorcode = sqlite3.SQLITE_BUSY
            error.sqlite_errorname = "SQLITE_BUSY"
            raise error
        return super().execute(sql, parameters)


class ScalarCursor:
    def __init__(self, value: object) -> None:
        self._row = (value,)

    def fetchone(self) -> tuple[object] | None:
        row = self._row
        self._row = None
        return row

    def close(self) -> None:
        pass


class WalReadbackConnection(sqlite3.Connection):
    outcomes: list[str] = []
    set_attempts = 0

    def execute(
        self,
        sql: str,
        parameters: tuple[object, ...] = (),
        /,
    ) -> sqlite3.Cursor:
        normalized = " ".join(sql.strip().upper().split())
        if normalized == "PRAGMA JOURNAL_MODE = WAL":
            outcome = type(self).outcomes[type(self).set_attempts]
            type(self).set_attempts += 1
            if outcome == "non-wal":
                return ScalarCursor("delete")  # type: ignore[return-value]
            if outcome == "busy":
                error = sqlite3.OperationalError("synthetic WAL transition busy")
                error.sqlite_errorcode = sqlite3.SQLITE_BUSY
                error.sqlite_errorname = "SQLITE_BUSY"
                raise error
        return super().execute(sql, parameters)


class SynchronousMismatchConnection(sqlite3.Connection):
    def execute(
        self,
        sql: str,
        parameters: tuple[object, ...] = (),
        /,
    ) -> sqlite3.Cursor:
        normalized = " ".join(sql.strip().upper().split())
        if normalized == "PRAGMA SYNCHRONOUS":
            return ScalarCursor(2)  # type: ignore[return-value]
        return super().execute(sql, parameters)


class ConfigurationAndCloseFaultConnection(SynchronousMismatchConnection):
    close_failures = 0

    def close(self) -> None:
        super().close()
        if type(self).close_failures == 0:
            type(self).close_failures += 1
            raise sqlite3.OperationalError("synthetic configuration close failure")


@pytest.mark.parametrize(
    ("outcomes", "expected_code"),
    [
        (("non-wal", "wal"), None),
        (("non-wal", "non-wal"), OPEN_FAILED),
        (("non-wal", "busy"), BUSY),
    ],
)
async def test_wal_readback_recovery_is_one_fresh_attempt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    outcomes: tuple[str, str],
    expected_code: str | None,
) -> None:
    path = tmp_path / f"wal-readback-{'-'.join(outcomes)}.sqlite3"
    WalReadbackConnection.outcomes = list(outcomes)
    WalReadbackConnection.set_attempts = 0
    opens = 0

    def factory(*args: Any, **kwargs: Any) -> sqlite3.Connection:
        nonlocal opens
        opens += 1
        return sqlite3.connect(*args, **kwargs, factory=WalReadbackConnection)

    monkeypatch.setattr(sqlite_adapter, "_CONNECTION_FACTORY", factory)
    executor = ThreadPoolExecutor(max_workers=SQLITE_TEST_EXECUTOR_WORKERS)
    try:
        if expected_code is None:
            persistence = await SqliteSessionPersistence.open(path, executor=executor)
            assert opens == 2
            assert isinstance(
                await persistence.sessions.create("wal-readback", now=NOW),
                SessionCreated,
            )
            assert opens == 3
        else:
            with pytest.raises(StateWriteError) as caught:
                await SqliteSessionPersistence.open(path, executor=executor)
            assert caught.value.error_code == expected_code
            assert opens == 2

        assert WalReadbackConnection.set_attempts == 2
    finally:
        executor.shutdown(wait=True)


async def test_non_wal_pragma_mismatch_does_not_retry_initialization(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opens = 0

    def factory(*args: Any, **kwargs: Any) -> sqlite3.Connection:
        nonlocal opens
        opens += 1
        return sqlite3.connect(
            *args,
            **kwargs,
            factory=SynchronousMismatchConnection,
        )

    monkeypatch.setattr(sqlite_adapter, "_CONNECTION_FACTORY", factory)
    executor = ThreadPoolExecutor(max_workers=SQLITE_TEST_EXECUTOR_WORKERS)
    try:
        with pytest.raises(StateWriteError) as caught:
            await SqliteSessionPersistence.open(
                tmp_path / "synchronous-mismatch.sqlite3",
                executor=executor,
            )

        assert caught.value.error_code == OPEN_FAILED
        assert opens == 1
    finally:
        executor.shutdown(wait=True)


async def test_configuration_error_is_not_replaced_by_close_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ConfigurationAndCloseFaultConnection.close_failures = 0

    def factory(*args: Any, **kwargs: Any) -> sqlite3.Connection:
        return sqlite3.connect(
            *args,
            **kwargs,
            factory=ConfigurationAndCloseFaultConnection,
        )

    monkeypatch.setattr(sqlite_adapter, "_CONNECTION_FACTORY", factory)
    executor = ThreadPoolExecutor(max_workers=SQLITE_TEST_EXECUTOR_WORKERS)
    try:
        with pytest.raises(StateWriteError) as caught:
            await SqliteSessionPersistence.open(
                tmp_path / "configuration-and-close-fail.sqlite3",
                executor=executor,
            )

        assert caught.value.error_code == OPEN_FAILED
        assert ConfigurationAndCloseFaultConnection.close_failures == 1
    finally:
        executor.shutdown(wait=True)


async def test_open_retries_wal_transition_busy_on_one_fresh_connection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "wal-transition-race.sqlite3"
    executor = ThreadPoolExecutor(max_workers=SQLITE_TEST_EXECUTOR_WORKERS)
    try:
        await SqliteSessionPersistence.open(path, executor=executor)
        connection = _connect(path)
        try:
            row = connection.execute("PRAGMA journal_mode = DELETE").fetchone()
            assert row is not None and str(row[0]).lower() == "delete"
        finally:
            connection.close()
        BusyWalTransitionConnection.raised_count = 0

        def factory(*args: Any, **kwargs: Any) -> sqlite3.Connection:
            return sqlite3.connect(
                *args,
                **kwargs,
                factory=BusyWalTransitionConnection,
            )

        monkeypatch.setattr(sqlite_adapter, "_CONNECTION_FACTORY", factory)
        reopened = await SqliteSessionPersistence.open(path, executor=executor)

        assert BusyWalTransitionConnection.raised_count == 1
        assert isinstance(
            await reopened.sessions.create("wal-race", now=NOW),
            SessionCreated,
        )
    finally:
        executor.shutdown(wait=True)


async def test_open_busy_recovery_is_bounded_to_one_fresh_connection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = 0

    def always_busy(*args: Any, **kwargs: Any) -> sqlite3.Connection:
        nonlocal attempts
        attempts += 1
        error = sqlite3.OperationalError("synthetic persistent init busy")
        error.sqlite_errorcode = sqlite3.SQLITE_BUSY
        error.sqlite_errorname = "SQLITE_BUSY"
        raise error

    monkeypatch.setattr(sqlite_adapter, "_CONNECTION_FACTORY", always_busy)
    executor = ThreadPoolExecutor(max_workers=SQLITE_TEST_EXECUTOR_WORKERS)
    try:
        with pytest.raises(StateWriteError) as caught:
            await SqliteSessionPersistence.open(
                tmp_path / "persistent-init-busy.sqlite3",
                executor=executor,
                busy_timeout_ms=1_000,
            )

        assert caught.value.error_code == BUSY
        assert attempts == 2
    finally:
        executor.shutdown(wait=True)


async def test_foreign_migration_namespace_is_ignored_and_preserved(tmp_path: Path) -> None:
    path = tmp_path / "foreign-namespace.sqlite3"
    async with _opened(path) as (primary, _):
        _execute(
            path,
            "INSERT INTO schema_migrations(component, version) VALUES (?, ?)",
            ("research-corpus", 41),
        )
        reopen_executor = ThreadPoolExecutor(max_workers=SQLITE_TEST_EXECUTOR_WORKERS)
        try:
            reopened = await SqliteSessionPersistence.open(path, executor=reopen_executor)
            assert isinstance(
                await reopened.sessions.create("still-works", now=NOW),
                SessionCreated,
            )
        finally:
            reopen_executor.shutdown(wait=True)
        assert _fetchall(
            path,
            "SELECT component, version FROM schema_migrations ORDER BY component, version",
        ) == [("research-corpus", 41), ("session", 1)]


@pytest.mark.parametrize("versions", [(1, 2), (2,)])
async def test_future_or_gapped_session_ledger_is_schema_incompatible(
    tmp_path: Path,
    versions: tuple[int, ...],
) -> None:
    path = tmp_path / f"incompatible-{'-'.join(map(str, versions))}.sqlite3"
    executor = ThreadPoolExecutor(max_workers=SQLITE_TEST_EXECUTOR_WORKERS)
    try:
        await SqliteSessionPersistence.open(path, executor=executor)
        connection = _connect(path)
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("DELETE FROM schema_migrations WHERE component = 'session'")
            connection.executemany(
                "INSERT INTO schema_migrations(component, version) VALUES ('session', ?)",
                [(version,) for version in versions],
            )
            connection.commit()
        finally:
            connection.close()

        with pytest.raises(StateWriteError) as caught:
            await SqliteSessionPersistence.open(path, executor=executor)
        assert caught.value.error_code == SCHEMA_INCOMPATIBLE
    finally:
        executor.shutdown(wait=True)


async def test_drifted_known_schema_shape_is_schema_incompatible(tmp_path: Path) -> None:
    path = tmp_path / "schema-shape-drift.sqlite3"
    executor = ThreadPoolExecutor(max_workers=SQLITE_TEST_EXECUTOR_WORKERS)
    try:
        await SqliteSessionPersistence.open(path, executor=executor)
        _execute(path, "ALTER TABLE session_states ADD COLUMN unexpected INTEGER")

        with pytest.raises(StateWriteError) as caught:
            await SqliteSessionPersistence.open(path, executor=executor)
        assert caught.value.error_code == SCHEMA_INCOMPATIBLE
    finally:
        executor.shutdown(wait=True)


async def test_open_failure_has_dedicated_typed_code(tmp_path: Path) -> None:
    executor = ThreadPoolExecutor(max_workers=SQLITE_TEST_EXECUTOR_WORKERS)
    missing_parent = tmp_path / "missing" / "cannot-open.sqlite3"
    try:
        with pytest.raises(StateWriteError) as caught:
            await SqliteSessionPersistence.open(missing_parent, executor=executor)
        assert caught.value.error_code == OPEN_FAILED
    finally:
        executor.shutdown(wait=True)


async def test_known_migration_failure_rolls_back_ddl_and_ledger(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "migration-failure.sqlite3"
    executor = ThreadPoolExecutor(max_workers=SQLITE_TEST_EXECUTOR_WORKERS)
    try:
        await SqliteSessionPersistence.open(path, executor=executor)
        broken = sqlite_adapter._Migration(
            version=2,
            statements=(
                "CREATE TABLE migration_probe(value INTEGER NOT NULL)",
                "THIS IS NOT SQL",
            ),
        )
        monkeypatch.setattr(
            sqlite_adapter,
            "_SESSION_MIGRATIONS",
            (*sqlite_adapter._SESSION_MIGRATIONS, broken),
        )

        with pytest.raises(StateWriteError) as caught:
            await SqliteSessionPersistence.open(path, executor=executor)
        assert caught.value.error_code == MIGRATION_FAILED
        assert _fetchall(
            path,
            "SELECT version FROM schema_migrations WHERE component = 'session' ORDER BY version",
        ) == [(1,)]
        assert _fetchall(
            path,
            "SELECT name FROM sqlite_schema WHERE type = 'table' AND name = 'migration_probe'",
        ) == []
    finally:
        executor.shutdown(wait=True)


class ObservedConnection(sqlite3.Connection):
    instances: list["ObservedConnection"] = []
    instances_lock = threading.Lock()

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.owner_thread_id = threading.get_ident()
        self.statements: list[str] = []
        self.effective_at_close: tuple[int, int, str, int] | None = None
        self.was_closed = False
        self.set_trace_callback(self.statements.append)
        with type(self).instances_lock:
            type(self).instances.append(self)

    def close(self) -> None:
        if not self.was_closed:
            foreign_keys = self.execute("PRAGMA foreign_keys").fetchone()
            busy_timeout = self.execute("PRAGMA busy_timeout").fetchone()
            journal_mode = self.execute("PRAGMA journal_mode").fetchone()
            synchronous = self.execute("PRAGMA synchronous").fetchone()
            assert foreign_keys is not None
            assert busy_timeout is not None
            assert journal_mode is not None
            assert synchronous is not None
            self.effective_at_close = (
                foreign_keys[0],
                busy_timeout[0],
                str(journal_mode[0]).lower(),
                synchronous[0],
            )
            self.was_closed = True
        super().close()


def _observed_factory(*args: Any, **kwargs: Any) -> sqlite3.Connection:
    return sqlite3.connect(*args, **kwargs, factory=ObservedConnection)


class CloseAfterRealCloseConnection(sqlite3.Connection):
    armed = False
    failures = 0

    def close(self) -> None:
        super().close()
        if type(self).armed and type(self).failures == 0:
            type(self).failures += 1
            raise sqlite3.OperationalError("synthetic post-close failure")


def _close_fault_factory(*args: Any, **kwargs: Any) -> sqlite3.Connection:
    return sqlite3.connect(*args, **kwargs, factory=CloseAfterRealCloseConnection)


def _database_snapshot(path: Path) -> tuple[list[tuple[Any, ...]], list[tuple[Any, ...]]]:
    return (
        _fetchall(
            path,
            "SELECT session_id, state_version, created_at_us, expires_at_us, "
            "hard_expires_at_us, payload_version, state_json "
            "FROM session_states ORDER BY session_id",
        ),
        _fetchall(
            path,
            "SELECT session_id, expires_at_us, payload_version, turns_json "
            "FROM session_dialogs ORDER BY session_id",
        ),
    )


async def _exercise_close_path(
    path: Path,
    operation: str,
    *,
    fail_close: bool,
) -> tuple[object, tuple[list[tuple[Any, ...]], list[tuple[Any, ...]]]]:
    executor = ThreadPoolExecutor(max_workers=SQLITE_TEST_EXECUTOR_WORKERS)
    CloseAfterRealCloseConnection.armed = operation == "open" and fail_close
    try:
        persistence = await SqliteSessionPersistence.open(path, executor=executor)
        CloseAfterRealCloseConnection.armed = False
        if operation == "open":
            outcome: object = (
                isinstance(persistence, SqliteSessionPersistence),
                persistence.sessions is persistence.sessions,
                persistence.dialogs is persistence.dialogs,
                await persistence.sessions.create("post-open", now=NOW),
            )
        else:
            if operation == "create":
                pass
            elif operation in {"get", "compare_and_set", "append"}:
                await create_session(persistence, "close-target")
            elif operation in {"read", "clear", "touch", "delete"}:
                await create_session(persistence, "close-target")
                await persistence.dialogs.append(
                    "close-target",
                    make_turn(1),
                    now=NOW,
                )
            elif operation == "reset":
                await populate_with_dialog(persistence, "close-target")
            else:
                await create_session(
                    persistence,
                    "close-target",
                    now=NOW - timedelta(days=8),
                )

            CloseAfterRealCloseConnection.armed = fail_close
            if operation == "create":
                outcome = await persistence.sessions.create("close-target", now=NOW)
            elif operation == "get":
                outcome = await persistence.sessions.get("close-target", now=NOW)
            elif operation == "compare_and_set":
                outcome = await persistence.sessions.compare_and_set(
                    "close-target",
                    0,
                    DELTA,
                    now=NOW,
                )
            elif operation == "append":
                outcome = await persistence.dialogs.append(
                    "close-target",
                    make_turn(1),
                    now=NOW,
                )
            elif operation == "read":
                outcome = await persistence.dialogs.read("close-target", now=NOW)
            elif operation == "clear":
                outcome = await persistence.dialogs.clear("close-target", now=NOW)
            elif operation == "touch":
                outcome = await persistence.touch("close-target", now=NOW)
            elif operation == "reset":
                outcome = await persistence.reset(
                    "close-target",
                    1,
                    now=NOW,
                )
            elif operation == "delete":
                outcome = await persistence.delete("close-target")
            else:
                outcome = await persistence.reap_expired(now=NOW)
            CloseAfterRealCloseConnection.armed = False

        return outcome, _database_snapshot(path)
    finally:
        CloseAfterRealCloseConnection.armed = False
        executor.shutdown(wait=True)


@pytest.mark.parametrize(
    "operation",
    [
        "open",
        "create",
        "get",
        "compare_and_set",
        "append",
        "read",
        "clear",
        "touch",
        "reset",
        "delete",
        "reap_expired",
    ],
)
async def test_close_failure_does_not_replace_completed_public_outcome(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    operation: str,
) -> None:
    monkeypatch.setattr(sqlite_adapter, "_CONNECTION_FACTORY", _close_fault_factory)
    baseline_path = tmp_path / f"close-baseline-{operation}.sqlite3"
    fault_path = tmp_path / f"close-fault-{operation}.sqlite3"

    CloseAfterRealCloseConnection.failures = 0
    baseline = await _exercise_close_path(
        baseline_path,
        operation,
        fail_close=False,
    )
    caplog.clear()
    fault = await _exercise_close_path(
        fault_path,
        operation,
        fail_close=True,
    )

    assert fault == baseline
    assert CloseAfterRealCloseConnection.failures == 1
    messages = [record.getMessage() for record in caplog.records]
    assert len(messages) == 1
    assert "cleanup failed" in messages[0]
    assert "close-target" not in messages[0]
    assert str(fault_path) not in messages[0]


class ProgrammingCloseConnection(sqlite3.Connection):
    armed = False
    failures = 0

    def close(self) -> None:
        super().close()
        if type(self).armed and type(self).failures == 0:
            type(self).failures += 1
            raise sqlite3.ProgrammingError("synthetic programming defect")


def _programming_close_factory(*args: Any, **kwargs: Any) -> sqlite3.Connection:
    return sqlite3.connect(*args, **kwargs, factory=ProgrammingCloseConnection)


async def test_programming_error_from_close_is_not_typed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "programming-close.sqlite3"
    monkeypatch.setattr(
        sqlite_adapter,
        "_CONNECTION_FACTORY",
        _programming_close_factory,
    )
    ProgrammingCloseConnection.armed = False
    ProgrammingCloseConnection.failures = 0
    executor = ThreadPoolExecutor(max_workers=SQLITE_TEST_EXECUTOR_WORKERS)
    try:
        persistence = await SqliteSessionPersistence.open(path, executor=executor)
        await create_session(persistence, "programming-close")
        ProgrammingCloseConnection.armed = True

        with pytest.raises(sqlite3.ProgrammingError):
            await persistence.sessions.get("programming-close", now=NOW)

        assert ProgrammingCloseConnection.failures == 1
    finally:
        ProgrammingCloseConnection.armed = False
        executor.shutdown(wait=True)


async def test_close_classification_does_not_depend_on_callers_except_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "caller-except-close.sqlite3"
    monkeypatch.setattr(sqlite_adapter, "_CONNECTION_FACTORY", _close_fault_factory)
    CloseAfterRealCloseConnection.armed = False
    CloseAfterRealCloseConnection.failures = 0
    executor = ThreadPoolExecutor(max_workers=SQLITE_TEST_EXECUTOR_WORKERS)
    try:
        persistence = await SqliteSessionPersistence.open(path, executor=executor)
        expected = await create_session(persistence, "caller-except")
        CloseAfterRealCloseConnection.armed = True

        try:
            raise LookupError("unrelated caller error")
        except LookupError:
            actual = await persistence.sessions.get("caller-except", now=NOW)

        assert actual == expected
        assert CloseAfterRealCloseConnection.failures == 1
    finally:
        CloseAfterRealCloseConnection.armed = False
        executor.shutdown(wait=True)


class RollbackFaultConnection(sqlite3.Connection):
    rollback_armed = False
    close_armed = False
    rollback_failures = 0
    close_failures = 0

    def rollback(self) -> None:
        if type(self).rollback_armed and type(self).rollback_failures == 0:
            type(self).rollback_failures += 1
            raise sqlite3.OperationalError("synthetic rollback failure")
        super().rollback()

    def close(self) -> None:
        super().close()
        if type(self).close_armed and type(self).close_failures == 0:
            type(self).close_failures += 1
            raise sqlite3.OperationalError("synthetic post-close failure")


def _rollback_fault_factory(*args: Any, **kwargs: Any) -> sqlite3.Connection:
    return sqlite3.connect(*args, **kwargs, factory=RollbackFaultConnection)


class ProgrammingRollbackConnection(sqlite3.Connection):
    armed = False

    def rollback(self) -> None:
        if type(self).armed:
            raise sqlite3.ProgrammingError("synthetic rollback programming defect")
        super().rollback()


def _programming_rollback_factory(*args: Any, **kwargs: Any) -> sqlite3.Connection:
    return sqlite3.connect(*args, **kwargs, factory=ProgrammingRollbackConnection)


async def _prepare_normal_no_commit_outcome(
    persistence: SqliteSessionPersistence,
    operation: str,
) -> tuple[object, Callable[[], Any]]:
    if operation == "create-conflict":
        await create_session(persistence, "rollback-target")
        expected: object = SessionIdConflict(session_id="rollback-target")

        async def invoke() -> object:
            return await persistence.sessions.create("rollback-target", now=NOW)

    elif operation == "cas-conflict":
        expected_state = await create_session(persistence, "rollback-target")
        expected = VersionConflict(actual=expected_state)

        async def invoke() -> object:
            return await persistence.sessions.compare_and_set(
                "rollback-target",
                7,
                DELTA,
                now=NOW,
            )

    elif operation == "touch-absent":
        expected = SessionAbsent(reason="not_found")

        async def invoke() -> object:
            return await persistence.touch("rollback-target", now=NOW)

    else:
        await create_session(persistence, "rollback-target")
        turn = make_turn(1)
        await persistence.dialogs.append("rollback-target", turn, now=NOW)
        expected = (turn,)

        async def invoke() -> object:
            return await persistence.dialogs.read("rollback-target", now=NOW)

    return expected, invoke


@pytest.mark.parametrize(
    "operation",
    ["create-conflict", "cas-conflict", "touch-absent", "dialog-read"],
)
async def test_rollback_failure_preserves_normal_outcome_when_close_succeeds(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    path = tmp_path / f"rollback-close-success-{operation}.sqlite3"
    monkeypatch.setattr(sqlite_adapter, "_CONNECTION_FACTORY", _rollback_fault_factory)
    RollbackFaultConnection.rollback_armed = False
    RollbackFaultConnection.close_armed = False
    RollbackFaultConnection.rollback_failures = 0
    RollbackFaultConnection.close_failures = 0
    executor = ThreadPoolExecutor(max_workers=SQLITE_TEST_EXECUTOR_WORKERS)
    try:
        persistence = await SqliteSessionPersistence.open(path, executor=executor)
        expected, invoke = await _prepare_normal_no_commit_outcome(
            persistence,
            operation,
        )
        before = _database_snapshot(path)
        RollbackFaultConnection.rollback_armed = True

        assert await invoke() == expected

        RollbackFaultConnection.rollback_armed = False
        assert RollbackFaultConnection.rollback_failures == 1
        assert RollbackFaultConnection.close_failures == 0
        assert _database_snapshot(path) == before
    finally:
        RollbackFaultConnection.rollback_armed = False
        RollbackFaultConnection.close_armed = False
        executor.shutdown(wait=True)


async def test_rollback_and_close_failure_is_typed_and_releases_test_handle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "rollback-and-close-fail.sqlite3"
    monkeypatch.setattr(sqlite_adapter, "_CONNECTION_FACTORY", _rollback_fault_factory)
    RollbackFaultConnection.rollback_armed = False
    RollbackFaultConnection.close_armed = False
    RollbackFaultConnection.rollback_failures = 0
    RollbackFaultConnection.close_failures = 0
    executor = ThreadPoolExecutor(max_workers=SQLITE_TEST_EXECUTOR_WORKERS)
    try:
        persistence = await SqliteSessionPersistence.open(path, executor=executor)
        expected_state = await create_session(persistence, "rollback-target")
        RollbackFaultConnection.rollback_armed = True
        RollbackFaultConnection.close_armed = True

        with pytest.raises(StateWriteError) as caught:
            await persistence.sessions.compare_and_set(
                "rollback-target",
                7,
                DELTA,
                now=NOW,
            )

        RollbackFaultConnection.rollback_armed = False
        RollbackFaultConnection.close_armed = False
        assert caught.value.error_code == WRITE_FAILED
        assert RollbackFaultConnection.rollback_failures == 1
        assert RollbackFaultConnection.close_failures == 1
        assert await persistence.sessions.get(
            "rollback-target",
            now=NOW,
        ) == expected_state
        _execute(
            path,
            "UPDATE session_states SET state_version = state_version "
            "WHERE session_id = 'rollback-target'",
        )
    finally:
        RollbackFaultConnection.rollback_armed = False
        RollbackFaultConnection.close_armed = False
        executor.shutdown(wait=True)


async def test_programming_error_from_rollback_is_not_typed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "programming-rollback.sqlite3"
    monkeypatch.setattr(
        sqlite_adapter,
        "_CONNECTION_FACTORY",
        _programming_rollback_factory,
    )
    ProgrammingRollbackConnection.armed = False
    executor = ThreadPoolExecutor(max_workers=SQLITE_TEST_EXECUTOR_WORKERS)
    try:
        persistence = await SqliteSessionPersistence.open(path, executor=executor)
        await create_session(persistence, "programming-rollback")
        ProgrammingRollbackConnection.armed = True

        with pytest.raises(sqlite3.ProgrammingError):
            await persistence.sessions.compare_and_set(
                "programming-rollback",
                7,
                DELTA,
                now=NOW,
            )
    finally:
        ProgrammingRollbackConnection.armed = False
        executor.shutdown(wait=True)


async def test_close_failure_does_not_replace_existing_typed_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "error-and-close-fail.sqlite3"
    monkeypatch.setattr(sqlite_adapter, "_CONNECTION_FACTORY", _close_fault_factory)
    CloseAfterRealCloseConnection.armed = False
    CloseAfterRealCloseConnection.failures = 0
    executor = ThreadPoolExecutor(max_workers=SQLITE_TEST_EXECUTOR_WORKERS)
    try:
        persistence = await SqliteSessionPersistence.open(path, executor=executor)
        await create_session(persistence, "corrupt-and-close")
        _execute(
            path,
            "UPDATE session_states SET state_json = '{' "
            "WHERE session_id = 'corrupt-and-close'",
        )
        CloseAfterRealCloseConnection.armed = True

        with pytest.raises(StateReadError) as caught:
            await persistence.sessions.get("corrupt-and-close", now=NOW)

        assert caught.value.error_code == DATA_CORRUPT
        assert CloseAfterRealCloseConnection.failures == 1
    finally:
        CloseAfterRealCloseConnection.armed = False
        executor.shutdown(wait=True)


def _normalized_statements(connection: ObservedConnection) -> list[str]:
    return [" ".join(statement.strip().upper().split()) for statement in connection.statements]


async def test_every_connection_has_effective_pragmas_and_is_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "observed-connections.sqlite3"
    ObservedConnection.instances = []
    monkeypatch.setattr(sqlite_adapter, "_CONNECTION_FACTORY", _observed_factory)
    executor = ThreadPoolExecutor(max_workers=SQLITE_TEST_EXECUTOR_WORKERS)
    try:
        persistence = await SqliteSessionPersistence.open(
            path,
            executor=executor,
            busy_timeout_ms=731,
        )
        assert isinstance(await persistence.sessions.create("observed", now=NOW), SessionCreated)
        assert isinstance(await persistence.sessions.get("observed", now=NOW), SessionState)
        assert await persistence.dialogs.read("observed", now=NOW) == ()
    finally:
        executor.shutdown(wait=True)

    assert len(ObservedConnection.instances) == 4
    assert all(connection.was_closed for connection in ObservedConnection.instances)
    assert all(
        connection.effective_at_close == (1, 731, "wal", 1)
        for connection in ObservedConnection.instances
    )
    assert all(
        connection.owner_thread_id != threading.get_ident()
        for connection in ObservedConnection.instances
    )

    initialization, create_connection, get_connection, read_connection = (
        ObservedConnection.instances
    )
    init_trace = _normalized_statements(initialization)
    expected_init_prefixes = [
        "PRAGMA FOREIGN_KEYS = ON",
        "PRAGMA FOREIGN_KEYS",
        "PRAGMA BUSY_TIMEOUT = 731",
        "PRAGMA BUSY_TIMEOUT",
        "PRAGMA JOURNAL_MODE = WAL",
        "PRAGMA SYNCHRONOUS = NORMAL",
        "PRAGMA SYNCHRONOUS",
        "BEGIN IMMEDIATE",
    ]
    cursor = -1
    for expected in expected_init_prefixes:
        cursor = next(
            index
            for index in range(cursor + 1, len(init_trace))
            if init_trace[index] == expected
        )
    assert any(
        statement.startswith("PRAGMA JOURNAL_MODE")
        for statement in init_trace
    )
    for operation_connection in (create_connection, get_connection, read_connection):
        before_close_readbacks = _normalized_statements(operation_connection)[:-4]
        assert not any(
            statement.startswith("PRAGMA JOURNAL_MODE")
            for statement in before_close_readbacks
        )


async def test_invalid_config_precedes_connection_factory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connections = 0

    def counting_factory(*args: Any, **kwargs: Any) -> sqlite3.Connection:
        nonlocal connections
        connections += 1
        return sqlite3.connect(*args, **kwargs)

    monkeypatch.setattr(sqlite_adapter, "_CONNECTION_FACTORY", counting_factory)
    executor = RecordingExecutor(max_workers=SQLITE_TEST_EXECUTOR_WORKERS)
    try:
        baseline_submits = executor.submit_count
        with pytest.raises(ValueError):
            await SqliteSessionPersistence.open(
                tmp_path / "invalid-config.sqlite3",
                executor=executor,
                busy_timeout_ms=-1,
            )
        assert executor.submit_count == baseline_submits
        assert connections == 0
    finally:
        executor.shutdown(wait=True)


async def test_transaction_modes_are_observable_before_operation_reads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "transaction-modes.sqlite3"
    ObservedConnection.instances = []
    monkeypatch.setattr(sqlite_adapter, "_CONNECTION_FACTORY", _observed_factory)
    executor = ThreadPoolExecutor(max_workers=SQLITE_TEST_EXECUTOR_WORKERS)
    try:
        persistence = await SqliteSessionPersistence.open(path, executor=executor)
        await create_session(persistence, "transactions")
        await persistence.dialogs.read("transactions", now=NOW)
        await persistence.touch("transactions", now=NOW)
    finally:
        executor.shutdown(wait=True)

    create_trace = _normalized_statements(ObservedConnection.instances[-3])
    read_trace = _normalized_statements(ObservedConnection.instances[-2])
    touch_trace = _normalized_statements(ObservedConnection.instances[-1])

    assert next(item for item in create_trace if item.startswith("BEGIN")) == "BEGIN IMMEDIATE"
    assert next(item for item in read_trace if item.startswith("BEGIN")) in {
        "BEGIN",
        "BEGIN DEFERRED",
    }
    assert "ROLLBACK" in read_trace
    assert next(item for item in touch_trace if item.startswith("BEGIN")) == "BEGIN IMMEDIATE"
    assert touch_trace.index("BEGIN IMMEDIATE") < next(
        index
        for index, item in enumerate(touch_trace)
        if item.startswith("SELECT")
    )


@pytest.mark.parametrize(
    "operation",
    ["create", "compare_and_set", "append", "clear", "touch", "delete", "reaper"],
)
async def test_every_write_operation_begins_immediate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    path = tmp_path / f"begin-immediate-{operation}.sqlite3"
    ObservedConnection.instances = []
    monkeypatch.setattr(sqlite_adapter, "_CONNECTION_FACTORY", _observed_factory)
    executor = ThreadPoolExecutor(max_workers=SQLITE_TEST_EXECUTOR_WORKERS)
    try:
        persistence = await SqliteSessionPersistence.open(path, executor=executor)
        if operation != "create":
            await populate_with_dialog(persistence, "write-mode")
        ObservedConnection.instances = []

        if operation == "create":
            await persistence.sessions.create("write-mode", now=NOW)
        elif operation == "compare_and_set":
            await persistence.sessions.compare_and_set("write-mode", 1, DELTA, now=NOW)
        elif operation == "append":
            await persistence.dialogs.append("write-mode", make_turn(2), now=NOW)
        elif operation == "clear":
            await persistence.dialogs.clear("write-mode", now=NOW)
        elif operation == "touch":
            await persistence.touch("write-mode", now=NOW)
        elif operation == "delete":
            await persistence.delete("write-mode")
        else:
            await persistence.reap_expired(now=NOW)
    finally:
        executor.shutdown(wait=True)

    assert len(ObservedConnection.instances) == 1
    trace = _normalized_statements(ObservedConnection.instances[0])
    first_transaction = next(item for item in trace if item.startswith("BEGIN"))
    assert first_transaction == "BEGIN IMMEDIATE"
    if operation in {"compare_and_set", "append", "clear", "touch"}:
        assert trace.index("BEGIN IMMEDIATE") < next(
            index for index, item in enumerate(trace) if item.startswith("SELECT")
        )


async def test_foreign_key_cascade_is_effective_in_public_delete(tmp_path: Path) -> None:
    path = tmp_path / "cascade.sqlite3"
    async with _opened(path) as (persistence, _):
        await populate_with_dialog(persistence, "cascade")
        assert _fetchone(
            path,
            "SELECT COUNT(*) FROM session_dialogs WHERE session_id = 'cascade'",
        ) == (1,)

        await persistence.delete("cascade")

        assert _fetchone(
            path,
            "SELECT COUNT(*) FROM session_states WHERE session_id = 'cascade'",
        ) == (0,)
        assert _fetchone(
            path,
            "SELECT COUNT(*) FROM session_dialogs WHERE session_id = 'cascade'",
        ) == (0,)


MICRO_NOW = datetime(2026, 9, 5, 12, 34, 56, 789_123, tzinfo=UTC)

PAYLOAD_COMPATIBILITY_FAILURE = (
    "persisted payload v1 changed; either prove the change remains backward "
    "compatible and update the golden fixture, add a new payload version with "
    "an old-version decoder or migration, or prove that only irrelevant test "
    "representation/Pydantic output changed"
)


def _golden_models() -> tuple[
    dict[str, SessionState],
    tuple[DialogTurn, ...],
    dict[str, StateDelta],
]:
    unknown_birth = BirthInput(
        birth_date=date(1987, 1, 2),
        birth_time=None,
        place_id="moscow-earth",
    )
    unknown_anchor, unknown_offset, unknown_domain = (
        resolve_unknown_birth_time_for_migration(
            unknown_birth.birth_date,
            "Europe/Moscow",
        )
    )
    unknown_resolved = ResolvedBirthData(
        utc_datetime=unknown_anchor,
        latitude=55.75,
        longitude=37.62,
        tz_id="Europe/Moscow",
        utc_offset_seconds=unknown_offset,
        canonical_place="Moscow",
        time_unknown=True,
        birth_time_domain=unknown_domain,
        warnings=(
            ResolutionWarning(
                source="time",
                code="TIME_UNKNOWN",
                message="Assumed noon",
            ),
        ),
    )
    unknown_spec = NatalChartSpec(
        chart_kind="natal",
        include=("rulers", "positions", "houses", "aspects"),
        rulership=RulershipScheme.TRADITIONAL,
        near_interception_threshold=0.125,
    )
    known_birth = BirthInput(
        birth_date=date(1990, 6, 15),
        birth_time=time(8, 30),
        place_id="paris",
    )
    known_resolved = ResolvedBirthData(
        utc_datetime=datetime(1990, 6, 15, 6, 30, tzinfo=UTC),
        latitude=48.86,
        longitude=2.35,
        tz_id="Europe/Paris",
        utc_offset_seconds=7_200,
        canonical_place="Paris",
        time_unknown=False,
        birth_time_domain=None,
        warnings=(),
    )
    known_spec = NatalChartSpec(
        chart_kind="cosmogram",
        include=("positions", "aspects"),
    )
    states = {
        "golden-empty": SessionState(
            session_id="golden-empty",
            birth_input=None,
            birth_resolved=None,
            state_version=0,
            base_chart=None,
            created_at=MICRO_NOW,
            expires_at=MICRO_NOW + SLIDING_TTL,
            hard_expires_at=MICRO_NOW + HARD_TTL,
        ),
        "golden-full": SessionState(
            session_id="golden-full",
            birth_input=unknown_birth,
            birth_resolved=unknown_resolved,
            state_version=1,
            base_chart=ChartRef(state_version=1, spec=unknown_spec),
            created_at=MICRO_NOW,
            expires_at=MICRO_NOW + SLIDING_TTL,
            hard_expires_at=MICRO_NOW + HARD_TTL,
        ),
        "golden-known": SessionState(
            session_id="golden-known",
            birth_input=known_birth,
            birth_resolved=known_resolved,
            state_version=1,
            base_chart=ChartRef(state_version=1, spec=known_spec),
            created_at=MICRO_NOW,
            expires_at=MICRO_NOW + SLIDING_TTL,
            hard_expires_at=MICRO_NOW + HARD_TTL,
        ),
    }
    dialog = (
        DialogTurn(
            turn_id="complete",
            created_at=MICRO_NOW + timedelta(microseconds=1),
            selection=Selection(topic="natal", focus="relationships"),
            state_version_at_answer=1,
            status="complete",
            truncated=True,
            text="Unicode: ☃",
        ),
        DialogTurn(
            turn_id="partial",
            created_at=MICRO_NOW + timedelta(microseconds=2),
            selection=Selection(topic="natal", focus="career"),
            state_version_at_answer=99,
            status="partial",
            truncated=False,
            text="partial answer",
        ),
    )
    deltas = {
        "golden-full": StateDelta(
            birth_input=unknown_birth,
            birth_resolved=unknown_resolved,
            base_chart_spec=unknown_spec,
        ),
        "golden-known": StateDelta(
            birth_input=known_birth,
            birth_resolved=known_resolved,
            base_chart_spec=known_spec,
        ),
    }
    return states, dialog, deltas


def _load_golden_payload_v1() -> dict[str, Any]:
    return json.loads(GOLDEN_PAYLOAD_V1.read_text(encoding="utf-8"))


def _load_golden_payload_v2() -> dict[str, Any]:
    return json.loads(GOLDEN_PAYLOAD_V2.read_text(encoding="utf-8"))


def _assert_payload_contract_equal(actual: object, expected: object) -> None:
    assert actual == expected, PAYLOAD_COMPATIBILITY_FAILURE


def _insert_golden_payload_v1(path: Path, fixture: dict[str, Any]) -> None:
    connection = _connect(path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        for row in fixture["states"]:
            metadata = row["metadata"]
            connection.execute(
                "INSERT INTO session_states "
                "(session_id, state_version, created_at_us, expires_at_us, "
                "hard_expires_at_us, payload_version, state_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    metadata["session_id"],
                    metadata["state_version"],
                    metadata["created_at_us"],
                    metadata["expires_at_us"],
                    metadata["hard_expires_at_us"],
                    metadata["payload_version"],
                    _json(row["payload"]),
                ),
            )
        dialog = fixture["dialog"]
        metadata = dialog["metadata"]
        connection.execute(
            "INSERT INTO session_dialogs "
            "(session_id, expires_at_us, payload_version, turns_json) "
            "VALUES (?, ?, ?, ?)",
            (
                metadata["session_id"],
                metadata["expires_at_us"],
                metadata["payload_version"],
                _json(dialog["payload"]),
            ),
        )
        connection.commit()
    finally:
        connection.close()


def test_payload_manifests_match_codec_versions_and_dialog_limits() -> None:
    fixture = _load_golden_payload_v1()

    _assert_payload_contract_equal(fixture["state_payload_version"], 1)
    _assert_payload_contract_equal(sqlite_adapter._STATE_PAYLOAD_VERSION, 2)
    _assert_payload_contract_equal(
        fixture["dialog_payload_version"],
        sqlite_adapter._DIALOG_PAYLOAD_VERSION,
    )
    _assert_payload_contract_equal(
        fixture["dialog_limits"],
        {
            "max_turns": MAX_DIALOG_TURNS,
            "max_turn_chars": MAX_DIALOG_TURN_CHARS,
            "max_chars": MAX_DIALOG_CHARS,
        },
    )
    assert set(fixture["reference_environment"]) == {
        "python",
        "sqlite",
        "pydantic",
    }
    fixture_v2 = _load_golden_payload_v2()
    _assert_payload_contract_equal(
        fixture_v2["state_payload_version"],
        sqlite_adapter._STATE_PAYLOAD_VERSION,
    )
    _assert_payload_contract_equal(
        fixture_v2["dialog_payload_version"],
        sqlite_adapter._DIALOG_PAYLOAD_VERSION,
    )


async def test_frozen_payload_v1_is_read_through_public_ports(
    tmp_path: Path,
) -> None:
    path = tmp_path / "golden-v1-read.sqlite3"
    fixture = _load_golden_payload_v1()
    expected_states, expected_dialog, _ = _golden_models()
    live_now = datetime.fromisoformat(fixture["reference_now"]["live"])
    touch_now = datetime.fromisoformat(fixture["reference_now"]["touch"])
    expired_now = datetime.fromisoformat(fixture["reference_now"]["expired"])

    async with _opened(path) as (persistence, _):
        _insert_golden_payload_v1(path, fixture)

        for session_id, expected in expected_states.items():
            _assert_payload_contract_equal(
                await persistence.sessions.get(session_id, now=live_now),
                expected,
            )
        _assert_payload_contract_equal(
            await persistence.dialogs.read("golden-full", now=live_now),
            expected_dialog,
        )
        _assert_payload_contract_equal(
            await persistence.touch("golden-full", now=touch_now),
            SessionSnapshot(
                state=expected_states["golden-full"],
                dialog=expected_dialog,
            ),
        )
        assert _fetchone(
            path,
            "SELECT payload_version FROM session_states WHERE session_id = ?",
            ("golden-full",),
        ) == (2,)

        for session_id in expected_states:
            assert await persistence.sessions.get(
                session_id,
                now=expired_now,
            ) == SessionAbsent(reason="expired")

        assert _fetchone(path, "SELECT COUNT(*) FROM session_states") == (3,)


@pytest.mark.parametrize(
    "migrator",
    [None, lambda _birth_date, _tz_id: (_ for _ in ()).throw(ValueError("bad tz"))],
)
async def test_unknown_time_v1_requires_successful_injected_migrator(
    tmp_path: Path,
    migrator: Callable[..., object] | None,
) -> None:
    path = tmp_path / "golden-v1-migration-failure.sqlite3"
    fixture = _load_golden_payload_v1()
    executor = ThreadPoolExecutor(max_workers=SQLITE_TEST_EXECUTOR_WORKERS)
    try:
        persistence = await SqliteSessionPersistence.open(
            path,
            executor=executor,
            unknown_time_migrator=migrator,
        )
        _insert_golden_payload_v1(path, fixture)

        with pytest.raises(StateReadError) as caught:
            await persistence.sessions.get(
                "golden-full",
                now=datetime.fromisoformat(fixture["reference_now"]["live"]),
            )
        assert caught.value.error_code == MIGRATION_FAILED
    finally:
        executor.shutdown(wait=True)


async def test_public_writes_match_frozen_payload_v2(
    tmp_path: Path,
) -> None:
    path = tmp_path / "golden-v2-write.sqlite3"
    fixture = _load_golden_payload_v2()
    expected_states, expected_dialog, deltas = _golden_models()

    async with _opened(path) as (persistence, _):
        for session_id in expected_states:
            assert isinstance(
                await persistence.sessions.create(session_id, now=MICRO_NOW),
                SessionCreated,
            )
        for session_id, delta in deltas.items():
            assert await persistence.sessions.compare_and_set(
                session_id,
                0,
                delta,
                now=MICRO_NOW,
            ) == 1
        for turn in expected_dialog:
            assert await persistence.dialogs.append(
                "golden-full",
                turn,
                now=MICRO_NOW,
            ) is None

        fixture_states = {
            row["metadata"]["session_id"]: row for row in fixture["states"]
        }
        for session_id, expected in expected_states.items():
            row = _fetchone(
                path,
                "SELECT state_version, created_at_us, expires_at_us, "
                "hard_expires_at_us, payload_version, state_json "
                "FROM session_states WHERE session_id = ?",
                (session_id,),
            )
            fixture_row = fixture_states[session_id]
            metadata = fixture_row["metadata"]
            _assert_payload_contract_equal(
                row[:5],
                (
                    metadata["state_version"],
                    metadata["created_at_us"],
                    metadata["expires_at_us"],
                    metadata["hard_expires_at_us"],
                    metadata["payload_version"],
                ),
            )
            _assert_payload_contract_equal(
                json.loads(row[5]),
                fixture_row["payload"],
            )
            assert await persistence.sessions.get(
                session_id,
                now=MICRO_NOW,
            ) == expected

        dialog_row = _fetchone(
            path,
            "SELECT expires_at_us, payload_version, turns_json "
            "FROM session_dialogs WHERE session_id = 'golden-full'",
        )
        dialog_metadata = fixture["dialog"]["metadata"]
        _assert_payload_contract_equal(
            dialog_row[:2],
            (
                dialog_metadata["expires_at_us"],
                dialog_metadata["payload_version"],
            ),
        )
        _assert_payload_contract_equal(
            json.loads(dialog_row[2]),
            fixture["dialog"]["payload"],
        )


async def test_full_models_round_trip_losslessly_across_independent_handles(
    tmp_path: Path,
) -> None:
    path = tmp_path / "round-trip.sqlite3"
    birth_input = BirthInput(
        birth_date=date(1987, 1, 2),
        birth_time=None,
        place_id="москва-🌍",
    )
    resolved = ResolvedBirthData(
        utc_datetime=datetime(1987, 1, 2, 9, 0, 0, tzinfo=UTC),
        latitude=55.75,
        longitude=37.62,
        tz_id="Europe/Moscow",
        utc_offset_seconds=10_800,
        canonical_place="Москва 🌍",
        time_unknown=True,
        birth_time_domain=build_birth_time_domain(
            date(1987, 1, 2),
            "Europe/Moscow",
        ),
        warnings=(
            ResolutionWarning(
                source="time",
                code="TIME_UNKNOWN",
                message="Полдень принят условно 🌒",
            ),
        ),
    )
    spec = NatalChartSpec(
        chart_kind="natal",
        include=("rulers", "positions", "houses", "aspects"),
        rulership=RulershipScheme.TRADITIONAL,
        near_interception_threshold=0.125,
    )
    delta = StateDelta(
        birth_input=birth_input,
        birth_resolved=resolved,
        base_chart_spec=spec,
    )
    complete = DialogTurn(
        turn_id="complete-🌕",
        created_at=MICRO_NOW + timedelta(microseconds=1),
        selection=Selection(topic="натал", focus="отношения ✨"),
        state_version_at_answer=1,
        status="complete",
        truncated=True,
        text="Unicode и astral: 🌌🪐",
    )
    partial = DialogTurn(
        turn_id="partial",
        created_at=MICRO_NOW + timedelta(microseconds=2),
        selection=Selection(topic="natal", focus="career"),
        state_version_at_answer=99,
        status="partial",
        truncated=False,
        text="partial answer",
    )

    async with _opened(path) as (primary, peer):
        created = await primary.sessions.create("round-trip-🌙", now=MICRO_NOW)
        assert isinstance(created, SessionCreated)
        assert await primary.sessions.compare_and_set(
            "round-trip-🌙",
            0,
            delta,
            now=MICRO_NOW,
        ) == 1
        await primary.dialogs.append("round-trip-🌙", complete, now=MICRO_NOW)
        await primary.dialogs.append("round-trip-🌙", partial, now=MICRO_NOW)

        expected_state = SessionState(
            session_id="round-trip-🌙",
            birth_input=birth_input,
            birth_resolved=resolved,
            state_version=1,
            base_chart=ChartRef(state_version=1, spec=spec),
            created_at=MICRO_NOW,
            expires_at=MICRO_NOW + SLIDING_TTL,
            hard_expires_at=MICRO_NOW + HARD_TTL,
        )
        loaded_state = await peer.sessions.get("round-trip-🌙", now=MICRO_NOW)
        loaded_dialog = await peer.dialogs.read("round-trip-🌙", now=MICRO_NOW)

        assert loaded_state == expected_state
        assert loaded_dialog == (complete, partial)
        assert type(loaded_dialog) is tuple
        metadata = _fetchone(
            path,
            "SELECT created_at_us, expires_at_us, hard_expires_at_us "
            "FROM session_states WHERE session_id = ?",
            ("round-trip-🌙",),
        )
        assert metadata == (
            _epoch_us(MICRO_NOW),
            _epoch_us(MICRO_NOW + SLIDING_TTL),
            _epoch_us(MICRO_NOW + HARD_TTL),
        )


async def test_historical_hard_deadline_is_not_rederived_from_current_ttl(
    tmp_path: Path,
) -> None:
    path = tmp_path / "historical-hard-expiry.sqlite3"
    async with _opened(path) as (persistence, _):
        created = await create_session(persistence, "historical", now=MICRO_NOW)
        historical = created.model_copy(
            update={"hard_expires_at": MICRO_NOW + timedelta(days=365, microseconds=7)}
        )
        _execute(
            path,
            "UPDATE session_states SET hard_expires_at_us = ?, state_json = ? "
            "WHERE session_id = ?",
            (
                _epoch_us(historical.hard_expires_at),
                historical.model_dump_json(),
                historical.session_id,
            ),
        )

        assert await persistence.sessions.get("historical", now=MICRO_NOW) == historical


@pytest.mark.parametrize(
    ("target", "mutation", "expected_code"),
    [
        (
            "state",
            "UPDATE session_states SET payload_version = 3 WHERE session_id = ?",
            PAYLOAD_UNSUPPORTED,
        ),
        (
            "state",
            "UPDATE session_states SET state_json = '{' WHERE session_id = ?",
            DATA_CORRUPT,
        ),
        (
            "dialog",
            "UPDATE session_dialogs SET payload_version = 2 WHERE session_id = ?",
            PAYLOAD_UNSUPPORTED,
        ),
        (
            "dialog",
            "UPDATE session_dialogs SET turns_json = '{' WHERE session_id = ?",
            DATA_CORRUPT,
        ),
    ],
)
async def test_payload_version_is_distinct_from_known_payload_corruption(
    tmp_path: Path,
    target: str,
    mutation: str,
    expected_code: str,
) -> None:
    path = tmp_path / f"payload-{target}-{expected_code}.sqlite3"
    async with _opened(path) as (persistence, _):
        await populate_with_dialog(persistence, "payload")
        _execute(path, mutation, ("payload",))

        with pytest.raises(StateReadError) as caught:
            if target == "state":
                await persistence.sessions.get("payload", now=NOW)
            else:
                await persistence.dialogs.read("payload", now=NOW)
        assert caught.value.error_code == expected_code


@pytest.mark.parametrize(
    ("payload_field", "replacement"),
    [
        ("session_id", "different"),
        ("state_version", 7),
        ("created_at", "2026-09-05T12:00:00.000001Z"),
        ("expires_at", "2026-09-12T12:00:00.000001Z"),
        ("hard_expires_at", "2026-10-05T12:00:00.000001Z"),
    ],
)
async def test_state_payload_must_match_relational_metadata(
    tmp_path: Path,
    payload_field: str,
    replacement: object,
) -> None:
    path = tmp_path / f"metadata-{payload_field}.sqlite3"
    async with _opened(path) as (persistence, _):
        await create_session(persistence, "metadata")
        raw_payload = _fetchone(
            path,
            "SELECT state_json FROM session_states WHERE session_id = 'metadata'",
        )[0]
        decoded = json.loads(raw_payload)
        decoded[payload_field] = replacement
        _execute(
            path,
            "UPDATE session_states SET state_json = ? WHERE session_id = 'metadata'",
            (_json(decoded),),
        )

        with pytest.raises(StateReadError) as caught:
            await persistence.sessions.get("metadata", now=NOW)
        assert caught.value.error_code == DATA_CORRUPT


@pytest.mark.parametrize(
    "turns",
    [
        [make_turn(index).model_dump(mode="json") for index in range(MAX_DIALOG_TURNS + 1)],
        [make_turn(text="x" * (MAX_DIALOG_TURN_CHARS + 1)).model_dump(mode="json")],
        [
            make_turn(index, text="x" * MAX_DIALOG_TURN_CHARS).model_dump(mode="json")
            for index in range((MAX_DIALOG_CHARS // MAX_DIALOG_TURN_CHARS) + 1)
        ],
    ],
    ids=["turn-count", "single-turn-chars", "total-chars"],
)
async def test_persisted_dialog_over_limits_is_corruption_not_silently_trimmed(
    tmp_path: Path,
    turns: list[dict[str, Any]],
) -> None:
    path = tmp_path / f"dialog-over-limit-{len(turns)}.sqlite3"
    async with _opened(path) as (persistence, _):
        await populate_with_dialog(persistence, "over-limit")
        _execute(
            path,
            "UPDATE session_dialogs SET turns_json = ? WHERE session_id = 'over-limit'",
            (_json(turns),),
        )

        with pytest.raises(StateReadError) as caught:
            await persistence.dialogs.read("over-limit", now=NOW)
        assert caught.value.error_code == DATA_CORRUPT


async def test_dialog_deadline_drift_is_ignored_by_read_and_repaired_by_touch(
    tmp_path: Path,
) -> None:
    path = tmp_path / "dialog-drift.sqlite3"
    async with _opened(path) as (persistence, _):
        state, turn = await populate_with_dialog(persistence, "drift")
        drifted_us = _epoch_us(NOW - timedelta(days=90))
        _execute(
            path,
            "UPDATE session_dialogs SET expires_at_us = ? WHERE session_id = 'drift'",
            (drifted_us,),
        )

        assert await persistence.dialogs.read("drift", now=NOW) == (turn,)
        assert _fetchone(
            path,
            "SELECT expires_at_us FROM session_dialogs WHERE session_id = 'drift'",
        ) == (drifted_us,)

        touched = await persistence.touch("drift", now=NOW + timedelta(days=1))

        assert isinstance(touched, SessionSnapshot)
        assert touched.dialog == (turn,)
        assert touched.state.expires_at > state.expires_at
        assert _fetchone(
            path,
            "SELECT s.expires_at_us, d.expires_at_us "
            "FROM session_states s JOIN session_dialogs d USING(session_id) "
            "WHERE s.session_id = 'drift'",
        ) == (_epoch_us(touched.state.expires_at), _epoch_us(touched.state.expires_at))


@pytest.mark.parametrize("renewer", ["compare_and_set", "append", "touch"])
async def test_successful_renewers_persist_equal_parent_and_dialog_deadlines(
    tmp_path: Path,
    renewer: str,
) -> None:
    path = tmp_path / f"equal-deadline-{renewer}.sqlite3"
    async with _opened(path) as (persistence, _):
        await populate_with_dialog(persistence, "equal-deadline")
        renew_at = NOW + timedelta(days=6)
        if renewer == "compare_and_set":
            assert await persistence.sessions.compare_and_set(
                "equal-deadline",
                1,
                DELTA,
                now=renew_at,
            ) == 2
        elif renewer == "append":
            assert await persistence.dialogs.append(
                "equal-deadline",
                make_turn(2),
                now=renew_at,
            ) is None
        else:
            assert isinstance(
                await persistence.touch("equal-deadline", now=renew_at),
                SessionSnapshot,
            )

        parent_us, dialog_us = _fetchone(
            path,
            "SELECT s.expires_at_us, d.expires_at_us "
            "FROM session_states s JOIN session_dialogs d USING(session_id) "
            "WHERE s.session_id = 'equal-deadline'",
        )
        assert parent_us == dialog_us


@pytest.mark.parametrize("remover", ["clear", "reset", "delete"])
async def test_dialog_row_is_physically_removed_by_aggregate_removers(
    tmp_path: Path,
    remover: str,
) -> None:
    path = tmp_path / f"physical-dialog-removal-{remover}.sqlite3"
    async with _opened(path) as (persistence, _):
        await populate_with_dialog(persistence, "remove-dialog")
        if remover == "clear":
            assert await persistence.dialogs.clear("remove-dialog", now=NOW) is None
        elif remover == "reset":
            assert await persistence.reset("remove-dialog", 1, now=NOW) == 2
        else:
            assert await persistence.delete("remove-dialog") is None

        assert _fetchone(
            path,
            "SELECT COUNT(*) FROM session_dialogs WHERE session_id = 'remove-dialog'",
        ) == (0,)


async def test_logical_expiry_does_not_physically_delete_or_free_session_id(
    tmp_path: Path,
) -> None:
    path = tmp_path / "logical-expiry.sqlite3"
    async with _opened(path) as (persistence, _):
        created = await create_session(persistence, "expired-physical")
        await persistence.dialogs.append("expired-physical", make_turn(), now=NOW)

        assert await persistence.sessions.get(
            "expired-physical",
            now=created.expires_at,
        ) == SessionAbsent(reason="expired")
        assert await persistence.dialogs.read(
            "expired-physical",
            now=created.expires_at,
        ) == SessionAbsent(reason="expired")
        assert _fetchone(
            path,
            "SELECT (SELECT COUNT(*) FROM session_states WHERE session_id = ?), "
            "(SELECT COUNT(*) FROM session_dialogs WHERE session_id = ?)",
            ("expired-physical", "expired-physical"),
        ) == (1, 1)
        assert await persistence.sessions.create(
            "expired-physical",
            now=created.expires_at,
        ) == SessionIdConflict(session_id="expired-physical")


async def test_reaper_deletes_only_expired_parents_at_exact_boundary(tmp_path: Path) -> None:
    path = tmp_path / "reaper-boundary.sqlite3"
    async with _opened(path) as (persistence, _):
        expired_at_boundary, _ = await populate_with_dialog(
            persistence,
            "boundary",
            now=NOW,
        )
        earlier, _ = await populate_with_dialog(
            persistence,
            "earlier",
            now=NOW - timedelta(days=1),
        )
        live, _ = await populate_with_dialog(
            persistence,
            "live",
            now=NOW + timedelta(microseconds=1),
        )
        assert earlier.expires_at < expired_at_boundary.expires_at < live.expires_at

        deleted = await persistence.reap_expired(now=expired_at_boundary.expires_at)

        assert deleted == 2
        assert await persistence.sessions.get(
            "boundary",
            now=expired_at_boundary.expires_at,
        ) == SessionAbsent(reason="not_found")
        assert await persistence.dialogs.read(
            "earlier",
            now=expired_at_boundary.expires_at,
        ) == SessionAbsent(reason="not_found")
        assert await persistence.sessions.get(
            "live",
            now=expired_at_boundary.expires_at,
        ) == live
        assert _fetchone(path, "SELECT COUNT(*) FROM session_dialogs") == (1,)


async def test_reaper_ignores_dialog_deadline_and_redundant_hard_predicate(
    tmp_path: Path,
) -> None:
    path = tmp_path / "reaper-parent-only.sqlite3"
    async with _opened(path) as (persistence, _):
        parent, turn = await populate_with_dialog(persistence, "parent-live")
        _execute(
            path,
            "UPDATE session_dialogs SET expires_at_us = ? WHERE session_id = 'parent-live'",
            (_epoch_us(NOW - timedelta(days=1)),),
        )

        assert await persistence.reap_expired(now=NOW) == 0
        assert await persistence.sessions.get("parent-live", now=NOW) == parent
        assert await persistence.dialogs.read("parent-live", now=NOW) == (turn,)

        # A schema-valid historical parent can have a hard deadline much later than its
        # sliding deadline. Deletion at the sliding boundary proves parent expires_at is
        # the sole predicate; hard expiry need not be mentioned separately.
        historical = parent.model_copy(
            update={"hard_expires_at": parent.hard_expires_at + timedelta(days=365)}
        )
        _execute(
            path,
            "UPDATE session_states SET hard_expires_at_us = ?, state_json = ? "
            "WHERE session_id = 'parent-live'",
            (_epoch_us(historical.hard_expires_at), historical.model_dump_json()),
        )
        assert await persistence.reap_expired(now=parent.expires_at) == 1
        assert _fetchone(path, "SELECT COUNT(*) FROM session_dialogs") == (0,)


async def _reaper_race(
    persistence: SqliteSessionPersistence,
    peer: SqliteSessionPersistence,
    operation: str,
    *,
    boundary: datetime,
) -> tuple[object, object]:
    barrier = asyncio.Barrier(2)

    async def reap() -> object:
        await barrier.wait()
        return await persistence.reap_expired(now=boundary)

    async def mutate() -> object:
        await barrier.wait()
        if operation == "cas":
            return await peer.sessions.compare_and_set(
                "reaper-race",
                1,
                DELTA,
                now=boundary - timedelta(microseconds=1),
            )
        return await peer.dialogs.append(
            "reaper-race",
            make_turn(2),
            now=boundary - timedelta(microseconds=1),
        )

    return tuple(
        await asyncio.wait_for(asyncio.gather(reap(), mutate()), timeout=5)
    )  # type: ignore[return-value]


@pytest.mark.parametrize("operation", ["cas", "append"])
async def test_reaper_race_has_only_serial_outcomes_and_no_orphan(
    tmp_path: Path,
    operation: str,
) -> None:
    path = tmp_path / f"reaper-race-{operation}.sqlite3"
    async with _opened(path) as (persistence, peer):
        state, original_turn = await populate_with_dialog(persistence, "reaper-race")
        boundary = state.expires_at

        reaped, mutated = await _reaper_race(
            persistence,
            peer,
            operation,
            boundary=boundary,
        )

        assert reaped in {0, 1}
        if reaped == 1:
            assert mutated == SessionAbsent(reason="not_found")
            assert _fetchone(
                path,
                "SELECT (SELECT COUNT(*) FROM session_states), "
                "(SELECT COUNT(*) FROM session_dialogs)",
            ) == (0, 0)
        else:
            assert mutated == (2 if operation == "cas" else None)
            loaded = await persistence.sessions.get(
                "reaper-race",
                now=boundary - timedelta(microseconds=1),
            )
            dialog = await persistence.dialogs.read(
                "reaper-race",
                now=boundary - timedelta(microseconds=1),
            )
            assert isinstance(loaded, SessionState)
            assert dialog == (
                (original_turn,) if operation == "cas" else (original_turn, make_turn(2))
            )


def _raw_aggregate(path: Path, session_id: str) -> tuple[tuple[Any, ...], tuple[Any, ...]]:
    return (
        _fetchone(
            path,
            "SELECT * FROM session_states WHERE session_id = ?",
            (session_id,),
        ),
        _fetchone(
            path,
            "SELECT * FROM session_dialogs WHERE session_id = ?",
            (session_id,),
        ),
    )


@pytest.mark.parametrize("operation", ["compare_and_set", "append", "clear", "touch"])
async def test_guarded_parent_rowcount_zero_is_invariant_failure_and_rolls_back(
    tmp_path: Path,
    operation: str,
) -> None:
    path = tmp_path / f"rowcount-invariant-{operation}.sqlite3"
    async with _opened(path) as (persistence, _):
        await populate_with_dialog(persistence, "rowcount")
        before = _raw_aggregate(path, "rowcount")
        _execute(
            path,
            "CREATE TRIGGER ignore_guarded_parent_update "
            "BEFORE UPDATE ON session_states "
            "BEGIN SELECT RAISE(IGNORE); END",
        )

        expected_error = StateReadError if operation == "touch" else StateWriteError
        with pytest.raises(expected_error) as caught:
            if operation == "compare_and_set":
                await persistence.sessions.compare_and_set(
                    "rowcount",
                    1,
                    DELTA,
                    now=NOW + timedelta(days=1),
                )
            elif operation == "append":
                await persistence.dialogs.append(
                    "rowcount",
                    make_turn(2),
                    now=NOW + timedelta(days=1),
                )
            elif operation == "clear":
                await persistence.dialogs.clear(
                    "rowcount",
                    now=NOW + timedelta(days=1),
                )
            else:
                await persistence.touch(
                    "rowcount",
                    now=NOW + timedelta(days=1),
                )

        assert caught.value.error_code == INVARIANT_VIOLATION
        assert _raw_aggregate(path, "rowcount") == before


async def test_dialog_write_failure_after_parent_update_rolls_back_both_rows(
    tmp_path: Path,
) -> None:
    path = tmp_path / "atomic-append-rollback.sqlite3"
    async with _opened(path) as (persistence, _):
        await populate_session(persistence, "atomic-append")
        parent_before = _fetchone(
            path,
            "SELECT * FROM session_states WHERE session_id = 'atomic-append'",
        )
        _execute(
            path,
            "CREATE TRIGGER reject_dialog_insert BEFORE INSERT ON session_dialogs "
            "BEGIN SELECT RAISE(ABORT, 'injected dialog failure'); END",
        )

        with pytest.raises(StateWriteError) as caught:
            await persistence.dialogs.append(
                "atomic-append",
                make_turn(),
                now=NOW + timedelta(days=1),
            )

        assert caught.value.error_code == WRITE_FAILED
        assert _fetchone(
            path,
            "SELECT * FROM session_states WHERE session_id = 'atomic-append'",
        ) == parent_before
        assert _fetchone(
            path,
            "SELECT COUNT(*) FROM session_dialogs WHERE session_id = 'atomic-append'",
        ) == (0,)


@pytest.mark.parametrize("operation", ["clear", "reset"])
async def test_dialog_delete_failure_rolls_back_parent_and_dialog(
    tmp_path: Path,
    operation: str,
) -> None:
    path = tmp_path / f"atomic-delete-rollback-{operation}.sqlite3"
    async with _opened(path) as (persistence, _):
        await populate_with_dialog(persistence, "atomic-delete")
        before = _raw_aggregate(path, "atomic-delete")
        _execute(
            path,
            "CREATE TRIGGER reject_dialog_delete BEFORE DELETE ON session_dialogs "
            "BEGIN SELECT RAISE(ABORT, 'injected dialog failure'); END",
        )

        with pytest.raises(StateWriteError) as caught:
            if operation == "clear":
                await persistence.dialogs.clear(
                    "atomic-delete",
                    now=NOW + timedelta(days=1),
                )
            else:
                await persistence.reset(
                    "atomic-delete",
                    1,
                    now=NOW + timedelta(days=1),
                )

        assert caught.value.error_code == WRITE_FAILED
        assert _raw_aggregate(path, "atomic-delete") == before


async def test_real_numeric_busy_is_distinct_from_other_write_failure(tmp_path: Path) -> None:
    path = tmp_path / "numeric-busy.sqlite3"
    executor = ThreadPoolExecutor(max_workers=SQLITE_TEST_EXECUTOR_WORKERS)
    raw_lock: sqlite3.Connection | None = None
    try:
        persistence = await SqliteSessionPersistence.open(
            path,
            executor=executor,
            busy_timeout_ms=0,
        )
        raw_lock = _connect(path)
        raw_lock.execute("BEGIN IMMEDIATE")

        with pytest.raises(StateWriteError) as caught:
            await persistence.sessions.create("blocked", now=NOW)

        assert caught.value.error_code == BUSY
        assert _fetchone(
            path,
            "SELECT COUNT(*) FROM session_states WHERE session_id = 'blocked'",
        ) == (0,)
    finally:
        if raw_lock is not None:
            raw_lock.rollback()
            raw_lock.close()
        executor.shutdown(wait=True)


async def test_text_only_locked_exception_is_not_misclassified_as_busy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "text-only-lock.sqlite3"
    async with _opened(path) as (persistence, _):
        def raise_text_only(*args: Any, **kwargs: Any) -> sqlite3.Connection:
            raise sqlite3.OperationalError("database is locked")

        monkeypatch.setattr(sqlite_adapter, "_CONNECTION_FACTORY", raise_text_only)
        with pytest.raises(StateReadError) as caught:
            await persistence.sessions.get("anything", now=NOW)
        assert caught.value.error_code == READ_FAILED


@pytest.mark.parametrize("operation", ["open", "get", "create"])
async def test_sqlite_programming_errors_are_not_mapped_to_public_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    path = tmp_path / f"programming-error-{operation}.sqlite3"
    executor = ThreadPoolExecutor(max_workers=SQLITE_TEST_EXECUTOR_WORKERS)
    try:
        persistence = None
        if operation != "open":
            persistence = await SqliteSessionPersistence.open(path, executor=executor)

        def raise_programming_error(*args: Any, **kwargs: Any) -> sqlite3.Connection:
            raise sqlite3.ProgrammingError("injected adapter programming error")

        monkeypatch.setattr(
            sqlite_adapter,
            "_CONNECTION_FACTORY",
            raise_programming_error,
        )

        with pytest.raises(sqlite3.ProgrammingError):
            if operation == "open":
                await SqliteSessionPersistence.open(path, executor=executor)
            elif operation == "get":
                assert persistence is not None
                await persistence.sessions.get("failure", now=NOW)
            else:
                assert persistence is not None
                await persistence.sessions.create("failure", now=NOW)
    finally:
        executor.shutdown(wait=True)


@pytest.mark.parametrize(
    ("operation", "expected_error", "expected_code"),
    [
        ("get", StateReadError, READ_FAILED),
        ("touch", StateReadError, READ_FAILED),
        ("create", StateWriteError, WRITE_FAILED),
        ("delete", StateWriteError, WRITE_FAILED),
        ("reaper", StateWriteError, WRITE_FAILED),
    ],
)
async def test_oserror_never_crosses_persistence_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
    expected_error: type[StateReadError] | type[StateWriteError],
    expected_code: str,
) -> None:
    path = tmp_path / f"oserror-{operation}.sqlite3"
    async with _opened(path) as (persistence, _):
        def raise_oserror(*args: Any, **kwargs: Any) -> sqlite3.Connection:
            raise OSError("injected filesystem failure")

        monkeypatch.setattr(sqlite_adapter, "_CONNECTION_FACTORY", raise_oserror)
        with pytest.raises(expected_error) as caught:
            if operation == "get":
                await persistence.sessions.get("failure", now=NOW)
            elif operation == "touch":
                await persistence.touch("failure", now=NOW)
            elif operation == "create":
                await persistence.sessions.create("failure", now=NOW)
            elif operation == "delete":
                await persistence.delete("failure")
            else:
                await persistence.reap_expired(now=NOW)
        assert caught.value.error_code == expected_code


async def test_non_primary_key_integrity_failure_is_not_create_conflict(
    tmp_path: Path,
) -> None:
    path = tmp_path / "create-integrity.sqlite3"
    async with _opened(path) as (persistence, _):
        assert isinstance(await persistence.sessions.create("existing", now=NOW), SessionCreated)
        assert await persistence.sessions.create(
            "existing",
            now=NOW,
        ) == SessionIdConflict(session_id="existing")
        _execute(
            path,
            "CREATE TRIGGER reject_new_state BEFORE INSERT ON session_states "
            "BEGIN SELECT RAISE(ABORT, 'not a primary-key collision'); END",
        )

        with pytest.raises(StateWriteError) as caught:
            await persistence.sessions.create("new-id", now=NOW)

        assert caught.value.error_code == WRITE_FAILED


class CommitThenRaiseConnection(sqlite3.Connection):
    enabled = False
    commits_that_raised = 0
    closed_count = 0

    def commit(self) -> None:
        super().commit()
        if type(self).enabled:
            type(self).commits_that_raised += 1
            raise sqlite3.OperationalError("commit acknowledgement lost")

    def close(self) -> None:
        type(self).closed_count += 1
        super().close()


def _commit_fault_factory(*args: Any, **kwargs: Any) -> sqlite3.Connection:
    return sqlite3.connect(*args, **kwargs, factory=CommitThenRaiseConnection)


async def test_commit_exception_is_unknown_without_retry_or_readback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "commit-unknown.sqlite3"
    CommitThenRaiseConnection.enabled = False
    CommitThenRaiseConnection.commits_that_raised = 0
    CommitThenRaiseConnection.closed_count = 0
    connection_opens = 0

    def factory(*args: Any, **kwargs: Any) -> sqlite3.Connection:
        nonlocal connection_opens
        connection_opens += 1
        return _commit_fault_factory(*args, **kwargs)

    monkeypatch.setattr(sqlite_adapter, "_CONNECTION_FACTORY", factory)
    executor = ThreadPoolExecutor(max_workers=SQLITE_TEST_EXECUTOR_WORKERS)
    try:
        persistence = await SqliteSessionPersistence.open(path, executor=executor)
        baseline_opens = connection_opens
        baseline_closes = CommitThenRaiseConnection.closed_count
        CommitThenRaiseConnection.enabled = True

        with pytest.raises(StateWriteError) as caught:
            await persistence.sessions.create("uncertain", now=NOW)

        assert caught.value.error_code == COMMIT_UNKNOWN
        assert connection_opens - baseline_opens == 1
        assert CommitThenRaiseConnection.commits_that_raised == 1
        assert CommitThenRaiseConnection.closed_count - baseline_closes == 1

        CommitThenRaiseConnection.enabled = False
        # The seam commits before losing acknowledgement. This positive control proves
        # the operation did not retry or classify by reading the post-commit state.
        assert isinstance(await persistence.sessions.get("uncertain", now=NOW), SessionState)
    finally:
        CommitThenRaiseConnection.enabled = False
        executor.shutdown(wait=True)


async def test_corrupt_database_is_mapped_to_typed_data_error(tmp_path: Path) -> None:
    path = tmp_path / "not-a-database.sqlite3"
    path.write_bytes(b"not a sqlite database")
    executor = ThreadPoolExecutor(max_workers=SQLITE_TEST_EXECUTOR_WORKERS)
    try:
        with pytest.raises(StateWriteError) as caught:
            await SqliteSessionPersistence.open(path, executor=executor)
        assert caught.value.error_code == DATA_CORRUPT
    finally:
        executor.shutdown(wait=True)


async def _invoke_now_method(
    persistence: SqliteSessionPersistence,
    method_name: str,
    *,
    now: datetime,
) -> object:
    if method_name == "create":
        return await persistence.sessions.create("invalid-now", now=now)
    if method_name == "get":
        return await persistence.sessions.get("invalid-now", now=now)
    if method_name == "compare_and_set":
        return await persistence.sessions.compare_and_set(
            "invalid-now",
            0,
            DELTA,
            now=now,
        )
    if method_name == "append":
        return await persistence.dialogs.append(
            "invalid-now",
            make_turn(),
            now=now,
        )
    if method_name == "read":
        return await persistence.dialogs.read("invalid-now", now=now)
    if method_name == "clear":
        return await persistence.dialogs.clear("invalid-now", now=now)
    if method_name == "touch":
        return await persistence.touch("invalid-now", now=now)
    if method_name == "reset":
        return await persistence.reset("invalid-now", 0, now=now)
    if method_name == "reaper":
        return await persistence.reap_expired(now=now)
    raise AssertionError(f"unknown method {method_name}")


async def test_invalid_now_has_zero_submit_and_connect_delta_then_valid_control_offloads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "offload-validation.sqlite3"
    connection_threads: list[int] = []
    original_factory = sqlite_adapter._CONNECTION_FACTORY

    def recording_factory(*args: Any, **kwargs: Any) -> sqlite3.Connection:
        connection_threads.append(threading.get_ident())
        return original_factory(*args, **kwargs)

    monkeypatch.setattr(sqlite_adapter, "_CONNECTION_FACTORY", recording_factory)
    executor = RecordingExecutor(max_workers=SQLITE_TEST_EXECUTOR_WORKERS)
    try:
        persistence = await SqliteSessionPersistence.open(path, executor=executor)
        baseline_submits = executor.submit_count
        baseline_connections = len(connection_threads)
        invalid_now = datetime(2026, 9, 5, 12)

        for method_name in (
            "create",
            "get",
            "compare_and_set",
            "append",
            "read",
            "clear",
            "touch",
            "reset",
            "reaper",
        ):
            with pytest.raises(ValueError):
                await _invoke_now_method(persistence, method_name, now=invalid_now)

        assert executor.submit_count == baseline_submits
        assert len(connection_threads) == baseline_connections

        result = await persistence.sessions.get("valid-control", now=NOW)
        assert result == SessionAbsent(reason="not_found")
        assert executor.submit_count - baseline_submits == 1
        assert len(connection_threads) - baseline_connections == 1
        assert connection_threads[-1] != threading.get_ident()
        assert executor.worker_thread_ids[-1] == connection_threads[-1]
    finally:
        executor.shutdown(wait=True)


async def test_two_operations_enter_distinct_executor_workers_before_sqlite(
    tmp_path: Path,
) -> None:
    path = tmp_path / "worker-barrier.sqlite3"
    executor = RecordingExecutor(max_workers=SQLITE_TEST_EXECUTOR_WORKERS)
    try:
        persistence = await SqliteSessionPersistence.open(path, executor=executor)
        await create_session(persistence, "parallel-left")
        await create_session(persistence, "parallel-right")
        probe = threading.Barrier(2)
        executor.probe = probe
        try:
            left, right = await asyncio.wait_for(
                asyncio.gather(
                    persistence.sessions.get("parallel-left", now=NOW),
                    persistence.sessions.get("parallel-right", now=NOW),
                ),
                timeout=5,
            )
        finally:
            executor.probe = None

        assert isinstance(left, SessionState)
        assert isinstance(right, SessionState)
        assert len(set(executor.worker_thread_ids[-2:])) == 2
    finally:
        executor.shutdown(wait=True)


async def test_aggregate_does_not_take_ownership_of_injected_executor(tmp_path: Path) -> None:
    executor = ThreadPoolExecutor(max_workers=SQLITE_TEST_EXECUTOR_WORKERS)
    try:
        persistence = await SqliteSessionPersistence.open(
            tmp_path / "executor-ownership.sqlite3",
            executor=executor,
        )
        assert isinstance(await persistence.sessions.create("owned-elsewhere", now=NOW), SessionCreated)
        future = executor.submit(lambda: threading.get_ident())
        assert future.result(timeout=5) != threading.get_ident()
    finally:
        executor.shutdown(wait=True)


async def test_dialog_read_keeps_one_deferred_snapshot_across_parent_and_dialog(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "read-snapshot.sqlite3"
    async with _opened(path) as (persistence, peer):
        _, original_turn = await populate_with_dialog(persistence, "snapshot")
        loop = asyncio.get_running_loop()
        parent_read = asyncio.Event()
        release_worker = threading.Event()

        def pause_after_parent() -> None:
            loop.call_soon_threadsafe(parent_read.set)
            assert release_worker.wait(timeout=5)

        monkeypatch.setattr(
            sqlite_adapter,
            "_DIALOG_READ_AFTER_PARENT_HOOK",
            pause_after_parent,
        )
        read_task = asyncio.create_task(persistence.dialogs.read("snapshot", now=NOW))
        try:
            await asyncio.wait_for(parent_read.wait(), timeout=5)
            appended_turn = make_turn(2)
            assert await peer.dialogs.append("snapshot", appended_turn, now=NOW) is None
        finally:
            release_worker.set()

        original_snapshot = await asyncio.wait_for(read_task, timeout=5)
        monkeypatch.setattr(sqlite_adapter, "_DIALOG_READ_AFTER_PARENT_HOOK", None)

        assert original_snapshot == (original_turn,)
        assert await persistence.dialogs.read("snapshot", now=NOW) == (
            original_turn,
            appended_turn,
        )


async def test_read_failure_connection_is_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "failure-close.sqlite3"
    executor = ThreadPoolExecutor(max_workers=SQLITE_TEST_EXECUTOR_WORKERS)
    try:
        initial = await SqliteSessionPersistence.open(path, executor=executor)
        await create_session(initial, "corrupt-close")
        _execute(
            path,
            "UPDATE session_states SET state_json = '{' WHERE session_id = 'corrupt-close'",
        )

        ObservedConnection.instances = []
        monkeypatch.setattr(sqlite_adapter, "_CONNECTION_FACTORY", _observed_factory)
        with pytest.raises(StateReadError) as caught:
            await initial.sessions.get("corrupt-close", now=NOW)
        assert caught.value.error_code == DATA_CORRUPT
        assert len(ObservedConnection.instances) == 1
        assert ObservedConnection.instances[0].was_closed
    finally:
        executor.shutdown(wait=True)


RESTART_WRITER = r"""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, time
import json
import os
from pathlib import Path
import sys

from exact_orb.birth.types import BirthInput, ResolvedBirthData
from exact_orb.calculation.spec import NatalChartSpec
from exact_orb.session.adapters.sqlite import SqliteSessionPersistence
from exact_orb.session.dialog import DialogTurn, Selection
from exact_orb.session.state import StateDelta

NOW = datetime(2026, 9, 5, 12, 0, 0, 123456, tzinfo=UTC)

async def main():
    executor = ThreadPoolExecutor(max_workers=4)
    try:
        persistence = await SqliteSessionPersistence.open(Path(sys.argv[1]), executor=executor)
        await persistence.sessions.create("restart", now=NOW)
        delta = StateDelta(
            birth_input=BirthInput(
                birth_date=date(1990, 9, 2), birth_time=time(12, 30), place_id="moscow"
            ),
            birth_resolved=ResolvedBirthData(
                utc_datetime=datetime(1990, 9, 2, 8, 30, tzinfo=UTC),
                latitude=55.75,
                longitude=37.62,
                tz_id="Europe/Moscow",
                utc_offset_seconds=14400,
                canonical_place="Moscow",
                time_unknown=False,
                birth_time_domain=None,
            ),
            base_chart_spec=NatalChartSpec(chart_kind="natal"),
        )
        await persistence.sessions.compare_and_set("restart", 0, delta, now=NOW)
        turn = DialogTurn(
            turn_id="restart-turn",
            created_at=datetime(2026, 9, 5, 12, 0, 0, 654321, tzinfo=UTC),
            selection=Selection(topic="natal", focus="restart 🌙"),
            state_version_at_answer=1,
            status="complete",
            text="persisted across process",
        )
        await persistence.dialogs.append("restart", turn, now=NOW)
        state = await persistence.sessions.get("restart", now=NOW)
        dialog = await persistence.dialogs.read("restart", now=NOW)
        print(json.dumps({
            "pid": os.getpid(),
            "state": state.model_dump(mode="json"),
            "dialog": [item.model_dump(mode="json") for item in dialog],
        }))
    finally:
        executor.shutdown(wait=True)

asyncio.run(main())
"""


RESTART_READER = r"""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import sys

from exact_orb.session.adapters.sqlite import SqliteSessionPersistence

NOW = datetime(2026, 9, 5, 12, 0, 0, 123456, tzinfo=UTC)

async def main():
    executor = ThreadPoolExecutor(max_workers=4)
    try:
        persistence = await SqliteSessionPersistence.open(Path(sys.argv[1]), executor=executor)
        state = await persistence.sessions.get("restart", now=NOW)
        dialog = await persistence.dialogs.read("restart", now=NOW)
        print(json.dumps({
            "pid": os.getpid(),
            "state": state.model_dump(mode="json"),
            "dialog": [item.model_dump(mode="json") for item in dialog],
        }))
    finally:
        executor.shutdown(wait=True)

asyncio.run(main())
"""


def _run_restart_process(script: str, path: Path, cwd: Path) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(SRC_ROOT)
    return subprocess.run(
        [sys.executable, "-c", script, str(path)],
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_independent_process_restart_reads_full_models(tmp_path: Path) -> None:
    path = tmp_path / "restart.sqlite3"

    writer = _run_restart_process(RESTART_WRITER, path, tmp_path)
    assert writer.returncode == 0, writer.stderr
    writer_payload = json.loads(writer.stdout)
    assert writer_payload["pid"] != os.getpid()
    assert writer_payload["state"]["session_id"] == "restart"
    assert writer_payload["dialog"][0]["turn_id"] == "restart-turn"

    reader = _run_restart_process(RESTART_READER, path, tmp_path)
    assert reader.returncode == 0, reader.stderr
    reader_payload = json.loads(reader.stdout)
    assert reader_payload["pid"] not in {os.getpid(), writer_payload["pid"]}
    assert reader_payload["state"] == writer_payload["state"]
    assert reader_payload["dialog"] == writer_payload["dialog"]


async def test_backend_directory_can_be_renamed_after_executor_teardown(tmp_path: Path) -> None:
    backend_directory = tmp_path / "backend-directory"
    backend_directory.mkdir()
    database_path = backend_directory / "session.sqlite3"
    executor = ThreadPoolExecutor(max_workers=SQLITE_TEST_EXECUTOR_WORKERS)
    try:
        persistence = await SqliteSessionPersistence.open(database_path, executor=executor)
        await populate_with_dialog(persistence, "rename")
        assert isinstance(await persistence.sessions.get("rename", now=NOW), SessionState)
    finally:
        executor.shutdown(wait=True)

    moved = tmp_path / "backend-directory-moved"
    backend_directory.rename(moved)
    assert (moved / "session.sqlite3").exists()
    moved.rename(backend_directory)
    assert database_path.exists()
