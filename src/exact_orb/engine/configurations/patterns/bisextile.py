"""Bisextile pattern detection.

The source labels examples as a name triple with a visual middle point, but
the checked geometry is stricter and unambiguous here: a bisextile is a
three-point triangle with two sextiles sharing one geometric center and the
remaining side closed by a trine.
"""

from __future__ import annotations

from itertools import combinations

from exact_orb.engine.aspects import AspectType

from ..types import Configuration, ConfigurationConfig, ConfigurationType
from .common import build_configuration, sorted_points


def find(graph, config: ConfigurationConfig) -> list[Configuration]:
    results: list[Configuration] = []
    seen: set[frozenset[tuple[str, str]]] = set()

    for point_1, point_2, point_3 in combinations(graph.points, 3):
        points = (point_1, point_2, point_3)
        sextiles = [
            (left, right, aspect)
            for left, right in combinations(points, 2)
            if (aspect := graph.aspect_between(left, right, AspectType.SEXTILE)) is not None
        ]
        trines = [
            (left, right, aspect)
            for left, right in combinations(points, 2)
            if (aspect := graph.aspect_between(left, right, AspectType.TRINE)) is not None
        ]
        if len(sextiles) != 2 or len(trines) != 1:
            continue

        centers = set(sextiles[0][:2]) & set(sextiles[1][:2])
        if len(centers) != 1:
            continue
        center = centers.pop()
        wings = sorted_points(point for point in points if point != center)
        key = frozenset(points)
        if key in seen:
            continue
        seen.add(key)
        results.append(
            build_configuration(
                ConfigurationType.BISEXTILE,
                {"center": center, "wing_1": wings[0], "wing_2": wings[1]},
                (sextiles[0][2], sextiles[1][2], trines[0][2]),
                graph,
                config,
            )
        )

    return results
