"""Data models for aspect calculation."""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class AspectType(str, Enum):
    """Supported deterministic aspect types."""

    CONJUNCTION = "conjunction"
    SEMISEXTILE = "semisextile"
    SEXTILE = "sextile"
    SQUARE = "square"
    TRINE = "trine"
    QUINCUNX = "quincunx"
    OPPOSITION = "opposition"


class AspectCategory(str, Enum):
    """Orb category for presentation and downstream interpretation."""

    EXACT = "exact"
    WORKING = "working"
    BACKGROUND = "background"


class AspectPointRef(BaseModel):
    """Stable reference to a point in one chart."""

    chart: str = Field(..., min_length=1)
    body: str = Field(..., min_length=1)


class PositionedPoint(BaseModel):
    """A point with explicit chart ownership and ecliptic longitude."""

    chart: str = Field(..., min_length=1)
    body: str = Field(..., min_length=1)
    longitude: float

    @property
    def ref(self) -> AspectPointRef:
        return AspectPointRef(chart=self.chart, body=self.body)


class Aspect(BaseModel):
    """One aspect between two positioned points.

    ``applying`` is ``None`` for static charts such as natal charts: a single
    snapshot has no time derivative, so convergence/divergence is undefined.
    Transit phase logic can fill this field later from motion over time.
    """

    from_point: AspectPointRef
    to_point: AspectPointRef
    aspect_type: AspectType
    exact_angle: float
    orb: float = Field(..., ge=0.0)
    category: AspectCategory
    applying: bool | None = None


class CategoryThresholds(BaseModel):
    """Orb thresholds shared by natal and transit aspect sets."""

    exact: float = Field(default=1.0, gt=0.0)
    working: float = Field(default=3.0, gt=0.0)


class AspectOrbSet(BaseModel):
    """Differentiated orb limits for one calculation context."""

    max_orb: float = Field(default=7.0, ge=0.0)
    aspect_orbs: dict[AspectType, float] = Field(default_factory=dict)
    body_orbs: dict[str, float] = Field(default_factory=dict)
    aspect_body_overrides: dict[AspectType, dict[str, float]] = Field(default_factory=dict)


class AspectConfig(BaseModel):
    """Full aspect configuration.

    ``mode`` selects which independent orb set is active. Natal aspects use a
    broader default set, while transit aspects are capped more tightly.
    """

    mode: Literal["natal", "transit"] = "natal"
    categories: CategoryThresholds = Field(default_factory=CategoryThresholds)
    natal_orbs: AspectOrbSet = Field(default_factory=lambda: _default_natal_orbs())
    transit_orbs: AspectOrbSet = Field(default_factory=lambda: _default_transit_orbs())
    point_aliases: dict[str, str] = Field(default_factory=lambda: dict(DEFAULT_POINT_ALIASES))
    natal_points: tuple[str, ...] = (
        "sun",
        "moon",
        "mercury",
        "venus",
        "mars",
        "jupiter",
        "saturn",
        "uranus",
        "neptune",
        "pluto",
        "chiron",
        "true_node",
        "south_node",
        "mean_apog",
        "selena",
        "pars_fortune",
        "vertex",
        "asc",
        "mc",
    )

    @property
    def active_orbs(self) -> AspectOrbSet:
        return self.natal_orbs if self.mode == "natal" else self.transit_orbs

    @classmethod
    def natal(cls, *, max_orb: float | None = None) -> "AspectConfig":
        natal_orbs = _default_natal_orbs()
        if max_orb is not None:
            natal_orbs = _with_max_orb(natal_orbs, max_orb)
        return cls(mode="natal", natal_orbs=natal_orbs)

    @classmethod
    def transit(cls, *, max_orb: float | None = None) -> "AspectConfig":
        transit_orbs = _default_transit_orbs()
        if max_orb is not None:
            transit_orbs = _with_max_orb(transit_orbs, max_orb)
        return cls(mode="transit", transit_orbs=transit_orbs)


ASPECT_ANGLES: dict[AspectType, float] = {
    AspectType.CONJUNCTION: 0.0,
    AspectType.SEMISEXTILE: 30.0,
    AspectType.SEXTILE: 60.0,
    AspectType.SQUARE: 90.0,
    AspectType.TRINE: 120.0,
    AspectType.QUINCUNX: 150.0,
    AspectType.OPPOSITION: 180.0,
}

ASPECT_PRIORITY: dict[AspectType, int] = {
    aspect_type: index for index, aspect_type in enumerate(ASPECT_ANGLES)
}

DEFAULT_POINT_ALIASES: dict[str, str] = {
    "true_node": "north_node",
    "mean_apog": "lilith",
    "pars_fortune": "pars",
}


def _default_natal_orbs() -> AspectOrbSet:
    luminaries = {"sun": 7.0, "moon": 7.0}
    planets = {
        "mercury": 6.0,
        "venus": 5.0,
        "mars": 3.0,
        "jupiter": 6.0,
        "saturn": 6.0,
        "uranus": 7.0,
        "neptune": 7.0,
        "pluto": 7.0,
        "chiron": 7.0,
    }
    points = {
        "north_node": 3.0,
        "south_node": 3.0,
        "lilith": 3.0,
        "selena": 3.0,
        "pars": 3.0,
        "vertex": 3.0,
        "asc": 6.0,
        "mc": 6.0,
    }
    return AspectOrbSet(
        max_orb=7.0,
        aspect_orbs={
            AspectType.CONJUNCTION: 7.0,
            AspectType.SEMISEXTILE: 1.0,
            AspectType.SEXTILE: 7.0,
            AspectType.SQUARE: 7.0,
            AspectType.TRINE: 7.0,
            AspectType.QUINCUNX: 7.0,
            AspectType.OPPOSITION: 7.0,
        },
        body_orbs={**luminaries, **planets, **points},
        aspect_body_overrides={
            AspectType.TRINE: {"pars": 2.0},
            AspectType.OPPOSITION: {"pars": 2.0},
            AspectType.SQUARE: {"pluto": 3.0},
        },
    )


def _default_transit_orbs() -> AspectOrbSet:
    natal = _default_natal_orbs()
    transit_body_orbs = {name: min(orb, 6.0) for name, orb in natal.body_orbs.items()}
    return AspectOrbSet(
        max_orb=6.0,
        aspect_orbs={
            aspect_type: min(orb, 6.0)
            for aspect_type, orb in natal.aspect_orbs.items()
        },
        body_orbs=transit_body_orbs,
        aspect_body_overrides=natal.aspect_body_overrides,
    )


def _with_max_orb(orb_set: AspectOrbSet, max_orb: float) -> AspectOrbSet:
    return AspectOrbSet(
        max_orb=max_orb,
        aspect_orbs=orb_set.aspect_orbs,
        body_orbs=orb_set.body_orbs,
        aspect_body_overrides=orb_set.aspect_body_overrides,
    )
