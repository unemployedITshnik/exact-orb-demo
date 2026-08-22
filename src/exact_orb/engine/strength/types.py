"""Data models for natal strength and structure."""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class DignityStatus(str, Enum):
    """Essential dignity state."""

    DOMICILE = "domicile"
    EXALTATION = "exaltation"
    DETRIMENT = "detriment"
    FALL = "fall"
    PEREGRINE = "peregrine"


class StrengthCategory(str, Enum):
    """Neutral strength label.

    ``weak`` is not a moral or quality judgment. It means the planet tends to
    act indirectly, through other chart factors, or requires more effort to
    express cleanly.
    """

    STRONG = "strong"
    MODERATE = "moderate"
    WEAK = "weak"


class HouseType(str, Enum):
    """Angular, succedent, or cadent house group."""

    ANGULAR = "angular"
    SUCCEDENT = "succedent"
    CADENT = "cadent"


class BalanceState(str, Enum):
    """Relative balance label against an even distribution."""

    DEFICIT = "deficit"
    BALANCED = "balanced"
    EXCESS = "excess"


class Dignity(BaseModel):
    """Essential dignity score from table lookup."""

    body: str
    sign: str
    system: Literal["traditional", "modern"]
    status: DignityStatus
    score: int


class AccidentalModifier(BaseModel):
    """One accidental strength modifier."""

    name: str
    score: int


class AccidentalStrength(BaseModel):
    """Accidental strength from house type and configured modifiers."""

    body: str
    house: int = Field(..., ge=1, le=12)
    house_type: HouseType
    house_score: int
    modifiers: tuple[AccidentalModifier, ...] = ()
    score: int


class PlanetStrength(BaseModel):
    """Essential and accidental strength for one planet."""

    body: str
    dignity: Dignity
    accidental: AccidentalStrength
    essential_score: int
    accidental_score: int
    total: int
    category: StrengthCategory
    note: str | None = None


class BalanceBucket(BaseModel):
    """One weighted balance bucket."""

    score: float
    percentage: float
    state: BalanceState
    contributors: tuple[str, ...] = ()


class HemisphereBalance(BaseModel):
    """North/south and east/west weighted distribution."""

    north: float
    south: float
    east: float
    west: float


class HouseTypeBalance(BaseModel):
    """Weighted distribution by house type."""

    angular: float
    succedent: float
    cadent: float


class ChartBalance(BaseModel):
    """Weighted balance by elements, modalities, hemispheres, and house types."""

    elements: dict[str, BalanceBucket]
    modalities: dict[str, BalanceBucket]
    hemispheres: HemisphereBalance
    house_types: HouseTypeBalance
    total_weight: float
    dominant_elements: tuple[str, ...]
    deficient_elements: tuple[str, ...]
    dominant_modalities: tuple[str, ...]
    deficient_modalities: tuple[str, ...]


class DispositorChain(BaseModel):
    """Finite dispositor chain ending in a detected cycle."""

    body: str
    chain: tuple[str, ...]
    steps_to_cycle: int
    cycle: tuple[str, ...]


class MutualReception(BaseModel):
    """Two-planet dispositor cycle."""

    body_1: str
    body_2: str


class LunarPhase(BaseModel):
    """Natal Moon phase in Rudhyar's eight-phase system."""

    elongation: float = Field(..., ge=0.0, lt=360.0)
    phase_number: int = Field(..., ge=1, le=8)
    phase_name: str
    phase_start: float
    phase_end: float
    distance_from_previous_boundary: float
    distance_to_next_boundary: float
    degrees_after_exact_opposition: float | None = None


class DegreeFlag(BaseModel):
    """Special degree flags for a body, angle, or house cusp."""

    point: str
    point_type: Literal["body", "angle", "cusp"]
    longitude: float = Field(..., ge=0.0, lt=360.0)
    sign: str
    degree_in_sign: float = Field(..., ge=0.0, lt=30.0)
    is_zero_degree: bool
    is_anaretic: bool
    is_critical: bool
    critical_tradition: str | None = None
    matched_critical_degree: int | None = None


class InterceptionEntry(BaseModel):
    """Interception data shaped for JSON consumers."""

    sign: str
    house: int = Field(..., ge=1, le=12)
    remaining_arc: float | None = None
    threshold: float | None = None


class InterceptionSummary(BaseModel):
    """Separated full and near interceptions."""

    intercepted: tuple[InterceptionEntry, ...]
    near_intercepted: tuple[InterceptionEntry, ...]


class NatalStrength(BaseModel):
    """Complete natal strength and structure report."""

    dignity_system: Literal["traditional", "modern"]
    planets: dict[str, PlanetStrength]
    balance: ChartBalance
    dispositors: dict[str, DispositorChain]
    mutual_receptions: tuple[MutualReception, ...]
    lunar_phase: LunarPhase
    degree_flags: tuple[DegreeFlag, ...]
    interceptions: InterceptionSummary
    weak_note: str


class StrengthConfig(BaseModel):
    """Configurable strength rules."""

    dignity_system: Literal["traditional", "modern"] = "modern"
    planets: tuple[str, ...] = (
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
    )
    house_scores: dict[HouseType, int] = {
        HouseType.ANGULAR: 4,
        HouseType.SUCCEDENT: 2,
        HouseType.CADENT: 0,
    }
    retrograde_modifier: int = -2
    angle_conjunction_modifier: int = 3
    angle_conjunction_orb: float = Field(default=3.0, ge=0.0)
    strong_threshold: int = 5
    weak_threshold: int = 0
    balance_weights: dict[str, float] = {
        "sun": 3.0,
        "moon": 3.0,
        "asc": 3.0,
        "mc": 3.0,
        "mercury": 2.0,
        "venus": 2.0,
        "mars": 2.0,
        "jupiter": 2.0,
        "saturn": 2.0,
        "uranus": 1.0,
        "neptune": 1.0,
        "pluto": 1.0,
    }
    balance_tolerance: float = Field(default=0.10, ge=0.0)
    use_critical_degrees: bool = True
    critical_degree_orb: float = Field(default=1.0, ge=0.0)
    near_interception_threshold: float = Field(default=1.0, ge=0.0)
