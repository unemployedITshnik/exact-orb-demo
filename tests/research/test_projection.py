"""Whitelist projection and closed-vocabulary drift tests for Research v1."""

from __future__ import annotations

import ast
from datetime import UTC, datetime
import inspect
import logging
from pathlib import Path
from typing import get_args

import pytest

from exact_orb.calculation.types import ChartArtifact
from exact_orb.domain import ChartKind
from exact_orb.engine.aspects import (
    Aspect,
    AspectCategory as EngineAspectCategory,
    AspectConfig,
    AspectPointRef,
    AspectType as EngineAspectType,
)
from exact_orb.engine.charts import natal as natal_module
from exact_orb.engine.charts.uncertainty import (
    CosmogramTimeUncertainty,
    UnstableAspect,
    UnstableAspectReason,
)
from exact_orb.engine.configurations.patterns import common as configuration_common
from exact_orb.engine.configurations.types import (
    Configuration,
    ConfigurationCategory as EngineConfigurationCategory,
    ConfigurationConfig,
    ConfigurationType as EngineConfigurationType,
)
from exact_orb.engine.ephemeris.types import (
    ANGLE_INDICES,
    DEFAULT_BODY_IDS,
    ZODIAC_SIGNS,
    AnglePosition,
    BodyPosition,
    CalculationWarning,
    ZodiacPosition,
)
from exact_orb.engine.strength import balance as balance_module
from exact_orb.engine.strength.lunar_phase import PHASE_NAMES
from exact_orb.engine.strength.types import (
    AccidentalStrength,
    BalanceBucket,
    BalanceState as EngineBalanceState,
    ChartBalance,
    Dignity,
    DignityStatus as EngineDignityStatus,
    HemisphereBalance,
    HouseType as EngineHouseType,
    HouseTypeBalance,
    InterceptionSummary,
    LunarPhase,
    NatalStrength,
    PlanetStrength,
    StrengthCategory as EngineStrengthCategory,
    StrengthConfig,
)
from exact_orb.research import (
    ANGLE_FEATURE_POINTS,
    BODY_FEATURE_POINTS,
    CONFIGURATION_ROLE_SETS,
    RELATIONAL_POINTS,
    STRENGTH_POINTS,
    AngleFeature,
    AspectCategory,
    AspectFeature,
    AspectType,
    BalanceState,
    BodyFeature,
    ChartFeatures,
    ConfigurationCategory,
    ConfigurationFeature,
    ConfigurationPointFeature,
    ConfigurationRole,
    ConfigurationType,
    DignityFeature,
    DignityStatus,
    Element,
    ElementBalanceFeature,
    HouseType,
    LunarPhaseFeature,
    Modality,
    ModalityBalanceFeature,
    ResearchProjectionError,
    ResearchRecord,
    StrengthCategory,
    StrengthFeature,
    ZodiacSign,
)
from exact_orb.research.projection import project_chart_features
from tests.fixtures.calculation import artifact, chart_spec, raw_chart
from tests.research.conformance import make_record


pytestmark = pytest.mark.no_ephemeris_autoinit


def _zodiac(sign: str, *, longitude: float = 10.25) -> ZodiacPosition:
    sign_index = ZODIAC_SIGNS.index(sign)
    return ZodiacPosition(
        longitude=longitude,
        sign_index=sign_index,
        sign=sign,
        degree_in_sign=longitude % 30,
        degree=int(longitude % 30),
        minute=15,
        second=30,
    )


def _body(
    name: str,
    sign: str,
    *,
    house: int | None,
    retrograde: bool = False,
    longitude: float = 10.25,
) -> BodyPosition:
    return BodyPosition(
        name=name,
        chart="natal",
        source="derived" if name in {"south_node", "pars_fortune"} else "swisseph",
        method=None,
        swe_id=None,
        longitude=longitude,
        latitude=1.25,
        distance=2.5,
        longitude_speed=-0.5 if retrograde else 0.5,
        latitude_speed=0.1,
        distance_speed=0.01,
        retrograde=retrograde,
        house=house,
        zodiac=_zodiac(sign, longitude=longitude),
        retflags=258,
    )


