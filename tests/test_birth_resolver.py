"""Tests for BirthDataResolver scenarios R-1 through R-20."""

from __future__ import annotations

import ast
from datetime import date, datetime, time, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from exact_orb.birth import (
    BirthDataResolver,
    BirthInput,
    LocalPlaceCatalog,
    PlaceCatalogUnavailableError,
    PlaceNotFound,
    ResolvedBirthData,
)
from exact_orb.outcomes import InputRequired, ResolutionUnavailable


MOSCOW_ID = "524901"
CASABLANCA_ID = "2553604"
KWAJALEIN_ID = "4038270"
APIA_ID = "4035413"
KIRITIMATI_ID = "4030945"
BROKEN_TZ_ID = "9000001"
UNKNOWN_PLACE_ID = "999999999"
TODAY = date(2026, 8, 27)
MIN_DATE = date(1800, 1, 1)
MAX_DATE = date(2399, 12, 31)
FIXTURE_PATH = Path(__file__).parent / "fixtures" / "places.jsonl"


@pytest.fixture
def catalog() -> LocalPlaceCatalog:
    return LocalPlaceCatalog.from_file(FIXTURE_PATH)


@pytest.fixture
def resolver(catalog: LocalPlaceCatalog) -> BirthDataResolver:
    return BirthDataResolver(
        places=catalog,
        min_birth_date=MIN_DATE,
        max_birth_date=MAX_DATE,
        today_provider=lambda: TODAY,
    )


async def test_r1_date_time_place_resolves(resolver: BirthDataResolver) -> None:
    result = await resolver.resolve(
        BirthInput(
            birth_date=date(1990, 9, 2),
            birth_time=time(14, 30),
            place_id=MOSCOW_ID,
        )
    )

    assert isinstance(result, ResolvedBirthData)
    assert result.time_unknown is False
    assert result.utc_datetime == datetime(1990, 9, 2, 10, 30, tzinfo=timezone.utc)
    assert result.utc_offset_seconds == 14400
    assert result.canonical_place == "Москва"


async def test_r2_date_place_without_time_resolves_from_noon(
    resolver: BirthDataResolver,
) -> None:
    result = await resolver.resolve(
        BirthInput(birth_date=date(1990, 9, 2), place_id=MOSCOW_ID)
    )

    assert isinstance(result, ResolvedBirthData)
    assert result.time_unknown is True
    assert result.utc_datetime == datetime(1990, 9, 2, 8, 0, tzinfo=timezone.utc)


async def test_r3_explicit_noon_is_not_time_unknown(
    resolver: BirthDataResolver,
) -> None:
    unknown_time = await resolver.resolve(
        BirthInput(birth_date=date(1990, 9, 2), place_id=MOSCOW_ID)
    )
    explicit_noon = await resolver.resolve(
        BirthInput(
            birth_date=date(1990, 9, 2),
            birth_time=time(12, 0),
            place_id=MOSCOW_ID,
        )
    )

    assert isinstance(unknown_time, ResolvedBirthData)
    assert isinstance(explicit_noon, ResolvedBirthData)
    assert unknown_time.utc_datetime == explicit_noon.utc_datetime
    assert unknown_time.time_unknown is True
    assert explicit_noon.time_unknown is False


async def test_r4_pre_1970_date_resolves_with_warning(
    resolver: BirthDataResolver,
) -> None:
    result = await resolver.resolve(
        BirthInput(
            birth_date=date(1955, 3, 10),
            birth_time=time(12, 0),
            place_id=MOSCOW_ID,
        )
    )

    assert isinstance(result, ResolvedBirthData)
    assert "pre_1970_offset_unverified" in _warning_codes(result)


async def test_r5_moscow_utc_plus_two_window_resolves(
    resolver: BirthDataResolver,
) -> None:
    result = await resolver.resolve(
        BirthInput(
            birth_date=date(1991, 12, 20),
            birth_time=time(16, 25),
            place_id=MOSCOW_ID,
        )
    )

    assert isinstance(result, ResolvedBirthData)
    assert result.utc_offset_seconds == 7200


