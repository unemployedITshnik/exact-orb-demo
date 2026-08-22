"""Weighted chart balance by elements, modalities, hemispheres, and houses."""

from __future__ import annotations

from collections.abc import Mapping
import logging

from .accidental import house_type_for_house
from .types import (
    BalanceBucket,
    BalanceState,
    ChartBalance,
    HemisphereBalance,
    HouseType,
    HouseTypeBalance,
    StrengthConfig,
)


LOGGER = logging.getLogger(__name__)
ELEMENTS = ("fire", "earth", "air", "water")
MODALITIES = ("cardinal", "fixed", "mutable")
EASTERN_HOUSES = {10, 11, 12, 1, 2, 3}
WESTERN_HOUSES = {4, 5, 6, 7, 8, 9}
NORTHERN_HOUSES = {1, 2, 3, 4, 5, 6}
SOUTHERN_HOUSES = {7, 8, 9, 10, 11, 12}


def calculate_balance(
    bodies: Mapping[str, object],
    angles: Mapping[str, object],
    config: StrengthConfig,
) -> ChartBalance:
    """Calculate weighted chart balance."""

    participants = _participants(bodies, angles, config)
    LOGGER.debug("calculate_balance start participants=%d", len(participants))
    element_scores = {name: 0.0 for name in ELEMENTS}
    modality_scores = {name: 0.0 for name in MODALITIES}
    element_contributors = {name: [] for name in ELEMENTS}
    modality_contributors = {name: [] for name in MODALITIES}
    hemispheres = {"north": 0.0, "south": 0.0, "east": 0.0, "west": 0.0}
    house_types = {HouseType.ANGULAR: 0.0, HouseType.SUCCEDENT: 0.0, HouseType.CADENT: 0.0}

    for name, sign_index, house, weight in participants:
        if weight <= 0.0:
            continue

        element = element_for_sign(sign_index)
        modality = modality_for_sign(sign_index)
        element_scores[element] += weight
        modality_scores[modality] += weight
        element_contributors[element].append(name)
        modality_contributors[modality].append(name)

        if house is not None:
            if house in NORTHERN_HOUSES:
                hemispheres["north"] += weight
            if house in SOUTHERN_HOUSES:
                hemispheres["south"] += weight
            if house in EASTERN_HOUSES:
                hemispheres["east"] += weight
            if house in WESTERN_HOUSES:
                hemispheres["west"] += weight
            house_types[house_type_for_house(house)] += weight

    total = sum(element_scores.values())
    elements = _bucketize(element_scores, element_contributors, total, 4, config)
    modalities = _bucketize(modality_scores, modality_contributors, total, 3, config)

    result = ChartBalance(
        elements=elements,
        modalities=modalities,
        hemispheres=HemisphereBalance(**hemispheres),
        house_types=HouseTypeBalance(
            angular=house_types[HouseType.ANGULAR],
            succedent=house_types[HouseType.SUCCEDENT],
            cadent=house_types[HouseType.CADENT],
        ),
        total_weight=total,
        dominant_elements=tuple(name for name, item in elements.items() if item.state is BalanceState.EXCESS),
        deficient_elements=tuple(name for name, item in elements.items() if item.state is BalanceState.DEFICIT),
        dominant_modalities=tuple(name for name, item in modalities.items() if item.state is BalanceState.EXCESS),
        deficient_modalities=tuple(name for name, item in modalities.items() if item.state is BalanceState.DEFICIT),
    )
    LOGGER.debug(
        "calculate_balance complete total_weight=%.3f dominant_elements=%s deficient_elements=%s "
        "dominant_modalities=%s deficient_modalities=%s",
        result.total_weight,
        result.dominant_elements,
        result.deficient_elements,
        result.dominant_modalities,
        result.deficient_modalities,
    )
    return result


def element_for_sign(sign_index: int) -> str:
    return ELEMENTS[sign_index % 4]


def modality_for_sign(sign_index: int) -> str:
    return MODALITIES[sign_index % 3]


def _participants(
    bodies: Mapping[str, object],
    angles: Mapping[str, object],
    config: StrengthConfig,
) -> tuple[tuple[str, int, int | None, float], ...]:
    participants: list[tuple[str, int, int | None, float]] = []
    for name, body in bodies.items():
        weight = config.balance_weights.get(name, 0.0)
        participants.append((name, body.zodiac.sign_index, body.house, weight))

    for name, default_house in (("asc", 1), ("mc", 10)):
        angle = angles.get(name)
        if angle is None:
            continue
        weight = config.balance_weights.get(name, 0.0)
        participants.append((name, angle.zodiac.sign_index, default_house, weight))

    return tuple(participants)


def _bucketize(
    scores: Mapping[str, float],
    contributors: Mapping[str, list[str]],
    total: float,
    bucket_count: int,
    config: StrengthConfig,
) -> dict[str, BalanceBucket]:
    ideal = 100.0 / bucket_count
    low = ideal * (1.0 - config.balance_tolerance)
    high = ideal * (1.0 + config.balance_tolerance)
    result: dict[str, BalanceBucket] = {}

    for name, score in scores.items():
        percentage = 0.0 if total == 0.0 else score / total * 100.0
        if percentage < low:
            state = BalanceState.DEFICIT
        elif percentage > high:
            state = BalanceState.EXCESS
        else:
            state = BalanceState.BALANCED
        result[name] = BalanceBucket(
            score=score,
            percentage=percentage,
            state=state,
            contributors=tuple(contributors[name]),
        )

    return result
