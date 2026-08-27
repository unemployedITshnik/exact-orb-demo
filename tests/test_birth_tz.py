"""Tests for historical timezone resolution."""

from __future__ import annotations

import ast
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from exact_orb.birth.tz import (
    TzAmbiguous,
    TzNonexistent,
    TzOk,
    UnknownTimezoneError,
    local_date_exists,
    resolve_anomaly,
    resolve_historical_tz,
)


@pytest.mark.parametrize(
    ("local_datetime", "tz_id", "offset_seconds", "utc_datetime"),
    [
        (
            datetime(1990, 9, 2, 14, 30),
            "Europe/Moscow",
            14400,
            datetime(1990, 9, 2, 10, 30, tzinfo=timezone.utc),
        ),
        (
            datetime(1985, 6, 14, 4, 25),
            "Europe/Moscow",
            14400,
            datetime(1985, 6, 14, 0, 25, tzinfo=timezone.utc),
        ),
        (
            datetime(1987, 1, 6, 16, 25),
            "Europe/Moscow",
            10800,
            datetime(1987, 1, 6, 13, 25, tzinfo=timezone.utc),
        ),
        (
            datetime(1987, 7, 15, 16, 25),
            "Europe/Moscow",
            14400,
            datetime(1987, 7, 15, 12, 25, tzinfo=timezone.utc),
        ),
        (
            datetime(1991, 12, 20, 16, 25),
            "Europe/Moscow",
            7200,
            datetime(1991, 12, 20, 14, 25, tzinfo=timezone.utc),
        ),
        (
            datetime(2013, 12, 20, 16, 25),
            "Europe/Moscow",
            14400,
            datetime(2013, 12, 20, 12, 25, tzinfo=timezone.utc),
        ),
        (
            datetime(2016, 12, 20, 16, 25),
            "Europe/Moscow",
            10800,
            datetime(2016, 12, 20, 13, 25, tzinfo=timezone.utc),
        ),
        (
            datetime(2011, 12, 29, 12, 0),
            "Pacific/Apia",
            -36000,
            datetime(2011, 12, 29, 22, 0, tzinfo=timezone.utc),
        ),
    ],
)
def test_control_values_resolve_to_expected_utc_and_offset_seconds(
    local_datetime: datetime,
    tz_id: str,
    offset_seconds: int,
    utc_datetime: datetime,
) -> None:
    result = resolve_historical_tz(local_datetime, tz_id)

    assert isinstance(result, TzOk)
    assert result.utc_offset_seconds == offset_seconds
    assert result.utc_datetime == utc_datetime


@pytest.mark.parametrize(
    ("local_datetime", "tz_id", "offset_seconds"),
    [
        (datetime(1800, 1, 1, 12, 0), "Europe/Moscow", 9017),
        (datetime(1800, 1, 1, 12, 0), "Europe/Paris", 561),
        (datetime(1880, 1, 1, 12, 0), "Asia/Tokyo", 33539),
    ],
)
def test_fractional_minute_offsets_are_kept_as_exact_seconds(
    local_datetime: datetime,
    tz_id: str,
    offset_seconds: int,
) -> None:
    result = resolve_historical_tz(local_datetime, tz_id)

    assert isinstance(result, TzOk)
    assert result.utc_offset_seconds == offset_seconds


@pytest.mark.parametrize(
    ("local_datetime", "offset_seconds"),
    [
        (datetime(1991, 9, 28, 12, 0), 10800),
        (datetime(1991, 9, 29, 12, 0), 7200),
        (datetime(1992, 1, 18, 12, 0), 7200),
        (datetime(1992, 1, 19, 12, 0), 10800),
        (datetime(2011, 3, 26, 12, 0), 10800),
        (datetime(2011, 3, 27, 12, 0), 14400),
        (datetime(2014, 10, 25, 12, 0), 14400),
        (datetime(2014, 10, 26, 12, 0), 10800),
    ],
)
def test_moscow_offset_window_boundaries(local_datetime: datetime, offset_seconds: int) -> None:
    result = resolve_historical_tz(local_datetime, "Europe/Moscow")

    assert isinstance(result, TzOk)
    assert result.utc_offset_seconds == offset_seconds


