"""Research v1 model, digest, outcome, and error contracts."""

from __future__ import annotations

import json
from hashlib import sha256
import math
from datetime import UTC, datetime, timedelta, timezone, tzinfo
from pathlib import Path
from typing import get_args
from uuid import UUID

import pytest
from pydantic import TypeAdapter, ValidationError

import exact_orb.research as research_api
import exact_orb.research.models as research_models

from exact_orb.research import (
    ANGLE_FEATURE_POINTS,
    BODY_FEATURE_POINTS,
    CONFIGURATION_ROLE_SETS,
    FEATURE_SCHEMA_VERSION,
    RELATIONAL_POINTS,
    RESEARCH_DIGEST_FORMAT_VERSION,
    STRENGTH_POINTS,
    AngleFeature,
    AnglePoint,
    AspectCategory,
    AspectFeature,
    AspectType,
    BalanceFeature,
    BalanceState,
    BodyFeature,
    BodyPoint,
    ChartFeatures,
    ConfigurationCategory,
    ConfigurationFeature,
    ConfigurationPointFeature,
    ConfigurationRole,
    ConfigurationType,
    CopyEvent,
    DignityFeature,
    DignityStatus,
    DignitySystem,
    Element,
    ElementBalanceFeature,
    HouseType,
    LunarPhaseFeature,
    Modality,
    ModalityBalanceFeature,
    QualityAlreadyStored,
    QualityEventIdConflict,
    QualityStored,
    RatingEvent,
    ReadingTimeEvent,
    RegenerateEvent,
    RelationalPoint,
    ResearchAlreadyStored,
    ResearchChartKind,
    ResearchFocus,
    ResearchIdConflict,
    ResearchProjectionError,
    ResearchQualityEvent,
    ResearchRecord,
    ResearchRecordAbsent,
    ResearchSelection,
    ResearchStored,
    ResearchTopic,
    ResearchWriteError,
    StrengthCategory,
    StrengthFeature,
    StrengthPoint,
    ZodiacSign,
    event_content_digest,
    floor_to_utc_hour,
    record_content_digest,
)
from tests.research.conformance import (
    EVENT_ID,
    NOW,
    OTHER_EVENT_ID,
    OTHER_RESEARCH_ID,
    RESEARCH_ID,
    make_event,
    make_record,
)


pytestmark = pytest.mark.no_ephemeris_autoinit


GOLDEN_PATH = Path(__file__).parent / "golden" / "research_digest_v1.json"


def rich_features() -> ChartFeatures:
    return ChartFeatures(
        chart_kind="natal",
        bodies=(
            BodyFeature(point="moon", sign="Cancer", house=4, retrograde=False),
            BodyFeature(point="sun", sign="Aries", house=1, retrograde=False),
        ),
        angles=(
            AngleFeature(point="mc", sign="Capricorn"),
            AngleFeature(point="asc", sign="Libra"),
        ),
        aspects=(
            AspectFeature(
                from_point="vertex",
                to_point="sun",
                aspect_type="square",
                category="working",
            ),
        ),
        configurations=(
            ConfigurationFeature(
                configuration_type="t_square",
                category="tight",
                points=(
                    ConfigurationPointFeature(role="base_2", point="moon"),
                    ConfigurationPointFeature(role="apex", point="sun"),
                    ConfigurationPointFeature(role="base_1", point="asc"),
                ),
                element=None,
                modality="cardinal",
            ),
        ),
        dignities=(
            DignityFeature(point="moon", system="modern", status="domicile"),
            DignityFeature(point="sun", system="modern", status="exaltation"),
        ),
        strengths=(
            StrengthFeature(point="moon", category="moderate", house_type="cadent"),
            StrengthFeature(point="sun", category="strong", house_type="angular"),
        ),
        balance=(
            ModalityBalanceFeature(axis="modality", bucket="fixed", state="deficit"),
            ElementBalanceFeature(axis="element", bucket="fire", state="excess"),
        ),
        lunar_phase=LunarPhaseFeature(phase_number=5),
    )


def golden_record() -> ResearchRecord:
    return make_record().model_copy(update={"chart_features": rich_features()})


