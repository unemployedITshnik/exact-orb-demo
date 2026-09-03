"""Shared test runtime setup."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
import re
import shutil
import uuid

import pytest

from exact_orb.config import (
    EphemerisStatus,
    _reset_ephemeris_state_for_tests,
    configure_ephemeris,
)


REPO_ROOT = Path(__file__).resolve().parents[1]


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "no_ephemeris_autoinit: skip automatic configure_ephemeris() for this test",
    )


@pytest.fixture(autouse=True)
def ephemeris_runtime(request: pytest.FixtureRequest) -> Iterator[None]:
    _reset_ephemeris_state_for_tests()
    if request.node.get_closest_marker("no_ephemeris_autoinit") is None:
        status = configure_ephemeris(REPO_ROOT / "ephe", selena_method="true_perigee")
        if status.missing_files:
            pytest.exit(_missing_ephemeris_message(status), returncode=pytest.ExitCode.USAGE_ERROR)
    try:
        yield
    finally:
        _reset_ephemeris_state_for_tests()


def _missing_ephemeris_message(status: EphemerisStatus) -> str:
    """Report a broken environment once instead of failing every calculation.

    Without the ephemeris files Swiss Ephemeris raises inside the calculation
    for Chiron and Selena, so the real cause is buried under a hundred
    unrelated ``RuntimeError`` tracebacks.
    """

    return (
        "Файлы эфемерид Swiss Ephemeris не найдены, окружение не готово.\n"
        f"Каталог: {status.path}\n"
        f"Отсутствуют: {', '.join(status.missing_files)}\n"
        "Файлы должны лежать в репозитории; см. ephe/README.md."
    )


@pytest.fixture
def tmp_path(request: pytest.FixtureRequest) -> Iterator[Path]:
    """Workspace-local tmp_path for Windows sandboxes with locked system temp."""

    safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", request.node.name).strip("_")
    path = REPO_ROOT / "logs" / "pytest-tmp" / f"{safe_name[:80]}-{uuid.uuid4().hex[:8]}"
    path.mkdir(parents=True, exist_ok=False)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)
