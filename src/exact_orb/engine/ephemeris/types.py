"""Primitive ephemeris models and lookup tables."""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field

from exact_orb import swiss_backend


ZODIAC_SIGNS: tuple[str, ...] = (
    "Aries",
    "Taurus",
    "Gemini",
    "Cancer",
    "Leo",
    "Virgo",
    "Libra",
    "Scorpio",
    "Sagittarius",
    "Capricorn",
    "Aquarius",
    "Pisces",
)

MODERN_RULERS: tuple[tuple[str, ...], ...] = (
    ("mars",),
    ("venus",),
    ("mercury",),
    ("moon",),
    ("sun",),
    ("mercury",),
    ("venus",),
    ("pluto",),
    ("jupiter",),
    ("saturn",),
    ("uranus",),
    ("neptune",),
)

TRADITIONAL_RULERS: tuple[tuple[str, ...], ...] = (
    ("mars",),
    ("venus",),
    ("mercury",),
    ("moon",),
    ("sun",),
    ("mercury",),
    ("venus",),
    ("mars",),
    ("jupiter",),
    ("saturn",),
    ("saturn",),
    ("jupiter",),
)

COMBINED_RULERS: tuple[tuple[str, ...], ...] = (
    ("mars",),
    ("venus",),
    ("mercury",),
    ("moon",),
    ("sun",),
    ("mercury",),
    ("venus",),
    ("mars", "pluto"),
    ("jupiter",),
    ("saturn",),
    ("saturn", "uranus"),
    ("jupiter", "neptune"),
)

DEFAULT_BODY_IDS: dict[str, int] = {
    "sun": swiss_backend.swe.SUN,
    "moon": swiss_backend.swe.MOON,
    "mercury": swiss_backend.swe.MERCURY,
    "venus": swiss_backend.swe.VENUS,
    "mars": swiss_backend.swe.MARS,
    "jupiter": swiss_backend.swe.JUPITER,
    "saturn": swiss_backend.swe.SATURN,
    "uranus": swiss_backend.swe.URANUS,
    "neptune": swiss_backend.swe.NEPTUNE,
    "pluto": swiss_backend.swe.PLUTO,
    "chiron": swiss_backend.swe.CHIRON,
    "true_node": swiss_backend.swe.TRUE_NODE,
    "mean_apog": swiss_backend.swe.MEAN_APOG,
}

ANGLE_INDICES: tuple[tuple[str, int], ...] = (
    ("asc", swiss_backend.swe.ASC),
    ("mc", swiss_backend.swe.MC),
    ("armc", swiss_backend.swe.ARMC),
    ("vertex", swiss_backend.swe.VERTEX),
    ("equatorial_ascendant", swiss_backend.swe.EQUASC),
    ("co_ascendant_koch", swiss_backend.swe.COASC1),
    ("co_ascendant_munkasey", swiss_backend.swe.COASC2),
    ("polar_ascendant", swiss_backend.swe.POLASC),
)

SECONDS_PER_SIGN = 30 * 60 * 60
FULL_CIRCLE = 360.0
EPSILON = 1e-10


class RulershipScheme(str, Enum):
    """Available deterministic sign ruler tables."""

    COMBINED = "combined"
    MODERN = "modern"
    TRADITIONAL = "traditional"


class ZodiacPosition(BaseModel):
    """A normalized tropical zodiac position."""

    longitude: float = Field(..., ge=0.0, lt=360.0)
    sign_index: int = Field(..., ge=0, le=11)
    sign: str
    degree_in_sign: float = Field(..., ge=0.0, lt=30.0)
    degree: int = Field(..., ge=0, le=29)
    minute: int = Field(..., ge=0, le=59)
    second: int = Field(..., ge=0, le=59)


class BodyPosition(BaseModel):
    """Geocentric ecliptic body or derived point position."""

    name: str
    chart: Literal["natal"] = "natal"
    source: Literal["swisseph", "derived", "selena"] = "swisseph"
    method: str | None = None
    swe_id: int | None
    longitude: float = Field(..., ge=0.0, lt=360.0)
    latitude: float
    distance: float
    longitude_speed: float
    latitude_speed: float
    distance_speed: float
    retrograde: bool
    house: int | None = Field(default=None, ge=1, le=12)
    zodiac: ZodiacPosition
    retflags: int


class HouseCusp(BaseModel):
    """One house cusp from swe.houses_ex."""

    house: int = Field(..., ge=1, le=12)
    longitude: float = Field(..., ge=0.0, lt=360.0)
    zodiac: ZodiacPosition


class AnglePosition(BaseModel):
    """One auxiliary angle from the ascmc tuple returned by swe.houses_ex."""

    name: str
    longitude: float = Field(..., ge=0.0, lt=360.0)
    zodiac: ZodiacPosition


class CalculationWarning(BaseModel):
    """Non-fatal warning reported by Swiss Ephemeris."""

    source: str
    message: str
    retflags: int | None = None
