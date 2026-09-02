"""Shared test runtime setup."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
import re
import shutil
import uuid

import pytest

from exact_orb.config import _reset_ephemeris_state_for_tests, configure_ephemeris


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
        configure_ephemeris(REPO_ROOT / "ephe", selena_method="true_perigee")
    try:
        yield
    finally:
        _reset_ephemeris_state_for_tests()


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
