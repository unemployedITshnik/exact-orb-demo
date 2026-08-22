"""Logging subsystem tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import logging
from pathlib import Path
import re
import uuid

import pytest

from exact_orb import cli
from exact_orb.logging_setup import (
    LOG_FILE_NAME_FORMAT,
    LOG_LINE_DATE_FORMAT,
    SessionFilter,
    UTCFormatter,
    UTCSizeRotatingFileHandler,
)


LOG_NAME_RE = re.compile(r"^\d{8}T\d{6}Z\.log$")


class IncrementingClock:
    def __init__(self, start: datetime) -> None:
        self.current = start

    def __call__(self) -> datetime:
        value = self.current
        self.current += timedelta(seconds=1)
        return value


def test_utc_size_rotating_handler_uses_utc_file_names_without_suffixes() -> None:
    log_dir = _workspace_log_dir("rotation")
    handler = UTCSizeRotatingFileHandler(
        log_dir,
        stream_name="debug",
        max_bytes=260,
        retention_bytes=10_000,
        header_context=_header_context,
        utc_now=IncrementingClock(datetime(2026, 8, 17, 11, 32, 45, tzinfo=UTC)),
    )
    handler.setFormatter(
        UTCFormatter(
            "%(asctime)s %(levelname)s session=%(session)s logger=%(name)s %(message)s",
            datefmt=LOG_LINE_DATE_FORMAT,
        )
    )
    handler.addFilter(SessionFilter())
    handler.start()

    logger = logging.getLogger("exact_orb.tests.logging_rotation")
    old_handlers = list(logger.handlers)
    logger.handlers[:] = [handler]
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    try:
        for index in range(6):
            logger.debug("rotation_probe index=%d payload=%s", index, "x" * 80)
    finally:
        handler.close()
        logger.handlers[:] = old_handlers

    names = sorted(path.name for path in log_dir.glob("*.log"))
    assert len(names) >= 2
    assert names == sorted(names)
    assert all(LOG_NAME_RE.match(name) for name in names)
    assert all(":" not in name and ".log." not in name for name in names)
    assert names[0] == datetime(2026, 8, 17, 11, 32, 45, tzinfo=UTC).strftime(LOG_FILE_NAME_FORMAT)


def test_cli_writes_general_and_debug_logs(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    log_dir = _workspace_log_dir("cli-success")
    monkeypatch.setenv("EXACT_ORB_LOG_DIR", str(log_dir))
    monkeypatch.setenv("EXACT_ORB_LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("EXACT_ORB_LOG_MAX_BYTES", "4096")

    assert cli.main(["2.09.1985", "00.45", "gmt+4", "--no-warnings"]) == 0
    captured = capsys.readouterr()

    assert "НАТАЛЬНАЯ КАРТА" in captured.out
    general = _read_logs(log_dir / "general")
    debug = _read_logs(log_dir / "debug")
    first_general_line = sorted((log_dir / "general").glob("*.log"))[0].read_text(encoding="utf-8").splitlines()[0]

    assert "session_start" in first_general_line
    assert "ephemeris_mode=" in first_general_line
    assert "house_system_default=P" in first_general_line
    assert "cli_call status=ok" in general
    assert "input='2.09.1985 00.45 gmt+4'" in general
    assert "duration_ms=" in general
    assert "output_summary=bodies=" in general
    assert "cli_args argv=" in debug
    assert "cli_response format=human text=НАТАЛЬНАЯ КАРТА" in debug


def test_cli_logs_traceback_for_bad_input(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    log_dir = _workspace_log_dir("cli-error")
    monkeypatch.setenv("EXACT_ORB_LOG_DIR", str(log_dir))
    monkeypatch.setenv("EXACT_ORB_LOG_LEVEL", "DEBUG")

    with pytest.raises(SystemExit) as exc_info:
        cli.main(["мусор"])
    captured = capsys.readouterr()

    assert exc_info.value.code == 2
    assert "exact-orb: expected input format" in captured.err
    logs = _read_logs(log_dir / "general") + "\n" + _read_logs(log_dir / "debug")
    assert "cli_call status=error input='мусор'" in logs
    assert "Traceback" in logs
    assert "ValueError: expected input format" in logs


def test_cli_degrades_to_stderr_when_file_log_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    blocked = _workspace_log_dir("fallback") / "not_a_directory"
    blocked.write_text("not a directory", encoding="utf-8")
    monkeypatch.setenv("EXACT_ORB_LOG_DIR", str(blocked))
    monkeypatch.setenv("EXACT_ORB_LOG_LEVEL", "DEBUG")

    assert cli.main(["2.09.1985", "00.45", "gmt+4", "--no-warnings"]) == 0
    captured = capsys.readouterr()

    assert "НАТАЛЬНАЯ КАРТА" in captured.out
    assert captured.err.count("file logging unavailable") == 1


def _header_context(stream_name: str) -> str:
    return (
        "stream=%s python=test exact_orb=test ephemeris_mode=files "
        "ephemeris_source=argument ephemeris_path=test house_system_default=P"
    ) % stream_name


def _read_logs(directory: Path) -> str:
    return "\n".join(path.read_text(encoding="utf-8") for path in sorted(directory.glob("*.log")))


def _workspace_log_dir(label: str) -> Path:
    path = Path.cwd() / "logs" / "test-runs" / ("%s-%s" % (label, uuid.uuid4().hex[:8]))
    path.mkdir(parents=True, exist_ok=True)
    return path
