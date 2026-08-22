"""Special degree flags."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import logging

from .types import DegreeFlag, StrengthConfig


LOGGER = logging.getLogger(__name__)
CRITICAL_DEGREES = {
    "cardinal": (0, 13, 26),
    "fixed": (8, 9, 21, 22),
    "mutable": (4, 17),
}
MODALITIES = ("cardinal", "fixed", "mutable")
CRITICAL_TRADITION = "lunar_mansion"


def find_special_degrees(
    bodies: Mapping[str, object],
    angles: Mapping[str, object],
    cusps: Sequence[object],
    config: StrengthConfig,
) -> tuple[DegreeFlag, ...]:
    """Flag zero, anaretic, and configured critical degrees."""

    flags: list[DegreeFlag] = []
    for name, body in bodies.items():
        flags.append(_flag(name, "body", body.longitude, body.zodiac, config, allow_critical=True))

    for name in ("asc", "mc", "vertex"):
        angle = angles.get(name)
        if angle is not None:
            flags.append(_flag(name, "angle", angle.longitude, angle.zodiac, config, allow_critical=True))

    for cusp in cusps:
        flags.append(
            _flag(
                f"house_{cusp.house}",
                "cusp",
                cusp.longitude,
                cusp.zodiac,
                config,
                allow_critical=False,
            )
        )

    result = tuple(flags)
    LOGGER.debug(
        "find_special_degrees bodies=%d angles=%d cusps=%d flags=%d visible_flags=%d",
        len(bodies),
        len(angles),
        len(cusps),
        len(result),
        sum(1 for flag in result if flag.is_zero_degree or flag.is_anaretic or flag.is_critical),
    )
    return result


def _flag(
    point: str,
    point_type: str,
    longitude: float,
    zodiac: object,
    config: StrengthConfig,
    *,
    allow_critical: bool,
) -> DegreeFlag:
    critical = (
        _critical_match(zodiac.sign_index, zodiac.degree_in_sign, config)
        if allow_critical
        else None
    )
    return DegreeFlag(
        point=point,
        point_type=point_type,
        longitude=longitude,
        sign=zodiac.sign,
        degree_in_sign=zodiac.degree_in_sign,
        is_zero_degree=zodiac.degree_in_sign < 1.0,
        is_anaretic=zodiac.degree_in_sign >= 29.0,
        is_critical=critical is not None,
        critical_tradition=CRITICAL_TRADITION if critical is not None else None,
        matched_critical_degree=critical,
    )


def _critical_match(
    sign_index: int,
    degree_in_sign: float,
    config: StrengthConfig,
) -> int | None:
    if not config.use_critical_degrees:
        return None

    modality = MODALITIES[sign_index % 3]
    candidates = CRITICAL_DEGREES[modality]
    matches = [
        (abs(degree_in_sign - degree), degree)
        for degree in candidates
        if abs(degree_in_sign - degree) <= config.critical_degree_orb
    ]
    if not matches:
        return None
    return min(matches, key=lambda item: item[0])[1]
