"""Command line interface for natal chart calculation."""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from time import perf_counter
from typing import Iterable, Sequence

from .engine.aspects import Aspect as CalculatedAspect
from .engine.aspects import AspectCategory, AspectConfig
from .engine.charts.natal import NatalChart, calculate_natal
from .engine.configurations import Configuration as CalculatedConfiguration
from .engine.configurations import ConfigurationCategory
from .engine.ephemeris.types import RulershipScheme, ZodiacPosition
from .config import configure_ephemeris
from .logging_setup import init_logging


LOGGER = logging.getLogger(__name__)
DEFAULT_HOUSE_SYSTEM = "P"
INPUT_PATTERN = re.compile(
    r"^\s*"
    r"(?P<day>\d{1,2})\.(?P<month>\d{1,2})\.(?P<year>\d{4})"
    r"\s+"
    r"(?P<hour>\d{1,2})[\.:](?P<minute>\d{2})"
    r"\s+"
    r"gmt(?P<sign>[+-])(?P<tzhour>\d{1,2})(?:(?:\:|\.)(?P<tzminute>\d{2}))?"
    r"\s*$",
    re.IGNORECASE,
)

MONTHS_RU = (
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
)

SIGNS_RU = {
    "Aries": "Овен",
    "Taurus": "Телец",
    "Gemini": "Близнецы",
    "Cancer": "Рак",
    "Leo": "Лев",
    "Virgo": "Дева",
    "Libra": "Весы",
    "Scorpio": "Скорпион",
    "Sagittarius": "Стрелец",
    "Capricorn": "Козерог",
    "Aquarius": "Водолей",
    "Pisces": "Рыбы",
}

POINTS_RU = {
    "sun": "Солнце",
    "moon": "Луна",
    "mercury": "Меркурий",
    "venus": "Венера",
    "mars": "Марс",
    "jupiter": "Юпитер",
    "saturn": "Сатурн",
    "uranus": "Уран",
    "neptune": "Нептун",
    "pluto": "Плутон",
    "chiron": "Хирон",
    "north_node": "Сев. узел",
    "true_node": "Сев. узел",
    "south_node": "Юж. узел",
    "lilith": "Лилит",
    "mean_apog": "Лилит",
    "pars": "Фортуна",
    "pars_fortune": "Фортуна",
    "selena": "Селена",
    "asc": "ASC",
    "mc": "MC",
}

HOUSE_SYSTEMS_RU = {
    "P": "Плацидус",
    "K": "Кох",
    "O": "Порфирий",
    "R": "Региомонтан",
    "C": "Кампано",
    "E": "Равнодомная",
    "W": "Whole Sign",
}

BODY_ORDER = (
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
)

POINT_ORDER = ("true_node", "south_node", "mean_apog", "pars_fortune", "selena")
CARDINAL_SIGN_INDICES = {0, 3, 6, 9}
SIGN_INDICES = {
    "Aries": 0,
    "Taurus": 1,
    "Gemini": 2,
    "Cancer": 3,
    "Leo": 4,
    "Virgo": 5,
    "Libra": 6,
    "Scorpio": 7,
    "Sagittarius": 8,
    "Capricorn": 9,
    "Aquarius": 10,
    "Pisces": 11,
}
MODALITY_BY_SIGN_INDEX = ("cardinal", "fixed", "mutable")


@dataclass(frozen=True)
class ParsedDateTime:
    """Input datetime with the original fixed GMT offset."""

    local_datetime: datetime

    @property
    def utc_datetime(self) -> datetime:
        return self.local_datetime.astimezone(timezone.utc)

    @property
    def offset_label(self) -> str:
        return format_utc_offset(self.local_datetime)


ASPECT_SYMBOLS = {
    "conjunction": "☌",
    "semisextile": "⚺",
    "sextile": "⚹",
    "square": "□",
    "trine": "△",
    "quincunx": "⚻",
    "opposition": "☍",
}

