"""T-square pattern detection."""

from __future__ import annotations

from ..types import Configuration, ConfigurationConfig, ConfigurationType
from .common import build_configuration, role_edge_type, sorted_points


def find(graph, config: ConfigurationConfig) -> list[Configuration]:
    results: list[Configuration] = []
    seen: set[tuple[tuple[str, str], ...]] = set()
    opposition_type = role_edge_type(
        ConfigurationType.T_SQUARE, "base_1", "base_2"
    )
    square_type = role_edge_type(ConfigurationType.T_SQUARE, "apex", "base_1")

    for base_1, base_2, opposition in graph.edges_of_type(opposition_type):
        for apex in graph.points:
            if apex in {base_1, base_2}:
                continue
            square_1 = graph.aspect_between(apex, base_1, square_type)
            square_2 = graph.aspect_between(apex, base_2, square_type)
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
