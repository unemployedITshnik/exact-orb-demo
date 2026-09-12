"""Explicit whitelist projection from calculation artifacts to Research v1."""

from __future__ import annotations

import logging

from pydantic import ValidationError

from exact_orb.calculation.types import ChartArtifact
from exact_orb.research.errors import (
    RESEARCH_PROJECTION_UNSUPPORTED_VALUE,
    ResearchProjectionError,
)
from exact_orb.research.models import (
    AngleFeature,
    AspectFeature,
    BodyFeature,
    ChartFeatures,
    ConfigurationFeature,
    ConfigurationPointFeature,
    DignityFeature,
    ElementBalanceFeature,
    LunarPhaseFeature,
    ModalityBalanceFeature,
    StrengthFeature,
)


_LOGGER = logging.getLogger(__name__)
_RESEARCH_ANGLES = frozenset({"asc", "mc"})


def _category_value(value: object) -> object:
    return getattr(value, "value", value)


def project_chart_features(artifact: ChartArtifact, /) -> ChartFeatures:
    """Project only the closed, de-identified categorical Research schema."""

    chart = artifact.chart
    try:
        bodies = (
            None
            if chart.bodies is None
            else tuple(
                BodyFeature(
                    point=body.name,
                    sign=body.zodiac.sign,
                    house=body.house,
                    retrograde=body.retrograde,
                )
                for body in chart.bodies.values()
            )
        )
        angles = (
            None
            if chart.angles is None
            else tuple(
                AngleFeature(point=angle.name, sign=angle.zodiac.sign)
                for angle in chart.angles.values()
                if angle.name in _RESEARCH_ANGLES
            )
        )
        aspects = (
            None
            if chart.aspects is None
            else tuple(
                AspectFeature(
                    from_point=aspect.from_point.body,
                    to_point=aspect.to_point.body,
                    aspect_type=_category_value(aspect.aspect_type),
                    category=_category_value(aspect.category),
                )
                for aspect in chart.aspects
            )
        )
        configurations = (
            None
            if chart.configurations is None
            else tuple(
                ConfigurationFeature(
                    configuration_type=_category_value(configuration.type),
                    category=_category_value(configuration.category),
                    points=tuple(
                        ConfigurationPointFeature(
                            role=role,
                            point=point.body,
                        )
                        for role, point in configuration.points.items()
                    ),
                    element=configuration.element,
                    modality=configuration.modality,
                )
                for configuration in chart.configurations
            )
        )

        if chart.strength is None:
            dignities = None
            strengths = None
            balance = None
            lunar_phase = None
        else:
            dignities = tuple(
                DignityFeature(
                    point=planet.body,
                    system=chart.strength.dignity_system,
                    status=_category_value(planet.dignity.status),
                )
                for planet in chart.strength.planets.values()
            )
            strengths = tuple(
                StrengthFeature(
                    point=planet.body,
                    category=_category_value(planet.category),
                    house_type=_category_value(planet.accidental.house_type),
                )
                for planet in chart.strength.planets.values()
            )
            balance = tuple(
                ElementBalanceFeature(
                    axis="element",
                    bucket=bucket,
                    state=_category_value(value.state),
                )
                for bucket, value in chart.strength.balance.elements.items()
            ) + tuple(
                ModalityBalanceFeature(
                    axis="modality",
                    bucket=bucket,
                    state=_category_value(value.state),
                )
                for bucket, value in chart.strength.balance.modalities.items()
            )
            lunar_phase = LunarPhaseFeature(
                phase_number=chart.strength.lunar_phase.phase_number,
            )

        return ChartFeatures(
            chart_kind=chart.chart_kind,
            bodies=bodies,
            angles=angles,
            aspects=aspects,
            configurations=configurations,
            dignities=dignities,
            strengths=strengths,
            balance=balance,
            lunar_phase=lunar_phase,
        )
    except ValidationError as exc:
        validation_errors = tuple(
            (
                ".".join(str(part) for part in error["loc"]),
                error["type"],
            )
            for error in exc.errors(
                include_input=False,
                include_context=False,
                include_url=False,
            )
        )
        _LOGGER.warning(
            "Research projection validation failed",
            extra={
                "error_code": RESEARCH_PROJECTION_UNSUPPORTED_VALUE,
                "validation_errors": validation_errors,
            },
        )
        raise ResearchProjectionError(RESEARCH_PROJECTION_UNSUPPORTED_VALUE) from None


__all__ = ["project_chart_features"]