def _angle(name: str, sign: str, longitude: float) -> AnglePosition:
    return AnglePosition(name=name, longitude=longitude, zodiac=_zodiac(sign, longitude=longitude))


def _ref(body: str) -> AspectPointRef:
    return AspectPointRef(chart="natal", body=body)


def _aspect(
    left: str,
    right: str,
    *,
    aspect_type: EngineAspectType,
    category: EngineAspectCategory,
) -> Aspect:
    return Aspect(
        from_point=_ref(left),
        to_point=_ref(right),
        aspect_type=aspect_type,
        exact_angle=120.0,
        orb=1.25,
        category=category,
        applying=None,
    )


def _planet(
    body: str,
    sign: str,
    *,
    status: EngineDignityStatus,
    category: EngineStrengthCategory,
    house: int,
    house_type: EngineHouseType,
) -> PlanetStrength:
    return PlanetStrength(
        body=body,
        dignity=Dignity(
            body=body,
            sign=sign,
            system="modern",
            status=status,
            score=5,
        ),
        accidental=AccidentalStrength(
            body=body,
            house=house,
            house_type=house_type,
            house_score=4,
            modifiers=(),
            score=4,
        ),
        essential_score=5,
        accidental_score=4,
        total=9,
        category=category,
        note=None,
    )


def _bucket(state: EngineBalanceState, score: float) -> BalanceBucket:
    return BalanceBucket(score=score, percentage=score * 10, state=state, contributors=("sun",))


def _strength() -> NatalStrength:
    return NatalStrength(
        dignity_system="modern",
        planets={
            "sun": _planet(
                "sun", "Aries", status=EngineDignityStatus.EXALTATION,
                category=EngineStrengthCategory.STRONG, house=1,
                house_type=EngineHouseType.ANGULAR,
            ),
            "moon": _planet(
                "moon", "Cancer", status=EngineDignityStatus.DOMICILE,
                category=EngineStrengthCategory.MODERATE, house=4,
                house_type=EngineHouseType.CADENT,
            ),
        },
        balance=ChartBalance(
            elements={
                "fire": _bucket(EngineBalanceState.EXCESS, 4.0),
                "earth": _bucket(EngineBalanceState.DEFICIT, 1.0),
            },
            modalities={
                "cardinal": _bucket(EngineBalanceState.BALANCED, 3.0),
                "fixed": _bucket(EngineBalanceState.DEFICIT, 1.0),
            },
            hemispheres=HemisphereBalance(north=1, south=2, east=3, west=4),
            house_types=HouseTypeBalance(angular=5, succedent=6, cadent=7),
            total_weight=10,
            dominant_elements=("fire",),
            deficient_elements=("earth",),
            dominant_modalities=(),
            deficient_modalities=("fixed",),
        ),
        dispositors={},
        mutual_receptions=(),
        lunar_phase=LunarPhase(
            elongation=180.5,
            phase_number=5,
            phase_name="полнолуние",
            phase_start=180,
            phase_end=225,
            distance_from_previous_boundary=0.5,
            distance_to_next_boundary=44.5,
            degrees_after_exact_opposition=0.5,
        ),
        degree_flags=(),
        interceptions=InterceptionSummary(intercepted=(), near_intercepted=()),
        weak_note="display-only note",
    )


