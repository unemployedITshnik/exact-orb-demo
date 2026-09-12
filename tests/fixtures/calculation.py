"""Shared calculation artifact fixtures that do not call Swiss Ephemeris."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from exact_orb.birth.types import BirthTimeDomain, ResolvedBirthData, UtcMinuteRange
from exact_orb.calculation.chart_contract import calculation_input_from_chart
from exact_orb.calculation.engine import CalculationResult
from exact_orb.calculation.keys import calculation_input_from, calculation_key
from exact_orb.calculation.spec import NatalChartSpec
from exact_orb.calculation.types import ChartArtifact
from exact_orb.config import EphemerisStatus
from exact_orb.domain import DEFAULT_INCLUDE_BY_CHART_KIND
from exact_orb.engine.charts.natal import NatalChart
from exact_orb.engine.charts.uncertainty import CosmogramTimeUncertainty
from exact_orb.engine.ephemeris.types import CalculationWarning
from exact_orb.engine.strength.types import (
    ChartBalance,
    HemisphereBalance,
    HouseTypeBalance,
    InterceptionSummary,
    LunarPhase,
    NatalStrength,
)
from exact_orb.run_context import RunContext


BASE_UTC = datetime(1990, 9, 2, 10, 30, 45, tzinfo=timezone.utc)
RUN_ID = UUID("11111111-1111-4111-8111-111111111111")
RUN_ID_B = UUID("22222222-2222-4222-8222-222222222222")
VERSION = "test-version-1"
OTHER_VERSION = "test-version-2"
EPHE_FILES = ("sepl_18.se1", "semo_18.se1", "seas_18.se1")
SENSITIVE_WARNING = "sensitive warning for 1990-09-02 55.7558 37.6173 Moscow"


def calculation_key_for(
    spec: NatalChartSpec,
    resolved: ResolvedBirthData,
    *,
    version: str = VERSION,
) -> str:
    return calculation_key(calculation_input_from(resolved), spec, version)


def artifact(
    *,
    spec: NatalChartSpec | None = None,
    resolved: ResolvedBirthData | None = None,
    chart: NatalChart | None = None,
    warnings: tuple[CalculationWarning, ...] | None = None,
    key: str | None = None,
    version: str = VERSION,
) -> ChartArtifact:
    spec = spec or chart_spec()
    resolved = resolved or resolved_birth_data()
    chart = chart or raw_chart(
        chart_kind=spec.chart_kind,
        utc_datetime=resolved.utc_datetime,
        latitude=resolved.latitude,
        longitude=resolved.longitude,
        house_system=spec.house_system,
        include=spec.include,
        warnings=warnings or (),
        birth_time_domain=resolved.birth_time_domain,
    )
    key = key or calculation_key(calculation_input_from_chart(chart), spec, version)
    return ChartArtifact(
        calculation_key=key,
        calculation_version=version,
        spec=spec,
        chart=chart,
    )


def calculation_result(
    *,
    chart_kind: str = "natal",
    warnings: tuple[CalculationWarning, ...] = (),
) -> CalculationResult:
    chart = raw_chart(chart_kind=chart_kind, warnings=warnings)
    return CalculationResult(chart=chart)


def raw_chart(
    *,
    chart_kind: str = "natal",
    utc_datetime: datetime = BASE_UTC,
    latitude: float = 55.7558,
    longitude: float = 37.6173,
    house_system: str = "P",
    include: tuple[str, ...] | None = None,
    warnings: tuple[CalculationWarning, ...] = (),
    birth_time_domain: BirthTimeDomain | None = None,
) -> NatalChart:
    included = frozenset(
        DEFAULT_INCLUDE_BY_CHART_KIND[chart_kind] if include is None else include
    )
    return NatalChart(
        chart_kind=chart_kind,
        datetime_utc=utc_datetime,
        julian_day_ut=2448136.0,
        latitude=latitude,
        longitude=longitude,
        house_system=house_system,
        ephemeris_flags=0,
        ephemeris=EphemerisStatus(
            path=r"C:\Users\KateUser\secret\ephe",
            source="argument",
            mode="files",
            required_files=EPHE_FILES,
            found_files=EPHE_FILES,
            missing_files=(),
        ),
        selena_method="true_perigee",
        bodies={} if "positions" in included else None,
        cusps=() if "houses" in included else None,
        angles={} if "houses" in included else None,
        house_rulers=() if "rulers" in included else None,
        interceptions=() if "rulers" in included else None,
        aspects=() if "aspects" in included else None,
        configurations=() if "configurations" in included else None,
        strength=_minimal_strength() if "strength" in included else None,
        time_uncertainty=(
            CosmogramTimeUncertainty(
                domain=birth_time_domain
                or BirthTimeDomain(
                    ranges=(UtcMinuteRange(first_utc=utc_datetime, count=1),)
                ),
                excluded_aspects=() if "aspects" in included else None,
            )
            if chart_kind == "cosmogram"
            else None
        ),
        warnings=warnings,
    )


def _minimal_strength() -> NatalStrength:
    return NatalStrength(
        dignity_system="modern",
        dispositor_system="modern",
        planets={},
        balance=ChartBalance(
            elements={},
            modalities={},
            hemispheres=HemisphereBalance(north=0, south=0, east=0, west=0),
            house_types=HouseTypeBalance(angular=0, succedent=0, cadent=0),
            total_weight=0,
            dominant_elements=(),
            deficient_elements=(),
            dominant_modalities=(),
            deficient_modalities=(),
        ),
        dispositors={},
        mutual_receptions=(),
        lunar_phase=LunarPhase(
            elongation=0,
            phase_number=1,
            phase_name="new_moon",
            phase_start=0,
            phase_end=45,
            distance_from_previous_boundary=0,
            distance_to_next_boundary=45,
            degrees_after_exact_opposition=None,
        ),
        degree_flags=(),
        interceptions=InterceptionSummary(intercepted=(), near_intercepted=()),
        weak_note="",
    )


def resolved_birth_data(
    *,
    latitude: float = 55.7558,
    longitude: float = 37.6173,
    canonical_place: str = "Moscow",
) -> ResolvedBirthData:
    return ResolvedBirthData(
        utc_datetime=BASE_UTC,
        latitude=latitude,
        longitude=longitude,
        tz_id="Europe/Moscow",
        utc_offset_seconds=10800,
        canonical_place=canonical_place,
        time_unknown=False,
        birth_time_domain=None,
        warnings=(),
    )


def chart_spec(
    *,
    chart_kind: str = "natal",
    house_system: str = "P",
) -> NatalChartSpec:
    return NatalChartSpec(chart_kind=chart_kind, house_system=house_system)


def run_context(run_id: UUID = RUN_ID) -> RunContext:
    return RunContext(run_id=run_id, started_at=BASE_UTC)


def calculation_warning(message: str = SENSITIVE_WARNING) -> CalculationWarning:
    return CalculationWarning(source="fixture", message=message, retflags=None)