ASPECT_CATEGORY_HEADERS = {
    AspectCategory.EXACT: "ТОЧНЫЕ (орбис < 1°)",
    AspectCategory.WORKING: "РАБОЧИЕ (1–3°)",
    AspectCategory.BACKGROUND: "ФОНОВЫЕ (3–{max_orb:g}°)",
}

CONFIGURATION_TYPES_RU = {
    "t_square": "Тау-квадрат",
    "yod": "Йод",
    "bisextile": "Бисекстиль",
    "grand_cross": "Большой крест",
    "grand_trine": "Большой тригон",
    "trapeze": "Трапеция",
}

CONFIGURATION_CATEGORY_HEADERS = {
    ConfigurationCategory.TIGHT: "ПЛОТНЫЕ (< 3°)",
    ConfigurationCategory.MODERATE: "УМЕРЕННЫЕ (3–5°)",
    ConfigurationCategory.LOOSE: "РЫХЛЫЕ (> 5°)",
}

CONFIGURATION_CATEGORY_ORDER = (
    ConfigurationCategory.TIGHT,
    ConfigurationCategory.MODERATE,
    ConfigurationCategory.LOOSE,
)

ASPECT_CATEGORY_ORDER = (
    AspectCategory.EXACT,
    AspectCategory.WORKING,
    AspectCategory.BACKGROUND,
)

DIGNITY_RU = {
    "domicile": "обитель",
    "exaltation": "экз.",
    "detriment": "изгн.",
    "fall": "пад.",
    "peregrine": "перегрин",
}

HOUSE_TYPES_RU = {
    "angular": "угл.",
    "succedent": "посл.",
    "cadent": "пад.",
}

STRENGTH_CATEGORIES_RU = {
    "strong": "сильная",
    "moderate": "умеренная",
    "weak": "слабая",
}