def rich_artifact() -> ChartArtifact:
    configuration_aspects = (
        _aspect(
            "mean_apog",
            "pars_fortune",
            aspect_type=EngineAspectType.SEXTILE,
            category=EngineAspectCategory.EXACT,
        ),
        _aspect(
            "true_node",
            "mean_apog",
            aspect_type=EngineAspectType.QUINCUNX,
            category=EngineAspectCategory.WORKING,
        ),
        _aspect(
            "true_node",
            "pars_fortune",
            aspect_type=EngineAspectType.QUINCUNX,
            category=EngineAspectCategory.EXACT,
        ),
    )
    aspects = (
        _aspect("true_node", "asc", aspect_type=EngineAspectType.TRINE, category=EngineAspectCategory.EXACT),
        _aspect("mc", "vertex", aspect_type=EngineAspectType.SQUARE, category=EngineAspectCategory.WORKING),
        *configuration_aspects,
    )
    configuration = Configuration(
        type=EngineConfigurationType.YOD,
        points={
            "base_2": _ref("pars_fortune"),
            "apex": _ref("true_node"),
            "base_1": _ref("mean_apog"),
        },
        aspects=configuration_aspects,
        max_orb=1.25,
        category=EngineConfigurationCategory.TIGHT,
        chart="natal",
        element="fire",
        modality="cardinal",
    )
    chart = raw_chart().model_copy(
        update={
            "bodies": {
                "true_node": _body("true_node", "Aries", house=3, retrograde=True, longitude=15),
                "south_node": _body("south_node", "Libra", house=9, retrograde=True, longitude=195),
                "sun": _body("sun", "Leo", house=1, longitude=125),
                "moon": _body("moon", "Cancer", house=4, longitude=95),
                "mean_apog": _body("mean_apog", "Scorpio", house=8, longitude=225),
                "pars_fortune": _body("pars_fortune", "Taurus", house=2, longitude=45),
            },
            "angles": {
                "vertex": _angle("vertex", "Gemini", 75),
                "mc": _angle("mc", "Capricorn", 285),
                "asc": _angle("asc", "Libra", 195),
            },
            "aspects": aspects,
            "configurations": (configuration,),
            "strength": _strength(),
        }
    )
    return artifact(chart=chart)


def expected_features() -> ChartFeatures:
    return ChartFeatures(
        chart_kind="natal",
        bodies=(
            BodyFeature(point="true_node", sign="Aries", house=3, retrograde=True),
            BodyFeature(point="south_node", sign="Libra", house=9, retrograde=True),
            BodyFeature(point="sun", sign="Leo", house=1, retrograde=False),
            BodyFeature(point="moon", sign="Cancer", house=4, retrograde=False),
            BodyFeature(point="mean_apog", sign="Scorpio", house=8, retrograde=False),
            BodyFeature(point="pars_fortune", sign="Taurus", house=2, retrograde=False),
        ),
        angles=(
            AngleFeature(point="mc", sign="Capricorn"),
            AngleFeature(point="asc", sign="Libra"),
        ),
        aspects=(
            AspectFeature(from_point="true_node", to_point="asc", aspect_type="trine", category="exact"),
            AspectFeature(from_point="mc", to_point="vertex", aspect_type="square", category="working"),
            AspectFeature(
                from_point="mean_apog",
                to_point="pars_fortune",
                aspect_type="sextile",
                category="exact",
            ),
            AspectFeature(
                from_point="true_node",
                to_point="mean_apog",
                aspect_type="quincunx",
                category="working",
            ),
            AspectFeature(
                from_point="true_node",
                to_point="pars_fortune",
                aspect_type="quincunx",
                category="exact",
            ),
        ),
        configurations=(
            ConfigurationFeature(
                configuration_type="yod",
                category="tight",
                points=(
                    ConfigurationPointFeature(role="base_2", point="pars_fortune"),
                    ConfigurationPointFeature(role="apex", point="true_node"),
                    ConfigurationPointFeature(role="base_1", point="mean_apog"),
                ),
                element="fire",
                modality="cardinal",
            ),
        ),
        dignities=(
            DignityFeature(point="sun", system="modern", status="exaltation"),
            DignityFeature(point="moon", system="modern", status="domicile"),
        ),
        strengths=(
            StrengthFeature(point="sun", category="strong", house_type="angular"),
            StrengthFeature(point="moon", category="moderate", house_type="cadent"),
        ),
        balance=(
            ElementBalanceFeature(axis="element", bucket="fire", state="excess"),
            ElementBalanceFeature(axis="element", bucket="earth", state="deficit"),
            ModalityBalanceFeature(axis="modality", bucket="cardinal", state="balanced"),
            ModalityBalanceFeature(axis="modality", bucket="fixed", state="deficit"),
        ),
        lunar_phase=LunarPhaseFeature(phase_number=5),
    )