MODEL_CASES = (
    BodyFeature(point="sun", sign="Aries", house=1, retrograde=False),
    AngleFeature(point="asc", sign="Aries"),
    AspectFeature(
        from_point="moon", to_point="sun", aspect_type="trine", category="exact"
    ),
    ConfigurationPointFeature(role="apex", point="sun"),
    ConfigurationFeature(
        configuration_type="t_square",
        category="tight",
        points=(
            ConfigurationPointFeature(role="apex", point="sun"),
            ConfigurationPointFeature(role="base_1", point="moon"),
            ConfigurationPointFeature(role="base_2", point="mars"),
        ),
    ),
    DignityFeature(point="sun", system="modern", status="domicile"),
    StrengthFeature(point="sun", category="strong", house_type="angular"),
    ElementBalanceFeature(axis="element", bucket="fire", state="balanced"),
    ModalityBalanceFeature(axis="modality", bucket="fixed", state="balanced"),
    LunarPhaseFeature(phase_number=1),
    ChartFeatures(chart_kind="natal"),
    ResearchSelection(topic="natal", focus="general"),
    make_record(),
    make_event("rating"),
    make_event("regenerate"),
    make_event("copy"),
    make_event("reading_time"),
    ResearchStored(research_id=RESEARCH_ID),
    ResearchAlreadyStored(research_id=RESEARCH_ID),
    ResearchIdConflict(research_id=RESEARCH_ID),
    QualityStored(event_id=EVENT_ID, research_id=RESEARCH_ID),
    QualityAlreadyStored(event_id=EVENT_ID, research_id=RESEARCH_ID),
    QualityEventIdConflict(event_id=EVENT_ID),
    ResearchRecordAbsent(event_id=EVENT_ID, research_id=RESEARCH_ID),
)


@pytest.mark.parametrize("model", MODEL_CASES, ids=lambda item: type(item).__name__)
def test_all_models_are_frozen_and_forbid_extra(model: object) -> None:
    with pytest.raises(ValidationError):
        model.__class__.model_validate({**model.model_dump(mode="python"), "extra": True})
    field = next(iter(type(model).model_fields))
    with pytest.raises(ValidationError):
        setattr(model, field, getattr(model, field))


def test_nested_collections_are_immutable_tuples() -> None:
    features = rich_features()
    assert isinstance(features.bodies, tuple)
    assert isinstance(features.configurations[0].points, tuple)
    with pytest.raises(TypeError):
        features.bodies[0] = features.bodies[0]


@pytest.mark.parametrize(
    ("enum_type", "expected"),
    [
        (ResearchChartKind, {"natal", "cosmogram"}),
        (ResearchTopic, {"natal"}),
        (ResearchFocus, {"general", "career", "money", "love"}),
        (ZodiacSign, {"Aries", "Taurus", "Gemini", "Cancer", "Leo", "Virgo", "Libra", "Scorpio", "Sagittarius", "Capricorn", "Aquarius", "Pisces"}),
        (AspectType, {"conjunction", "semisextile", "sextile", "square", "trine", "quincunx", "opposition"}),
        (AspectCategory, {"exact", "working", "background"}),
        (ConfigurationType, {"t_square", "yod", "bisextile", "grand_cross", "grand_trine", "trapeze"}),
        (ConfigurationCategory, {"tight", "moderate", "loose"}),
        (DignitySystem, {"traditional", "modern"}),
        (DignityStatus, {"domicile", "exaltation", "detriment", "fall", "peregrine"}),
        (StrengthCategory, {"strong", "moderate", "weak"}),
        (HouseType, {"angular", "succedent", "cadent"}),
        (Element, {"fire", "earth", "air", "water"}),
        (Modality, {"cardinal", "fixed", "mutable"}),
        (BalanceState, {"deficit", "balanced", "excess"}),
    ],
)
def test_closed_vocabularies_are_exact(enum_type: type, expected: set[str]) -> None:
    assert {item.value for item in enum_type} == expected


def test_quality_event_kinds_are_derived_from_discriminated_union() -> None:
    event_union = get_args(ResearchQualityEvent)[0]
    event_models = get_args(event_union)
    assert event_models
    observed = {
        kind
        for event_model in event_models
        for kind in get_args(event_model.model_fields["kind"].annotation)
    }
    assert observed == {"rating", "regenerate", "copy", "reading_time"}
    assert not hasattr(research_models, "QualityKind")
    assert not hasattr(research_api, "QualityKind")


