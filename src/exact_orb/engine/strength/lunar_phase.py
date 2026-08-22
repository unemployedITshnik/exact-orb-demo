"""Natal lunar phase."""

from __future__ import annotations

import logging

from .types import LunarPhase


LOGGER = logging.getLogger(__name__)
PHASE_NAMES = (
    "новолуние",
    "растущий серп",
    "первая четверть",
    "растущая выпуклая",
    "полнолуние",
    "распространяющаяся",
    "последняя четверть",
    "бальзамическая",
)


def calculate_lunar_phase(sun_longitude: float, moon_longitude: float) -> LunarPhase:
    """Calculate Rudhyar eight-phase Moon phase."""

    elongation = (moon_longitude - sun_longitude) % 360.0
    if elongation >= 360.0:
        elongation = 0.0
    phase_index = min(int(elongation // 45.0), 7)
    phase_start = phase_index * 45.0
    phase_end = (phase_index + 1) * 45.0
    result = LunarPhase(
        elongation=elongation,
        phase_number=phase_index + 1,
        phase_name=PHASE_NAMES[phase_index],
        phase_start=phase_start,
        phase_end=phase_end,
        distance_from_previous_boundary=elongation - phase_start,
        distance_to_next_boundary=phase_end - elongation,
        degrees_after_exact_opposition=elongation - 180.0 if phase_index == 4 else None,
    )
    LOGGER.debug(
        "calculate_lunar_phase sun_longitude=%.6f moon_longitude=%.6f elongation=%.6f phase=%s",
        sun_longitude,
        moon_longitude,
        elongation,
        result.phase_name,
    )
    return result
