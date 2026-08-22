"""Essential dignity tables."""

from __future__ import annotations

import logging

from .types import Dignity, DignityStatus


LOGGER = logging.getLogger(__name__)
DIGNITY_SCORES = {
    DignityStatus.DOMICILE: 5,
    DignityStatus.EXALTATION: 4,
    DignityStatus.DETRIMENT: -5,
    DignityStatus.FALL: -4,
    DignityStatus.PEREGRINE: 0,
}

SIGN_NAMES = (
    "Aries",
    "Taurus",
    "Gemini",
    "Cancer",
    "Leo",
    "Virgo",
    "Libra",
    "Scorpio",
    "Sagittarius",
    "Capricorn",
    "Aquarius",
    "Pisces",
)

TRADITIONAL_DIGNITIES = {
    "sun": {
        DignityStatus.DOMICILE: (4,),
        DignityStatus.EXALTATION: (0,),
        DignityStatus.DETRIMENT: (10,),
        DignityStatus.FALL: (6,),
    },
    "moon": {
        DignityStatus.DOMICILE: (3,),
        DignityStatus.EXALTATION: (1,),
        DignityStatus.DETRIMENT: (9,),
        DignityStatus.FALL: (7,),
    },
    "mercury": {
        DignityStatus.DOMICILE: (2, 5),
        DignityStatus.EXALTATION: (5,),
        DignityStatus.DETRIMENT: (8, 11),
        DignityStatus.FALL: (11,),
    },
    "venus": {
        DignityStatus.DOMICILE: (1, 6),
        DignityStatus.EXALTATION: (11,),
        DignityStatus.DETRIMENT: (0, 7),
        DignityStatus.FALL: (5,),
    },
    "mars": {
        DignityStatus.DOMICILE: (0, 7),
        DignityStatus.EXALTATION: (9,),
        DignityStatus.DETRIMENT: (6, 1),
        DignityStatus.FALL: (3,),
    },
    "jupiter": {
        DignityStatus.DOMICILE: (8, 11),
        DignityStatus.EXALTATION: (3,),
        DignityStatus.DETRIMENT: (2, 5),
        DignityStatus.FALL: (9,),
    },
    "saturn": {
        DignityStatus.DOMICILE: (9, 10),
        DignityStatus.EXALTATION: (6,),
        DignityStatus.DETRIMENT: (3, 4),
        DignityStatus.FALL: (0,),
    },
}

MODERN_DIGNITIES = {
    **TRADITIONAL_DIGNITIES,
    "uranus": {
        DignityStatus.DOMICILE: (10,),
        DignityStatus.DETRIMENT: (4,),
    },
    "neptune": {
        DignityStatus.DOMICILE: (11,),
        DignityStatus.DETRIMENT: (5,),
    },
    "pluto": {
        DignityStatus.DOMICILE: (7,),
        DignityStatus.DETRIMENT: (1,),
    },
}


def evaluate_dignity(
    body: str,
    sign_index: int,
    *,
    system: str = "modern",
) -> Dignity:
    """Evaluate essential dignity by direct table lookup."""

    tables = MODERN_DIGNITIES if system == "modern" else TRADITIONAL_DIGNITIES
    body_table = tables.get(body, {})
    status = DignityStatus.PEREGRINE
    for candidate in (
        DignityStatus.DOMICILE,
        DignityStatus.EXALTATION,
        DignityStatus.DETRIMENT,
        DignityStatus.FALL,
    ):
        if sign_index in body_table.get(candidate, ()):
            status = candidate
            break

    result = Dignity(
        body=body,
        sign=SIGN_NAMES[sign_index % 12],
        system=system,
        status=status,
        score=DIGNITY_SCORES[status],
    )
    LOGGER.debug(
        "evaluate_dignity body=%s sign=%s system=%s status=%s score=%d",
        body,
        result.sign,
        system,
        status.value,
        result.score,
    )
    return result
