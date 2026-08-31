"""Ephemeris runtime configuration tests."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from datetime import datetime, timezone
import logging
from pathlib import Path
import threading
import time
from typing import Any

import pytest

from exact_orb import config, swiss_backend
from exact_orb.config import (
    EphemerisNotInitializedError,
    EphemerisPathMismatchError,
    configure_ephemeris,
    get_ephemeris_status,
    get_selena_method_name,
)
from exact_orb.engine.charts.natal import NatalChart, calculate_natal
from exact_orb.engine.charts.transit import calculate_transits
from tests.conftest import REPO_ROOT
from tests.fixtures.natal_1985 import REFERENCE


TIMEOUT_SECONDS = 3.0
THREAD_COUNT = 8


@pytest.fixture(autouse=True)
def capture_config_logs_after_cli_logging_tests() -> Iterator[None]:
    package_logger = logging.getLogger("exact_orb")
    old_propagate = package_logger.propagate
    package_logger.propagate = True
    try:
        yield
    finally:
        package_logger.propagate = old_propagate


class SpySwe:
    def __init__(self) -> None:
        self.set_ephe_path_calls: list[str] = []

    def set_ephe_path(self, path: str) -> None:
        self.set_ephe_path_calls.append(path)


@pytest.mark.no_ephemeris_autoinit
def test_calculate_natal_requires_explicit_ephemeris_startup() -> None:
    with pytest.raises(EphemerisNotInitializedError):
        calculate_natal(
            datetime(1985, 9, 1, 20, 45, tzinfo=timezone.utc),
            REFERENCE["latitude"],
            REFERENCE["longitude"],
            chart_kind="natal",
        )


@pytest.mark.no_ephemeris_autoinit
def test_calculate_transits_requires_explicit_ephemeris_startup() -> None:
    configure_ephemeris(REPO_ROOT / "ephe")
    chart = _reference_natal_chart()
    config._reset_ephemeris_state_for_tests()

    with pytest.raises(EphemerisNotInitializedError):
        calculate_transits(chart, datetime(2026, 1, 1, tzinfo=timezone.utc))


@pytest.mark.no_ephemeris_autoinit
def test_get_ephemeris_status_does_not_lazy_initialize(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(config.EPHEMERIS_ENV_VAR, "must-not-be-read")

    def fail_read() -> Mapping[str, Any]:
        raise AssertionError("pyproject should not be read")

    def fail_resolve(
        path: object,
        pyproject_config: Mapping[str, Any],
    ) -> object:
        _ = path, pyproject_config
        raise AssertionError("path resolution should not run")

    monkeypatch.setattr(config, "_read_exact_orb_pyproject_config", fail_read)
    monkeypatch.setattr(config, "_resolve_ephemeris_path", fail_resolve)

    with pytest.raises(EphemerisNotInitializedError):
        get_ephemeris_status()


@pytest.mark.no_ephemeris_autoinit
def test_configure_ephemeris_is_idempotent_without_repeating_backend_or_status_log(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spy = SpySwe()
    monkeypatch.setattr(swiss_backend, "swe", spy)
    caplog.set_level(logging.DEBUG, logger="exact_orb.config")
    ephe_path = REPO_ROOT / "ephe"

    first = configure_ephemeris(ephe_path)

    def fail_resolve(
        path: object,
        pyproject_config: Mapping[str, Any],
    ) -> object:
        _ = path, pyproject_config
        raise AssertionError("path resolution should not run for configure_ephemeris(None)")

    monkeypatch.setattr(config, "_resolve_ephemeris_path", fail_resolve)
    second = configure_ephemeris(None)
    third = configure_ephemeris(ephe_path)

    assert first is second is third
    assert spy.set_ephe_path_calls == [first.path]
    assert len(_status_log_records(caplog)) == 1


@pytest.mark.no_ephemeris_autoinit
def test_configure_ephemeris_rejects_different_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(swiss_backend, "swe", SpySwe())
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()

    configure_ephemeris(first)

    with pytest.raises(EphemerisPathMismatchError):
        configure_ephemeris(second)


@pytest.mark.no_ephemeris_autoinit
def test_configure_ephemeris_serializes_concurrent_startup(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spy = SpySwe()
    monkeypatch.setattr(swiss_backend, "swe", spy)
    caplog.set_level(logging.DEBUG, logger="exact_orb.config")
    pyproject_reads: list[int] = []
    resolve_calls: list[int] = []
    original_resolve = config._resolve_ephemeris_path

    def read_pyproject_once() -> Mapping[str, Any]:
        pyproject_reads.append(threading.get_ident())
        return {
            "ephemeris_path": str(REPO_ROOT / "ephe"),
            "selena_method": "true_perigee",
        }

    def slow_resolve(
        path: str | Path | None,
        pyproject_config: Mapping[str, Any],
    ) -> tuple[Path, str]:
        resolve_calls.append(threading.get_ident())
        time.sleep(0.02)
        return original_resolve(path, pyproject_config)

    monkeypatch.setattr(config, "_read_exact_orb_pyproject_config", read_pyproject_once)
    monkeypatch.setattr(config, "_resolve_ephemeris_path", slow_resolve)

    barrier = threading.Barrier(THREAD_COUNT)
    results: list[object] = []
    errors: list[BaseException] = []

    def worker() -> None:
        try:
            barrier.wait(TIMEOUT_SECONDS)
            results.append(configure_ephemeris())
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(THREAD_COUNT)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(TIMEOUT_SECONDS)

    assert all(not thread.is_alive() for thread in threads)
    assert errors == []
    assert len(results) == THREAD_COUNT
    first_status = results[0]
    assert all(status is first_status for status in results)
    assert spy.set_ephe_path_calls == [first_status.path]
    assert len(pyproject_reads) == 1
    assert len(resolve_calls) == 1
    assert len(_status_log_records(caplog)) == 1


@pytest.mark.no_ephemeris_autoinit
def test_ephemeris_runtime_state_publication_is_atomic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(swiss_backend, "swe", SpySwe())
    real_state_factory = config._EphemerisRuntimeState
    state_build_started = threading.Event()
    release_state_build = threading.Event()
    errors: list[BaseException] = []
    result: dict[str, object] = {}

    def slow_state_factory(*args: Any, **kwargs: Any) -> object:
        state_build_started.set()
        assert release_state_build.wait(TIMEOUT_SECONDS), "timed out releasing runtime state build"
        return real_state_factory(*args, **kwargs)

    # The patch works because configure_ephemeris() looks the name up on the
    # module at call time. Moving it to a local import would silently disarm
    # this test.
    monkeypatch.setattr(config, "_EphemerisRuntimeState", slow_state_factory)

    def startup() -> None:
        try:
            result["status"] = configure_ephemeris(
                REPO_ROOT / "ephe",
                selena_method="true_perigee",
            )
        except BaseException as exc:
            errors.append(exc)

    thread = threading.Thread(target=startup)
    thread.start()
    assert state_build_started.wait(TIMEOUT_SECONDS), "startup did not reach runtime state build"

    with pytest.raises(EphemerisNotInitializedError):
        get_ephemeris_status()
    with pytest.raises(EphemerisNotInitializedError):
        get_selena_method_name()

    release_state_build.set()
    thread.join(TIMEOUT_SECONDS)

    assert not thread.is_alive()
    assert errors == []
    assert get_ephemeris_status() is result["status"]
    assert get_selena_method_name() == "true_perigee"


def test_calculation_explicit_ephemeris_path_checks_against_frozen_path(
    tmp_path: Path,
) -> None:
    frozen = get_ephemeris_status().path
    mismatch = tmp_path / "calculation-path-mismatch"
    mismatch.mkdir()

    chart = calculate_natal(
        datetime(1985, 9, 1, 20, 45, tzinfo=timezone.utc),
        REFERENCE["latitude"],
        REFERENCE["longitude"],
        chart_kind="natal",
        house_system=REFERENCE["house_system"],
        ephemeris_path=frozen,
    )

    assert chart.ephemeris.path == frozen
    with pytest.raises(EphemerisPathMismatchError):
        calculate_natal(
            datetime(1985, 9, 1, 20, 45, tzinfo=timezone.utc),
            REFERENCE["latitude"],
            REFERENCE["longitude"],
            chart_kind="natal",
            house_system=REFERENCE["house_system"],
            ephemeris_path=str(mismatch),
        )
    with pytest.raises(EphemerisPathMismatchError):
        calculate_transits(
            chart,
            datetime(2026, 1, 1, tzinfo=timezone.utc),
            ephemeris_path=str(mismatch),
        )


def test_path_comparison_handles_case_variants_without_samefile(
    tmp_path: Path,
) -> None:
    ephe_path = tmp_path / "Ephe"
    ephe_path.mkdir()
    def lowercase(value: str) -> str:
        """Stand in for the Windows ``os.path.normcase`` on any platform."""

        return value.lower()

    def fail_samefile(left: str | Path, right: str | Path) -> bool:
        _ = left, right
        raise AssertionError("samefile should not run")

    frozen_normalized = config._normalize_path_for_comparison(ephe_path, normcase=lowercase)

    assert config._is_same_ephemeris_path(
        str(ephe_path).upper(),
        frozen_normalized,
        frozen_path=ephe_path,
        normcase=lowercase,
        samefile=fail_samefile,
    )


def test_get_selena_method_name_uses_frozen_default_without_runtime_io(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(config.SELENA_METHOD_ENV_VAR, "mean_perigee")

    def fail_read() -> object:
        raise AssertionError("pyproject should not be read after startup")

    monkeypatch.setattr(config, "_read_exact_orb_pyproject_config", fail_read)

    assert get_selena_method_name() == "true_perigee"
    assert get_selena_method_name("mean_perigee") == "mean_perigee"


def test_invalid_explicit_selena_method_keeps_value_error() -> None:
    with pytest.raises(ValueError, match="selena_method must be"):
        get_selena_method_name("bad-method")


def _reference_natal_chart() -> NatalChart:
    return calculate_natal(
        REFERENCE["datetime_utc"],
        REFERENCE["latitude"],
        REFERENCE["longitude"],
        chart_kind="natal",
        house_system=REFERENCE["house_system"],
    )


def _status_log_records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [
        record
        for record in caplog.records
        if record.name == "exact_orb.config"
        and (
            "Swiss Ephemeris files found in" in record.getMessage()
            or "Swiss Ephemeris fallback mode:" in record.getMessage()
        )
    ]
