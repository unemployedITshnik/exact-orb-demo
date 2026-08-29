"""Tests for the local place catalog."""

from __future__ import annotations

from pathlib import Path

import pytest

from exact_orb.birth import LocalPlaceCatalog, PlaceNotFound, ResolvedPlace


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "places.jsonl"


def test_from_file_loads_fixture_records() -> None:
    catalog = LocalPlaceCatalog.from_file(FIXTURE_PATH)

    assert len(catalog._places) == 6


async def test_lookup_known_place_returns_resolved_place() -> None:
    catalog = LocalPlaceCatalog.from_file(FIXTURE_PATH)

    result = await catalog.lookup("524901")

    assert isinstance(result, ResolvedPlace)
    assert result.canonical_name == "Москва"
    assert result.tz_id == "Europe/Moscow"
    assert result.latitude == pytest.approx(55.75222)


async def test_lookup_unknown_place_returns_place_not_found() -> None:
    catalog = LocalPlaceCatalog.from_file(FIXTURE_PATH)

    result = await catalog.lookup("999999999")

    assert isinstance(result, PlaceNotFound)
    assert result.place_id == "999999999"


def test_from_file_raises_value_error_with_line_number_for_bad_json(tmp_path: Path) -> None:
    path = tmp_path / "bad.jsonl"
    path.write_text(
        '{"place_id": "1", "name": "Good", "latitude": 0, "longitude": 0, "tz_id": "UTC"}\n'
        "{bad json}\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="line 2"):
        LocalPlaceCatalog.from_file(path)


async def test_from_file_skips_rows_without_tz_id(tmp_path: Path) -> None:
    path = tmp_path / "places.jsonl"
    path.write_text(
        "\n".join(
            [
                '{"place_id": "1", "name": "No Zone", "latitude": 0, "longitude": 0, "tz_id": ""}',
                '{"place_id": "2", "name": "With Zone", "latitude": 1, "longitude": 2, "tz_id": "UTC"}',
            ]
        ),
        encoding="utf-8",
    )

    catalog = LocalPlaceCatalog.from_file(path)

    assert isinstance(await catalog.lookup("1"), PlaceNotFound)
    assert isinstance(await catalog.lookup("2"), ResolvedPlace)


async def test_from_file_skips_empty_line_in_middle(tmp_path: Path) -> None:
    path = tmp_path / "places.jsonl"
    path.write_text(
        '{"place_id": "1", "name": "One", "latitude": 1, "longitude": 2, "tz_id": "UTC"}\n'
        "\n"
        '{"place_id": "2", "name": "Two", "latitude": 3, "longitude": 4, "tz_id": "UTC"}\n',
        encoding="utf-8",
    )

    catalog = LocalPlaceCatalog.from_file(path)

    assert isinstance(await catalog.lookup("1"), ResolvedPlace)
    assert isinstance(await catalog.lookup("2"), ResolvedPlace)


async def test_from_file_skips_empty_line_at_end(tmp_path: Path) -> None:
    path = tmp_path / "places.jsonl"
    path.write_text(
        '{"place_id": "1", "name": "One", "latitude": 1, "longitude": 2, "tz_id": "UTC"}\n\n',
        encoding="utf-8",
    )

    catalog = LocalPlaceCatalog.from_file(path)

    assert isinstance(await catalog.lookup("1"), ResolvedPlace)


def test_from_file_reports_line_and_missing_field_for_schema_error(
    tmp_path: Path,
) -> None:
    path = tmp_path / "places.jsonl"
    path.write_text(
        '{"place_id": "1", "latitude": 1, "longitude": 2, "tz_id": "UTC"}\n',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match=r"line 1.*name"):
        LocalPlaceCatalog.from_file(path)


def test_from_file_reports_line_for_invalid_field_type(tmp_path: Path) -> None:
    path = tmp_path / "places.jsonl"
    path.write_text(
        '{"place_id": "1", "name": "One", "latitude": "абв", "longitude": 2, "tz_id": "UTC"}\n',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="line 1"):
        LocalPlaceCatalog.from_file(path)


async def test_catalog_does_not_reread_file_after_loading(tmp_path: Path) -> None:
    path = tmp_path / "places.jsonl"
    path.write_text(
        '{"place_id": "2", "name": "With Zone", "latitude": 1, "longitude": 2, "tz_id": "UTC"}\n',
        encoding="utf-8",
    )
    catalog = LocalPlaceCatalog.from_file(path)
    path.unlink()

    result = await catalog.lookup("2")

    assert isinstance(result, ResolvedPlace)
    assert result.tz_id == "UTC"
