"""Project configuration for exact-orb."""

from __future__ import annotations

import logging
import os
from pathlib import Path
import tomllib
from typing import Any, Literal

from pydantic import BaseModel
import swisseph as swe


LOGGER = logging.getLogger(__name__)
DEFAULT_EPHEMERIS_PATH = "data/ephe"
EPHEMERIS_ENV_VAR = "EXACT_ORB_EPHE_PATH"
REQUIRED_EPHEMERIS_FILES = ("sepl_18.se1", "semo_18.se1", "seas_18.se1")
SELENA_METHOD_ENV_VAR = "EXACT_ORB_SELENA_METHOD"
SelenaMethodName = Literal["mean_perigee", "true_perigee"]

_STATUS: "EphemerisStatus | None" = None


class EphemerisStatus(BaseModel):
    """Actual Swiss Ephemeris file mode used by calculations."""

    path: str
    source: Literal["argument", "environment", "pyproject", "default"]
    mode: Literal["files", "fallback"]
    required_files: tuple[str, ...]
    found_files: tuple[str, ...]
    missing_files: tuple[str, ...]

    @property
    def using_files(self) -> bool:
        return self.mode == "files"


def configure_ephemeris(path: str | os.PathLike[str] | None = None) -> EphemerisStatus:
    """Configure ``swisseph`` before the first calculation.

    The path priority is: explicit argument, ``EXACT_ORB_EPHE_PATH``,
    ``[tool.exact_orb].ephemeris_path`` in ``pyproject.toml``, then
    ``data/ephe``.
    """

    global _STATUS

    resolved_path, source = _resolve_ephemeris_path(path)
    status = _build_status(resolved_path, source)
    if _STATUS is None or _STATUS.path != status.path:
        swe.set_ephe_path(status.path)
        _log_status(status)
        _STATUS = status
    return _STATUS


def get_ephemeris_status() -> EphemerisStatus:
    """Return current ephemeris status, configuring it on first access."""

    return configure_ephemeris()


def get_selena_method_name(method: str | None = None) -> SelenaMethodName:
    """Return configured Selena method name."""

    value = method or os.environ.get(SELENA_METHOD_ENV_VAR) or _read_pyproject_value("selena_method")
    if value is None:
        return "mean_perigee"
    if value not in {"mean_perigee", "true_perigee"}:
        raise ValueError("selena_method must be 'mean_perigee' or 'true_perigee'")
    return value


def read_exact_orb_pyproject_value(name: str) -> Any | None:
    """Return one value from ``[tool.exact_orb]`` in the nearest pyproject."""

    for directory in (Path.cwd(), *Path.cwd().parents):
        pyproject = directory / "pyproject.toml"
        if not pyproject.exists():
            continue

        with pyproject.open("rb") as handle:
            data = tomllib.load(handle)
        value = data.get("tool", {}).get("exact_orb", {}).get(name)
        if value in (None, ""):
            return None
        if name in {"ephemeris_path", "log_dir"} and isinstance(value, str):
            return str((directory / value).resolve())
        return value
    return None


def resolve_ephemeris_status(path: str | os.PathLike[str] | None = None) -> EphemerisStatus:
    """Resolve the ephemeris status without configuring Swiss Ephemeris."""

    resolved_path, source = _resolve_ephemeris_path(path)
    return _build_status(resolved_path, source)


def _resolve_ephemeris_path(
    path: str | os.PathLike[str] | None,
) -> tuple[Path, Literal["argument", "environment", "pyproject", "default"]]:
    if path is not None:
        return _absolute_path(Path(path)), "argument"

    env_path = os.environ.get(EPHEMERIS_ENV_VAR)
    if env_path:
        return _absolute_path(Path(env_path)), "environment"

    pyproject_path = _read_pyproject_value("ephemeris_path")
    if pyproject_path:
        return _absolute_path(Path(pyproject_path)), "pyproject"

    return _absolute_path(Path(DEFAULT_EPHEMERIS_PATH)), "default"


def _absolute_path(path: Path) -> Path:
    if path.is_absolute():
        return path.resolve()
    return (Path.cwd() / path).resolve()


def _read_pyproject_value(name: str) -> str | None:
    value = read_exact_orb_pyproject_value(name)
    return value if isinstance(value, str) else None


def _build_status(
    path: Path,
    source: Literal["argument", "environment", "pyproject", "default"],
) -> EphemerisStatus:
    found = tuple(name for name in REQUIRED_EPHEMERIS_FILES if (path / name).is_file())
    missing = tuple(name for name in REQUIRED_EPHEMERIS_FILES if name not in found)
    return EphemerisStatus(
        path=str(path),
        source=source,
        mode="files" if not missing else "fallback",
        required_files=REQUIRED_EPHEMERIS_FILES,
        found_files=found,
        missing_files=missing,
    )


def _log_status(status: EphemerisStatus) -> None:
    if status.mode == "files":
        LOGGER.info("Swiss Ephemeris files found in %s", status.path)
    else:
        LOGGER.warning(
            "Swiss Ephemeris fallback mode: missing %s in %s",
            ", ".join(status.missing_files),
            status.path,
        )