def test_point_vocabularies_are_exact_and_disjoint_from_aliases() -> None:
    body = {item.value for item in BODY_FEATURE_POINTS}
    assert body == {
        "sun", "moon", "mercury", "venus", "mars", "jupiter", "saturn", "uranus",
        "neptune", "pluto", "chiron", "true_node", "mean_apog", "south_node",
        "pars_fortune", "selena",
    }
    assert {item.value for item in ANGLE_FEATURE_POINTS} == {"asc", "mc"}
    assert {item.value for item in STRENGTH_POINTS} == {
        "sun", "moon", "mercury", "venus", "mars", "jupiter", "saturn", "uranus",
        "neptune", "pluto",
    }
    assert {item.value for item in RELATIONAL_POINTS} == body | {"asc", "mc", "vertex"}
    assert {"north_node", "lilith", "pars"}.isdisjoint(item.value for item in RelationalPoint)


def test_research_v1_keeps_south_node_as_a_legacy_relational_value() -> None:
    features = ChartFeatures(
        feature_schema_version=1,
        chart_kind="natal",
        aspects=(
            AspectFeature(
                from_point="south_node",
                to_point="sun",
                aspect_type="trine",
                category="working",
            ),
        ),
    )

    assert features.aspects[0].from_point is RelationalPoint.SOUTH_NODE


@pytest.mark.parametrize("house", [0, 13])
def test_house_bounds(house: int) -> None:
    with pytest.raises(ValidationError):
        BodyFeature(point="sun", sign="Aries", house=house, retrograde=False)


@pytest.mark.parametrize("rating", [0, 6])
def test_rating_bounds(rating: int) -> None:
    with pytest.raises(ValidationError):
        RatingEvent(
            kind="rating", event_id=EVENT_ID, research_id=RESEARCH_ID,
            observed_at=NOW, rating=rating,
        )


@pytest.mark.parametrize("phase", [0, 9])
def test_lunar_phase_bounds_and_no_name_field(phase: int) -> None:
    with pytest.raises(ValidationError):
        LunarPhaseFeature(phase_number=phase)
    assert set(LunarPhaseFeature.model_fields) == {"phase_number"}


@pytest.mark.parametrize("field", ["tokens_in", "tokens_out", "cost_usd", "latency_ms"])
@pytest.mark.parametrize("value", [-1, math.inf, -math.inf, math.nan])
def test_metrics_reject_negative_and_non_finite(field: str, value: float) -> None:
    with pytest.raises(ValidationError):
        make_record().model_copy(update={field: value}).__class__.model_validate(
            {**make_record().model_dump(mode="python"), field: value}
        )


def test_negative_zero_is_normalized() -> None:
    record = ResearchRecord.model_validate(
        {**make_record().model_dump(mode="python"), "cost_usd": -0.0, "latency_ms": -0.0}
    )
    assert math.copysign(1.0, record.cost_usd) == 1.0
    assert math.copysign(1.0, record.latency_ms) == 1.0


def test_uuid_fields_require_version_four() -> None:
    version_one = UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")
    with pytest.raises(ValidationError):
        make_record(version_one)
    with pytest.raises(ValidationError):
        make_event(event_id=version_one)


@pytest.mark.parametrize("value", ["a", "text-bison@002", "model~preview", "A" * 128])
def test_bounded_identifiers_accept_contract_values(value: str) -> None:
    assert make_record(model=value).model == value


@pytest.mark.parametrize("value", ["", "A" * 129, " has-space", "has space", "модель", "a\nb"])
def test_bounded_identifiers_reject_out_of_contract_values(value: str) -> None:
    with pytest.raises(ValidationError):
        make_record(model=value)


class _ZeroOffset(tzinfo):
    def utcoffset(self, dt: datetime | None) -> timedelta:
        return timedelta(0)

    def dst(self, dt: datetime | None) -> timedelta:
        return timedelta(0)


