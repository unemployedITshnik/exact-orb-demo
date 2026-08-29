"""Birth-data resolver orchestrating place and historical time resolution."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import date, datetime, time
from time import perf_counter

from exact_orb.birth.places import (
    PlaceCatalog,
    PlaceCatalogUnavailableError,
    PlaceNotFound,
)
from exact_orb.birth.types import BirthInput, ResolvedBirthData, ResolutionWarning
from exact_orb.birth.tz import (
    TzAmbiguous,
    TzNonexistent,
    TzOk,
    UnknownTimezoneError,
    local_date_exists,
    resolve_anomaly,
    resolve_historical_tz,
)
from exact_orb.outcomes import InputRequired, Issue, ResolutionUnavailable
from exact_orb.run_context import RunContext


LOGGER = logging.getLogger(__name__)
NOON = time(12, 0)


class BirthDataResolver:
    """Resolve structured birth input into calculation-ready facts."""

    def __init__(
        self,
        *,
        places: PlaceCatalog,
        min_birth_date: date,
        max_birth_date: date,
        today_provider: Callable[[], date] = date.today,
    ) -> None:
        if min_birth_date > max_birth_date:
            raise ValueError("min_birth_date must be <= max_birth_date")

        self._places = places
        self._min_birth_date = min_birth_date
        self._max_birth_date = max_birth_date
        self._today_provider = today_provider

    async def resolve(
        self,
        birth_input: BirthInput,
        *,
        run: RunContext | None = None,
    ) -> ResolvedBirthData | InputRequired | ResolutionUnavailable:
        started_at = perf_counter()
        _log_start(run)
        issues: list[Issue] = []

        effective_max = min(self._max_birth_date, self._today_provider())
        if not self._min_birth_date <= birth_input.birth_date <= effective_max:
            issues.append(
                Issue(
                    field="birth.date",
                    code="UNSUPPORTED",
                    constraints={
                        "min": self._min_birth_date.isoformat(),
                        "max": effective_max.isoformat(),
                    },
                )
            )

        try:
            place_resolution = await self._places.lookup(birth_input.place_id)
        except PlaceCatalogUnavailableError:
            outcome = ResolutionUnavailable(
                error_code="PLACE_CATALOG_UNAVAILABLE",
                retryable=True,
            )
            _log_resolution_unavailable(run, outcome, _elapsed_ms(started_at))
            return outcome

        if isinstance(place_resolution, PlaceNotFound):
            issues.append(Issue(field="birth.place", code="INVALID"))
        else:
            place = place_resolution

        if issues:
            outcome = InputRequired(issues=tuple(issues))
            _log_input_required(run, outcome, _elapsed_ms(started_at))
            return outcome

        time_unknown = birth_input.birth_time is None
        local_datetime = datetime.combine(
            birth_input.birth_date,
            birth_input.birth_time or NOON,
        )

        try:
            tz_resolution = resolve_historical_tz(local_datetime, place.tz_id)
        except UnknownTimezoneError:
            outcome = ResolutionUnavailable(error_code="UNKNOWN_TIMEZONE", retryable=False)
            _log_resolution_unavailable(run, outcome, _elapsed_ms(started_at))
            return outcome

        if isinstance(tz_resolution, TzNonexistent) and not local_date_exists(
            birth_input.birth_date,
            place.tz_id,
        ):
            outcome = InputRequired(issues=(Issue(field="birth.date", code="INVALID"),))
            _log_input_required(run, outcome, _elapsed_ms(started_at))
            return outcome

        if isinstance(tz_resolution, TzOk):
            tz_ok = tz_resolution
            warnings = list(tz_ok.warnings)
        elif isinstance(tz_resolution, TzNonexistent):
            if not time_unknown:
                outcome = InputRequired(
                    issues=(Issue(field="birth.time", code="INVALID"),)
                )
                _log_input_required(run, outcome, _elapsed_ms(started_at))
                return outcome
            tz_ok = resolve_anomaly(tz_resolution)
            warnings = [
                *tz_ok.warnings,
                _noon_anchor_adjusted_warning(tz_resolution),
            ]
        elif isinstance(tz_resolution, TzAmbiguous):
            if not time_unknown:
                outcome = InputRequired(
                    issues=(
                        Issue(
                            field="birth.time",
                            code="AMBIGUOUS",
                            candidates=tz_resolution.offsets,
                        ),
                    )
                )
                _log_input_required(run, outcome, _elapsed_ms(started_at))
                return outcome
            tz_ok = resolve_anomaly(tz_resolution)
            warnings = [
                *tz_ok.warnings,
                _noon_anchor_ambiguous_warning(tz_resolution),
            ]
        else:
            raise TypeError(f"Unexpected timezone resolution: {type(tz_resolution)!r}")

        resolved = ResolvedBirthData(
            utc_datetime=tz_ok.utc_datetime,
            latitude=place.latitude,
            longitude=place.longitude,
            tz_id=place.tz_id,
            utc_offset_seconds=tz_ok.utc_offset_seconds,
            canonical_place=place.canonical_name,
            time_unknown=time_unknown,
            warnings=tuple(warnings),
        )
        _log_resolved(run, resolved, _elapsed_ms(started_at))
        return resolved


def _run_id(run: RunContext | None) -> str:
    if run is None:
        return "-"
    return str(run.run_id)


def _elapsed_ms(started_at: float) -> float:
    return (perf_counter() - started_at) * 1000.0


def _log_start(run: RunContext | None) -> None:
    LOGGER.debug("birth_resolution run_id=%s event=start", _run_id(run))


def _log_resolved(
    run: RunContext | None,
    resolved: ResolvedBirthData,
    duration_ms: float,
) -> None:
    LOGGER.info(
        "birth_resolution run_id=%s outcome=resolved tz_id=%s duration_ms=%.3f",
        _run_id(run),
        resolved.tz_id,
        duration_ms,
    )


def _log_input_required(
    run: RunContext | None,
    outcome: InputRequired,
    duration_ms: float,
) -> None:
    LOGGER.info(
        "birth_resolution run_id=%s outcome=input_required issues=%s duration_ms=%.3f",
        _run_id(run),
        ",".join(f"{issue.field}:{issue.code}" for issue in outcome.issues),
        duration_ms,
    )


def _log_resolution_unavailable(
    run: RunContext | None,
    outcome: ResolutionUnavailable,
    duration_ms: float,
) -> None:
    LOGGER.info(
        "birth_resolution run_id=%s outcome=resolution_unavailable error_code=%s "
        "duration_ms=%.3f",
        _run_id(run),
        outcome.error_code,
        duration_ms,
    )


def _noon_anchor_adjusted_warning(anomaly: TzNonexistent) -> ResolutionWarning:
    return ResolutionWarning(
        source="time",
        code="noon_anchor_adjusted",
        message=(
            f"Noon anchor moved to {_format_local_time(anomaly.normalized)}, "
            f"the nominal noon did not exist in {anomaly.tz_id}"
        ),
    )


def _noon_anchor_ambiguous_warning(anomaly: TzAmbiguous) -> ResolutionWarning:
    offset0, offset1 = anomaly.offsets
    return ResolutionWarning(
        source="time",
        code="noon_anchor_ambiguous",
        message=(
            f"Noon anchor is ambiguous in {anomaly.tz_id}: "
            f"offsets {_format_utc_offset(offset0)} and {_format_utc_offset(offset1)}; "
            "the earlier instant (fold=0) was used"
        ),
    )


def _format_local_time(value: datetime) -> str:
    if value.second:
        return value.strftime("%H:%M:%S")
    return value.strftime("%H:%M")


def _format_utc_offset(offset_seconds: int) -> str:
    sign = "+" if offset_seconds >= 0 else "-"
    total_seconds = abs(offset_seconds)
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if seconds:
        return f"UTC{sign}{hours:02d}:{minutes:02d}:{seconds:02d}"
    return f"UTC{sign}{hours:02d}:{minutes:02d}"


__all__ = ["BirthDataResolver", "NOON"]
