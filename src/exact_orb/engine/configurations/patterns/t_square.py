"""T-square pattern detection."""

from __future__ import annotations

from exact_orb.engine.aspects import AspectType

from ..types import Configuration, ConfigurationConfig, ConfigurationType
from .common import build_configuration, sorted_points


def find(graph, config: ConfigurationConfig) -> list[Configuration]:
    results: list[Configuration] = []
    seen: set[tuple[tuple[str, str], ...]] = set()

    for base_1, base_2, opposition in graph.edges_of_type(AspectType.OPPOSITION):
        for apex in graph.points:
            if apex in {base_1, base_2}:
                continue
            square_1 = graph.aspect_between(apex, base_1, AspectType.SQUARE)
            square_2 = graph.aspect_between(apex, base_2, AspectType.SQUARE)
            if square_1 is None or square_2 is None:
                continue

            base_a, base_b = sorted_points((base_1, base_2))
            key = (apex, base_a, base_b)
            if key in seen:
                continue
            seen.add(key)
            results.append(
                build_configuration(
                    ConfigurationType.T_SQUARE,
                    {"apex": apex, "base_1": base_a, "base_2": base_b},
                    (opposition, square_1, square_2),
                    graph,
                    config,
                )
            )

    return results
