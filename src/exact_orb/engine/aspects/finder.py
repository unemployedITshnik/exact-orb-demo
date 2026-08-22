"""Shared aspect finder for natal and cross-chart calculations."""

from __future__ import annotations

from collections.abc import Sequence
import logging
from math import isfinite

from .categories import categorize_orb
from .orbs import resolve_orb
from .types import (
    ASPECT_ANGLES,
    ASPECT_PRIORITY,
    Aspect,
    AspectConfig,
    AspectType,
    PositionedPoint,
)


LOGGER = logging.getLogger(__name__)
FULL_CIRCLE = 360.0
HALF_CIRCLE = 180.0
ORB_EPSILON = 1e-9


def find_aspects(
    set_a: Sequence[PositionedPoint],
    set_b: Sequence[PositionedPoint] | None,
    config: AspectConfig,
) -> list[Aspect]:
    """Find aspects inside one point set or between two point sets."""

    LOGGER.debug(
        "find_aspects start set_a=%d set_b=%s mode=%s max_orb=%.3f",
        len(set_a),
        len(set_b) if set_b is not None else None,
        config.mode,
        config.active_orbs.max_orb,
    )
    pairs = _single_set_pairs(set_a) if set_b is None else _cross_set_pairs(set_a, set_b)
    aspects: list[Aspect] = []
    orb_set = config.active_orbs

    for from_point, to_point in pairs:
        hit = _closest_allowed_aspect(from_point, to_point, config)
        if hit is None:
            continue

        aspect_type, exact_angle, orb = hit
        if orb_set.max_orb <= 0.0:
            continue
        aspects.append(
            Aspect(
                from_point=from_point.ref,
                to_point=to_point.ref,
                aspect_type=aspect_type,
                exact_angle=exact_angle,
                orb=orb,
                category=categorize_orb(orb, config.categories),
                applying=None,
            )
        )

    result = sorted(aspects, key=_sort_key)
    LOGGER.debug("find_aspects complete pairs=%d aspects=%d", len(pairs), len(result))
    return result


def _single_set_pairs(
    points: Sequence[PositionedPoint],
) -> list[tuple[PositionedPoint, PositionedPoint]]:
    pairs: list[tuple[PositionedPoint, PositionedPoint]] = []
    for left_index, from_point in enumerate(points):
        for to_point in points[left_index + 1 :]:
            if _same_point(from_point, to_point):
                continue
            pairs.append((from_point, to_point))
    return pairs


def _cross_set_pairs(
    set_a: Sequence[PositionedPoint],
    set_b: Sequence[PositionedPoint],
) -> list[tuple[PositionedPoint, PositionedPoint]]:
    pairs: list[tuple[PositionedPoint, PositionedPoint]] = []
    for from_point in set_a:
        for to_point in set_b:
            if _same_point(from_point, to_point):
                continue
            pairs.append((from_point, to_point))
    return pairs


def _closest_allowed_aspect(
    from_point: PositionedPoint,
    to_point: PositionedPoint,
    config: AspectConfig,
) -> tuple[AspectType, float, float] | None:
    distance = angular_distance(from_point.longitude, to_point.longitude)
    candidates: list[tuple[float, int, AspectType, float]] = []

    for aspect_type, exact_angle in ASPECT_ANGLES.items():
        orb = abs(distance - exact_angle)
        allowed_orb = resolve_orb(aspect_type, from_point, to_point, config.active_orbs)
        if allowed_orb <= 0.0:
            continue
        if orb <= allowed_orb + ORB_EPSILON:
            candidates.append((orb, ASPECT_PRIORITY[aspect_type], aspect_type, exact_angle))

    if not candidates:
        return None

    orb, _, aspect_type, exact_angle = min(candidates, key=lambda item: (item[0], item[1]))
    return aspect_type, exact_angle, orb


def angular_distance(left_longitude: float, right_longitude: float) -> float:
    """Return shortest angular distance in degrees across the 0 Aries boundary."""

    if not isfinite(left_longitude) or not isfinite(right_longitude):
        raise ValueError("longitude values must be finite")
    distance = abs((left_longitude - right_longitude) % FULL_CIRCLE)
    if distance > HALF_CIRCLE:
        return FULL_CIRCLE - distance
    return distance


def _same_point(left: PositionedPoint, right: PositionedPoint) -> bool:
    return left.chart == right.chart and left.body == right.body


def _sort_key(aspect: Aspect) -> tuple[int, float, str, str, int]:
    category_order = {"exact": 0, "working": 1, "background": 2}
    return (
        category_order[aspect.category.value],
        aspect.orb,
        aspect.from_point.body,
        aspect.to_point.body,
        ASPECT_PRIORITY[aspect.aspect_type],
    )
