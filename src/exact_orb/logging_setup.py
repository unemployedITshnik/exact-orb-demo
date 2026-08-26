"""File logging setup for exact-orb CLI and deterministic calculations."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
import importlib.metadata
import logging
from logging.handlers import BaseRotatingHandler
import os
from pathlib import Path
import platform
import sys
import time
from typing import Literal
import uuid

from exact_orb.config import EphemerisStatus, read_exact_orb_pyproject_value


LOGGER = logging.getLogger(__name__)
LOG_LEVEL_ENV_VAR = "EXACT_ORB_LOG_LEVEL"
LOG_DIR_ENV_VAR = "EXACT_ORB_LOG_DIR"
LOG_MAX_BYTES_ENV_VAR = "EXACT_ORB_LOG_MAX_BYTES"
DEFAULT_LOG_LEVEL = "DEBUG"
DEFAULT_LOG_DIR_NAME = "logs"
# 10 MiB keeps individual debug files comfortable to open in PyCharm on
# Windows, while still avoiding frequent rotation for normal CLI use.
DEFAULT_LOG_MAX_BYTES = 10 * 1024 * 1024
DEFAULT_LOG_RETENTION_BYTES = 200 * 1024 * 1024
LOG_FILE_NAME_FORMAT = "%Y%m%dT%H%M%SZ.log"
LOG_LINE_DATE_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
LOGGER_NAME = "exact_orb"

_SESSION_ID = uuid.uuid4().hex[:8]
_STATE: "LoggingState | None" = None


@dataclass(frozen=True)
class LoggingSettings:
    """Resolved logging settings."""

    level_name: str
    level: int
    log_dir: Path
    max_bytes: int
    retention_bytes: int = DEFAULT_LOG_RETENTION_BYTES


@dataclass(frozen=True)
class LoggingState:
    """Runtime logging setup state."""

    settings: LoggingSettings
    file_logging: bool
    ephemeris_status: EphemerisStatus
    house_system_default: str


class SessionFilter(logging.Filter):
    """Attach the process session id to every handled record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.session = get_session_id()
        return True


class UTCFormatter(logging.Formatter):
    """Formatter that renders record timestamps in UTC."""

    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:
        created = datetime.fromtimestamp(record.created, tz=UTC)
        return created.strftime(datefmt or LOG_LINE_DATE_FORMAT)


class CompactStderrFormatter(logging.Formatter):
    """Compact stderr formatter that suppresses traceback expansion."""

    def format(self, record: logging.LogRecord) -> str:
        exc_info = record.exc_info
        exc_text = record.exc_text
        record.exc_info = None
        record.exc_text = None
        try:
            return super().format(record)
        finally:
            record.exc_info = exc_info
            record.exc_text = exc_text


class UTCSizeRotatingFileHandler(BaseRotatingHandler):
    """Rotate by size into fresh UTC timestamped files."""

    def __init__(
        self,
        directory: Path,
        *,
        stream_name: Literal["general", "debug"],
        max_bytes: int,
        retention_bytes: int,
        header_context: Callable[[str], str],
        utc_now: Callable[[], datetime] | None = None,
    ) -> None:
        self.directory = directory
        self.stream_name = stream_name
        self.max_bytes = max_bytes
        self.retention_bytes = retention_bytes
        self._header_context = header_context
        self._utc_now = utc_now or (lambda: datetime.now(UTC))
        self._last_path: Path | None = None
        self.directory.mkdir(parents=True, exist_ok=True)
        filename = self._next_log_path()
        super().__init__(str(filename), mode="a", encoding="utf-8", delay=True)

    def start(self) -> None:
        """Open the initial stream and write the session header."""

        self._ensure_stream(force_header=True)
        self._enforce_retention()

    def shouldRollover(self, record: logging.LogRecord) -> bool:
        if self.stream is None:
            self._ensure_stream()
        if self.max_bytes <= 0:
            return False

        if self.stream is not None:
            self.stream.flush()
        try:
            current_size = Path(self.baseFilename).stat().st_size
        except OSError:
            current_size = 0
        message = "%s%s" % (self.format(record), self.terminator)
        projected_size = current_size + len(message.encode(self.encoding or "utf-8", errors=self.errors or "strict"))
        return current_size > 0 and projected_size >= self.max_bytes

    def doRollover(self) -> None:
        if self.stream:
            self.stream.close()
            self.stream = None
        self.baseFilename = str(self._next_log_path())
        self._ensure_stream(force_header=True)
        self._enforce_retention()

    def _ensure_stream(self, *, force_header: bool = False) -> None:
        if self.stream is None:
            self.stream = self._open()
        file_path = Path(self.baseFilename)
        try:
            is_empty = file_path.stat().st_size == 0
        except OSError:
            is_empty = True
        if force_header or is_empty:
            self._write_session_header()

    def _write_session_header(self) -> None:
        if self.stream is None:
            return
        record = logging.LogRecord(
            name=__name__,
            level=logging.INFO,
            pathname=__file__,
            lineno=0,
            msg="session_start %s",
            args=(self._header_context(self.stream_name),),
            exc_info=None,
            func="init_logging",
            sinfo=None,
        )
        record.session = get_session_id()
        self.stream.write("%s%s" % (self.format(record), self.terminator))
        self.flush()

    def _next_log_path(self) -> Path:
        while True:
            now = self._utc_now()
            if now.tzinfo is None:
                now = now.replace(tzinfo=UTC)
            now = now.astimezone(UTC)
            path = self.directory / now.strftime(LOG_FILE_NAME_FORMAT)
            if path != self._last_path and not path.exists():
                self._last_path = path
                return path
            self._sleep_until_next_second(now)

    @staticmethod
    def _sleep_until_next_second(now: datetime) -> None:
        remaining = 1_000_000 - now.microsecond
        time.sleep(max(remaining / 1_000_000, 0.001))

    def _enforce_retention(self) -> None:
        if self.retention_bytes <= 0:
            return
        files = sorted(
            (path for path in self.directory.glob("*.log") if path.is_file()),
            key=lambda path: (path.name, path.stat().st_mtime),
        )
        sizes: dict[Path, int] = {}
        total = 0
        for path in files:
            try:
                size = path.stat().st_size
            except OSError:
                continue
            sizes[path] = size
            total += size

        current = Path(self.baseFilename)
        for path in files:
            if total <= self.retention_bytes:
                break
            if path == current:
                continue
            try:
                path.unlink()
            except OSError:
                continue
            total -= sizes.get(path, 0)


