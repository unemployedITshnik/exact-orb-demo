"""Cross-object integrity checks for materialized aspect configurations."""

from __future__ import annotations

from collections.abc import Sequence
from itertools import combinations

from exact_orb.engine.aspects import Aspect

from .patterns.common import (
    CONFIGURATION_ROLE_SETS,
    matches_role_topology,
    point_key,
)
from .types import Configuration, ConfigurationType, PointKey


def validate_configuration_tree(
    configuration: Configuration,
    canonical_aspects: Sequence[Aspect],
    path: str,
) -> None:
    """Require one configuration tree to materialize canonical chart aspects."""

    _validate_configuration(configuration, canonical_aspects, path)

    if configuration.contains and configuration.type is not ConfigurationType.GRAND_CROSS:
        raise ValueError(f"{path}.contains is only valid for grand_cross")

    parent_points = _configuration_points(configuration)
    for index, nested in enumerate(configuration.contains):
        nested_path = f"{path}.contains[{index}]"
        if nested.type is not ConfigurationType.T_SQUARE:
            raise ValueError(f"{nested_path}.type must be t_square")
        nested_points = _configuration_points(nested)
        if not nested_points < parent_points:
            raise ValueError(
                f"{nested_path}.points must be a strict subset of {path}.points"
            )
        for aspect_index, aspect in enumerate(nested.aspects):
            if aspect not in configuration.aspects:
                raise ValueError(
                    f"{nested_path}.aspects[{aspect_index}] must materialize an aspect "
                    f"from {path}.aspects"
                )
        if nested.contains:
            raise ValueError(f"{nested_path}.contains must be empty")
        validate_configuration_tree(nested, canonical_aspects, nested_path)


def _validate_configuration(
    configuration: Configuration,
    canonical_aspects: Sequence[Aspect],
    path: str,
) -> None:
    expected_roles = CONFIGURATION_ROLE_SETS[configuration.type]
    actual_roles = frozenset(configuration.points)
    if actual_roles != expected_roles:
        raise ValueError(
            f"{path}.points roles must equal {sorted(expected_roles)}; "
            f"got {sorted(actual_roles)}"
        )

    role_points = tuple(point_key(point) for point in configuration.points.values())
    if len(set(role_points)) != len(role_points):
        raise ValueError(f"{path}.points must reference distinct participants")

    if not configuration.aspects:
        raise ValueError(f"{path}.aspects must not be empty")

    edge_by_pair: dict[frozenset[PointKey], Aspect] = {}
    endpoint_points: set[PointKey] = set()
    for index, aspect in enumerate(configuration.aspects):
        aspect_path = f"{path}.aspects[{index}]"
        if aspect not in canonical_aspects:
            raise ValueError(
                f"{aspect_path} must exactly materialize an aspect from NatalChart.aspects"
            )
        pair = _aspect_pair(aspect)
        if len(pair) != 2:
            raise ValueError(f"{aspect_path} must connect two distinct points")
        if pair in edge_by_pair:
            raise ValueError(f"{aspect_path} duplicates a participant pair")
        edge_by_pair[pair] = aspect
        endpoint_points.update(pair)

    points = set(role_points)
    if endpoint_points != points:
        raise ValueError(
            f"{path}.points must equal the endpoints of {path}.aspects"
        )

    expected_pairs = {frozenset(pair) for pair in combinations(points, 2)}
    if set(edge_by_pair) != expected_pairs:
        raise ValueError(
            f"{path}.aspects must contain exactly one edge for every participant pair"
        )

    if not matches_role_topology(
        configuration.type,
        {role: point_key(point) for role, point in configuration.points.items()},
        configuration.aspects,
    ):
        raise ValueError(
            f"{path}.aspects do not match the role topology "
            f"for {configuration.type.value}"
        )

    expected_max_orb = max(aspect.orb for aspect in configuration.aspects)
    if configuration.max_orb != expected_max_orb:
        raise ValueError(
            f"{path}.max_orb must equal the maximum orb of {path}.aspects"
        )

    charts = {point.chart for point in configuration.points.values()}
    expected_chart = charts.pop() if len(charts) == 1 else "mixed"
    if configuration.chart != expected_chart:
        raise ValueError(
            f"{path}.chart must equal {expected_chart!r} for {path}.points"
        )


def _configuration_points(configuration: Configuration) -> set[PointKey]:
    return {point_key(point) for point in configuration.points.values()}


def _aspect_pair(aspect: Aspect) -> frozenset[PointKey]:
    return _pair(point_key(aspect.from_point), point_key(aspect.to_point))


def _pair(left: PointKey, right: PointKey) -> frozenset[PointKey]:
    return frozenset((left, right))


__all__ = ["CONFIGURATION_ROLE_SETS", "validate_configuration_tree"]
