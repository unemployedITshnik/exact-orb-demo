"""Yod pattern detection."""

from __future__ import annotations

from itertools import combinations

from exact_orb.engine.aspects import AspectType

from ..types import Configuration, ConfigurationConfig, ConfigurationType
from .common import build_configuration, sorted_points


def find(graph, config: ConfigurationConfig) -> list[Configuration]:
    results: list[Configuration] = []
    seen: set[tuple[tuple[str, str], ...]] = set()

    for apex in graph.points:
        candidates = [point for point in graph.points if point != apex]
        for base_1, base_2 in combinations(candidates, 2):
            quincunx_1 = graph.aspect_between(apex, base_1, AspectType.QUINCUNX)
            quincunx_2 = graph.aspect_between(apex, base_2, AspectType.QUINCUNX)
            sextile = graph.aspect_between(base_1, base_2, AspectType.SEXTILE)
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