def init_logging(
    *,
    ephemeris_status: EphemerisStatus,
    log_level: str | int | None = None,
    log_dir: str | os.PathLike[str] | None = None,
    log_max_bytes: int | str | None = None,
    house_system_default: str = "P",
    force: bool = False,
) -> LoggingState:
    """Initialize package logging once, with file and stderr handlers."""

    global _STATE

    settings, warnings = resolve_logging_settings(
        log_level=log_level,
        log_dir=log_dir,
        log_max_bytes=log_max_bytes,
    )
    requested_state = LoggingState(
        settings=settings,
        file_logging=True,
        ephemeris_status=ephemeris_status,
        house_system_default=house_system_default,
    )
    if _STATE is not None and not force and _same_runtime_state(_STATE, requested_state):
        return _STATE

    package_logger = logging.getLogger(LOGGER_NAME)
    _remove_managed_handlers(package_logger)
    package_logger.setLevel(settings.level)
    package_logger.propagate = False

    stderr_handler = _stderr_handler()
    handlers: list[logging.Handler] = [stderr_handler]
    file_logging = True
    file_error: OSError | None = None
    try:
        header_context = _header_context_factory(ephemeris_status, house_system_default)
        file_handlers = _file_handlers(settings, header_context)
        handlers.extend(file_handlers)
    except OSError as exc:
        file_logging = False
        file_error = exc
        for handler in handlers:
            if isinstance(handler, UTCSizeRotatingFileHandler):
                handler.close()

    for handler in handlers:
        package_logger.addHandler(handler)

    _STATE = LoggingState(
        settings=settings,
        file_logging=file_logging,
        ephemeris_status=ephemeris_status,
        house_system_default=house_system_default,
    )
    for warning in warnings:
        LOGGER.warning("%s", warning)
    if file_error is not None:
        LOGGER.warning("file logging unavailable: %s", file_error)
    LOGGER.debug(
        "logging initialized file_logging=%s log_dir=%s max_bytes=%s level=%s session=%s",
        file_logging,
        settings.log_dir,
        settings.max_bytes,
        settings.level_name,
        get_session_id(),
    )
    return _STATE


def get_session_id() -> str:
    """Return the short process session id used in log records."""

    return _SESSION_ID


def resolve_logging_settings(
    *,
    log_level: str | int | None = None,
    log_dir: str | os.PathLike[str] | None = None,
    log_max_bytes: int | str | None = None,
) -> tuple[LoggingSettings, tuple[str, ...]]:
    """Resolve logging settings by argument, environment, pyproject, default."""

    warnings: list[str] = []
    level_value = _first_configured(
        log_level,
        os.environ.get(LOG_LEVEL_ENV_VAR),
        read_exact_orb_pyproject_value("log_level"),
        DEFAULT_LOG_LEVEL,
    )
    level_name, level = _resolve_level(level_value, warnings)

    dir_value = _first_configured(
        log_dir,
        os.environ.get(LOG_DIR_ENV_VAR),
        read_exact_orb_pyproject_value("log_dir"),
        None,
    )
    log_directory = _resolve_log_dir(dir_value)

    max_bytes_value = _first_configured(
        log_max_bytes,
        os.environ.get(LOG_MAX_BYTES_ENV_VAR),
        read_exact_orb_pyproject_value("log_max_bytes"),
        DEFAULT_LOG_MAX_BYTES,
    )
    max_bytes = _resolve_positive_int(max_bytes_value, DEFAULT_LOG_MAX_BYTES, "log_max_bytes", warnings)

    return (
        LoggingSettings(
            level_name=level_name,
            level=level,
            log_dir=log_directory,
            max_bytes=max_bytes,
        ),
        tuple(warnings),
    )