def test_nonexistent_time_is_detected_before_ambiguity() -> None:
    """PEP 495 gives nonexistent times different fold offsets too, so checking
    ambiguity first would misclassify Moscow 2011-03-27 02:30 as ambiguous."""

    result = resolve_historical_tz(datetime(2011, 3, 27, 2, 30), "Europe/Moscow")

    assert isinstance(result, TzNonexistent)
    assert result.normalized == datetime(2011, 3, 27, 3, 30)


def test_ambiguous_time_reports_both_offsets_in_seconds() -> None:
    result = resolve_historical_tz(datetime(2014, 10, 26, 1, 30), "Europe/Moscow")

    assert isinstance(result, TzAmbiguous)
    assert result.offsets == (14400, 10800)


@pytest.mark.parametrize(
    ("tz_id", "local_datetime", "offsets"),
    [
        ("Pacific/Kanton", datetime(1937, 8, 30, 12, 0), (0, -43200)),
        ("Pacific/Kwajalein", datetime(1969, 9, 30, 12, 0), (39600, -43200)),
    ],
)
def test_all_known_ambiguous_noons_are_reported(
    tz_id: str,
    local_datetime: datetime,
    offsets: tuple[int, int],
) -> None:
    result = resolve_historical_tz(local_datetime, tz_id)

    assert isinstance(result, TzAmbiguous)
    assert result.offsets == offsets


@pytest.mark.parametrize(
    ("tz_id", "local_datetime", "normalized"),
    [
        ("Africa/Casablanca", datetime(1967, 6, 3, 12, 0), datetime(1967, 6, 3, 13, 0)),
        ("Africa/Ceuta", datetime(1967, 6, 3, 12, 0), datetime(1967, 6, 3, 13, 0)),
        ("Africa/Khartoum", datetime(2000, 1, 15, 12, 0), datetime(2000, 1, 15, 13, 0)),
        ("America/Havana", datetime(1925, 7, 19, 12, 0), datetime(1925, 7, 19, 12, 29, 36)),
        ("Pacific/Apia", datetime(2011, 12, 30, 12, 0), datetime(2011, 12, 31, 12, 0)),
        ("Pacific/Kiritimati", datetime(1994, 12, 31, 12, 0), datetime(1995, 1, 1, 12, 0)),
        ("Pacific/Kwajalein", datetime(1993, 8, 21, 12, 0), datetime(1993, 8, 22, 12, 0)),
    ],
)
def test_representative_nonexistent_noons_are_normalized(
    tz_id: str,
    local_datetime: datetime,
    normalized: datetime,
) -> None:
    result = resolve_historical_tz(local_datetime, tz_id)

    assert isinstance(result, TzNonexistent)
    assert result.normalized == normalized
    assert result.normalized.tzinfo is None


def test_pre_1970_ok_result_warns_about_unverified_offset() -> None:
    result = resolve_historical_tz(datetime(1955, 3, 10, 12, 0), "Europe/Moscow")

    assert isinstance(result, TzOk)
    assert _warning_codes(result) == {"pre_1970_offset_unverified"}


def test_post_1970_ok_result_has_no_pre_1970_warning() -> None:
    result = resolve_historical_tz(datetime(1990, 9, 2, 14, 30), "Europe/Moscow")

    assert isinstance(result, TzOk)
    assert "pre_1970_offset_unverified" not in _warning_codes(result)


@pytest.mark.parametrize(
    ("local_datetime", "has_warning"),
    [
        (datetime(1969, 12, 31, 23, 59), True),
        (datetime(1970, 1, 1, 2, 0), True),
        (datetime(1970, 1, 1, 4, 0), False),
    ],
)
def test_pre_1970_warning_boundary_uses_utc_epoch(
    local_datetime: datetime,
    has_warning: bool,
) -> None:
    result = resolve_historical_tz(local_datetime, "Europe/Moscow")

    assert isinstance(result, TzOk)
    assert ("pre_1970_offset_unverified" in _warning_codes(result)) is has_warning


def test_aware_datetime_is_rejected() -> None:
    with pytest.raises(ValueError, match="local_datetime must be naive"):
        resolve_historical_tz(
            datetime(1990, 1, 1, 12, 0, tzinfo=timezone.utc),
            "Europe/Moscow",
        )


def test_unknown_timezone_raises_domain_error() -> None:
    with pytest.raises(UnknownTimezoneError):
        resolve_historical_tz(datetime(1990, 1, 1, 12, 0), "Nowhere/Fake")


