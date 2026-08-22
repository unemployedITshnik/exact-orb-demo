"""Shared helpers for configuration pattern modules."""

from __future__ import annotations

from collections.abc import Iterable

from exact_orb.engine.aspects import Aspect, AspectPointRef

from ..types import (
    Configuration,
    ConfigurationCategory,
    ConfigurationConfig,
    ConfigurationType,
    PointKey,
)


ELEMENTS = ("fire", "earth", "air", "water")
MODALITIES = ("cardinal", "fixed", "mutable")


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