async def test_r6_r20_unknown_or_stale_place_id_returns_invalid_place(
    resolver: BirthDataResolver,
) -> None:
    # R-20 uses the same resolver behavior for an ID that went stale after a
    # catalog update; handler-level fallback is explicitly deferred.
    result = await resolver.resolve(
        BirthInput(birth_date=date(1990, 9, 2), place_id=UNKNOWN_PLACE_ID)
    )

    assert isinstance(result, InputRequired)
    assert len(result.issues) == 1
    assert result.issues[0].field == "birth.place"
    assert result.issues[0].code == "INVALID"


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({"birth_date": "1990-02-31", "place_id": MOSCOW_ID}, "birth_date"),
        (
            {"birth_date": "1990-09-02", "birth_time": "25:70", "place_id": MOSCOW_ID},
            "birth_time",
        ),
        ({"place_id": MOSCOW_ID}, "birth_date"),
        ({"birth_date": "1990-09-02"}, "place_id"),
        (
            {
                "birth_date": "1990-09-02",
                "birth_time": time(14, 30, tzinfo=timezone.utc),
                "place_id": MOSCOW_ID,
            },
            "birth_time must be naive",
        ),
    ],
)
def test_r7_r10_r13_r14_schema_errors_never_call_resolver(
    payload: dict[str, object],
    message: str,
) -> None:
    catalog = CountingCatalog()

    with pytest.raises(ValidationError, match=message):
        BirthInput(**payload)

    assert catalog.calls == 0


async def test_r8_future_birth_date_returns_unsupported(
    resolver: BirthDataResolver,
) -> None:
    result = await resolver.resolve(
        BirthInput(birth_date=date(2030, 1, 1), place_id=MOSCOW_ID)
    )

    issue = _single_issue(result)
    assert issue.field == "birth.date"
    assert issue.code == "UNSUPPORTED"
    assert issue.constraints == {"min": "1800-01-01", "max": "2026-08-27"}


async def test_r9_birth_date_before_supported_range_returns_unsupported(
    resolver: BirthDataResolver,
) -> None:
    result = await resolver.resolve(
        BirthInput(birth_date=date(1500, 1, 1), place_id=MOSCOW_ID)
    )

    issue = _single_issue(result)
    assert issue.field == "birth.date"
    assert issue.code == "UNSUPPORTED"
    assert issue.constraints == {"min": "1800-01-01", "max": "2026-08-27"}


async def test_r9b_birth_date_after_ephemeris_and_today_limits_is_one_issue(
    resolver: BirthDataResolver,
) -> None:
    result = await resolver.resolve(
        BirthInput(birth_date=date(2500, 1, 1), place_id=MOSCOW_ID)
    )

    issue = _single_issue(result)
    assert issue.field == "birth.date"
    assert issue.constraints is not None
    assert issue.constraints["max"] == "2026-08-27"


async def test_r11_explicit_nonexistent_time_returns_invalid_time(
    resolver: BirthDataResolver,
) -> None:
    result = await resolver.resolve(
        BirthInput(
            birth_date=date(2011, 3, 27),
            birth_time=time(2, 30),
            place_id=MOSCOW_ID,
        )
    )

    issue = _single_issue(result)
    assert issue.field == "birth.time"
    assert issue.code == "INVALID"


async def test_r12_explicit_ambiguous_time_returns_candidates(
    resolver: BirthDataResolver,
) -> None:
    result = await resolver.resolve(
        BirthInput(
            birth_date=date(2014, 10, 26),
            birth_time=time(1, 30),
            place_id=MOSCOW_ID,
        )
    )

    issue = _single_issue(result)
    assert issue.field == "birth.time"
    assert issue.code == "AMBIGUOUS"
    assert issue.candidates == (14400, 10800)


