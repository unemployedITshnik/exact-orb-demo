"""Aspect category classification."""

from __future__ import annotations

from .types import AspectCategory, CategoryThresholds


def categorize_orb(orb: float, thresholds: CategoryThresholds) -> AspectCategory:
    """Classify an orb into exact, working, or background."""

    if orb < thresholds.exact:
        return AspectCategory.EXACT
    if orb <= thresholds.working:
        return AspectCategory.WORKING
    return AspectCategory.BACKGROUND