BALANCE_NAMES_RU = {
    "fire": "огонь",
    "earth": "земля",
    "air": "воздух",
    "water": "вода",
    "cardinal": "кард.",
    "fixed": "фикс.",
    "mutable": "мутаб.",
}


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entrypoint."""

    _configure_output_encoding()
    argv_for_log = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        ephemeris_status = configure_ephemeris(args.ephe_path)
    except Exception as exc:
        sys.stderr.write("exact-orb: %s\n" % exc)
        return 2
    init_logging(ephemeris_status=ephemeris_status, house_system_default=DEFAULT_HOUSE_SYSTEM)
    started_at = perf_counter()

    input_text = " ".join(args.input).strip()
    if not input_text and not sys.stdin.isatty():
        input_text = sys.stdin.read().strip()
    LOGGER.debug("cli_args argv=%s args=%s raw_input=%r", argv_for_log, _args_for_log(args), input_text)
    if not input_text:
        LOGGER.error(
            "cli_call status=error input=%r duration_ms=%.3f error=%s",
            input_text,
            _elapsed_ms(started_at),
            "input is required: dd.mm.yyyy hh.mm gmt+x",
        )
        parser.error("input is required: dd.mm.yyyy hh.mm gmt+x")

    try:
        parsed = parse_datetime_input(input_text)
        LOGGER.debug(
            "user_input raw=%r datetime_local=%s datetime_utc=%s place=%s lat=%.6f lon=%.6f "
            "house_system=%s rulership=%s format=%s max_aspect_orb=%.3f",
            input_text,
            parsed.local_datetime.isoformat(),
            parsed.utc_datetime.isoformat(),
            args.place,
            args.lat,
            args.lon,
            args.house_system,
            args.rulership,
            args.format,
            args.max_aspect_orb,
        )
        include = {"positions", "houses", "rulers"} if args.no_aspects else None
        chart = calculate_natal(
            parsed.local_datetime,
            args.lat,
            args.lon,
            chart_kind="natal",
            house_system=args.house_system,
            rulership=args.rulership,
            ephemeris_path=args.ephe_path,
            include=include,
            aspect_config=AspectConfig.natal(max_orb=args.max_aspect_orb),
        )
        LOGGER.debug("natal_chart summary=%s", _chart_summary(chart))
    except Exception as exc:
        LOGGER.error(
            "cli_call status=error input=%r duration_ms=%.3f error=%s",
            input_text,
            _elapsed_ms(started_at),
            exc,
            exc_info=True,
        )
        parser.exit(2, "exact-orb: %s\n" % exc)

    if args.format == "json":
        output_text = format_json(chart, parsed, args.place, include_warnings=not args.no_warnings)
    else:
        output_text = format_human(
            chart,
            parsed,
            place=args.place,
            body_order=BODY_ORDER,
            point_order=() if args.planets_only else POINT_ORDER,
            max_aspect_orb=args.max_aspect_orb,
            show_aspects=not args.no_aspects,
            show_warnings=not args.no_warnings,
        )
    LOGGER.debug("cli_response format=%s text=%s", args.format, output_text)
    LOGGER.info(
        "cli_call status=ok input=%r datetime_local=%s datetime_utc=%s place=%s lat=%.6f lon=%.6f "
        "house_system=%s duration_ms=%.3f output_summary=%s",
        input_text,
        parsed.local_datetime.isoformat(),
        parsed.utc_datetime.isoformat(),
        args.place,
        args.lat,
        args.lon,
        args.house_system,
        _elapsed_ms(started_at),
        _chart_summary(chart),
    )
    print(output_text)

    return 0


def _configure_output_encoding() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")


def _elapsed_ms(started_at: float) -> float:
    return (perf_counter() - started_at) * 1000.0


def _args_for_log(args: argparse.Namespace) -> dict[str, object]:
    return {
        "input": list(args.input),
        "lat": args.lat,
        "lon": args.lon,
        "place": args.place,
        "house_system": args.house_system,
        "ephe_path": args.ephe_path,
        "rulership": args.rulership,
        "format": args.format,
        "max_aspect_orb": args.max_aspect_orb,
        "planets_only": args.planets_only,
        "no_aspects": args.no_aspects,
        "no_warnings": args.no_warnings,
    }


def _chart_summary(chart: NatalChart) -> str:
    bodies_count = len(chart.bodies or {})
    houses_count = len(chart.cusps or ())
    aspects_count = len(chart.aspects or ())
    configurations_count = len(chart.configurations or ())
    strength_count = len(chart.strength.planets) if chart.strength is not None else 0
    return (
        "bodies=%d houses=%d aspects=%d configurations=%d strength_planets=%d warnings=%d"
        % (
            bodies_count,
            houses_count,
            aspects_count,
            configurations_count,
            strength_count,
            len(chart.warnings),
        )
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="exact-orb",
        description="Calculate a natal chart from 'dd.mm.yyyy hh.mm gmt+x'.",
    )
    parser.add_argument(
        "input",
        nargs="*",
        metavar="INPUT",
        help="date/time in format: dd.mm.yyyy hh.mm gmt+x",
    )
    parser.add_argument("--lat", type=float, default=55.7522, help="latitude, north positive")
    parser.add_argument("--lon", type=float, default=37.6155, help="longitude, east positive")
    parser.add_argument("--place", default="Москва", help="place name for human output")
    parser.add_argument("--house-system", default=DEFAULT_HOUSE_SYSTEM, help="one-letter Swiss Ephemeris house code")
    parser.add_argument("--ephe-path", default=None, help="Swiss Ephemeris files directory")
    parser.add_argument(
        "--rulership",
        choices=[item.value for item in RulershipScheme],
        default=RulershipScheme.COMBINED.value,
        help="sign ruler table",
    )
    parser.add_argument(
        "--format",
        choices=("human", "json"),
        default="human",
        help="output format",
    )
    parser.add_argument(
        "--max-aspect-orb",
        type=float,
        default=7.0,
        help="maximum aspect orb in degrees for human output",
    )
    parser.add_argument("--planets-only", action="store_true", help="hide derived points")
    parser.add_argument("--no-aspects", action="store_true", help="hide ASPECTS section")
    parser.add_argument("--no-warnings", action="store_true", help="hide Swiss Ephemeris warnings")
    return parser


def parse_datetime_input(value: str) -> ParsedDateTime:
    LOGGER.debug("parse_datetime_input raw=%r", value)
    match = INPUT_PATTERN.match(value)
    if not match:
        raise ValueError("expected input format: dd.mm.yyyy hh.mm gmt+x")

    parts = match.groupdict()
    offset_hours = int(parts["tzhour"])
    offset_minutes = int(parts["tzminute"] or "0")
    if offset_hours > 14 or offset_minutes > 59:
        raise ValueError("GMT offset must be within +/-14:00")

    offset = timedelta(hours=offset_hours, minutes=offset_minutes)
    if parts["sign"] == "-":
        offset = -offset

    local_datetime = datetime(
        int(parts["year"]),
        int(parts["month"]),
        int(parts["day"]),
        int(parts["hour"]),
        int(parts["minute"]),
        tzinfo=timezone(offset),
    )
    parsed = ParsedDateTime(local_datetime=local_datetime)
    LOGGER.debug(
        "parse_datetime_input parsed datetime_local=%s datetime_utc=%s offset=%s",
        parsed.local_datetime.isoformat(),
        parsed.utc_datetime.isoformat(),
        parsed.offset_label,
    )
    return parsed


def format_human(
    chart: NatalChart,
    parsed: ParsedDateTime,
    *,
    place: str,
    body_order: Iterable[str],
    point_order: Iterable[str],
    max_aspect_orb: float,
    show_aspects: bool,
    show_warnings: bool,
) -> str:
    lines: list[str] = [
        "НАТАЛЬНАЯ КАРТА",
        _format_header(chart, parsed, place),
        "",
        "ПЛАНЕТЫ",
    ]

    for name in body_order:
        body = chart.bodies.get(name)
        if body is None:
            continue
        lines.append(_format_body_line(name, body))

    points = [name for name in point_order if name in chart.bodies]
    if points:
        lines.extend(["", "ТОЧКИ"])
        for name in points:
            lines.append(_format_body_line(name, chart.bodies[name]))

    lines.extend(["", "ДОМА"])
    house_rulers = {item.house: item for item in chart.house_rulers}
    for house in range(1, 13):
        lines.append(_format_house_line(chart, house, house_rulers[house]))

    lines.extend(["", "ИНТЕРЦЕПЦИИ"])
    lines.append("  " + _format_interceptions(chart))

    if show_aspects:
        lines.extend(["", "АСПЕКТЫ"])
        aspects = calculate_aspects(chart, max_orb=max_aspect_orb)
        if aspects:
            lines.extend(_format_aspect_groups(aspects, max_orb=max_aspect_orb))
        else:
            lines.append("  нет аспектов в заданном орбисе")

        lines.extend(["", "КОНФИГУРАЦИИ"])
        if chart.configurations:
            lines.extend(_format_configuration_groups(chart.configurations))
        else:
            lines.append("  нет конфигураций")

    if chart.strength is not None:
        lines.extend(["", "СИЛА И СТРУКТУРА"])
        lines.extend(_format_strength_lines(chart))
        special_degree_lines = _format_special_degree_lines(chart)
        if special_degree_lines:
            lines.extend(["", "ОСОБЫЕ ГРАДУСЫ"])
            lines.extend(special_degree_lines)

    if show_warnings and chart.warnings:
        lines.extend(["", "ПРЕДУПРЕЖДЕНИЯ"])
        for warning in _deduplicate_warnings(chart):
            lines.append(f"  {warning}")

    return "\n".join(lines)


def format_json(
    chart: NatalChart,
    parsed: ParsedDateTime,
    place: str,
    *,
    include_warnings: bool = True,
) -> str:
    chart_payload = chart.model_dump(mode="json")
    if not include_warnings:
        chart_payload["warnings"] = []

    payload = {
        "input": {
            "datetime_local": parsed.local_datetime.isoformat(),
            "datetime_utc": parsed.utc_datetime.isoformat(),
            "place": place,
            "timezone": parsed.offset_label,
        },
        "chart": chart_payload,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def calculate_aspects(chart: NatalChart, *, max_orb: float) -> tuple[CalculatedAspect, ...]:
    if chart.aspects is None:
        return ()
    return tuple(aspect for aspect in chart.aspects if aspect.orb <= max_orb)


def _format_header(chart: NatalChart, parsed: ParsedDateTime, place: str) -> str:
    value = parsed.local_datetime
    date_part = f"{value.day} {MONTHS_RU[value.month - 1]} {value.year}, {value:%H:%M}"
    coords = f"{_format_coord(chart.latitude, 'lat')} {_format_coord(chart.longitude, 'lon')}"
    house_system = HOUSE_SYSTEMS_RU.get(chart.house_system, chart.house_system)
    return f"{date_part} ({parsed.offset_label}) · {place} {coords} · {house_system}"


def _format_body_line(name: str, body: object) -> str:
    zodiac = body.zodiac
    point_name = POINTS_RU.get(name, name)
    sign = SIGNS_RU[zodiac.sign]
    retrograde = "R" if body.retrograde else ""
    return (
        f"  {point_name:<10}  {sign:<9}  {_format_zodiac_degrees(zodiac):>10}"
        f" {retrograde:<1} {body.house:>2} дом"
    )


def _format_house_line(chart: NatalChart, house: int, rulers: object) -> str:
    position = _house_display_position(chart, house)
    label = _house_label(house)
    sign = SIGNS_RU[position.zodiac.sign]
    return (
        f"  {label:<9}  {sign:<9}  {_format_zodiac_degrees(position.zodiac):>10}"
        f"    {_format_rulers(chart, rulers)}"
    )


def _house_display_position(chart: NatalChart, house: int) -> object:
    if house == 1:
        return chart.angles["asc"]
    if house == 4:
        return chart.angles["ic"]
    if house == 7:
        return chart.angles["dsc"]
    if house == 10:
        return chart.angles["mc"]
    return chart.cusps[house - 1]


def _house_label(house: int) -> str:
    labels = {1: "1 (ASC)", 4: "4 (IC)", 7: "7 (DSC)", 10: "10 (MC)"}
    return labels.get(house, str(house))


def _format_rulers(chart: NatalChart, rulers: object) -> str:
    primary = [_format_ruler_with_house(chart, ruler, compact=len(rulers.rulers) > 1) for ruler in rulers.rulers]
    text = "упр. " + " / ".join(primary)

    if rulers.intercepted_signs:
        co_parts = []
        for sign_name in rulers.intercepted_signs:
            co_rulers = _rulers_for_intercepted_sign(rulers, sign_name)
            co_parts.append(f"{_format_co_rulers(chart, co_rulers)} [{SIGNS_RU[sign_name]}]")
        text += " + " + "; ".join(co_parts)

    return text


def _format_co_rulers(chart: NatalChart, co_rulers: tuple[str, ...]) -> str:
    if len(co_rulers) <= 1:
        return ", ".join(POINTS_RU.get(item, item) for item in co_rulers)
    return ", ".join(_format_ruler_with_house(chart, item, compact=True) for item in co_rulers)


def _rulers_for_intercepted_sign(rulers: object, sign_name: str) -> tuple[str, ...]:
    sign_index_by_name = {
        "Aries": 0,
        "Taurus": 1,
        "Gemini": 2,
        "Cancer": 3,
        "Leo": 4,
        "Virgo": 5,
        "Libra": 6,
        "Scorpio": 7,
        "Sagittarius": 8,
        "Capricorn": 9,
        "Aquarius": 10,
        "Pisces": 11,
    }
    sign_index = sign_index_by_name[sign_name]
    combined = (
        ("mars",),
        ("venus",),
        ("mercury",),
        ("moon",),
        ("sun",),
        ("mercury",),
        ("venus",),
        ("mars", "pluto"),
        ("jupiter",),
        ("saturn",),
        ("saturn", "uranus"),
        ("jupiter", "neptune"),
    )
    return tuple(item for item in combined[sign_index] if item in rulers.co_rulers)


def _format_ruler_with_house(chart: NatalChart, ruler: str, *, compact: bool) -> str:
    label = POINTS_RU.get(ruler, ruler)
    body = chart.bodies.get(ruler)
    if body is None:
        return label
    if compact:
        return f"{label} ({body.house})"
    return f"{label} ({body.house} дом)"


def _format_interceptions(chart: NatalChart) -> str:
    if not chart.interceptions:
        return "нет"

    by_sign = {item.sign_index: item for item in chart.interceptions}
    seen: set[int] = set()
    parts: list[str] = []

    for item in chart.interceptions:
        if item.sign_index in seen:
            continue
        opposite_index = (item.sign_index + 6) % 12
        opposite = by_sign.get(opposite_index)
        if opposite is None:
            parts.append(f"{SIGNS_RU[item.sign]} в {item.house}")
            seen.add(item.sign_index)
            continue

        parts.append(
            f"{SIGNS_RU[item.sign]} в {item.house} / {SIGNS_RU[opposite.sign]} в {opposite.house}"
        )
        seen.add(item.sign_index)
        seen.add(opposite.sign_index)

    return " · ".join(parts)


def _format_aspect_groups(
    aspects: Iterable[CalculatedAspect],
    *,
    max_orb: float,
) -> list[str]:
    lines: list[str] = []
    grouped = {
        category: sorted(
            (aspect for aspect in aspects if aspect.category is category),
            key=lambda aspect: aspect.orb,
        )
        for category in ASPECT_CATEGORY_ORDER
    }

    for category in ASPECT_CATEGORY_ORDER:
        category_aspects = grouped[category]
        if not category_aspects:
            continue
        header = ASPECT_CATEGORY_HEADERS[category].format(max_orb=max_orb)
        lines.extend(["", f"  {header}"])
        for aspect in category_aspects:
            lines.append(_format_aspect_line(aspect))

    return lines


def _format_aspect_line(aspect: CalculatedAspect) -> str:
    left = POINTS_RU.get(aspect.from_point.body, aspect.from_point.body)
    right = POINTS_RU.get(aspect.to_point.body, aspect.to_point.body)
    symbol = ASPECT_SYMBOLS[aspect.aspect_type.value]
    return (
        f"    {left:<10} {symbol}  {right:<10}"
        f" {_format_orb(aspect.orb):>6}"
    )


def _format_configuration_groups(
    configurations: Iterable[CalculatedConfiguration],
) -> list[str]:
    lines: list[str] = []
    for category in CONFIGURATION_CATEGORY_ORDER:
        category_configurations = sorted(
            (configuration for configuration in configurations if configuration.category is category),
            key=lambda configuration: configuration.max_orb,
        )
        if not category_configurations:
            continue
        lines.extend(["", f"  {CONFIGURATION_CATEGORY_HEADERS[category]}"])
        for configuration in category_configurations:
            lines.append(_format_configuration_line(configuration))
    return lines


def _format_configuration_line(configuration: CalculatedConfiguration) -> str:
    label = CONFIGURATION_TYPES_RU[configuration.type.value]
    quality = f"max {_format_orb(configuration.max_orb)}"
    roles = _format_configuration_roles(configuration)
    extra = ""
    if configuration.element:
        extra += f" · {configuration.element}"
    if configuration.modality:
        extra += f" · {configuration.modality}"
    if configuration.contains:
        extra += f" · содержит {len(configuration.contains)}"
    return f"    {label:<14}  {quality:<9}  {roles}{extra}"


def _format_configuration_roles(configuration: CalculatedConfiguration) -> str:
    points = configuration.points
    config_type = configuration.type.value
    if config_type in {"t_square", "yod"}:
        return (
            f"apex: {_role_point(points, 'apex')} · "
            f"base: {_role_point(points, 'base_1')}, {_role_point(points, 'base_2')}"
        )
    if config_type == "bisextile":
        return (
            f"center: {_role_point(points, 'center')} · "
            f"wings: {_role_point(points, 'wing_1')}, {_role_point(points, 'wing_2')}"
        )
    if config_type == "trapeze":
        return (
            f"opp: {_role_point(points, 'opposition_1')}–{_role_point(points, 'opposition_2')} · "
            f"base: {_role_point(points, 'base_1')}, {_role_point(points, 'base_2')}"
        )
    return " · ".join(
        f"{role}: {POINTS_RU.get(point.body, point.body)}"
        for role, point in points.items()
    )


def _role_point(points: dict[str, object], role: str) -> str:
    point = points[role]
    return POINTS_RU.get(point.body, point.body)


def _format_special_degree_lines(chart: NatalChart) -> list[str]:
    if chart.strength is None:
        return []

    flags = [
        flag
        for flag in chart.strength.degree_flags
        if flag.is_zero_degree or flag.is_anaretic or flag.is_critical
    ]
    if not flags:
        return []

    order = {name: index for index, name in enumerate((*BODY_ORDER, "asc", "mc", "vertex"))}

    def sort_key(flag: object) -> tuple[int, str]:
        if flag.point_type == "cusp":
            try:
                return 200 + int(flag.point.split("_", 1)[1]), flag.point
            except (IndexError, ValueError):
                return 999, flag.point
        if flag.is_zero_degree:
            return 0, flag.point
        if flag.is_critical:
            return 10 + (flag.matched_critical_degree or 0), f"{order.get(flag.point, 90):02d}-{flag.point}"
        return 100 + order.get(flag.point, 90), flag.point

    return [_format_special_degree_line(flag) for flag in sorted(flags, key=sort_key)]


def _format_special_degree_line(flag: object) -> str:
    label = _degree_flag_label(flag)
    sign = SIGNS_RU[flag.sign]
    position = f"{_format_degree_in_sign_minutes(flag.degree_in_sign)} {sign}"
    notes = " · ".join(_degree_flag_notes(flag))
    return f"  {label:<10}  {position:<17} {notes}"


def _degree_flag_label(flag: object) -> str:
    if flag.point_type == "cusp":
        return "куспид " + flag.point.split("_", 1)[1]
    return POINTS_RU.get(flag.point, flag.point)


def _degree_flag_notes(flag: object) -> tuple[str, ...]:
    notes: list[str] = []
    sign_index = SIGN_INDICES[flag.sign]
    modality = MODALITY_BY_SIGN_INDEX[sign_index % 3]
    if flag.is_zero_degree and modality == "cardinal":
        notes.append("0° кардинального")
    elif flag.is_zero_degree:
        notes.append("0° знака")
    if flag.is_anaretic:
        notes.append("анаретический")
    if flag.is_critical:
        notes.append(_format_critical_degree_note(modality, flag.matched_critical_degree))
    return tuple(notes)


def _format_critical_degree_note(modality: str, matched_degree: int | None) -> str:
    if modality == "fixed" and matched_degree in {8, 9}:
        return "критический (фикс. 8–9°)"
    if modality == "fixed" and matched_degree in {21, 22}:
        return "критический (фикс. 21–22°)"
    if modality == "mutable":
        return f"критический (мутаб. {matched_degree}°)"
    if matched_degree == 0:
        return "критический"
    return f"критический (кард. {matched_degree}°)"


def _format_strength_lines(chart: NatalChart) -> list[str]:
    strength = chart.strength
    if strength is None:
        return ["  нет данных"]

    lines = ["  Планеты"]
    for name in BODY_ORDER:
        item = strength.planets.get(name)
        if item is None:
            continue
        dignity = DIGNITY_RU[item.dignity.status.value]
        house_type = HOUSE_TYPES_RU[item.accidental.house_type.value]
        category = STRENGTH_CATEGORIES_RU[item.category.value]
        lines.append(
            f"    {POINTS_RU.get(name, name):<10} {dignity:<9}"
            f" эсс. {item.essential_score:>2}  акц. {item.accidental_score:>2}"
            f"  итого {item.total:>2}  {category} ({house_type})"
        )

    lines.append(f"  Примечание: {strength.weak_note}")
    elements = ", ".join(_format_balance_item(name, bucket) for name, bucket in strength.balance.elements.items())
    modalities = ", ".join(_format_balance_item(name, bucket) for name, bucket in strength.balance.modalities.items())
    lines.append(f"  Стихии: {elements}")
    lines.append(f"  Кресты: {modalities}")
    phase = strength.lunar_phase
    lines.append(
        f"  Луна: {phase.phase_name}, фаза {phase.phase_number},"
        f" элонгация {phase.elongation:.3f}°"
    )
    if strength.mutual_receptions:
        receptions = ", ".join(
            f"{POINTS_RU.get(item.body_1, item.body_1)} ↔ {POINTS_RU.get(item.body_2, item.body_2)}"
            for item in strength.mutual_receptions
        )
        lines.append(f"  Взаимные рецепции: {receptions}")
    return lines


def _format_balance_item(name: str, bucket: object) -> str:
    label = BALANCE_NAMES_RU.get(name, name)
    return f"{label} {bucket.score:g} ({bucket.percentage:.1f}%, {bucket.state.value})"


def _format_zodiac_degrees(zodiac: ZodiacPosition) -> str:
    return f"{zodiac.degree:02d}°{zodiac.minute:02d}'{zodiac.second:02d}\""


def _format_orb(orb: float) -> str:
    total_minutes = int(round(orb * 60.0))
    return f"{total_minutes // 60}°{total_minutes % 60:02d}'"


def _format_degree_in_sign_minutes(degree_in_sign: float) -> str:
    total_minutes = min(int(degree_in_sign * 60.0), 29 * 60 + 59)
    return f"{total_minutes // 60}°{total_minutes % 60:02d}'"


def _cardinal_zero_note(zodiac: ZodiacPosition) -> str:
    if zodiac.sign_index in CARDINAL_SIGN_INDICES and zodiac.degree == 0:
        return "    ← 0° кардинального"
    return ""


def _format_coord(value: float, axis: str) -> str:
    if axis == "lat":
        suffix = "N" if value >= 0 else "S"
    else:
        suffix = "E" if value >= 0 else "W"
    return f"{abs(value):.2f}{suffix}"


def format_utc_offset(value: datetime) -> str:
    offset = value.utcoffset()
    if offset is None:
        raise ValueError("datetime must have a UTC offset")

    total_minutes = int(offset.total_seconds() // 60)
    sign = "+" if total_minutes >= 0 else "-"
    total_minutes = abs(total_minutes)
    return f"{sign}{total_minutes // 60:02d}:{total_minutes % 60:02d}"


def _deduplicate_warnings(chart: NatalChart) -> tuple[str, ...]:
    messages: dict[str, list[str]] = {}
    for warning in chart.warnings:
        message = " ".join(warning.message.split())
        messages.setdefault(message, []).append(warning.source)

    return tuple(f"{', '.join(sources)}: {message}" for message, sources in messages.items())
