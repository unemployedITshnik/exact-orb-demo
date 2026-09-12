"""Typed cosmogram uncertainty and pure all-minute aspect aggregation."""

from __future__ import annotations

from collections.abc import Sequence
from enum import Enum

from pydantic import BaseModel, ConfigDict, model_validator

from exact_orb.birth.types import BirthTimeDomain
from exact_orb.engine.aspects import (
    Aspect,
    AspectCategory,
    AspectConfig,
    AspectPointRef,
    AspectType,
    PositionedPoint,
    aspect_sort_key,
    find_aspects,
)
from exact_orb.engine.aspects.types import ASPECT_PRIORITY


class UnstableAspectReason(str, Enum):
    """Closed reasons why a pair cannot be published for a cosmogram."""

    NOT_PRESENT_FOR_ALL_TIMES = "not_present_for_all_times"
    ASPECT_TYPE_CHANGED = "aspect_type_changed"
    CATEGORY_CHANGED = "category_changed"


_REASON_ORDER = {
    reason: index for index, reason in enumerate(UnstableAspectReason)
}
_CATEGORY_ORDER = {
    AspectCategory.EXACT: 0,
    AspectCategory.WORKING: 1,
    AspectCategory.BACKGROUND: 2,
}


class UnstableAspect(BaseModel):
    """Machine-readable evidence for one excluded cosmogram pair."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    from_point: AspectPointRef
    to_point: AspectPointRef
    reasons: tuple[UnstableAspectReason, ...]
    includes_no_aspect: bool
    possible_aspect_types: tuple[AspectType, ...]
    possible_categories: tuple[AspectCategory, ...]

    @model_validator(mode="after")
    def _diagnostic_must_be_canonical(self) -> "UnstableAspect":
        if self.from_point == self.to_point:
            raise ValueError("unstable aspect endpoints must be distinct")
        if not self.reasons:
            raise ValueError("unstable aspect reasons must not be empty")
        if tuple(sorted(set(self.reasons), key=_REASON_ORDER.__getitem__)) != self.reasons:
            raise ValueError("unstable aspect reasons must be unique and sorted")
        if not self.possible_aspect_types or not self.possible_categories:
            raise ValueError("unstable aspect possible values must not be empty")
        if tuple(
            sorted(set(self.possible_aspect_types), key=ASPECT_PRIORITY.__getitem__)
        ) != self.possible_aspect_types:
            raise ValueError("possible_aspect_types must be unique and sorted")
        if tuple(
            sorted(set(self.possible_categories), key=_CATEGORY_ORDER.__getitem__)
        ) != self.possible_categories:
            raise ValueError("possible_categories must be unique and sorted")

        expected_reasons: list[UnstableAspectReason] = []
        if self.includes_no_aspect:
            expected_reasons.append(UnstableAspectReason.NOT_PRESENT_FOR_ALL_TIMES)
        if len(self.possible_aspect_types) > 1:
            expected_reasons.append(UnstableAspectReason.ASPECT_TYPE_CHANGED)
        if len(self.possible_categories) > 1:
            expected_reasons.append(UnstableAspectReason.CATEGORY_CHANGED)
        if tuple(expected_reasons) != self.reasons:
            raise ValueError("unstable aspect reasons do not match possible states")
        return self


class CosmogramTimeUncertainty(BaseModel):
    """Domain evaluated for a cosmogram and its excluded relationship facts."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    domain: BirthTimeDomain
    excluded_aspects: tuple[UnstableAspect, ...] | None


def find_time_stable_aspects(
    point_snapshots: Sequence[Sequence[PositionedPoint]],
    config: AspectConfig,
) -> tuple[tuple[Aspect, ...], tuple[UnstableAspect, ...]]:
    """Publish only pair states invariant across every supplied snapshot."""

    if not point_snapshots:
        raise ValueError("point_snapshots must not be empty")

    expected_refs = tuple(point.ref for point in point_snapshots[0])
    if len(set((ref.chart, ref.body) for ref in expected_refs)) != len(expected_refs):
        raise ValueError("snapshot point references must be unique")

    observations: dict[
        tuple[str, str, str, str],
        list[Aspect],
    ] = {}
    snapshot_count = len(point_snapshots)
    for snapshot in point_snapshots:
        refs = tuple(point.ref for point in snapshot)
        if refs != expected_refs:
            raise ValueError("all snapshots must contain the same ordered point set")
        for aspect in find_aspects(snapshot, None, config):
            observations.setdefault(_aspect_pair_key(aspect), []).append(aspect)

    stable: list[Aspect] = []
    excluded: list[UnstableAspect] = []
    for observed in observations.values():
        aspect_types = tuple(
            sorted({item.aspect_type for item in observed}, key=ASPECT_PRIORITY.__getitem__)
        )
        categories = tuple(
            sorted({item.category for item in observed}, key=_CATEGORY_ORDER.__getitem__)
        )
        includes_no_aspect = len(observed) != snapshot_count
        if not includes_no_aspect and len(aspect_types) == 1 and len(categories) == 1:
            widest = max(observed, key=lambda item: item.orb)
            stable.append(
                Aspect(
                    from_point=widest.from_point,
                    to_point=widest.to_point,
                    aspect_type=widest.aspect_type,
                    exact_angle=widest.exact_angle,
                    orb=widest.orb,
                    category=widest.category,
                    applying=None,
                )
            )
            continue

        reasons: list[UnstableAspectReason] = []
        if includes_no_aspect:
            reasons.append(UnstableAspectReason.NOT_PRESENT_FOR_ALL_TIMES)
        if len(aspect_types) > 1:
            reasons.append(UnstableAspectReason.ASPECT_TYPE_CHANGED)
        if len(categories) > 1:
            reasons.append(UnstableAspectReason.CATEGORY_CHANGED)
        first = observed[0]
        excluded.append(
            UnstableAspect(
                from_point=first.from_point,
                to_point=first.to_point,
                reasons=tuple(reasons),
                includes_no_aspect=includes_no_aspect,
                possible_aspect_types=aspect_types,
                possible_categories=categories,
            )
        )

    return (
        tuple(sorted(stable, key=aspect_sort_key)),
        tuple(sorted(excluded, key=unstable_aspect_sort_key)),
    )


def unstable_aspect_sort_key(
    item: UnstableAspect,
) -> tuple[str, str, str, str]:
    return (
        item.from_point.chart,
        item.from_point.body,
        item.to_point.chart,
        item.to_point.body,
    )


def unstable_aspect_pair_key(
    item: UnstableAspect,
) -> tuple[str, str, str, str]:
    return (
        item.from_point.chart,
        item.from_point.body,
        item.to_point.chart,
        item.to_point.body,
    )


def _aspect_pair_key(aspect: Aspect) -> tuple[str, str, str, str]:
    return (
        aspect.from_point.chart,
        aspect.from_point.body,
        aspect.to_point.chart,
        aspect.to_point.body,
    )


__all__ = [
    "CosmogramTimeUncertainty",
    "UnstableAspect",
    "UnstableAspectReason",
    "find_time_stable_aspects",
    "unstable_aspect_pair_key",
    "unstable_aspect_sort_key",
]
