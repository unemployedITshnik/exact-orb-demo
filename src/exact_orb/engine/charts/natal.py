"""Natal chart calculation backed by pysweph/swisseph."""

from __future__ import annotations

from datetime import datetime
import logging
from time import perf_counter
from typing import AbstractSet, Literal, Mapping

from pydantic import BaseModel, Field

from exact_orb import swiss_backend
from exact_orb.config import EphemerisStatus, get_selena_method_name, validate_ephemeris_path
from exact_orb.engine.aspects import Aspect, AspectConfig, PositionedPoint, find_aspects
from exact_orb.engine.configurations import Configuration, ConfigurationConfig, find_configurations
from exact_orb.engine.ephemeris.calc import (
    calculate_bodies,
    calculate_houses,
    house_for_longitude,
    julian_day_ut as ephemeris_jd_ut,
    normalize_degrees,
    normalize_house_system,
    rulers_for_sign,
    to_utc,
    validate_geography,
    zodiac_position,
)
from exact_orb.engine.ephemeris.runtime import ephemeris_session
from exact_orb.engine.ephemeris.types import (
    DEFAULT_BODY_IDS,
    EPSILON,
    FULL_CIRCLE,
    ZODIAC_SIGNS,
    AnglePosition,
    BodyPosition,
    CalculationWarning,
    HouseCusp,
    RulershipScheme,
)
from exact_orb.engine.strength import (
    NatalStrength,
    StrengthConfig,
    calculate_accidental_strength,
    calculate_balance,
    calculate_dispositor_chains,
    calculate_lunar_phase,
    evaluate_dignity,
    find_special_degrees,
)
from exact_orb.engine.strength.types import (
    InterceptionEntry,
    InterceptionSummary,
    PlanetStrength,
    StrengthCategory,
)


LOGGER = logging.getLogger(__name__)
ChartKind = Literal["natal", "cosmogram"]
DEFAULT_INCLUDE = frozenset(
    {"positions", "houses", "rulers", "aspects", "configurations", "strength"}
)
INCLUDE_BLOCKS = DEFAULT_INCLUDE


class Interception(BaseModel):
    """A whole zodiac sign contained between two consecutive house cusps."""

    house: int = Field(..., ge=1, le=12)
    sign_index: int = Field(..., ge=0, le=11)
    sign: str
    start_longitude: float = Field(..., ge=0.0, lt=360.0)
    end_longitude: float = Field(..., ge=0.0, lt=360.0)
    rulers: tuple[str, ...]
    fully_contained: bool
    near_interception: bool = False
    remaining_arc: float | None = None
    threshold: float | None = None


class HouseRulers(BaseModel):
    """Primary house rulers plus co-rulers for intercepted signs."""

    house: int = Field(..., ge=1, le=12)
    cusp_sign_index: int = Field(..., ge=0, le=11)
    cusp_sign: str
    rulers: tuple[str, ...]
    intercepted_signs: tuple[str, ...]
    co_rulers: tuple[str, ...]


class NatalChart(BaseModel):
    """Deterministic natal chart data."""

    chart_kind: ChartKind
    datetime_utc: datetime
    julian_day_ut: float
    latitude: float
    longitude: float
    house_system: str
    ephemeris_flags: int
    ephemeris: EphemerisStatus
    selena_method: str
    bodies: dict[str, BodyPosition] | None
    cusps: tuple[HouseCusp, ...] | None
    angles: dict[str, AnglePosition] | None
    house_rulers: tuple[HouseRulers, ...] | None
    interceptions: tuple[Interception, ...] | None
    aspects: tuple[Aspect, ...] | None = None
    configurations: tuple[Configuration, ...] | None = None
    strength: NatalStrength | None = None
    warnings: tuple[CalculationWarning, ...] = ()