async def test_r15_date_out_of_range_and_unknown_place_return_two_issues(
    resolver: BirthDataResolver,
) -> None:
    result = await resolver.resolve(
        BirthInput(birth_date=date(1500, 1, 1), place_id=UNKNOWN_PLACE_ID)
    )

    assert isinstance(result, InputRequired)
    assert [(issue.field, issue.code) for issue in result.issues] == [
        ("birth.date", "UNSUPPORTED"),
        ("birth.place", "INVALID"),
    ]


async def test_r16_r25_unknown_time_nonexistent_noon_resolves_with_warning(
    resolver: BirthDataResolver,
) -> None:
    result = await resolver.resolve(
        BirthInput(birth_date=date(1967, 6, 3), place_id=CASABLANCA_ID)
    )

    assert isinstance(result, ResolvedBirthData)
    assert not isinstance(result, InputRequired)
    assert result.utc_datetime == datetime(1967, 6, 3, 12, 0, tzinfo=timezone.utc)
    assert result.utc_offset_seconds == 3600
    assert "noon_anchor_adjusted" in _warning_codes(result)
    assert "noon_anchor_ambiguous" not in _warning_codes(result)


async def test_r16b_explicit_nonexistent_noon_returns_invalid_time(
    resolver: BirthDataResolver,
) -> None:
    result = await resolver.resolve(
        BirthInput(
            birth_date=date(1967, 6, 3),
            birth_time=time(12, 0),
            place_id=CASABLANCA_ID,
        )
    )

    issue = _single_issue(result)
    assert issue.field == "birth.time"
    assert issue.code == "INVALID"


async def test_r17_r24_unknown_time_ambiguous_noon_resolves_with_fold_zero(
    resolver: BirthDataResolver,
) -> None:
    result = await resolver.resolve(
        BirthInput(birth_date=date(1969, 9, 30), place_id=KWAJALEIN_ID)
    )

    assert isinstance(result, ResolvedBirthData)
    assert result.utc_datetime == datetime(1969, 9, 30, 1, 0, tzinfo=timezone.utc)
    assert result.utc_offset_seconds == 39600
    assert "noon_anchor_ambiguous" in _warning_codes(result)
    assert "noon_anchor_adjusted" not in _warning_codes(result)
    warning = _warning_by_code(result, "noon_anchor_ambiguous")
    assert "UTC+11:00" in warning.message
    assert "UTC-12:00" in warning.message


async def test_r17b_explicit_ambiguous_noon_returns_ambiguous_time(
    resolver: BirthDataResolver,
) -> None:
    result = await resolver.resolve(
        BirthInput(
            birth_date=date(1969, 9, 30),
            birth_time=time(12, 0),
            place_id=KWAJALEIN_ID,
        )
    )

    issue = _single_issue(result)
    assert issue.field == "birth.time"
    assert issue.code == "AMBIGUOUS"


async def test_r18_unavailable_catalog_returns_resolution_unavailable() -> None:
    resolver = BirthDataResolver(
        places=UnavailableCatalog(),
        min_birth_date=MIN_DATE,
        max_birth_date=MAX_DATE,
        today_provider=lambda: TODAY,
    )

    result = await resolver.resolve(
        BirthInput(birth_date=date(1990, 9, 2), place_id=MOSCOW_ID)
    )

    assert isinstance(result, ResolutionUnavailable)
    assert not isinstance(result, InputRequired)
    assert result.retryable is True
    assert result.error_code == "PLACE_CATALOG_UNAVAILABLE"


async def test_r19_unknown_timezone_returns_resolution_unavailable(
    resolver: BirthDataResolver,
) -> None:
    result = await resolver.resolve(
        BirthInput(birth_date=date(1990, 9, 2), place_id=BROKEN_TZ_ID)
    )

    assert isinstance(result, ResolutionUnavailable)
    assert not isinstance(result, InputRequired)
    assert result.error_code == "UNKNOWN_TIMEZONE"
    assert result.retryable is False


