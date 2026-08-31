"""Shared calculation artifact fixtures that do not call Swiss Ephemeris."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from exact_orb.birth.types import ResolvedBirthData
from exact_orb.calculation.engine import CalculationResult
from exact_orb.calculation.keys import calculation_input_from, calculation_key
from exact_orb.calculation.spec import NatalChartSpec
from exact_orb.calculation.types import ChartArtifact
from exact_orb.config import EphemerisStatus
from exact_orb.engine.charts.natal import NatalChart
from exact_orb.engine.ephemeris.types import CalculationWarning
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
    chart = chart or raw_chart(chart_kind=spec.chart_kind, warnings=warnings or ())
    warnings = warnings if warnings is not None else chart.warnings
    key = key or calculation_key_for(spec, resolved, version=version)
    return ChartArtifact(
        calculation_key=key,
        calculation_version=version,
        spec=spec,
        chart_kind=spec.chart_kind,
        chart=chart,
        warnings=warnings,
    )


def calculation_result(
    *,
    chart_kind: str = "natal",
    warnings: tuple[CalculationWarning, ...] = (),
) -> CalculationResult:
    chart = raw_chart(chart_kind=chart_kind, warnings=warnings)
    return CalculationResult(chart_kind=chart_kind, chart=chart, warnings=chart.warnings)


def raw_chart(
    *,
    chart_kind: str = "natal",
    latitude: float = 55.7558,
    longitude: float = 37.6173,
    warnings: tuple[CalculationWarning, ...] = (),
) -> NatalChart:
    return NatalChart(
        chart_kind=chart_kind,
        datetime_utc=BASE_UTC,
        julian_day_ut=2448136.0,
        latitude=latitude,
        longitude=longitude,
        house_system="P",
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
        bodies={},
        cusps=None,
        angles=None,
        house_rulers=None,
        interceptions=None,
        aspects=None,
        configurations=None,
        strength=None,
        warnings=warnings,
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