def calculate_natal(
    birth_datetime: datetime,
    latitude: float,
    longitude: float,
    *,
    chart_kind: ChartKind,
    house_system: str | bytes = b"P",
    body_ids: Mapping[str, int] | None = None,
    ephemeris_flags: int = swiss_backend.swe.FLG_SWIEPH,
    rulership: RulershipScheme | str = RulershipScheme.COMBINED,
    near_interception_threshold: float = 1.0,
    ephemeris_path: str | None = None,
    selena_method: str | None = None,
    include: AbstractSet[str] | None = None,
    aspect_config: AspectConfig | None = None,
    configuration_config: ConfigurationConfig | None = None,
    strength_config: StrengthConfig | None = None,
) -> NatalChart:
    """Calculate deterministic natal chart data.

    ``birth_datetime`` must be timezone-aware. It is converted to UTC before
    creating the UT decimal hour for ``swe.julday``.
    """

    with ephemeris_session():
        return _calculate_natal(
            birth_datetime,
            latitude,
            longitude,
            chart_kind=chart_kind,
            house_system=house_system,
            body_ids=body_ids,
            ephemeris_flags=ephemeris_flags,
            rulership=rulership,
            near_interception_threshold=near_interception_threshold,
            ephemeris_path=ephemeris_path,
            selena_method=selena_method,
            include=include,
            aspect_config=aspect_config,
            configuration_config=configuration_config,
            strength_config=strength_config,
        )


def _calculate_natal(
    birth_datetime: datetime,
    latitude: float,
    longitude: float,
    *,
    chart_kind: ChartKind,
    house_system: str | bytes,
    body_ids: Mapping[str, int] | None,
    ephemeris_flags: int,
    rulership: RulershipScheme | str,
    near_interception_threshold: float,
    ephemeris_path: str | None,
    selena_method: str | None,
    include: AbstractSet[str] | None,
    aspect_config: AspectConfig | None,
    configuration_config: ConfigurationConfig | None,
    strength_config: StrengthConfig | None,
) -> NatalChart:
    """Calculate deterministic natal chart data.

    ``birth_datetime`` must be timezone-aware. It is converted to UTC before
    creating the UT decimal hour for ``swe.julday``.
    """

    started_at = perf_counter()
    LOGGER.debug(
        "calculate_natal start chart_kind=%s birth_datetime=%s latitude=%.6f longitude=%.6f house_system=%s "
        "rulership=%s include=%s ephemeris_path=%s",
        chart_kind,
        birth_datetime.isoformat(),
        latitude,
        longitude,
        house_system,
        rulership,
        sorted(include) if include is not None else None,
        ephemeris_path,
    )
    include_blocks = _normalize_include(include)
    _validate_chart_kind_include(chart_kind, include_blocks)
    houses_included = "houses" in include_blocks
    ephemeris = validate_ephemeris_path(ephemeris_path)
    LOGGER.debug(
        "ephemeris configured mode=%s source=%s path=%s missing=%s",
        ephemeris.mode,
        ephemeris.source,
        ephemeris.path,
        ephemeris.missing_files,
    )
    configured_selena_method = get_selena_method_name(selena_method)
    validate_geography(latitude, longitude)
    if near_interception_threshold < 0.0:
        raise ValueError("near_interception_threshold must be non-negative")
    hsys = normalize_house_system(house_system)
    scheme = RulershipScheme(rulership)
    utc_datetime = to_utc(birth_datetime)
    julian_day_ut = ephemeris_jd_ut(utc_datetime)
    flags = ephemeris_flags | swiss_backend.swe.FLG_SPEED

    if houses_included:
        step_started_at = perf_counter()
        cusps, angles = calculate_houses(julian_day_ut, latitude, longitude, hsys)
        LOGGER.debug(
            "natal_houses calculated cusps=%d angles=%d duration_ms=%.3f",
            len(cusps),
            len(angles),
            _elapsed_ms(step_started_at),
        )
    else:
        cusps, angles = None, {}
        LOGGER.debug("natal_houses skipped include_houses=False")
    step_started_at = perf_counter()
    bodies, warnings = calculate_bodies(julian_day_ut, body_ids or DEFAULT_BODY_IDS, flags, cusps)
    if not houses_included:
        warnings.append(
            CalculationWarning(
                source="include",
                message=(
                    "houses were not requested: house placements, angles, and "
                    "angle-derived points are absent from this chart"
                ),
                retflags=None,
            )
        )
    LOGGER.debug(
        "natal_bodies calculated bodies=%d warnings=%d duration_ms=%.3f",
        len(bodies),
        len(warnings),
        _elapsed_ms(step_started_at),
    )
    step_started_at = perf_counter()
    _add_derived_points(bodies, angles, cusps, utc_datetime, julian_day_ut, flags, configured_selena_method)
    LOGGER.debug(
        "natal_derived_points ready bodies=%d duration_ms=%.3f",
        len(bodies),
        _elapsed_ms(step_started_at),
    )
    if houses_included:
        step_started_at = perf_counter()
        interceptions = _calculate_interceptions(cusps, scheme, near_interception_threshold)
        house_rulers = _calculate_house_rulers(cusps, interceptions, scheme)
        LOGGER.debug(
            "natal_rulership calculated interceptions=%d house_rulers=%d duration_ms=%.3f",
            len(interceptions),
            len(house_rulers),
            _elapsed_ms(step_started_at),
        )
    else:
        interceptions, house_rulers = (), ()
        LOGGER.debug("natal_rulership skipped include_houses=False")
    configured_aspects = aspect_config or AspectConfig.natal()
    step_started_at = perf_counter()
    calculated_aspects = (
        _calculate_natal_aspects(bodies, angles, configured_aspects)
        if {"aspects", "configurations"} & include_blocks
        else None
    )
    LOGGER.debug(
        "natal_aspects block included=%s aspects=%s duration_ms=%.3f",
        bool({"aspects", "configurations"} & include_blocks),
        len(calculated_aspects) if calculated_aspects is not None else None,
        _elapsed_ms(step_started_at),
    )
    step_started_at = perf_counter()
    configurations = (
        _calculate_natal_configurations(
            calculated_aspects or (),
            bodies,
            angles,
            configured_aspects,
            configuration_config or ConfigurationConfig(),
        )
        if "configurations" in include_blocks
        else None
    )
    LOGGER.debug(
        "natal_configurations block included=%s configurations=%s duration_ms=%.3f",
        "configurations" in include_blocks,
        len(configurations) if configurations is not None else None,
        _elapsed_ms(step_started_at),
    )
    step_started_at = perf_counter()
    strength = (
        _calculate_natal_strength(
            bodies,
            angles,
            cusps,
            interceptions,
            strength_config or StrengthConfig(),
        )
        if "strength" in include_blocks
        else None
    )
    LOGGER.debug(
        "natal_strength block included=%s planets=%s duration_ms=%.3f",
        "strength" in include_blocks,
        len(strength.planets) if strength is not None else None,
        _elapsed_ms(step_started_at),
    )

    chart = NatalChart(
        chart_kind=chart_kind,
        datetime_utc=utc_datetime,
        julian_day_ut=julian_day_ut,
        latitude=latitude,
        longitude=longitude,
        house_system=hsys.decode("ascii"),
        ephemeris_flags=flags,
        ephemeris=ephemeris,
        selena_method=configured_selena_method,
        bodies=bodies if "positions" in include_blocks else None,
        cusps=cusps if houses_included else None,
        angles=angles if houses_included else None,
        house_rulers=house_rulers if "rulers" in include_blocks else None,
        interceptions=interceptions if "rulers" in include_blocks else None,
        aspects=calculated_aspects if "aspects" in include_blocks else None,
        configurations=configurations,
        strength=strength,
        warnings=tuple(warnings),
    )
    LOGGER.debug(
        "calculate_natal complete duration_ms=%.3f bodies=%s aspects=%s configurations=%s strength=%s warnings=%d",
        _elapsed_ms(started_at),
        len(chart.bodies or {}),
        len(chart.aspects or ()),
        len(chart.configurations or ()),
        chart.strength is not None,
        len(chart.warnings),
    )
    return chart