def _file_handlers(
    settings: LoggingSettings,
    header_context: Callable[[str], str],
) -> list[UTCSizeRotatingFileHandler]:
    formatter = UTCFormatter(
        "%(asctime)s %(levelname)s session=%(session)s logger=%(name)s %(message)s",
        datefmt=LOG_LINE_DATE_FORMAT,
    )
    session_filter = SessionFilter()
    created: list[UTCSizeRotatingFileHandler] = []
    try:
        general = UTCSizeRotatingFileHandler(
            settings.log_dir / "general",
            stream_name="general",
            max_bytes=settings.max_bytes,
            retention_bytes=settings.retention_bytes,
            header_context=header_context,
        )
        debug = UTCSizeRotatingFileHandler(
            settings.log_dir / "debug",
            stream_name="debug",
            max_bytes=settings.max_bytes,
            retention_bytes=settings.retention_bytes,
            header_context=header_context,
        )
        general.setLevel(logging.INFO)
        debug.setLevel(logging.DEBUG)
        for handler in (general, debug):
            handler.setFormatter(formatter)
            handler.addFilter(session_filter)
            handler._exact_orb_managed = True
            handler.start()
            created.append(handler)
    except OSError:
        for handler in created:
            handler.close()
        raise
    return created


def _stderr_handler() -> logging.StreamHandler:
    handler = logging.StreamHandler(sys.stderr)
    handler.setLevel(logging.WARNING)
    handler.setFormatter(CompactStderrFormatter("%(levelname)s %(name)s: %(message)s"))
    handler._exact_orb_managed = True
    return handler


def _remove_managed_handlers(logger: logging.Logger) -> None:
    for handler in list(logger.handlers):
        if not getattr(handler, "_exact_orb_managed", False):
            continue
        logger.removeHandler(handler)
        handler.close()


def _same_runtime_state(left: LoggingState, right: LoggingState) -> bool:
    return (
        left.settings == right.settings
        and left.ephemeris_status == right.ephemeris_status
        and left.house_system_default == right.house_system_default
    )


def _header_context_factory(
    ephemeris_status: EphemerisStatus,
    house_system_default: str,
) -> Callable[[str], str]:
    version = _package_version()
    python = _python_version()

    def header_context(stream_name: str) -> str:
        return (
            "stream=%s python=%s exact_orb=%s ephemeris_mode=%s "
            "ephemeris_source=%s ephemeris_path=%s house_system_default=%s"
        ) % (
            stream_name,
            python,
            version,
            ephemeris_status.mode,
            ephemeris_status.source,
            ephemeris_status.path,
            house_system_default,
        )

    return header_context


def _package_version() -> str:
    try:
        return importlib.metadata.version("exact-orb")
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


def _python_version() -> str:
    return "%s %s" % (platform.python_implementation(), platform.python_version())


def _resolve_level(value: object, warnings: list[str]) -> tuple[str, int]:
    if isinstance(value, int):
        return logging.getLevelName(value), value
    level_name = str(value).upper()
    level = logging.getLevelName(level_name)
    if isinstance(level, int):
        return level_name, level
    warnings.append("invalid log_level %r; using %s" % (value, DEFAULT_LOG_LEVEL))
    return DEFAULT_LOG_LEVEL, logging.getLevelName(DEFAULT_LOG_LEVEL)


def _resolve_log_dir(value: object) -> Path:
    if value is not None:
        path = Path(os.fspath(value))
        if path.is_absolute():
            return path.resolve()
        return (Path.cwd() / path).resolve()
    return (_project_root() / DEFAULT_LOG_DIR_NAME).resolve()


def _resolve_positive_int(
    value: object,
    default: int,
    setting_name: str,
    warnings: list[str],
) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        warnings.append("invalid %s %r; using %s" % (setting_name, value, default))
        return default
    if parsed <= 0:
        warnings.append("invalid %s %r; using %s" % (setting_name, value, default))
        return default
    return parsed


def _first_configured(*values: object) -> object:
    for value in values:
        if value not in (None, ""):
            return value
    return None


def _project_root() -> Path:
    for directory in (Path.cwd(), *Path.cwd().parents):
        if (directory / "pyproject.toml").is_file():
            return directory
    return Path.cwd()
