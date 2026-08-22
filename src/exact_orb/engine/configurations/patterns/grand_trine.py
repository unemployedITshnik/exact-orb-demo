"""Grand trine pattern detection."""

from __future__ import annotations

from itertools import combinations

from exact_orb.engine.aspects import AspectType

from ..types import Configuration, ConfigurationConfig, ConfigurationType
from .common import build_configuration, shared_element, sorted_points


def find(graph, config: ConfigurationConfig) -> list[Configuration]:
    results: list[Configuration] = []

    for points in combinations(graph.points, 3):
        trines = [
            graph.aspect_between(left, right, AspectType.TRINE)
            for left, right in combinations(points, 2)
        ]
        if any(aspect is None for aspect in trines):
            continue

        point_1, point_2, point_3 = sorted_points(points)
        results.append(
            build_configuration(
                ConfigurationType.GRAND_TRINE,
                {"point_1": point_1, "point_2": point_2, "point_3": point_3},
                tuple(aspect for aspect in trines if aspect is not None),
                graph,
                config,
                element=shared_element(points, config),
            )
        )

    return results