def test_rich_artifact_projects_to_exact_literal_features_without_mutation() -> None:
    source = rich_artifact()
    before = source.model_dump(mode="python")
    projected = project_chart_features(source)
    assert projected == expected_features()
    assert source.model_dump(mode="python") == before
    assert all(
        getattr(projected, family)
        for family in (
            "bodies", "angles", "aspects", "configurations", "dignities",
            "strengths", "balance", "lunar_phase",
        )
    )


def test_only_asc_and_mc_get_angle_features_while_vertex_remains_relational() -> None:
    projected = project_chart_features(rich_artifact())
    assert {item.point.value for item in projected.angles} == {"asc", "mc"}
    endpoints = {
        endpoint.value
        for aspect in projected.aspects
        for endpoint in (aspect.from_point, aspect.to_point)
    }
    assert {"asc", "mc", "vertex"} <= endpoints


def test_south_node_projects_only_as_a_body_feature() -> None:
    projected = project_chart_features(rich_artifact())
    relational_points = {
        endpoint.value
        for aspect in projected.aspects
        for endpoint in (aspect.from_point, aspect.to_point)
    }
    relational_points.update(
        point.point.value
        for configuration in projected.configurations
        for point in configuration.points
    )

    assert any(body.point.value == "south_node" for body in projected.bodies)
    assert "south_node" not in relational_points


def test_none_and_computed_empty_families_remain_distinct() -> None:
    absent_spec = chart_spec(chart_kind="cosmogram").model_copy(update={"include": ()})
    empty_spec = chart_spec(chart_kind="cosmogram").model_copy(
        update={"include": ("aspects", "configurations", "positions")}
    )
    absent = artifact(
        spec=absent_spec,
        chart=raw_chart(chart_kind="cosmogram", include=absent_spec.include),
    )
    empty = artifact(
        spec=empty_spec,
        chart=raw_chart(chart_kind="cosmogram", include=empty_spec.include),
    )
    absent_features = project_chart_features(absent)
    empty_features = project_chart_features(empty)
    assert absent_features.bodies is None
    assert empty_features.bodies == ()
    assert empty_features.aspects == () and empty_features.configurations == ()


def test_source_collection_order_does_not_change_projection() -> None:
    source = rich_artifact()
    chart = source.chart
    strength = chart.strength
    reversed_balance = strength.balance.model_copy(
        update={
            "elements": dict(reversed(tuple(strength.balance.elements.items()))),
            "modalities": dict(reversed(tuple(strength.balance.modalities.items()))),
        }
    )
    reversed_strength = strength.model_copy(
        update={
            "planets": dict(reversed(tuple(strength.planets.items()))),
            "balance": reversed_balance,
        }
    )
    reversed_configurations = tuple(
        item.model_copy(update={"points": dict(reversed(tuple(item.points.items())))})
        for item in reversed(chart.configurations)
    )
    reordered = source.model_copy(
        update={
            "chart": chart.model_copy(
                update={
                    "bodies": dict(reversed(tuple(chart.bodies.items()))),
                    "angles": dict(reversed(tuple(chart.angles.items()))),
                    "aspects": tuple(reversed(chart.aspects)),
                    "configurations": reversed_configurations,
                    "strength": reversed_strength,
                }
            )
        }
    )
    assert project_chart_features(reordered) == project_chart_features(source)