def test_resolve_anomaly_handles_nonexistent_by_normalized_local_time() -> None:
    anomaly = resolve_historical_tz(datetime(1967, 6, 3, 12, 0), "Africa/Casablanca")
    assert isinstance(anomaly, TzNonexistent)

    result = resolve_anomaly(anomaly)

    assert result == TzOk(
        utc_datetime=datetime(1967, 6, 3, 12, 0, tzinfo=timezone.utc),
        utc_offset_seconds=3600,
        warnings=result.warnings,
    )


def test_resolve_anomaly_handles_ambiguous_with_fold_zero() -> None:
    anomaly = resolve_historical_tz(datetime(1969, 9, 30, 12, 0), "Pacific/Kwajalein")
    assert isinstance(anomaly, TzAmbiguous)

    result = resolve_anomaly(anomaly)

    assert result.utc_datetime == datetime(1969, 9, 30, 1, 0, tzinfo=timezone.utc)
    assert result.utc_offset_seconds == 39600


def test_time_functions_do_not_emit_noon_anchor_adjusted_warning() -> None:
    ok = resolve_historical_tz(datetime(1990, 9, 2, 14, 30), "Europe/Moscow")
    anomaly = resolve_historical_tz(datetime(1969, 9, 30, 12, 0), "Pacific/Kwajalein")
    assert isinstance(ok, TzOk)
    assert isinstance(anomaly, TzAmbiguous)

    resolved_anomaly = resolve_anomaly(anomaly)

    assert "noon_anchor_adjusted" not in _warning_codes(ok)
    assert "noon_anchor_adjusted" not in _warning_codes(resolved_anomaly)
    assert "noon_anchor_ambiguous" not in _warning_codes(ok)
    assert "noon_anchor_ambiguous" not in _warning_codes(resolved_anomaly)


@pytest.mark.parametrize(
    ("local_date", "tz_id", "exists"),
    [
        (date(2011, 12, 30), "Pacific/Apia", False),
        (date(2011, 12, 30), "Pacific/Fakaofo", False),
        (date(1994, 12, 31), "Pacific/Kiritimati", False),
        (date(1994, 12, 31), "Pacific/Kanton", False),
        (date(1993, 8, 21), "Pacific/Kwajalein", False),
        (date(1967, 6, 3), "Africa/Casablanca", True),
        (date(1920, 2, 14), "Africa/Algiers", True),
        (date(1927, 4, 9), "Africa/Ceuta", True),
        (date(1990, 9, 2), "Europe/Moscow", True),
    ],
)
def test_local_date_exists_detects_skipped_calendar_days(
    local_date: date,
    tz_id: str,
    exists: bool,
) -> None:
    result = local_date_exists(local_date, tz_id)

    assert result is exists


def test_local_date_exists_algiers_midnight_regression() -> None:
    """Africa/Algiers 1920-02-14 skips 23:00 to next midnight, but the
    calendar day exists; comparing normalized.date() would accuse the date."""

    assert local_date_exists(date(1920, 2, 14), "Africa/Algiers") is True


def test_local_date_exists_ceuta_midnight_regression() -> None:
    """Africa/Ceuta 1927-04-09 skips 23:00 to next midnight, but the calendar
    day exists; comparing normalized.date() would be a false positive."""

    assert local_date_exists(date(1927, 4, 9), "Africa/Ceuta") is True


@pytest.mark.parametrize(
    ("local_datetime", "tz_id"),
    [
        (datetime(1967, 6, 3, 12, 0), "Africa/Casablanca"),
        (datetime(1969, 9, 30, 12, 0), "Pacific/Kwajalein"),
        (datetime(1993, 8, 21, 12, 0), "Pacific/Kwajalein"),
    ],
)
def test_resolve_anomaly_does_not_raise_unknown_timezone_for_known_zones(
    local_datetime: datetime,
    tz_id: str,
) -> None:
    anomaly = resolve_historical_tz(local_datetime, tz_id)
    assert isinstance(anomaly, TzNonexistent | TzAmbiguous)

    result = resolve_anomaly(anomaly)

    assert isinstance(result, TzOk)


def test_tz_module_does_not_import_resolver() -> None:
    tree = ast.parse(Path("src/exact_orb/birth/tz.py").read_text(encoding="utf-8"))
    imports = _imported_modules(tree)

    assert "exact_orb.birth.resolver" not in imports


def _warning_codes(result: TzOk) -> set[str]:
    return {warning.code for warning in result.warnings}


def _imported_modules(tree: ast.AST) -> set[str]:
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imports.add(node.module)
    return imports
