"""Logging subsystem tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
import logging
from pathlib import Path
import re
import uuid

import pytest

from exact_orb import cli
from exact_orb import component_logging
from exact_orb.component_logging import log_component_message
from exact_orb.logging_setup import (
    LOG_FILE_NAME_FORMAT,
    LOG_LINE_FORMAT,
    LOG_LINE_DATE_FORMAT,
    SessionFilter,
    UTCFormatter,
    UTCSizeRotatingFileHandler,
    get_session_id,
)


LOG_NAME_RE = re.compile(r"^\d{8}T\d{6}Z\.log$")


class IncrementingClock:
    def __init__(self, start: datetime) -> None:
        self.current = start

    def __call__(self) -> datetime:
        value = self.current
        self.current += timedelta(seconds=1)
        return value


def test_session_filter_attaches_session_and_component_context() -> None:
    record = logging.LogRecord(
        name="exact_orb.application.handlers.build_natal",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="probe",
        args=(),
        exc_info=None,
    )

    assert SessionFilter().filter(record) is True
    assert record.session == get_session_id()
    assert record.component == "application.handlers.build_natal"


def test_component_message_is_complete_single_line_json(
    caplog: pytest.LogCaptureFixture,
) -> None:
    logger = logging.getLogger("exact_orb.tests.component_boundary")
    caplog.set_level(logging.DEBUG, logger=logger.name)

    log_component_message(
        logger,
        direction="in",
        operation="probe",
        run_id="run-1",
        message={
            "text": "первая строка\nвторая строка",
            "when": datetime(2026, 9, 9, 13, 10, 18, tzinfo=UTC),
        },
        message_type="ProbeRequest",
    )

    assert len(caplog.records) == 1
    logged = caplog.records[0].getMessage()
    assert (
        "direction=in operation=probe run_id=run-1 calculation_key=- "
        "status=ok payload_mode=full"
    ) in logged
    assert "message_type=ProbeRequest" in logged
    assert "\n" not in logged
    payload = json.loads(logged.partition(" message=")[2])
    assert payload == {
        "text": "первая строка\nвторая строка",
        "when": "2026-09-09T13:10:18+00:00",
    }


async def test_async_boundary_logs_full_result(
    caplog: pytest.LogCaptureFixture,
) -> None:
    logger = logging.getLogger("exact_orb.tests.component_boundary_full")
    caplog.set_level(logging.DEBUG, logger=logger.name)

    class LargeResult:
        count = 3

        def model_dump(self, *args: object, **kwargs: object) -> object:
            return {"count": self.count, "nested": {"value": "complete"}}

    result = LargeResult()

    async def call() -> LargeResult:
        return result

    returned = await component_logging.log_async_component_call(
        logger,
        operation="probe_full",
        request_type="ProbeRequest",
        run_id="run-2",
        request={"input": "small"},
        call=call,
        result_message_type="LargeResult",
        result_calculation_key=lambda value: "eo:calc:v2:full-key",
    )

    assert returned is result
    assert len(caplog.records) == 2
    outgoing = caplog.records[1].getMessage()
    assert "calculation_key=eo:calc:v2:full-key" in outgoing
    assert "payload_mode=full" in outgoing
    assert "message_type=LargeResult" in outgoing
    assert json.loads(outgoing.partition(" message=")[2]) == {
        "count": 3,
        "nested": {"value": "complete"},
    }


async def test_async_boundary_does_no_logging_work_below_debug(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    logger = logging.getLogger("exact_orb.tests.component_boundary_disabled")
    caplog.set_level(logging.INFO, logger=logger.name)
    projector_calls = 0
    key_calls = 0

    def forbidden_serialize(message: object) -> str:
        raise AssertionError("component payload must not be serialized below DEBUG")

    def project_result(value: object) -> object:
        nonlocal projector_calls
        projector_calls += 1
        return value

    def result_key(value: object) -> str:
        nonlocal key_calls
        key_calls += 1
        return "unexpected"

    monkeypatch.setattr(
        component_logging,
        "serialize_component_message",
        forbidden_serialize,
    )
    result = object()

    returned = await component_logging.log_async_component_call(
        logger,
        operation="probe_disabled",
        request_type="ProbeRequest",
        run_id="run-3",
        request={"input": "complete"},
        call=lambda: _return_async(result),
        result_projector=project_result,
        result_calculation_key=result_key,
    )

    assert returned is result
    assert projector_calls == 0
    assert key_calls == 0
    assert caplog.records == []


async def test_async_boundary_logs_structured_error_and_reraises(
    caplog: pytest.LogCaptureFixture,
) -> None:
    logger = logging.getLogger("exact_orb.tests.component_boundary_error")
    caplog.set_level(logging.DEBUG, logger=logger.name)
    error = RuntimeError("complete failure details")

    async def fail() -> object:
        raise error

    with pytest.raises(RuntimeError) as exc_info:
        await component_logging.log_async_component_call(
            logger,
            operation="probe_error",
            request_type="ProbeRequest",
            run_id="run-4",
            request={"input": "complete"},
            call=fail,
        )

    assert exc_info.value is error
    assert len(caplog.records) == 2
    outgoing = caplog.records[1].getMessage()
    assert "status=error" in outgoing
    assert "payload_mode=error" in outgoing
    assert "message_type=RuntimeError" in outgoing
    assert json.loads(outgoing.partition(" message=")[2]) == {
        "exception_type": "RuntimeError",
        "message": "complete failure details",
    }


async def _return_async(value: object) -> object:
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
            LOG_LINE_FORMAT,
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
    assert "component=logging_setup" in first_general_line
    assert "logger=exact_orb.logging_setup" in first_general_line
    assert "ephemeris_mode=" in first_general_line
    assert "house_system_default=P" in first_general_line
    assert "cli_call status=ok" in general
    assert "component=cli" in general
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
    assert "component=logging_setup" in captured.err


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