def _normalize_include(include: AbstractSet[str] | None) -> frozenset[str]:
    if include is None:
        return DEFAULT_INCLUDE

    include_blocks = frozenset(include)
    unknown = include_blocks - INCLUDE_BLOCKS
    if unknown:
        raise ValueError(f"unknown include block(s): {', '.join(sorted(unknown))}")
    if "rulers" in include_blocks and "houses" not in include_blocks:
        raise ValueError('include block "rulers" requires "houses"')
    if "strength" in include_blocks and "houses" not in include_blocks:
        raise ValueError('include block "strength" requires "houses"')
    return include_blocks


def _validate_chart_kind_include(chart_kind: ChartKind, include_blocks: frozenset[str]) -> None:
    if chart_kind not in {"natal", "cosmogram"}:
        raise ValueError("chart_kind must be 'natal' or 'cosmogram'")
    if chart_kind == "natal" and "houses" not in include_blocks:
        raise ValueError('chart_kind "natal" requires include block "houses"')
    if chart_kind == "cosmogram":
        forbidden = include_blocks & {"houses", "rulers", "strength"}
        if forbidden:
            raise ValueError(
                f'chart_kind "cosmogram" forbids include block(s): {", ".join(sorted(forbidden))}'
            )


def _elapsed_ms(started_at: float) -> float:
    return (perf_counter() - started_at) * 1000.0


