"""Backend-free domain constants and normalizers shared across layers."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from decimal import Decimal, ROUND_HALF_UP
from enum import Enum
from math import isfinite
from types import MappingProxyType
from typing import Literal, cast


ChartKind = Literal["natal", "cosmogram"]
IncludeBlock = Literal[
    "positions",
    "houses",
    "rulers",
    "aspects",
    "configurations",
    "strength",
]

INCLUDE_BLOCKS: frozenset[str] = frozenset(
    {"positions", "houses", "rulers", "aspects", "configurations", "strength"}
)
DEFAULT_INCLUDE_BY_CHART_KIND: Mapping[ChartKind, tuple[str, ...]] = MappingProxyType(
    {
        "natal": ("aspects", "configurations", "houses", "positions", "rulers", "strength"),
        "cosmogram": ("aspects", "configurations", "positions"),
    }
)

LATITUDE_MIN = -90.0
LATITUDE_MAX = 90.0
LONGITUDE_MIN = -180.0
LONGITUDE_MAX = 180.0
COORDINATE_QUANT = Decimal("0.000001")
SUPPORTED_NATAL_HOUSE_SYSTEM_CODES: frozenset[str] = frozenset({"P"})


class RulershipScheme(str, Enum):
    """Available deterministic sign ruler tables."""

    COMBINED = "combined"
    MODERN = "modern"
    TRADITIONAL = "traditional"


def normalize_include(chart_kind: str, include: Iterable[str] | None) -> tuple[IncludeBlock, ...]:
    """Return a sorted, deduplicated include tuple for the requested chart kind."""

    if chart_kind not in DEFAULT_INCLUDE_BY_CHART_KIND:
        raise ValueError("chart_kind must be 'natal' or 'cosmogram'")

    kind = cast(ChartKind, chart_kind)
    if include is None:
        include_blocks = frozenset(DEFAULT_INCLUDE_BY_CHART_KIND[kind])
    else:
        if isinstance(include, (str, bytes)):
            raise ValueError("include must be a collection of include block names")
        raw_blocks = tuple(include)
        non_string_blocks = [block for block in raw_blocks if not isinstance(block, str)]
        if non_string_blocks:
            raise ValueError("include blocks must be strings")
        include_blocks = frozenset(raw_blocks)

    unknown = include_blocks - INCLUDE_BLOCKS
    if unknown:
        raise ValueError(f"unknown include block(s): {', '.join(sorted(unknown))}")
    if "rulers" in include_blocks and "houses" not in include_blocks:
        raise ValueError('include block "rulers" requires "houses"')
    if "strength" in include_blocks and "houses" not in include_blocks:
        raise ValueError('include block "strength" requires "houses"')
    if kind == "natal" and "houses" not in include_blocks:
        raise ValueError('chart_kind "natal" requires include block "houses"')
    if kind == "cosmogram":
        forbidden = include_blocks & {"houses", "rulers", "strength"}
        if forbidden:
            raise ValueError(
                f'chart_kind "cosmogram" forbids include block(s): {", ".join(sorted(forbidden))}'
            )

    return cast(tuple[IncludeBlock, ...], tuple(sorted(include_blocks)))


def normalize_house_system_code(house_system: str | bytes) -> str:
    """Normalize a Swiss Ephemeris house-system code without native imports."""

    if isinstance(house_system, str):
        try:
            house_system.encode("ascii")
        except UnicodeEncodeError as exc:
            raise ValueError("house_system must be a single ASCII character or one byte") from exc
        code = house_system
    elif isinstance(house_system, bytes):
        try:
            code = house_system.decode("ascii")
        except UnicodeDecodeError as exc:
            raise ValueError("house_system must be a single ASCII character or one byte") from exc
    else:
        raise ValueError("house_system must be a single ASCII character or one byte")

    if len(code) != 1:
        raise ValueError("house_system must be a single ASCII character or one byte")
    return code.upper()


def normalize_natal_house_system_code(house_system: str | bytes) -> str:
    """Normalize the currently supported natal house-system code."""

    code = normalize_house_system_code(house_system)
    if code not in SUPPORTED_NATAL_HOUSE_SYSTEM_CODES:
        raise ValueError("natal house_system must be 'P' (Placidus)")
    return code


def validate_geography(latitude: float, longitude: float) -> None:
    """Validate the broad coordinate contract accepted by the calculation engine."""

    lat = _finite_float(latitude, "latitude")
    lon = _finite_float(longitude, "longitude")
    if not LATITUDE_MIN <= lat <= LATITUDE_MAX:
        raise ValueError("latitude must be a finite value in [-90, 90]")
    if not LONGITUDE_MIN <= lon <= LONGITUDE_MAX:
        raise ValueError("longitude must be a finite value in [-180, 180]")


def normalize_latitude(value: object) -> float:
    """Quantize a latitude for calculation-key projection."""

    numeric = _finite_float(value, "latitude")
    if not LATITUDE_MIN <= numeric <= LATITUDE_MAX:
        raise ValueError("latitude must be a finite value in [-90, 90]")

    normalized = _quantize_coordinate(numeric)
    if not LATITUDE_MIN <= normalized <= LATITUDE_MAX:
        raise ValueError("latitude must be a finite value in [-90, 90]")
    return _normalize_negative_zero(normalized)


def normalize_longitude(value: object) -> float:
    """Quantize a longitude for calculation-key projection."""

    numeric = _finite_float(value, "longitude")
    if not LONGITUDE_MIN <= numeric <= LONGITUDE_MAX:
        raise ValueError("longitude must be a finite value in [-180, 180]")

    normalized = _quantize_coordinate(numeric)
    if normalized == LONGITUDE_MAX:
        normalized = LONGITUDE_MIN
    if not LONGITUDE_MIN <= normalized < LONGITUDE_MAX:
        raise ValueError("longitude must be a finite value in [-180, 180)")
    return _normalize_negative_zero(normalized)


def _finite_float(value: object, name: str) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{name} must be a finite value") from exc
    if not isfinite(numeric):
        raise ValueError(f"{name} must be a finite value")
    return numeric


def _quantize_coordinate(value: float) -> float:
    return float(Decimal(str(value)).quantize(COORDINATE_QUANT, rounding=ROUND_HALF_UP))


def _normalize_negative_zero(value: float) -> float:
    if value == 0.0:
        return 0.0
    return value


__all__ = [
    "COORDINATE_QUANT",
    "DEFAULT_INCLUDE_BY_CHART_KIND",
    "INCLUDE_BLOCKS",
    "LATITUDE_MAX",
    "LATITUDE_MIN",
    "LONGITUDE_MAX",
    "LONGITUDE_MIN",
    "SUPPORTED_NATAL_HOUSE_SYSTEM_CODES",
    "ChartKind",
    "IncludeBlock",
    "RulershipScheme",
    "normalize_house_system_code",
    "normalize_include",
    "normalize_latitude",
    "normalize_longitude",
    "normalize_natal_house_system_code",
    "validate_geography",
]