def test_zero_offset_tzinfo_is_normalized_to_utc() -> None:
    value = datetime(2026, 9, 6, 12, tzinfo=_ZeroOffset())
    assert make_record().model_copy(update={"created_at": value}).created_at.tzinfo is not UTC
    validated = ResearchRecord.model_validate(
        {**make_record().model_dump(mode="python"), "created_at": value}
    )
    assert validated.created_at.tzinfo is UTC


@pytest.mark.parametrize(
    "value",
    [
        datetime(2026, 9, 6, 12),
        datetime(2026, 9, 6, 12, tzinfo=timezone(timedelta(hours=3))),
        datetime(2026, 9, 6, 12, 1, tzinfo=UTC),
        datetime(2026, 9, 6, 12, 0, 1, tzinfo=UTC),
        datetime(2026, 9, 6, 12, 0, 0, 1, tzinfo=UTC),
    ],
)
def test_models_reject_invalid_utc_hour(value: datetime) -> None:
    with pytest.raises(ValidationError):
        ResearchRecord.model_validate(
            {**make_record().model_dump(mode="python"), "created_at": value}
        )


def test_floor_to_utc_hour_has_exact_boundary_semantics() -> None:
    value = datetime(2026, 9, 6, 12, 59, 59, 999999, tzinfo=_ZeroOffset())
    assert floor_to_utc_hour(value) == datetime(2026, 9, 6, 12, tzinfo=UTC)
    assert floor_to_utc_hour(NOW) == NOW
    assert floor_to_utc_hour(NOW).tzinfo is UTC
    with pytest.raises(ValueError):
        floor_to_utc_hour(datetime(2026, 9, 6, 12))
    with pytest.raises(ValueError):
        floor_to_utc_hour(datetime(2026, 9, 6, 12, tzinfo=timezone(timedelta(hours=1))))


@pytest.mark.parametrize("field,value", [("topic", "transit"), ("topic", "other"), ("focus", "relationships"), ("focus", "other")])
def test_selection_fails_closed(field: str, value: str) -> None:
    payload = {"topic": "natal", "focus": "general", field: value}
    with pytest.raises(ValidationError):
        ResearchSelection.model_validate(payload)


@pytest.mark.parametrize("kind", ["rating", "regenerate", "copy", "reading_time"])
def test_quality_union_uses_kind_discriminator(kind: str) -> None:
    event = make_event(kind)
    assert TypeAdapter(ResearchQualityEvent).validate_python(event.model_dump()) == event


def test_quality_union_rejects_unknown_mixed_and_extra_payloads() -> None:
    adapter = TypeAdapter(ResearchQualityEvent)
    base = make_event("rating").model_dump(mode="python")
    with pytest.raises(ValidationError):
        adapter.validate_python({**base, "kind": "unknown"})
    with pytest.raises(ValidationError):
        adapter.validate_python({**base, "reading_time_ms": 5})
    with pytest.raises(ValidationError):
        adapter.validate_python({**base, "extra": True})


def test_balance_discriminator_rejects_axis_bucket_mismatch() -> None:
    adapter = TypeAdapter(BalanceFeature)
    assert isinstance(
        adapter.validate_python({"axis": "element", "bucket": "fire", "state": "balanced"}),
        ElementBalanceFeature,
    )
    with pytest.raises(ValidationError):
        adapter.validate_python({"axis": "element", "bucket": "fixed", "state": "balanced"})


@pytest.mark.parametrize("configuration_type", list(ConfigurationType))
def test_configuration_role_matrix(configuration_type: ConfigurationType) -> None:
    roles = CONFIGURATION_ROLE_SETS[configuration_type]
    available = iter(RelationalPoint)
    valid = tuple(
        ConfigurationPointFeature(role=role, point=next(available))
        for role in roles
    )
    assert ConfigurationFeature(
        configuration_type=configuration_type,
        category="tight",
        points=valid,
    )
    with pytest.raises(ValidationError):
        ConfigurationFeature(
            configuration_type=configuration_type,
            category="tight",
            points=valid[:-1],
        )