def _calculate_natal_aspects(
    bodies: Mapping[str, BodyPosition],
    angles: Mapping[str, AnglePosition],
    config: AspectConfig,
) -> tuple[Aspect, ...]:
    points = _natal_aspect_points(bodies, angles, config)
    aspects = tuple(find_aspects(points, None, config))
    LOGGER.debug(
        "calculate_natal_aspects points=%d aspects=%d max_orb=%.3f",
        len(points),
        len(aspects),
        config.active_orbs.max_orb,
    )
    return aspects


def _calculate_natal_configurations(
    aspects: tuple[Aspect, ...],
    bodies: Mapping[str, BodyPosition],
    angles: Mapping[str, AnglePosition],
    aspect_config: AspectConfig,
    configuration_config: ConfigurationConfig,
) -> tuple[Configuration, ...]:
    config = _configuration_config_with_signs(
        bodies,
        angles,
        aspect_config,
        configuration_config,
    )
    configurations = tuple(find_configurations(list(aspects), config))
    LOGGER.debug(
        "calculate_natal_configurations aspects=%d configurations=%d max_orb=%.3f",
        len(aspects),
        len(configurations),
        config.configuration_max_orb,
    )
    return configurations


def _configuration_config_with_signs(
    bodies: Mapping[str, BodyPosition],
    angles: Mapping[str, AnglePosition],
    aspect_config: AspectConfig,
    configuration_config: ConfigurationConfig,
) -> ConfigurationConfig:
    point_signs: dict[str, int] = {}
    for name in aspect_config.natal_points:
        if name in bodies:
            sign_index = bodies[name].zodiac.sign_index
        elif name in angles:
            sign_index = angles[name].zodiac.sign_index
        else:
            continue

        body_name = aspect_config.point_aliases.get(name, name)
        point_signs[body_name] = sign_index
        point_signs[f"natal:{body_name}"] = sign_index

    point_signs.update(configuration_config.point_signs)
    return configuration_config.model_copy(update={"point_signs": point_signs})


def _natal_aspect_points(
    bodies: Mapping[str, BodyPosition],
    angles: Mapping[str, AnglePosition],
    config: AspectConfig,
) -> tuple[PositionedPoint, ...]:
    points: list[PositionedPoint] = []
    for name in config.natal_points:
        if name in bodies:
            longitude = bodies[name].longitude
        elif name in angles:
            longitude = angles[name].longitude
        else:
            continue

        points.append(
            PositionedPoint(
                chart="natal",
                body=config.point_aliases.get(name, name),
                longitude=longitude,
            )
        )
    return tuple(points)


def _calculate_natal_strength(
    bodies: Mapping[str, BodyPosition],
    angles: Mapping[str, AnglePosition],
    cusps: tuple[HouseCusp, ...],
    interceptions: tuple[Interception, ...],
    config: StrengthConfig,
) -> NatalStrength | None:
    if "sun" not in bodies or "moon" not in bodies:
        LOGGER.debug("calculate_natal_strength skipped missing_sun_or_moon=True")
        return None

    started_at = perf_counter()
    planet_strengths: dict[str, PlanetStrength] = {}
    for name in config.planets:
        body = bodies.get(name)
        if body is None:
            continue
        dignity = evaluate_dignity(
            name,
            body.zodiac.sign_index,
            system=config.dignity_system,
        )
        accidental = calculate_accidental_strength(name, body, angles, config)
        total = dignity.score + accidental.score
        category = _strength_category(total, config)
        planet_strengths[name] = PlanetStrength(
            body=name,
            dignity=dignity,
            accidental=accidental,
            essential_score=dignity.score,
            accidental_score=accidental.score,
            total=total,
            category=category,
            note=_weak_strength_note() if category is StrengthCategory.WEAK else None,
        )
        LOGGER.debug(
            "planet_strength body=%s dignity=%s essential=%d accidental=%d total=%d category=%s",
            name,
            dignity.status.value,
            dignity.score,
            accidental.score,
            total,
            category.value,
        )

    body_signs = {
        name: bodies[name].zodiac.sign_index
        for name in config.planets
        if name in bodies
    }
    dispositors, mutual_receptions = calculate_dispositor_chains(
        body_signs,
        bodies=config.planets,
    )

    strength = NatalStrength(
        dignity_system=config.dignity_system,
        planets=planet_strengths,
        balance=calculate_balance(bodies, angles, config),
        dispositors=dispositors,
        mutual_receptions=mutual_receptions,
        lunar_phase=calculate_lunar_phase(
            bodies["sun"].longitude,
            bodies["moon"].longitude,
        ),
        degree_flags=find_special_degrees(bodies, angles, cusps, config),
        interceptions=_interception_summary(interceptions),
        weak_note=_weak_strength_note(),
    )
    LOGGER.debug(
        "calculate_natal_strength complete planets=%d mutual_receptions=%d degree_flags=%d duration_ms=%.3f",
        len(strength.planets),
        len(strength.mutual_receptions),
        len(strength.degree_flags),
        _elapsed_ms(started_at),
    )
    return strength