def test_unknown_categorical_value_is_a_safe_typed_error(caplog: pytest.LogCaptureFixture) -> None:
    source = rich_artifact()
    sun = source.chart.bodies["sun"]
    poisoned = source.model_copy(
        update={
            "chart": source.chart.model_copy(
                update={
                    "bodies": {
                        **source.chart.bodies,
                        "sun": sun.model_copy(
                            update={"zodiac": sun.zodiac.model_copy(update={"sign": "SECRET_UNKNOWN"})}
                        ),
                    }
                }
            )
        }
    )
    with pytest.raises(ResearchProjectionError) as caught:
        project_chart_features(poisoned)
    assert str(caught.value) == "RESEARCH_PROJECTION_UNSUPPORTED_VALUE"
    assert "SECRET_UNKNOWN" not in str(caught.value)
    assert caught.value.__cause__ is None
    assert len(caplog.records) == 1
    record = caplog.records[0]
    assert record.levelno == logging.WARNING
    assert record.getMessage() == "Research projection validation failed"
    assert record.error_code == "RESEARCH_PROJECTION_UNSUPPORTED_VALUE"
    assert record.validation_errors == (("sign", "enum"),)
    assert "SECRET_UNKNOWN" not in str((record.msg, record.args, record.validation_errors))


def test_structural_projection_failure_has_distinct_safe_diagnostics(
    caplog: pytest.LogCaptureFixture,
) -> None:
    source = rich_artifact()
    duplicate = source.chart.bodies["moon"].model_copy(update={"name": "sun"})
    poisoned = source.model_copy(
        update={
            "chart": source.chart.model_copy(
                update={"bodies": {**source.chart.bodies, "moon": duplicate}}
            )
        }
    )
    with pytest.raises(ResearchProjectionError) as caught:
        project_chart_features(poisoned)
    assert caught.value.error_code == str(caught.value) == (
        "RESEARCH_PROJECTION_UNSUPPORTED_VALUE"
    )
    assert caught.value.__cause__ is None
    assert len(caplog.records) == 1
    record = caplog.records[0]
    assert record.levelno == logging.WARNING
    assert record.validation_errors == (("bodies", "value_error"),)
    serialized_diagnostics = str((record.msg, record.args, record.validation_errors))
    assert "sun" not in serialized_diagnostics
    assert "moon" not in serialized_diagnostics


def test_cosmogram_keeps_house_absent_and_does_not_invent_house_families() -> None:
    chart = raw_chart(chart_kind="cosmogram").model_copy(
        update={
            "bodies": {"sun": _body("sun", "Aries", house=None)},
            "angles": None,
            "aspects": (),
            "configurations": (),
            "strength": None,
        }
    )
    source = artifact(spec=chart_spec(chart_kind="cosmogram"), chart=chart)
    projected = project_chart_features(source)
    assert projected.chart_kind.value == "cosmogram"
    assert projected.bodies[0].house is None
    assert projected.angles is None
    assert projected.dignities is projected.strengths is projected.balance is None


def test_cosmogram_projection_does_not_restore_excluded_aspect() -> None:
    base = raw_chart(chart_kind="cosmogram")
    diagnostic = UnstableAspect(
        from_point=AspectPointRef(chart="natal", body="sun"),
        to_point=AspectPointRef(chart="natal", body="moon"),
        reasons=(UnstableAspectReason.NOT_PRESENT_FOR_ALL_TIMES,),
        includes_no_aspect=True,
        possible_aspect_types=(EngineAspectType.CONJUNCTION,),
        possible_categories=(EngineAspectCategory.EXACT,),
    )
    chart = natal_module.NatalChart.model_validate(
        {
            **base.model_dump(),
            "bodies": {
                "sun": _body("sun", "Aries", house=None),
                "moon": _body("moon", "Aries", house=None),
            },
            "aspects": (),
            "configurations": (),
            "time_uncertainty": CosmogramTimeUncertainty(
                domain=base.time_uncertainty.domain,
                excluded_aspects=(diagnostic,),
            ),
        }
    )

    projected = project_chart_features(
        artifact(spec=chart_spec(chart_kind="cosmogram"), chart=chart)
    )

    assert projected.aspects == ()
    assert projected.configurations == ()