def test_configuration_rejects_duplicate_roles_and_points() -> None:
    valid = (
        ConfigurationPointFeature(role="apex", point="sun"),
        ConfigurationPointFeature(role="base_1", point="moon"),
        ConfigurationPointFeature(role="base_2", point="mars"),
    )
    for invalid in (
        (valid[0], valid[0], valid[2]),
        (valid[0], valid[1], valid[2].model_copy(update={"point": RelationalPoint.SUN})),
    ):
        with pytest.raises(ValidationError):
            ConfigurationFeature(
                configuration_type="t_square", category="tight", points=invalid
            )


@pytest.mark.parametrize("alias", ["north_node", "lilith", "pars"])
def test_aliases_are_rejected_in_all_point_fields(alias: str) -> None:
    invalid_payloads = (
        (BodyFeature, {"point": alias, "sign": "Aries", "retrograde": False}),
        (AngleFeature, {"point": alias, "sign": "Aries"}),
        (AspectFeature, {"from_point": alias, "to_point": "sun", "aspect_type": "trine", "category": "exact"}),
        (ConfigurationPointFeature, {"role": "apex", "point": alias}),
        (DignityFeature, {"point": alias, "system": "modern", "status": "domicile"}),
        (StrengthFeature, {"point": alias, "category": "strong", "house_type": "angular"}),
    )
    for model, payload in invalid_payloads:
        with pytest.raises(ValidationError):
            model.model_validate(payload)


@pytest.mark.parametrize("point", ["armc", "vertex", "equatorial_ascendant", "co_ascendant_koch", "co_ascendant_munkasey", "polar_ascendant"])
def test_auxiliary_angle_signs_are_rejected(point: str) -> None:
    with pytest.raises(ValidationError):
        AngleFeature(point=point, sign="Aries")
    if point == "vertex":
        assert AspectFeature(
            from_point="vertex", to_point="sun", aspect_type="trine", category="exact"
        )


def test_point_uniqueness_and_one_dignity_system() -> None:
    body = BodyFeature(point="sun", sign="Aries", retrograde=False)
    dignity = DignityFeature(point="sun", system="modern", status="domicile")
    strength = StrengthFeature(point="sun", category="strong", house_type="angular")
    balance = ElementBalanceFeature(axis="element", bucket="fire", state="balanced")
    for field, values in (
        ("bodies", (body, body)),
        ("dignities", (dignity, dignity)),
        ("strengths", (strength, strength)),
        ("balance", (balance, balance)),
    ):
        with pytest.raises(ValidationError):
            ChartFeatures.model_validate({"chart_kind": "natal", field: values})
    with pytest.raises(ValidationError):
        ChartFeatures(
            chart_kind="natal",
            dignities=(
                dignity,
                DignityFeature(point="moon", system="traditional", status="peregrine"),
            ),
        )


def test_aspects_orient_endpoints_and_reject_self_relation() -> None:
    aspect = AspectFeature(
        from_point="sun", to_point="moon", aspect_type="trine", category="exact"
    )
    assert (aspect.from_point.value, aspect.to_point.value) == ("moon", "sun")
    with pytest.raises(ValidationError):
        AspectFeature(
            from_point="sun", to_point="sun", aspect_type="trine", category="exact"
        )


def test_feature_tuples_use_full_canonical_order_and_keep_duplicates() -> None:
    first = rich_features()
    reversed_payload = first.model_dump(mode="python")
    for field in ("bodies", "angles", "aspects", "configurations", "dignities", "strengths", "balance"):
        reversed_payload[field] = tuple(reversed(reversed_payload[field]))
    second = ChartFeatures.model_validate(reversed_payload)
    assert second == first
    duplicated_aspects = ChartFeatures(
        chart_kind="natal", aspects=(first.aspects[0], first.aspects[0])
    )
    assert len(duplicated_aspects.aspects) == 2


def test_none_and_empty_tuple_remain_distinct() -> None:
    absent = ChartFeatures(chart_kind="natal")
    empty = ChartFeatures(
        chart_kind="natal",
        bodies=(), angles=(), aspects=(), configurations=(), dignities=(), strengths=(), balance=(),
    )
    assert absent != empty
    assert absent.bodies is None
    assert empty.bodies == ()


def test_feature_schema_and_digest_versions_have_one_literal_source() -> None:
    assert FEATURE_SCHEMA_VERSION == 1
    assert RESEARCH_DIGEST_FORMAT_VERSION == 1
    assert rich_features().feature_schema_version == 1
    assert "feature_schema_version" not in ResearchRecord.model_fields