def _strength_category(total: int, config: StrengthConfig) -> StrengthCategory:
    if total >= config.strong_threshold:
        return StrengthCategory.STRONG
    if total >= config.weak_threshold:
        return StrengthCategory.MODERATE
    return StrengthCategory.WEAK


def _weak_strength_note() -> str:
    return (
        "weak means indirect or effortful expression, not badness or harm"
    )


def _interception_summary(interceptions: tuple[Interception, ...]) -> InterceptionSummary:
    intercepted: list[InterceptionEntry] = []
    near: list[InterceptionEntry] = []

    for item in interceptions:
        entry = InterceptionEntry(
            sign=item.sign,
            house=item.house,
            remaining_arc=item.remaining_arc,
            threshold=item.threshold,
        )
        if item.near_interception:
            near.append(entry)
        elif item.fully_contained:
            intercepted.append(entry)

    return InterceptionSummary(
        intercepted=tuple(intercepted),
        near_intercepted=tuple(near),
    )


def _calculate_interceptions(
    cusps: tuple[HouseCusp, ...],
    scheme: RulershipScheme,
    near_interception_threshold: float,
) -> tuple[Interception, ...]:
    interceptions: list[Interception] = []

    for index, cusp in enumerate(cusps):
        next_cusp = cusps[(index + 1) % len(cusps)]
        for sign_index, fully_contained, near_interception, remaining_arc, threshold in _additional_signs_in_house(
            cusp.longitude,
            next_cusp.longitude,
            near_interception_threshold,
        ):
            interceptions.append(
                Interception(
                    house=cusp.house,
                    sign_index=sign_index,
                    sign=ZODIAC_SIGNS[sign_index],
                    start_longitude=sign_index * 30.0,
                    end_longitude=normalize_degrees((sign_index + 1) * 30.0),
                    rulers=rulers_for_sign(sign_index, scheme),
                    fully_contained=fully_contained,
                    near_interception=near_interception,
                    remaining_arc=remaining_arc,
                    threshold=threshold,
                )
            )

    return tuple(interceptions)


def _calculate_house_rulers(
    cusps: tuple[HouseCusp, ...],
    interceptions: tuple[Interception, ...],
    scheme: RulershipScheme,
) -> tuple[HouseRulers, ...]:
    interceptions_by_house: dict[int, list[Interception]] = {}
    for interception in interceptions:
        interceptions_by_house.setdefault(interception.house, []).append(interception)

    house_rulers: list[HouseRulers] = []
    for cusp in cusps:
        primary_rulers = rulers_for_sign(cusp.zodiac.sign_index, scheme)
        co_rulers: list[str] = []
        intercepted_signs: list[str] = []

        for interception in interceptions_by_house.get(cusp.house, []):
            intercepted_signs.append(interception.sign)
            for ruler in interception.rulers:
                if ruler not in primary_rulers and ruler not in co_rulers:
                    co_rulers.append(ruler)

        house_rulers.append(
            HouseRulers(
                house=cusp.house,
                cusp_sign_index=cusp.zodiac.sign_index,
                cusp_sign=cusp.zodiac.sign,
                rulers=primary_rulers,
                intercepted_signs=tuple(intercepted_signs),
                co_rulers=tuple(co_rulers),
            )
        )

    return tuple(house_rulers)


