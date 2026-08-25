"""CLI rendering tests for grouped interpretation data."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import re

from exact_orb.engine.aspects import AspectConfig
from exact_orb.cli import BODY_ORDER, POINT_ORDER, format_human, parse_datetime_input
from exact_orb.engine.configurations import ConfigurationConfig
from exact_orb.engine.charts.natal import calculate_natal
from tests.fixtures.natal_1985 import REFERENCE


def test_aspect_groups_are_rendered_by_category() -> None:
    output = _reference_output()

    exact = _between(output, "  ТОЧНЫЕ", "  РАБОЧИЕ")
    working = _between(output, "  РАБОЧИЕ", "  ФОНОВЫЕ")
    background = _between(output, "  ФОНОВЫЕ", "КОНФИГУРАЦИИ")

    assert re.search(r"Солнце\s+△\s+Лилит\s+0°52'", exact)
    assert re.search(r"Луна\s+⚹\s+Юпитер\s+1°01'", working)
    assert re.search(r"Венера\s+☍\s+Юпитер\s+3°05'", background)
    assert "← тесный" not in output


def test_empty_aspect_groups_are_not_rendered() -> None:
    output = _reference_output(max_aspect_orb=0.9)
    aspects = _between(output, "АСПЕКТЫ", "КОНФИГУРАЦИИ")

    assert "ТОЧНЫЕ" in aspects
    assert "РАБОЧИЕ" not in aspects
    assert "ФОНОВЫЕ" not in aspects


def test_configuration_groups_and_roles_are_rendered_by_category() -> None:
    output = _reference_output()
    configurations = _between(output, "КОНФИГУРАЦИИ", "СИЛА И СТРУКТУРА")

    assert "ПЛОТНЫЕ (< 3°)" in configurations
    assert "УМЕРЕННЫЕ" not in configurations
    assert "РЫХЛЫЕ (> 5°)" in configurations
    assert len(re.findall(r"^\s{4}.+max", _between(configurations, "ПЛОТНЫЕ", "РЫХЛЫЕ"), re.MULTILINE)) == 3
    assert len(re.findall(r"^\s{4}.+max", _between(configurations, "РЫХЛЫЕ", ""), re.MULTILINE)) == 5
    assert re.search(r"Йод\s+max 1°41'\s+apex: Солнце · base: Юпитер, Луна", configurations)
    assert re.search(r"Трапеция\s+max 6°47'\s+opp: Хирон–Уран · base: Юпитер, Луна", configurations)


def test_special_degrees_are_rendered_as_separate_section() -> None:
    output = _reference_output()

    assert "ОСОБЫЕ ГРАДУСЫ" in output
    assert "← 0° кардинального" not in output
    assert re.search(r"Нептун\s+0°52' Козерог\s+0° кардинального · критический", output)
    assert re.search(r"Юпитер\s+8°41' Водолей\s+критический \(фикс\. 8–9°\)", output)
    assert re.search(r"Меркурий\s+22°05' Лев\s+критический \(фикс\. 21–22°\)", output)
    assert re.search(r"Сатурн\s+22°36' Скорпион\s+критический \(фикс\. 21–22°\)", output)
    assert re.search(r"куспид 5\s+29°44' Дева\s+анаретический", output)
    assert re.search(r"куспид 11\s+29°44' Рыбы\s+анаретический", output)


def test_reference_cli_output_matches_golden_file() -> None:
    golden = Path("tests/golden/natal_1985_human.txt").read_text(encoding="utf-8")

    assert _reference_output() + "\n" == golden


def _reference_output(*, max_aspect_orb: float = 7.0) -> str:
    chart = calculate_natal(
        datetime(1985, 9, 1, 20, 45, tzinfo=timezone.utc),
        REFERENCE["latitude"],
        REFERENCE["longitude"],
        chart_kind="natal",
        house_system=REFERENCE["house_system"],
        aspect_config=AspectConfig.natal(max_orb=7.0),
        configuration_config=ConfigurationConfig(configuration_max_orb=7.0),
    )
    return format_human(
        chart,
        parse_datetime_input("02.09.1985 00.45 gmt+4"),
        place="Москва",
        body_order=BODY_ORDER,
        point_order=POINT_ORDER,
        max_aspect_orb=max_aspect_orb,
        show_aspects=True,
        show_warnings=False,
    )


def _between(text: str, start: str, end: str) -> str:
    start_index = text.index(start)
    if not end:
        return text[start_index:]
    end_index = text.index(end, start_index + len(start))
    return text[start_index:end_index]
