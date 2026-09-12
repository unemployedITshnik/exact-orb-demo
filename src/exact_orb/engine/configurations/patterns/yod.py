"""Yod pattern detection."""

from __future__ import annotations

from itertools import combinations

from ..types import Configuration, ConfigurationConfig, ConfigurationType
from .common import build_configuration, role_edge_type, sorted_points


def find(graph, config: ConfigurationConfig) -> list[Configuration]:
    results: list[Configuration] = []
    seen: set[tuple[tuple[str, str], ...]] = set()
    quincunx_type = role_edge_type(ConfigurationType.YOD, "apex", "base_1")
    sextile_type = role_edge_type(ConfigurationType.YOD, "base_1", "base_2")

    for apex in graph.points:
        candidates = [point for point in graph.points if point != apex]
        for base_1, base_2 in combinations(candidates, 2):
            quincunx_1 = graph.aspect_between(apex, base_1, quincunx_type)
            quincunx_2 = graph.aspect_between(apex, base_2, quincunx_type)
            sextile = graph.aspect_between(base_1, base_2, sextile_type)
            if quincunx_1 is None or quincunx_2 is None or sextile is None:
                continue

            base_a, base_b = sorted_points((base_1, base_2))
            key = (apex, base_a, base_b)
            if key in seen:
                continue
            seen.add(key)
            results.append(
                build_configuration(
                    ConfigurationType.YOD,
                    {"apex": apex, "base_1": base_a, "base_2": base_b},
                    (quincunx_1, quincunx_2, sextile),
                    graph,
                    config,
                )
            )

    return results
