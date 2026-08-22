"""Differentiated orb resolution."""

from __future__ import annotations

from .types import AspectOrbSet, AspectType, PositionedPoint


def resolve_orb(
    aspect_type: AspectType,
    from_point: PositionedPoint,
    to_point: PositionedPoint,
    orb_set: AspectOrbSet,
) -> float:
    """Return the effective maximum orb for an aspect candidate.

    Resolution is intentionally conservative. First, an
    ``aspect_body_overrides`` entry for ``(aspect_type, body)`` replaces that
    body's normal limit for each endpoint where it exists. Then the effective
    maximum is the minimum of the context maximum, the aspect's own orb, and
    both endpoint body limits. This lets luminaries keep wider defaults while
    fictitious points or specific aspect/body combinations stay narrower.
    """

    if orb_set.max_orb <= 0.0:
        return 0.0

    aspect_limit = orb_set.aspect_orbs.get(aspect_type, orb_set.max_orb)
    from_limit = _body_limit(aspect_type, from_point.body, orb_set)
    to_limit = _body_limit(aspect_type, to_point.body, orb_set)
    return min(orb_set.max_orb, aspect_limit, from_limit, to_limit)


def _body_limit(aspect_type: AspectType, body: str, orb_set: AspectOrbSet) -> float:
    overrides = orb_set.aspect_body_overrides.get(aspect_type, {})
    if body in overrides:
        return overrides[body]
    return orb_set.body_orbs.get(body, orb_set.max_orb)
