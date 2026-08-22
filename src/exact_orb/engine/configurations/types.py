"""Data models for aspect configurations."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field

from exact_orb.engine.aspects import Aspect, AspectPointRef


PointKey = tuple[str, str]


class ConfigurationType(str, Enum):
    """Supported aspect configuration types."""

    T_SQUARE = "t_square"
    YOD = "yod"
    BISEXTILE = "bisextile"
    GRAND_CROSS = "grand_cross"
    GRAND_TRINE = "grand_trine"
    TRAPEZE = "trapeze"


class ConfigurationCategory(str, Enum):
    """Figure quality category based on its worst edge orb."""

    TIGHT = "tight"
    MODERATE = "moderate"
    LOOSE = "loose"


class ConfigurationCategoryThresholds(BaseModel):
    """Independent orb thresholds for configuration quality labels."""

    tight: float = Field(default=3.0, gt=0.0)
    moderate: float = Field(default=5.0, gt=0.0)


class Configuration(BaseModel):
    """One complete aspect figure assembled from existing aspect edges."""

    type: ConfigurationType
    points: dict[str, AspectPointRef]
    aspects: tuple[Aspect, ...]
    max_orb: float = Field(..., ge=0.0)
    category: ConfigurationCategory
    chart: str = Field(..., min_length=1)
    element: str | None = None
    modality: str | None = None
    contains: tuple["Configuration", ...] = ()


class ConfigurationConfig(BaseModel):
    """Configuration finder options.

    ``configuration_max_orb`` is independent from aspect presentation filters.
    The finder still uses only the provided aspect edges; this threshold only
    removes edges that are too loose for a figure-level result.
    ``configuration_categories`` labels the finished figure by its ``max_orb``;
    these thresholds are separate from aspect categories because multi-edge
    figures can remain meaningful at wider orbs. ``point_signs`` is optional
    chart metadata keyed by ``body`` or ``chart:body`` and is used only to label
    grand trines and grand crosses.
    """

    configuration_max_orb: float = Field(default=7.0, ge=0.0)
    configuration_categories: ConfigurationCategoryThresholds = Field(
        default_factory=ConfigurationCategoryThresholds
    )
    include_nested: bool = False
    enabled_types: tuple[ConfigurationType, ...] = tuple(ConfigurationType)
    points: tuple[str, ...] | None = (
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
        "north_node",
        "south_node",
        "lilith",
    )
    point_signs: dict[str, int] = Field(default_factory=dict)