def test_persisted_models_expose_only_the_whitelisted_schema() -> None:
    assert set(ChartFeatures.model_fields) == {
        "feature_schema_version",
        "chart_kind",
        "bodies",
        "angles",
        "aspects",
        "configurations",
        "dignities",
        "strengths",
        "balance",
        "lunar_phase",
    }
    assert set(ResearchRecord.model_fields) == {
        "research_id",
        "created_at",
        "calculation_version",
        "chart_features",
        "selection",
        "recipe_version",
        "model",
        "tokens_in",
        "tokens_out",
        "cost_usd",
        "latency_ms",
    }
    forbidden = {
        "session_id", "calculation_key", "spec", "artifact", "warnings", "query",
        "response", "metadata", "birth_data", "latitude", "longitude", "julian_day_ut",
    }
    assert forbidden.isdisjoint(ChartFeatures.model_fields)
    assert forbidden.isdisjoint(ResearchRecord.model_fields)


def test_digest_identity_and_content_boundaries() -> None:
    record = golden_record()
    assert record_content_digest(record) == record_content_digest(
        record.model_copy(update={"research_id": OTHER_RESEARCH_ID})
    )
    reordered = ResearchRecord.model_validate(record.model_dump(mode="python"))
    assert record_content_digest(record) == record_content_digest(reordered)

    event = make_event("rating")
    digest = event_content_digest(event)
    assert digest == event_content_digest(event.model_copy(update={"event_id": OTHER_EVENT_ID}))
    variants = (
        event.model_copy(update={"research_id": OTHER_RESEARCH_ID}),
        make_event("copy", EVENT_ID),
        event.model_copy(update={"observed_at": NOW + timedelta(hours=1)}),
        event.model_copy(update={"rating": 1}),
    )
    assert all(event_content_digest(item) != digest for item in variants)


def test_digest_matches_frozen_canonical_json_and_hashes() -> None:
    golden = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    record = golden_record()
    events = {
        kind: make_event(kind, UUID(data["event_id"]))
        for kind, data in golden["events"].items()
    }
    record_json = json.dumps(
        record.model_dump(mode="json", exclude={"research_id"}),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    assert record_json == golden["record"]["canonical_json"]
    assert sha256(record_json.encode("utf-8")).hexdigest() == golden["record"]["sha256"]
    assert record_content_digest(record) == golden["record"]["sha256"]
    for kind, event in events.items():
        event_json = json.dumps(
            event.model_dump(mode="json", exclude={"event_id"}),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        assert event_json == golden["events"][kind]["canonical_json"]
        assert sha256(event_json.encode("utf-8")).hexdigest() == golden["events"][kind]["sha256"]
        assert event_content_digest(event) == golden["events"][kind]["sha256"]
    assert golden["record"]["canonical_payload"] == json.loads(golden["record"]["canonical_json"])
    for data in golden["events"].values():
        assert data["canonical_payload"] == json.loads(data["canonical_json"])


def test_float_digest_matches_frozen_shortest_round_trip_json() -> None:
    golden = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))["float_record"]
    payload = make_record().model_dump(mode="python")
    payload.update(latency_ms=0.1 + 0.2, cost_usd=1 / 3)
    record = ResearchRecord.model_validate(payload)
    canonical_json = json.dumps(
        record.model_dump(mode="json", exclude={"research_id"}),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    assert canonical_json == golden["canonical_json"]
    assert json.loads(canonical_json) == golden["canonical_payload"]
    assert sha256(canonical_json.encode("utf-8")).hexdigest() == golden["sha256"]
    assert record_content_digest(record) == golden["sha256"]


def test_typed_errors_have_stable_safe_codes() -> None:
    projection = ResearchProjectionError("RESEARCH_PROJECTION_UNSUPPORTED_VALUE")
    write = ResearchWriteError("RESEARCH_WRITE_FAILED")
    assert projection.error_code == str(projection) == "RESEARCH_PROJECTION_UNSUPPORTED_VALUE"
    assert write.error_code == str(write) == "RESEARCH_WRITE_FAILED"
    with pytest.raises(ValueError):
        ResearchWriteError("")
