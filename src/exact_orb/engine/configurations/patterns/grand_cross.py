"""Grand cross pattern detection."""

from __future__ import annotations

from itertools import combinations

from exact_orb.engine.aspects import AspectType

from ..types import Configuration, ConfigurationConfig, ConfigurationType
from .common import build_configuration, shared_modality, sorted_points


def find(graph, config: ConfigurationConfig) -> list[Configuration]:
    results: list[Configuration] = []
    seen: set[frozenset[tuple[str, str]]] = set()

    for points in combinations(graph.points, 4):
        opposition_pairs = [
            (left, right, aspect)
            for left, right in combinations(points, 2)
            if (aspect := graph.aspect_between(left, right, AspectType.OPPOSITION)) is not None
        ]
        if len(opposition_pairs) != 2:
            continue

        pair_1, pair_2 = opposition_pairs
        axis_1 = set(pair_1[:2])
        axis_2 = set(pair_2[:2])
        if axis_1 & axis_2:
            continue

        squares = []
        for left in axis_1:
            for right in axis_2:
                square = graph.aspect_between(left, right, AspectType.SQUARE)
                if square is None:
                    break
                squares.append(square)
            else:
                continue
            break
        if len(squares) != 4:
            continue

        key = frozenset(points)
        if key in seen:
            continue
        seen.add(key)
        axis_1_a, axis_1_b = sorted_points(axis_1)
        axis_2_a, axis_2_b = sorted_points(axis_2)
        results.append(
            build_configuration(
                ConfigurationType.GRAND_CROSS,
                {
                    "axis_1_a": axis_1_a,
                    "axis_1_b": axis_1_b,
                    "axis_2_a": axis_2_a,
                    "axis_2_b": axis_2_b,
                },
                (pair_1[2], pair_2[2], *squares),
                graph,
                config,
                modality=shared_modality(points, config),
            )
        )

    return results
