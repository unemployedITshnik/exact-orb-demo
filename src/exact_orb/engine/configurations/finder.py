"""Find aspect configurations from existing aspect edges."""

from __future__ import annotations

from collections.abc import Iterable
import logging

from exact_orb.engine.aspects import Aspect, AspectPointRef, AspectType

from .patterns import bisextile, grand_cross, grand_trine, t_square, trapeze, yod
from .patterns.common import canonical_configuration_key, pair_key, participants_key, point_key
from .types import Configuration, ConfigurationConfig, ConfigurationType, PointKey


LOGGER = logging.getLogger(__name__)
PATTERN_FINDERS = {
    ConfigurationType.GRAND_CROSS: grand_cross.find,
    ConfigurationType.TRAPEZE: trapeze.find,
    ConfigurationType.GRAND_TRINE: grand_trine.find,
    ConfigurationType.T_SQUARE: t_square.find,
    ConfigurationType.YOD: yod.find,
    ConfigurationType.BISEXTILE: bisextile.find,
}


def find_configurations(
    aspects: list[Aspect],
    config: ConfigurationConfig,
) -> list[Configuration]:
    """Assemble aspect configurations from already calculated aspects.

    The module deliberately treats aspects as graph edges and never
    recalculates longitude geometry. By default, grand crosses suppress their
    nested T-squares from top-level output; those nested figures are attached
    to the senior configuration's ``contains`` field.
    """

    LOGGER.debug(
        "find_configurations start aspects=%d max_orb=%.3f include_nested=%s enabled_types=%s",
        len(aspects),
        config.configuration_max_orb,
        config.include_nested,
        tuple(item.value for item in config.enabled_types),
    )
    if not aspects or config.configuration_max_orb <= 0.0:
        LOGGER.debug("find_configurations skipped reason=%s", "no_aspects_or_nonpositive_max_orb")
        return []

    graph = AspectGraph(
        aspects,
        max_orb=config.configuration_max_orb,
        allowed_points=config.points,
    )
    if not graph.edges:
        LOGGER.debug("find_configurations skipped reason=%s points=%d", "empty_graph", len(graph.points))
        return []

    found: list[Configuration] = []
    for config_type in config.enabled_types:
        finder = PATTERN_FINDERS.get(config_type)
        if finder is None:
            continue
        found.extend(finder(graph, config))

    deduped = _dedupe(found)
    LOGGER.debug(
        "find_configurations candidates=%d deduped=%d graph_points=%d graph_edges=%d",
        len(found),
        len(deduped),
        len(graph.points),
        len(graph.edges),
    )
    if not config.include_nested:
        deduped = _attach_and_suppress_nested(deduped)
    result = sorted(deduped, key=_sort_key)
    LOGGER.debug("find_configurations complete configurations=%d", len(result))
    return result


class AspectGraph:
    """Indexed aspect graph filtered to the configuration orb threshold."""

    def __init__(
        self,
        aspects: Iterable[Aspect],
        *,
        max_orb: float,
        allowed_points: tuple[str, ...] | None,
    ) -> None:
        self.points: tuple[PointKey, ...] = ()
        self.edges: dict[tuple[frozenset[PointKey], AspectType], Aspect] = {}
        point_refs: dict[PointKey, AspectPointRef] = {}
        allowed = set(allowed_points) if allowed_points is not None else None

        for aspect in aspects:
            if aspect.orb > max_orb:
                continue
            left = point_key(aspect.from_point)
            right = point_key(aspect.to_point)
            if allowed is not None and (left[1] not in allowed or right[1] not in allowed):
                continue
            if left == right:
                continue
            point_refs.setdefault(left, aspect.from_point)
            point_refs.setdefault(right, aspect.to_point)
            key = (pair_key(left, right), aspect.aspect_type)
            existing = self.edges.get(key)
            if existing is None or aspect.orb < existing.orb:
                self.edges[key] = aspect

        self._point_refs = point_refs
        self.points = tuple(sorted(point_refs, key=lambda item: (item[0], item[1])))

    def point_ref(self, point: PointKey) -> AspectPointRef:
        return self._point_refs[point]

    def aspect_between(
        self,
        left: PointKey,
        right: PointKey,
        aspect_type: AspectType,
    ) -> Aspect | None:
        return self.edges.get((pair_key(left, right), aspect_type))

    def edges_of_type(self, aspect_type: AspectType) -> tuple[tuple[PointKey, PointKey, Aspect], ...]:
        matches: list[tuple[PointKey, PointKey, Aspect]] = []
        for edge_key, edge_type in self.edges:
            if edge_type != aspect_type:
                continue
            left, right = tuple(edge_key)
            if (right[0], right[1]) < (left[0], left[1]):
                left, right = right, left
            matches.append((left, right, self.edges[(edge_key, edge_type)]))
        return tuple(sorted(matches, key=lambda item: (item[0], item[1])))


def _dedupe(configurations: Iterable[Configuration]) -> list[Configuration]:
    deduped: dict[tuple[str, tuple[PointKey, ...]], Configuration] = {}
    for configuration in configurations:
        key = canonical_configuration_key(configuration)
        existing = deduped.get(key)
        if existing is None or configuration.max_orb < existing.max_orb:
            deduped[key] = configuration
    return list(deduped.values())


def _attach_and_suppress_nested(configurations: list[Configuration]) -> list[Configuration]:
    grand_crosses = [
        configuration
        for configuration in configurations
        if configuration.type is ConfigurationType.GRAND_CROSS
    ]
    if not grand_crosses:
        return configurations

    suppressed: set[tuple[str, tuple[PointKey, ...]]] = set()
    enriched: list[Configuration] = []
    for configuration in configurations:
        if configuration.type is not ConfigurationType.GRAND_CROSS:
            continue

        participants = participants_key(configuration)
        nested = tuple(
            candidate
            for candidate in configurations
            if candidate.type is ConfigurationType.T_SQUARE
            and participants_key(candidate).issubset(participants)
        )
        for candidate in nested:
            suppressed.add(canonical_configuration_key(candidate))
        enriched.append(configuration.model_copy(update={"contains": nested}))

    result: list[Configuration] = []
    grand_cross_keys = {canonical_configuration_key(item) for item in grand_crosses}
    for configuration in configurations:
        key = canonical_configuration_key(configuration)
        if key in suppressed:
            continue
        if key in grand_cross_keys:
            enriched_item = next(item for item in enriched if canonical_configuration_key(item) == key)
            result.append(enriched_item)
            continue
        result.append(configuration)
    return result


def _sort_key(configuration: Configuration) -> tuple[str, float, tuple[PointKey, ...]]:
    return (
        configuration.type.value,
        configuration.max_orb,
        tuple(sorted(participants_key(configuration), key=lambda item: (item[0], item[1]))),
    )