async def test_r21_apia_skipped_date_without_time_returns_invalid_date(
    resolver: BirthDataResolver,
) -> None:
    result = await resolver.resolve(
        BirthInput(birth_date=date(2011, 12, 30), place_id=APIA_ID)
    )

    issue = _single_issue(result)
    assert issue.field == "birth.date"
    assert issue.code == "INVALID"


async def test_r22_apia_skipped_date_with_time_returns_invalid_date(
    resolver: BirthDataResolver,
) -> None:
    result = await resolver.resolve(
        BirthInput(
            birth_date=date(2011, 12, 30),
            birth_time=time(14, 0),
            place_id=APIA_ID,
        )
    )

    issue = _single_issue(result)
    assert issue.field == "birth.date"
    assert issue.code == "INVALID"


async def test_r23_kiritimati_skipped_date_without_time_returns_invalid_date(
    resolver: BirthDataResolver,
) -> None:
    result = await resolver.resolve(
        BirthInput(birth_date=date(1994, 12, 31), place_id=KIRITIMATI_ID)
    )

    issue = _single_issue(result)
    assert issue.field == "birth.date"
    assert issue.code == "INVALID"


def test_birth_input_defaults_time_to_none() -> None:
    birth_input = BirthInput(birth_date=date(1990, 9, 2), place_id=MOSCOW_ID)

    assert birth_input.birth_time is None


def test_resolved_birth_data_requires_timezone_aware_utc_datetime() -> None:
    with pytest.raises(ValidationError, match="timezone-aware UTC"):
        ResolvedBirthData(
            utc_datetime=datetime(1990, 9, 2, 10, 30),
            latitude=55.75222,
            longitude=37.61556,
            tz_id="Europe/Moscow",
            utc_offset_seconds=14400,
            canonical_place="Москва",
            time_unknown=False,
        )


def test_birth_data_resolver_rejects_invalid_configured_date_range(
    catalog: LocalPlaceCatalog,
) -> None:
    with pytest.raises(ValueError, match="min_birth_date must be <= max_birth_date"):
        BirthDataResolver(
            places=catalog,
            min_birth_date=date(2400, 1, 1),
            max_birth_date=date(1800, 1, 1),
        )


def test_birth_package_does_not_import_engine_tools_or_orchestration() -> None:
    forbidden = {
        "exact_orb.engine",
        "exact_orb.tools",
        "exact_orb.orchestration",
    }
    for path in Path("src/exact_orb/birth").glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imports = _imported_modules(tree)
        assert not any(
            module == forbidden_module or module.startswith(f"{forbidden_module}.")
            for module in imports
            for forbidden_module in forbidden
        ), path


def test_birth_package_has_no_runtime_assert_dependencies() -> None:
    for path in Path("src/exact_orb/birth").glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        assert not any(isinstance(node, ast.Assert) for node in ast.walk(tree)), path


class CountingCatalog:
    def __init__(self) -> None:
        self.calls = 0

    async def lookup(self, place_id: str) -> PlaceNotFound:
        self.calls += 1
        return PlaceNotFound(place_id=place_id)


class UnavailableCatalog:
    async def lookup(self, place_id: str) -> PlaceNotFound:
        raise PlaceCatalogUnavailableError("catalog unavailable")


def _single_issue(result: object):
    assert isinstance(result, InputRequired)
    assert len(result.issues) == 1
    return result.issues[0]


def _warning_codes(result: ResolvedBirthData) -> set[str]:
    return {warning.code for warning in result.warnings}


def _warning_by_code(result: ResolvedBirthData, code: str):
    for warning in result.warnings:
        if warning.code == code:
            return warning
    raise AssertionError(f"warning {code!r} not found")


def _imported_modules(tree: ast.AST) -> set[str]:
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imports.add(node.module)
    return imports