def _all_values(value: object):
    if isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from _all_values(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _all_values(item)
    else:
        yield value


def test_forbidden_artifact_families_and_sentinels_do_not_cross_whitelist() -> None:
    source = rich_artifact()
    numeric_sentinel = 271.8281828
    text_sentinel = "SECRET_FORBIDDEN_SENTINEL"
    bodies = {
        key: body.model_copy(
            update={
                "method": text_sentinel,
                "swe_id": 999999,
                "longitude": numeric_sentinel,
                "latitude": numeric_sentinel,
                "distance": numeric_sentinel,
                "longitude_speed": numeric_sentinel,
                "latitude_speed": numeric_sentinel,
                "distance_speed": numeric_sentinel,
                "retflags": 999999,
                "zodiac": body.zodiac.model_copy(
                    update={
                        "longitude": numeric_sentinel,
                        "degree_in_sign": numeric_sentinel,
                        "degree": 27,
                        "minute": 18,
                        "second": 28,
                    }
                ),
            }
        )
        for key, body in source.chart.bodies.items()
    }
    aspects = tuple(
        item.model_copy(update={"exact_angle": numeric_sentinel, "orb": numeric_sentinel})
        for item in source.chart.aspects
    )
    configurations = tuple(
        item.model_copy(
            update={"aspects": aspects, "max_orb": numeric_sentinel, "chart": text_sentinel}
        )
        for item in source.chart.configurations
    )
    strength = source.chart.strength
    angles = {
        key: angle.model_copy(
            update={
                "longitude": numeric_sentinel,
                "zodiac": angle.zodiac.model_copy(
                    update={
                        "longitude": numeric_sentinel,
                        "degree_in_sign": numeric_sentinel,
                        "degree": 27,
                        "minute": 18,
                        "second": 28,
                    }
                ),
            }
        )
        for key, angle in source.chart.angles.items()
    }
    poisoned_elements = {
        key: bucket.model_copy(
            update={
                "score": numeric_sentinel,
                "percentage": numeric_sentinel,
                "contributors": (text_sentinel,),
            }
        )
        for key, bucket in strength.balance.elements.items()
    }
    poisoned_modalities = {
        key: bucket.model_copy(
            update={
                "score": numeric_sentinel,
                "percentage": numeric_sentinel,
                "contributors": (text_sentinel,),
            }
        )
        for key, bucket in strength.balance.modalities.items()
    }
    planets = {
        key: planet.model_copy(
            update={
                "essential_score": 999999,
                "accidental_score": 999999,
                "total": 999999,
                "note": text_sentinel,
                "dignity": planet.dignity.model_copy(update={"score": 999999}),
                "accidental": planet.accidental.model_copy(
                    update={"house_score": 999999, "score": 999999}
                ),
            }
        )
        for key, planet in strength.planets.items()
    }
    poisoned = source.model_copy(
        update={
            "calculation_key": text_sentinel,
            "warnings": (CalculationWarning(source=text_sentinel, message=text_sentinel),),
            "chart": source.chart.model_copy(
                update={
                    "datetime_utc": datetime(2044, 4, 4, 4, 44, 44, tzinfo=UTC),
                    "julian_day_ut": numeric_sentinel,
                    "latitude": numeric_sentinel,
                    "longitude": numeric_sentinel,
                    "house_system": text_sentinel,
                    "ephemeris_flags": 999999,
                    "selena_method": text_sentinel,
                    "ephemeris": source.chart.ephemeris.model_copy(
                        update={
                            "required_files": (text_sentinel,),
                            "found_files": (text_sentinel,),
                            "missing_files": (text_sentinel,),
                        }
                    ),
                    "bodies": bodies,
                    "angles": angles,
                    "aspects": aspects,
                    "configurations": configurations,
                    "strength": strength.model_copy(
                        update={
                            "planets": planets,
                            "weak_note": text_sentinel,
                            "balance": strength.balance.model_copy(
                                update={
                                    "elements": poisoned_elements,
                                    "modalities": poisoned_modalities,
                                    "hemispheres": strength.balance.hemispheres.model_copy(
                                        update={
                                            "north": numeric_sentinel,
                                            "south": numeric_sentinel,
                                            "east": numeric_sentinel,
                                            "west": numeric_sentinel,
                                        }
                                    ),
                                    "house_types": strength.balance.house_types.model_copy(
                                        update={
                                            "angular": numeric_sentinel,
                                            "succedent": numeric_sentinel,
                                            "cadent": numeric_sentinel,
                                        }
                                    ),
                                    "total_weight": numeric_sentinel,
                                }
                            ),
                            "lunar_phase": strength.lunar_phase.model_copy(
                                update={
                                    "elongation": numeric_sentinel,
                                    "phase_name": text_sentinel,
                                    "phase_start": numeric_sentinel,
                                    "phase_end": numeric_sentinel,
                                    "distance_from_previous_boundary": numeric_sentinel,
                                    "distance_to_next_boundary": numeric_sentinel,
                                }
                            ),
                        }
                    ),
                    "warnings": (CalculationWarning(source=text_sentinel, message=text_sentinel),),
                }
            ),
        }
    )
    projected = project_chart_features(poisoned)
    assert projected == expected_features()
    record = make_record().model_copy(update={"chart_features": projected})
    values = tuple(_all_values(projected.model_dump(mode="json"))) + tuple(
        _all_values(record.model_dump(mode="json"))
    )
    assert text_sentinel not in values
    assert numeric_sentinel not in values
    assert all(
        getattr(projected, family)
        for family in (
            "bodies", "angles", "aspects", "configurations", "dignities",
            "strengths", "balance", "lunar_phase",
        )
    )


def test_allowed_categorical_changes_change_projection() -> None:
    source = rich_artifact()
    baseline = project_chart_features(source)
    chart = source.chart

    sun = chart.bodies["sun"]
    body_variant = source.model_copy(
        update={"chart": chart.model_copy(update={"bodies": {**chart.bodies, "sun": sun.model_copy(update={"retrograde": True})}})}
    )
    asc = chart.angles["asc"]
    angle_variant = source.model_copy(
        update={"chart": chart.model_copy(update={"angles": {**chart.angles, "asc": asc.model_copy(update={"zodiac": asc.zodiac.model_copy(update={"sign": "Virgo"})})}})}
    )
    aspect_variant = source.model_copy(
        update={"chart": chart.model_copy(update={"aspects": (chart.aspects[0].model_copy(update={"category": EngineAspectCategory.BACKGROUND}), chart.aspects[1])})}
    )
    config_variant = source.model_copy(
        update={"chart": chart.model_copy(update={"configurations": (chart.configurations[0].model_copy(update={"type": EngineConfigurationType.T_SQUARE}),)})}
    )
    planets = {
        key: planet.model_copy(
            update={"dignity": planet.dignity.model_copy(update={"system": "traditional"})}
        )
        for key, planet in chart.strength.planets.items()
    }
    dignity_variant = source.model_copy(
        update={
            "chart": chart.model_copy(
                update={
                    "strength": chart.strength.model_copy(
                        update={"dignity_system": "traditional", "planets": planets}
                    )
                }
            )
        }
    )
    sun_strength = chart.strength.planets["sun"]
    strength_variant = source.model_copy(
        update={"chart": chart.model_copy(update={"strength": chart.strength.model_copy(update={"planets": {**chart.strength.planets, "sun": sun_strength.model_copy(update={"category": EngineStrengthCategory.WEAK})}})})}
    )
    balance = chart.strength.balance
    balance_variant = source.model_copy(
        update={"chart": chart.model_copy(update={"strength": chart.strength.model_copy(update={"balance": balance.model_copy(update={"elements": {**balance.elements, "fire": balance.elements["fire"].model_copy(update={"state": EngineBalanceState.BALANCED})}})})})}
    )
    phase_variant = source.model_copy(
        update={"chart": chart.model_copy(update={"strength": chart.strength.model_copy(update={"lunar_phase": chart.strength.lunar_phase.model_copy(update={"phase_number": 6})})})}
    )
    chart_kind_variant = source.model_copy(
        update={"chart": chart.model_copy(update={"chart_kind": "cosmogram"})}
    )

    for variant in (
        body_variant, angle_variant, aspect_variant, config_variant, dignity_variant,
        strength_variant, balance_variant, phase_variant, chart_kind_variant,
    ):
        assert project_chart_features(variant) != baseline


def _enum_values(enum_type: type) -> set[str]:
    return {item.value for item in enum_type}


def test_engine_closed_vocabularies_have_not_drifted() -> None:
    assert set(get_args(ChartKind)) == {"natal", "cosmogram"}
    assert _enum_values(EngineAspectType) == _enum_values(AspectType)
    assert _enum_values(EngineAspectCategory) == _enum_values(AspectCategory)
    assert _enum_values(EngineConfigurationType) == _enum_values(ConfigurationType)
    assert _enum_values(EngineConfigurationCategory) == _enum_values(ConfigurationCategory)
    assert _enum_values(EngineDignityStatus) == _enum_values(DignityStatus)
    assert _enum_values(EngineStrengthCategory) == _enum_values(StrengthCategory)
    assert _enum_values(EngineHouseType) == _enum_values(HouseType)
    assert _enum_values(EngineBalanceState) == _enum_values(BalanceState)
    assert set(get_args(Dignity.model_fields["system"].annotation)) == {"traditional", "modern"}
    assert set(ZODIAC_SIGNS) == _enum_values(ZodiacSign)


def test_engine_and_research_point_vocabularies_have_not_drifted() -> None:
    body_points = _enum_values(type(next(iter(BODY_FEATURE_POINTS))))
    relational = _enum_values(type(next(iter(RELATIONAL_POINTS))))
    assert set(DEFAULT_BODY_IDS) <= body_points
    assert {"south_node", "pars_fortune", "selena"} <= body_points
    natal_source = inspect.getsource(natal_module._add_derived_points)
    assert all(f'"{name}"' in natal_source for name in ("south_node", "pars_fortune", "selena"))
    active_relational = set(AspectConfig().natal_points)
    assert active_relational == relational - {"south_node"}
    assert set(ConfigurationConfig().points) <= active_relational
    assert {"north_node", "lilith", "pars"}.isdisjoint(body_points | relational)
    assert _enum_values(type(next(iter(STRENGTH_POINTS)))) == set(StrengthConfig().planets)


def test_engine_angle_partition_and_eight_phase_system_have_not_drifted() -> None:
    included = {item.value for item in ANGLE_FEATURE_POINTS}
    excluded = {
        "armc", "vertex", "equatorial_ascendant", "co_ascendant_koch",
        "co_ascendant_munkasey", "polar_ascendant",
    }
    assert {name for name, _ in ANGLE_INDICES} == included | excluded
    assert included == {"asc", "mc"}
    assert "vertex" in {item.value for item in RELATIONAL_POINTS}
    assert len(PHASE_NAMES) == 8


def test_element_and_modality_sources_have_not_drifted() -> None:
    expected_elements = {"fire", "earth", "air", "water"}
    expected_modalities = {"cardinal", "fixed", "mutable"}
    assert set(balance_module.ELEMENTS) == set(configuration_common.ELEMENTS) == expected_elements
    assert set(balance_module.MODALITIES) == set(configuration_common.MODALITIES) == expected_modalities
    assert {item.value for item in Element} == expected_elements
    assert {item.value for item in Modality} == expected_modalities


def test_configuration_pattern_role_sets_have_not_drifted() -> None:
    patterns_root = Path(configuration_common.__file__).parent
    observed: dict[str, set[str]] = {}
    for path in patterns_root.glob("*.py"):
        if path.name in {"__init__.py", "common.py"}:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
                continue
            if node.func.id != "build_configuration" or len(node.args) < 2:
                continue
            enum_arg, roles_arg = node.args[:2]
            assert isinstance(enum_arg, ast.Attribute)
            assert isinstance(roles_arg, ast.Dict)
            observed[enum_arg.attr.lower()] = {
                key.value for key in roles_arg.keys if isinstance(key, ast.Constant)
            }
    expected = {
        configuration_type.name.lower(): {role.value for role in roles}
        for configuration_type, roles in CONFIGURATION_ROLE_SETS.items()
    }
    assert observed == expected
