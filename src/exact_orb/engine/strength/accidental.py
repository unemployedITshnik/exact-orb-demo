"""Accidental planetary strength."""

from __future__ import annotations

from collections.abc import Mapping
import logging

from .types import AccidentalModifier, AccidentalStrength, HouseType, StrengthConfig


LOGGER = logging.getLogger(__name__)
ANGULAR_HOUSES = {1, 4, 7, 10}
SUCCEDENT_HOUSES = {2, 5, 8, 11}
CADENT_HOUSES = {3, 6, 9, 12}


def calculate_accidental_strength(
    body: str,
    position: object,
    angles: Mapping[str, object],
    config: StrengthConfig,
) -> AccidentalStrength:
    """Calculate accidental strength from house placement and modifiers."""

    house = position.house
    if house is None:
        raise ValueError(f"body {body!r} has no house placement")

    house_type = house_type_for_house(house)
    house_score = config.house_scores[house_type]
    modifiers: list[AccidentalModifier] = []

    if position.retrograde:
        modifiers.append(AccidentalModifier(name="retrograde", score=config.retrograde_modifier))

    if _conjunct_angle(position.longitude, angles, config):
        modifiers.append(
            AccidentalModifier(
                name="asc_mc_conjunction",
                score=config.angle_conjunction_modifier,
            )
        )

    score = house_score + sum(item.score for item in modifiers)
    result = AccidentalStrength(
        body=body,
        house=house,
        house_type=house_type,
        house_score=house_score,
        modifiers=tuple(modifiers),
        score=score,
    )
    LOGGER.debug(
        "accidental_strength body=%s house=%d house_type=%s modifiers=%d score=%d",
        body,
        house,
        house_type.value,
        len(modifiers),
        score,
    )
    return result


def house_type_for_house(house: int) -> HouseType:
    if house in ANGULAR_HOUSES:
        return HouseType.ANGULAR
    if house in SUCCEDENT_HOUSES:
        return HouseType.SUCCEDENT
    if house in CADENT_HOUSES:
        return HouseType.CADENT
    raise ValueError("house must be in 1..12")


def _conjunct_angle(
    longitude: float,
    angles: Mapping[str, object],
    config: StrengthConfig,
) -> bool:
    for angle_name in ("asc", "mc"):
        angle = angles.get(angle_name)
        if angle is None:
            continue
        if _angular_distance(longitude, angle.longitude) < config.angle_conjunction_orb:
            return True
    return False


def _angular_distance(left: float, right: float) -> float:
    distance = abs((left - right) % 360.0)
    if distance > 180.0:
        return 360.0 - distance
    return distance