def _additional_signs_in_house(
    start_longitude: float,
    end_longitude: float,
    near_interception_threshold: float,
) -> tuple[tuple[int, bool, bool, float | None, float | None], ...]:
    start = normalize_degrees(start_longitude)
    end = start + ((normalize_degrees(end_longitude) - start) % FULL_CIRCLE)
    if end <= start + EPSILON:
        end += FULL_CIRCLE

    next_cusp_sign = int(normalize_degrees(end_longitude) // 30.0)
    intercepted: list[tuple[int, bool, bool, float | None, float | None]] = []
    for sign_index in range(12):
        sign_start = sign_index * 30.0
        while sign_start <= start + EPSILON:
            sign_start += FULL_CIRCLE

        if sign_start >= end - EPSILON:
            continue

        sign_end = sign_start + 30.0
        fully_contained = sign_end <= end + EPSILON
        near_interception = False

        if not fully_contained:
            remainder_after_next_cusp = sign_end - end
            near_interception = (
                sign_index == next_cusp_sign
                and remainder_after_next_cusp > EPSILON
                and remainder_after_next_cusp <= near_interception_threshold + EPSILON
            )

        if fully_contained or near_interception:
            intercepted.append(
                (
                    sign_index,
                    fully_contained,
                    near_interception,
                    remainder_after_next_cusp if near_interception else None,
                    near_interception_threshold if near_interception else None,
                )
            )

    return tuple(intercepted)


def _add_derived_points(
    bodies: dict[str, BodyPosition],
    angles: dict[str, AnglePosition],
    cusps: tuple[HouseCusp, ...] | None,
    moment_utc: datetime,
    julian_day_ut: float,
    flags: int,
    selena_method_name: str,
) -> None:
    if "true_node" in bodies and "south_node" not in bodies:
        true_node = bodies["true_node"]
        longitude = normalize_degrees(true_node.longitude + 180.0)
        bodies["south_node"] = BodyPosition(
            name="south_node",
            chart="natal",
            source="derived",
            swe_id=None,
            longitude=longitude,
            latitude=true_node.latitude,
            distance=true_node.distance,
            longitude_speed=true_node.longitude_speed,
            latitude_speed=true_node.latitude_speed,
            distance_speed=true_node.distance_speed,
            retrograde=true_node.retrograde,
            house=house_for_longitude(longitude, cusps) if cusps is not None else None,
            zodiac=zodiac_position(longitude),
            retflags=true_node.retflags,
        )
        LOGGER.debug("derived_point name=%s longitude=%.6f house=%s", "south_node", longitude, bodies["south_node"].house)

    if {"sun", "moon"}.issubset(bodies) and "asc" in angles and "pars_fortune" not in bodies:
        from exact_orb.engine.ephemeris.points import part_of_fortune

        longitude = part_of_fortune(
            angles["asc"].longitude,
            bodies["sun"].longitude,
            bodies["moon"].longitude,
            cusps,
        )
        bodies["pars_fortune"] = _derived_point("pars_fortune", longitude, cusps)
        LOGGER.debug(
            "derived_point name=%s longitude=%.6f house=%s",
            "pars_fortune",
            bodies["pars_fortune"].longitude,
            bodies["pars_fortune"].house,
        )

    if "selena" not in bodies:
        from exact_orb.engine.ephemeris.selena import get_selena_method

        method = get_selena_method(selena_method_name)
        selena = method.calculate(julian_day_ut, flags)
        bodies["selena"] = selena.model_copy(
            update={
                "house": house_for_longitude(selena.longitude, cusps)
                if cusps is not None
                else None
            }
        )
        LOGGER.debug(
            "derived_point name=%s method=%s longitude=%.6f house=%s",
            "selena",
            selena_method_name,
            bodies["selena"].longitude,
            bodies["selena"].house,
        )

    _ = moment_utc


def _derived_point(
    name: str,
    longitude: float,
    cusps: tuple[HouseCusp, ...] | None,
    *,
    source: Literal["derived", "selena"] = "derived",
) -> BodyPosition:
    normalized = normalize_degrees(longitude)
    return BodyPosition(
        name=name,
        chart="natal",
        source=source,
        method=None,
        swe_id=None,
        longitude=normalized,
        latitude=0.0,
        distance=0.0,
        longitude_speed=0.0,
        latitude_speed=0.0,
        distance_speed=0.0,
        retrograde=False,
        house=house_for_longitude(normalized, cusps) if cusps is not None else None,
        zodiac=zodiac_position(normalized),
        retflags=0,
    )
