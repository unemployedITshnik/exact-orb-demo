"""Project configuration for exact-orb."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import logging
import os
from pathlib import Path
import tomllib
from typing import Any, Literal

from pydantic import BaseModel

from exact_orb import swiss_backend
from exact_orb.ephemeris_runtime import ephemeris_session
from exact_orb.errors import (
    EphemerisConfigurationError,
    EphemerisNotInitializedError,
    EphemerisPathMismatchError,
    EphemerisRuntimeError,
    EphemerisSelenaMethodMismatchError,
    EphemerisSessionRequiredError,
)

LOGGER = logging.getLogger(__name__)
DEFAULT_EPHEMERIS_PATH = "data/ephe"
EPHEMERIS_ENV_VAR = "EXACT_ORB_EPHE_PATH"
REQUIRED_EPHEMERIS_FILES = ("sepl_18.se1", "semo_18.se1", "seas_18.se1")
SELENA_METHOD_ENV_VAR = "EXACT_ORB_SELENA_METHOD"
SelenaMethodName = Literal["mean_perigee", "true_perigee"]
_PROJECT_CONFIG_SEARCH_START = Path(__file__).resolve().parent

_STATE: "_EphemerisRuntimeState | None" = None


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


@dataclass(frozen=True)
class _EphemerisRuntimeState:
    status: EphemerisStatus
    normalized_path: str
    selena_method: SelenaMethodName


def configure_ephemeris(
    path: str | os.PathLike[str] | None = None,
    *,
    selena_method: str | None = None,
) -> EphemerisStatus:
    """Configure Swiss Ephemeris explicitly at process startup.

    Configuration is resolved once and frozen for the process. The priority is:
    explicit argument, environment, the exact-orb project ``pyproject.toml``,
    then the code default.
    """

    global _STATE

    state = _STATE
    if state is not None and path is None and selena_method is None:
        return state.status

    with ephemeris_session():
        state = _STATE
        if state is not None:
            if path is not None and not _is_same_ephemeris_path(
                path,
                state.normalized_path,
                frozen_path=state.status.path,
            ):
                raise EphemerisPathMismatchError(
                    "ephemeris path is already configured as %r, got %r"
                    % (state.status.path, os.fspath(path))
                )
            if selena_method is not None:
                requested_method = _validate_selena_method_name(selena_method)
                if requested_method != state.selena_method:
                    raise EphemerisSelenaMethodMismatchError(
                        "selena method is already configured as %r, got %r"
                        % (state.selena_method, requested_method)
                    )
            return state.status

        pyproject_config = _read_exact_orb_pyproject_config()
        resolved_path, source = _resolve_ephemeris_path(path, pyproject_config)
        configured_selena_method = _resolve_selena_method_name(selena_method, pyproject_config)
        status = _build_status(resolved_path, source)
        swiss_backend.swe.set_ephe_path(status.path)
        state = _EphemerisRuntimeState(
            status=status,
            normalized_path=_normalize_path_for_comparison(status.path),
            selena_method=configured_selena_method,
        )
        _STATE = state
        _log_status(status)
        return state.status


def get_ephemeris_status() -> EphemerisStatus:
    """Return frozen ephemeris status after explicit startup."""

    state = _STATE
    if state is None:
        raise EphemerisNotInitializedError(
            "Swiss Ephemeris is not configured; call configure_ephemeris() at startup"
        )
    return state.status


def validate_ephemeris_path(path: str | os.PathLike[str] | None) -> EphemerisStatus:
    """Return frozen status, rejecting an explicit path mismatch."""

    state = _STATE
    if state is None:
        raise EphemerisNotInitializedError(
            "Swiss Ephemeris is not configured; call configure_ephemeris() at startup"
        )
    if path is not None and not _is_same_ephemeris_path(
        path,
        state.normalized_path,
        frozen_path=state.status.path,
    ):
        raise EphemerisPathMismatchError(
            "ephemeris path is already configured as %r, got %r"
            % (state.status.path, os.fspath(path))
        )
    return state.status


def get_selena_method_name(method: str | None = None) -> SelenaMethodName:
    """Return the startup Selena default or a validated explicit method."""

    if method is not None:
        return _validate_selena_method_name(method)
    state = _STATE
    if state is None:
        raise EphemerisNotInitializedError(
            "Swiss Ephemeris is not configured; call configure_ephemeris() at startup"
        )
    return state.selena_method


def read_exact_orb_pyproject_value(name: str) -> Any | None:
    """Return one value from the exact-orb project ``pyproject.toml``."""

    data, directory = _read_exact_orb_pyproject_config_with_directory()
    if data is None or directory is None:
        return None

    value = data.get(name)
    if value in (None, ""):
        return None
    if name in {"ephemeris_path", "log_dir"} and isinstance(value, str):
        return str((directory / value).resolve())
    return value


def resolve_ephemeris_status(path: str | os.PathLike[str] | None = None) -> EphemerisStatus:
    """Resolve the ephemeris status without configuring Swiss Ephemeris."""

    pyproject_config = _read_exact_orb_pyproject_config()
    resolved_path, source = _resolve_ephemeris_path(path, pyproject_config)
    return _build_status(resolved_path, source)


def _read_exact_orb_pyproject_config() -> Mapping[str, Any]:
    data, directory = _read_exact_orb_pyproject_config_with_directory()
    if data is None or directory is None:
        return {}
    return _resolve_pyproject_paths(data, directory)


def _read_exact_orb_pyproject_config_with_directory(
) -> tuple[dict[str, Any] | None, Path | None]:
    search_start = _PROJECT_CONFIG_SEARCH_START.resolve()
    for directory in (search_start, *search_start.parents):
        pyproject = directory / "pyproject.toml"
        if not pyproject.exists():
            continue

        with pyproject.open("rb") as handle:
            data = tomllib.load(handle)
        config = data.get("tool", {}).get("exact_orb", {})
        return dict(config) if isinstance(config, dict) else {}, directory
    return None, None


def _resolve_pyproject_paths(data: Mapping[str, Any], directory: Path) -> dict[str, Any]:
    resolved = dict(data)
    for name in ("ephemeris_path", "log_dir"):
        value = resolved.get(name)
        if isinstance(value, str) and value:
            resolved[name] = str((directory / value).resolve())
    return resolved


def _resolve_ephemeris_path(
    path: str | os.PathLike[str] | None,
    pyproject_config: Mapping[str, Any],
) -> tuple[Path, Literal["argument", "environment", "pyproject", "default"]]:
    if path is not None:
        return _absolute_path(Path(path)), "argument"

    env_path = os.environ.get(EPHEMERIS_ENV_VAR)
    if env_path:
        return _absolute_path(Path(env_path)), "environment"

    pyproject_path = pyproject_config.get("ephemeris_path")
    if pyproject_path:
        return _absolute_path(Path(os.fspath(pyproject_path))), "pyproject"

    return _absolute_path(Path(DEFAULT_EPHEMERIS_PATH)), "default"


def _resolve_selena_method_name(
    method: str | None,
    pyproject_config: Mapping[str, Any],
) -> SelenaMethodName:
    value = (
        method
        or os.environ.get(SELENA_METHOD_ENV_VAR)
        or pyproject_config.get("selena_method")
        or "mean_perigee"
    )
    return _validate_selena_method_name(str(value))


def _validate_selena_method_name(method: str) -> SelenaMethodName:
    if method not in {"mean_perigee", "true_perigee"}:
        raise ValueError("selena_method must be 'mean_perigee' or 'true_perigee'")
    return method  # type: ignore[return-value]


def _absolute_path(path: Path) -> Path:
    if path.is_absolute():
        return path.resolve()
    return (Path.cwd() / path).resolve()


def _normalize_path_for_comparison(
    path: str | os.PathLike[str],
    *,
    normcase: Callable[[str], str] = os.path.normcase,
) -> str:
    return normcase(str(Path(path).resolve()))


def _is_same_ephemeris_path(
    candidate: str | os.PathLike[str],
    frozen_normalized: str,
    *,
    frozen_path: str | os.PathLike[str],
    normcase: Callable[[str], str] = os.path.normcase,
    samefile: Callable[[str | os.PathLike[str], str | os.PathLike[str]], bool] = os.path.samefile,
) -> bool:
    if _normalize_path_for_comparison(candidate, normcase=normcase) == frozen_normalized:
        return True
    # ``samefile`` always receives the original paths: the normalized form is a
    # comparison key, not a path the filesystem is guaranteed to resolve.
    try:
        return samefile(candidate, frozen_path)
    except OSError:
        return False


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


def _reset_ephemeris_state_for_tests() -> None:
    """Reset process ephemeris state for tests."""

    global _STATE

    _STATE = None


__all__ = [
    "DEFAULT_EPHEMERIS_PATH",
    "EPHEMERIS_ENV_VAR",
    "REQUIRED_EPHEMERIS_FILES",
    "SELENA_METHOD_ENV_VAR",
    "EphemerisConfigurationError",
    "EphemerisNotInitializedError",
    "EphemerisPathMismatchError",
    "EphemerisRuntimeError",
    "EphemerisSelenaMethodMismatchError",
    "EphemerisSessionRequiredError",
    "EphemerisStatus",
    "SelenaMethodName",
    "configure_ephemeris",
    "get_ephemeris_status",
    "get_selena_method_name",
    "read_exact_orb_pyproject_value",
    "resolve_ephemeris_status",
    "validate_ephemeris_path",
]
