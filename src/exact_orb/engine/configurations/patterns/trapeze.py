"""Trapeze pattern detection.

The reference geometry is a complete four-point graph with one opposition,
two trines, and three sextiles. This definition intentionally follows the
verified edge set instead of relying on ambiguous source naming.
"""

from __future__ import annotations

from itertools import combinations

from exact_orb.engine.aspects import AspectType

from ..types import Configuration, ConfigurationConfig, ConfigurationType
from .common import (
    TRAPEZE_REMAINING_EDGE_TYPE_COUNTS,
    build_configuration,
    role_edge_type,
    sorted_points,
)


def find(graph, config: ConfigurationConfig) -> list[Configuration]:
    results: list[Configuration] = []
    opposition_type = role_edge_type(
        ConfigurationType.TRAPEZE, "opposition_1", "opposition_2"
    )
    trine_count = TRAPEZE_REMAINING_EDGE_TYPE_COUNTS[AspectType.TRINE]
    sextile_count = TRAPEZE_REMAINING_EDGE_TYPE_COUNTS[AspectType.SEXTILE]

    for points in combinations(graph.points, 4):
        opposition_edges = []
        trine_edges = []
        sextile_edges = []

        for left, right in combinations(points, 2):
            if (aspect := graph.aspect_between(left, right, opposition_type)) is not None:
                opposition_edges.append((left, right, aspect))
            if (aspect := graph.aspect_between(left, right, AspectType.TRINE)) is not None:
                trine_edges.append(aspect)
            if (aspect := graph.aspect_between(left, right, AspectType.SEXTILE)) is not None:
                sextile_edges.append(aspect)

        if (
            len(opposition_edges) != 1
            or len(trine_edges) != trine_count
            or len(sextile_edges) != sextile_count
        ):
            continue

        opposition_1, opposition_2, opposition = opposition_edges[0]
        base_points = sorted_points(point for point in points if point not in {opposition_1, opposition_2})
        opposition_points = sorted_points((opposition_1, opposition_2))
        results.append(
            build_configuration(
                ConfigurationType.TRAPEZE,
                {
                    "opposition_1": opposition_points[0],
                    "opposition_2": opposition_points[1],
                    "base_1": base_points[0],
                    "base_2": base_points[1],
                },
                (opposition, *trine_edges, *sextile_edges),
                graph,
                config,
            )
        )

    return results
