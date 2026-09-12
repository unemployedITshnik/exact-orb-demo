"""Shared helpers for configuration pattern modules."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping

from exact_orb.engine.aspects import Aspect, AspectPointRef, AspectType

from ..types import (
    Configuration,
    ConfigurationCategory,
    ConfigurationConfig,
    ConfigurationType,
    PointKey,
)


ELEMENTS = ("fire", "earth", "air", "water")
MODALITIES = ("cardinal", "fixed", "mutable")
CONFIGURATION_ROLE_SETS: dict[ConfigurationType, frozenset[str]] = {
    ConfigurationType.T_SQUARE: frozenset({"apex", "base_1", "base_2"}),
    ConfigurationType.YOD: frozenset({"apex", "base_1", "base_2"}),
    ConfigurationType.BISEXTILE: frozenset({"center", "wing_1", "wing_2"}),
    ConfigurationType.GRAND_TRINE: frozenset({"point_1", "point_2", "point_3"}),
    ConfigurationType.GRAND_CROSS: frozenset(
        {"axis_1_a", "axis_1_b", "axis_2_a", "axis_2_b"}
    ),
    ConfigurationType.TRAPEZE: frozenset(
        {"opposition_1", "opposition_2", "base_1", "base_2"}
    ),
}
CONFIGURATION_ROLE_EDGE_TYPES: dict[
    ConfigurationType,
    dict[frozenset[str], AspectType],
] = {
    ConfigurationType.T_SQUARE: {
        frozenset(("base_1", "base_2")): AspectType.OPPOSITION,
        frozenset(("apex", "base_1")): AspectType.SQUARE,
        frozenset(("apex", "base_2")): AspectType.SQUARE,
    },
    ConfigurationType.YOD: {
        frozenset(("base_1", "base_2")): AspectType.SEXTILE,
        frozenset(("apex", "base_1")): AspectType.QUINCUNX,
        frozenset(("apex", "base_2")): AspectType.QUINCUNX,
    },
    ConfigurationType.BISEXTILE: {
        frozenset(("center", "wing_1")): AspectType.SEXTILE,
        frozenset(("center", "wing_2")): AspectType.SEXTILE,
        frozenset(("wing_1", "wing_2")): AspectType.TRINE,
    },
    ConfigurationType.GRAND_TRINE: {
        frozenset(("point_1", "point_2")): AspectType.TRINE,
        frozenset(("point_1", "point_3")): AspectType.TRINE,
        frozenset(("point_2", "point_3")): AspectType.TRINE,
    },
    ConfigurationType.GRAND_CROSS: {
        frozenset(("axis_1_a", "axis_1_b")): AspectType.OPPOSITION,
        frozenset(("axis_2_a", "axis_2_b")): AspectType.OPPOSITION,
        frozenset(("axis_1_a", "axis_2_a")): AspectType.SQUARE,
        frozenset(("axis_1_a", "axis_2_b")): AspectType.SQUARE,
        frozenset(("axis_1_b", "axis_2_a")): AspectType.SQUARE,
        frozenset(("axis_1_b", "axis_2_b")): AspectType.SQUARE,
    },
    ConfigurationType.TRAPEZE: {
        frozenset(("opposition_1", "opposition_2")): AspectType.OPPOSITION,
    },
}
TRAPEZE_REMAINING_EDGE_TYPE_COUNTS = Counter(
    {AspectType.TRINE: 2, AspectType.SEXTILE: 3}
)


def point_key(point: AspectPointRef) -> PointKey:
    return point.chart, point.body


def pair_key(left: PointKey, right: PointKey) -> frozenset[PointKey]:
    return frozenset((left, right))


def sorted_points(points: Iterable[PointKey]) -> tuple[PointKey, ...]:
    return tuple(sorted(points, key=lambda item: (item[0], item[1])))


def build_configuration(
    config_type: ConfigurationType,
    roles: dict[str, PointKey],
    aspects: Iterable[Aspect],
    graph,
    config: ConfigurationConfig,
    *,
    element: str | None = None,
    modality: str | None = None,
) -> Configuration:
    aspect_tuple = tuple(sorted(aspects, key=aspect_sort_key))
    point_refs = {role: graph.point_ref(point) for role, point in roles.items()}
    charts = {point.chart for point in point_refs.values()}
    max_orb = max(aspect.orb for aspect in aspect_tuple)
    return Configuration(
        type=config_type,
        points=point_refs,
        aspects=aspect_tuple,
        max_orb=max_orb,
        category=categorize_configuration(max_orb, config),
        chart=charts.pop() if len(charts) == 1 else "mixed",
        element=element,
        modality=modality,
    )


def matches_role_topology(
    config_type: ConfigurationType,
    roles: Mapping[str, PointKey],
    aspects: Iterable[Aspect],
) -> bool:
    """Return whether role names and aspect edges define the requested figure."""

    if frozenset(roles) != CONFIGURATION_ROLE_SETS[config_type]:
        return False

    point_roles = {point: role for role, point in roles.items()}
    if len(point_roles) != len(roles):
        return False

    actual: dict[frozenset[str], AspectType] = {}
    for aspect in aspects:
        try:
            role_pair = frozenset(
                (
                    point_roles[point_key(aspect.from_point)],
                    point_roles[point_key(aspect.to_point)],
                )
            )
        except KeyError:
            return False
        if len(role_pair) != 2 or role_pair in actual:
            return False
        actual[role_pair] = aspect.aspect_type

    if config_type is ConfigurationType.TRAPEZE:
        opposition_pair = frozenset(("opposition_1", "opposition_2"))
        opposition_type = role_edge_type(
            config_type,
            "opposition_1",
            "opposition_2",
        )
        remaining = Counter(
            aspect_type
            for pair, aspect_type in actual.items()
            if pair != opposition_pair
        )
        return (
            len(actual) == 6
            and actual.get(opposition_pair) is opposition_type
            and remaining == TRAPEZE_REMAINING_EDGE_TYPE_COUNTS
        )

    return actual == CONFIGURATION_ROLE_EDGE_TYPES[config_type]


def role_edge_type(
    config_type: ConfigurationType,
    left_role: str,
    right_role: str,
) -> AspectType:
    """Return the canonical aspect type for a named configuration edge."""

    return CONFIGURATION_ROLE_EDGE_TYPES[config_type][
        frozenset((left_role, right_role))
    ]


def categorize_configuration(orb: float, config: ConfigurationConfig) -> ConfigurationCategory:
    """Classify a configuration by its worst edge orb."""

    thresholds = config.configuration_categories
    if orb < thresholds.tight:
        return ConfigurationCategory.TIGHT
    if orb <= thresholds.moderate:
        return ConfigurationCategory.MODERATE
    return ConfigurationCategory.LOOSE


def aspect_sort_key(aspect: Aspect) -> tuple[str, str, str]:
    left = point_key(aspect.from_point)
    right = point_key(aspect.to_point)
    first, second = sorted_points((left, right))
    return first[0], first[1], second[1]


def participants_key(configuration: Configuration) -> frozenset[PointKey]:
    return frozenset(point_key(point) for point in configuration.points.values())


def canonical_configuration_key(configuration: Configuration) -> tuple[str, tuple[PointKey, ...]]:
    return configuration.type.value, sorted_points(participants_key(configuration))


def shared_element(points: Iterable[PointKey], config: ConfigurationConfig) -> str | None:
    values = {_element_for(point, config) for point in points}
    values.discard(None)
    if len(values) == 1:
        return values.pop()
    return None


def shared_modality(points: Iterable[PointKey], config: ConfigurationConfig) -> str | None:
    values = {_modality_for(point, config) for point in points}
    values.discard(None)
    if len(values) == 1:
        return values.pop()
    return None


def _element_for(point: PointKey, config: ConfigurationConfig) -> str | None:
    sign_index = _sign_index_for(point, config)
    if sign_index is None:
        return None
    return ELEMENTS[sign_index % 4]


def _modality_for(point: PointKey, config: ConfigurationConfig) -> str | None:
    sign_index = _sign_index_for(point, config)
    if sign_index is None:
        return None
    return MODALITIES[sign_index % 3]


def _sign_index_for(point: PointKey, config: ConfigurationConfig) -> int | None:
    chart, body = point
    value = config.point_signs.get(f"{chart}:{body}", config.point_signs.get(body))
    if value is None:
        return None
    return value % 12
