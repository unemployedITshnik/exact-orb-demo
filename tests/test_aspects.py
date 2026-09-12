"""Shared aspect module and natal integration tests."""

from __future__ import annotations

from datetime import datetime, timezone

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
import pytest
from pydantic import ValidationError

from exact_orb.engine.aspects import AspectCategory, AspectConfig, PositionedPoint, find_aspects
from exact_orb.engine.charts.natal import NatalChart, calculate_natal
from exact_orb.engine.charts.uncertainty import (
    UnstableAspectReason,
    find_time_stable_aspects,
)
from tests.fixtures.natal_1985 import REFERENCE


def _key(left: str, aspect_type: str, right: str) -> tuple[str, str, str]:
    first, second = sorted((left, right))
    return first, aspect_type, second


EXPECTED_ASPECTS: dict[tuple[str, str, str], tuple[float, AspectCategory]] = {
    _key("mean_apog", "quincunx", "pars_fortune"): (0.42, AspectCategory.EXACT),
    _key("moon", "square", "asc"): (0.44, AspectCategory.EXACT),
    _key("sun", "square", "pars_fortune"): (0.44, AspectCategory.EXACT),
    _key("uranus", "opposition", "chiron"): (0.44, AspectCategory.EXACT),
    _key("mercury", "square", "saturn"): (0.52, AspectCategory.EXACT),
    _key("jupiter", "quincunx", "asc"): (0.58, AspectCategory.EXACT),
    _key("sun", "quincunx", "jupiter"): (0.66, AspectCategory.EXACT),
    _key("true_node", "conjunction", "mean_apog"): (0.71, AspectCategory.EXACT),
    _key("sun", "trine", "mean_apog"): (0.86, AspectCategory.EXACT),
    _key("moon", "sextile", "jupiter"): (1.02, AspectCategory.WORKING),
    _key("jupiter", "sextile", "pars_fortune"): (1.10, AspectCategory.WORKING),
    _key("true_node", "quincunx", "pars_fortune"): (1.13, AspectCategory.WORKING),
    _key("sun", "sextile", "asc"): (1.24, AspectCategory.WORKING),
    _key("jupiter", "square", "mean_apog"): (1.52, AspectCategory.WORKING),
    _key("sun", "trine", "true_node"): (1.57, AspectCategory.WORKING),
    _key("sun", "quincunx", "moon"): (1.68, AspectCategory.WORKING),
    _key("pars_fortune", "quincunx", "asc"): (1.68, AspectCategory.WORKING),
    _key("neptune", "sextile", "pluto"): (1.78, AspectCategory.WORKING),
    _key("venus", "trine", "moon"): (2.07, AspectCategory.WORKING),
    _key("mean_apog", "sextile", "asc"): (2.10, AspectCategory.WORKING),
    _key("mars", "square", "saturn"): (2.18, AspectCategory.WORKING),
    _key("jupiter", "square", "true_node"): (2.23, AspectCategory.WORKING),
    _key("mercury", "square", "vertex"): (2.36, AspectCategory.WORKING),
    _key("mercury", "conjunction", "mars"): (2.70, AspectCategory.WORKING),
    _key("true_node", "sextile", "asc"): (2.81, AspectCategory.WORKING),
    _key("mars", "opposition", "mc"): (2.84, AspectCategory.WORKING),
    _key("saturn", "conjunction", "vertex"): (2.88, AspectCategory.WORKING),
    _key("venus", "square", "pluto"): (2.94, AspectCategory.WORKING),
    _key("venus", "opposition", "jupiter"): (3.08, AspectCategory.BACKGROUND),
    _key("neptune", "sextile", "mc"): (3.25, AspectCategory.BACKGROUND),
    _key("sun", "square", "uranus"): (4.66, AspectCategory.BACKGROUND),
    _key("venus", "quincunx", "neptune"): (4.72, AspectCategory.BACKGROUND),
    _key("saturn", "square", "mc"): (5.01, AspectCategory.BACKGROUND),
    _key("pluto", "quincunx", "moon"): (5.01, AspectCategory.BACKGROUND),
    _key("pluto", "trine", "mc"): (5.03, AspectCategory.BACKGROUND),
    _key("sun", "square", "chiron"): (5.10, AspectCategory.BACKGROUND),
    _key("jupiter", "sextile", "uranus"): (5.32, AspectCategory.BACKGROUND),
    _key("pluto", "trine", "asc"): (5.45, AspectCategory.BACKGROUND),
    _key("mercury", "opposition", "mc"): (5.53, AspectCategory.BACKGROUND),
    _key("jupiter", "trine", "chiron"): (5.76, AspectCategory.BACKGROUND),
    _key("asc", "quincunx", "uranus"): (5.90, AspectCategory.BACKGROUND),
    _key("moon", "trine", "uranus"): (6.34, AspectCategory.BACKGROUND),
    _key("sun", "sextile", "pluto"): (6.68, AspectCategory.BACKGROUND),
    _key("moon", "sextile", "chiron"): (6.78, AspectCategory.BACKGROUND),
    _key("moon", "square", "neptune"): (6.79, AspectCategory.BACKGROUND),
}


@pytest.mark.parametrize("factory", [AspectConfig.natal, AspectConfig.transit])
@pytest.mark.parametrize("invalid_max_orb", [-1.0, float("nan")])
def test_aspect_config_factories_validate_max_orb(factory, invalid_max_orb: float) -> None:
    with pytest.raises(ValidationError):
        factory(max_orb=invalid_max_orb)


@pytest.mark.parametrize(
    ("factory", "mode", "active_name", "inactive_name"),
    [
        (AspectConfig.natal, "natal", "natal_orbs", "transit_orbs"),
        (AspectConfig.transit, "transit", "transit_orbs", "natal_orbs"),
    ],
)
@pytest.mark.parametrize("max_orb", [0.0, 2.5])
def test_aspect_config_factories_replace_only_active_max_orb(
    factory,
    mode: str,
    active_name: str,
    inactive_name: str,
    max_orb: float,
) -> None:
    defaults = AspectConfig(mode=mode)

    config = factory(max_orb=max_orb)

    active_orbs = getattr(config, active_name)
    default_active_orbs = getattr(defaults, active_name)
    assert config.mode == mode
    assert active_orbs.max_orb == max_orb
    assert active_orbs.aspect_orbs == default_active_orbs.aspect_orbs
    assert active_orbs.body_orbs == default_active_orbs.body_orbs
    assert active_orbs.aspect_body_overrides == default_active_orbs.aspect_body_overrides
    assert getattr(config, inactive_name) == getattr(defaults, inactive_name)


def test_aspect_config_factory_defaults_remain_context_specific() -> None:
    assert AspectConfig.natal().active_orbs.max_orb == 7.0
    assert AspectConfig.transit().active_orbs.max_orb == 6.0


def test_aspect_config_rejects_removed_point_aliases() -> None:
    with pytest.raises(ValidationError, match="point_aliases"):
        AspectConfig(point_aliases={"true_node": "north_node"})


def test_aspect_config_rejects_south_node_as_independent_point() -> None:
    points = AspectConfig().natal_points + ("south_node",)

    with pytest.raises(ValidationError, match="derived lunar-node position"):
        AspectConfig(natal_points=points)


@pytest.mark.parametrize("factory", [AspectConfig.natal, AspectConfig.transit])
def test_default_orb_profiles_use_canonical_point_identifiers(factory) -> None:
    config = factory()
    orbs = config.active_orbs

    assert len(config.natal_points) == 18
    assert "south_node" not in config.natal_points
    assert {"north_node", "lilith", "pars"}.isdisjoint(orbs.body_orbs)
    assert orbs.body_orbs["true_node"] == 3.0
    assert "south_node" not in orbs.body_orbs
    assert orbs.body_orbs["mean_apog"] == 3.0
    assert orbs.body_orbs["pars_fortune"] == 3.0
    assert orbs.aspect_body_overrides["trine"]["pars_fortune"] == 2.0
    assert orbs.aspect_body_overrides["opposition"]["pars_fortune"] == 2.0


def test_natal_aspects_match_reference_without_selena() -> None:
    chart = _reference_chart()
    actual = {
        _aspect_key(aspect): aspect
        for aspect in chart.aspects or ()
        if "selena" not in {aspect.from_point.body, aspect.to_point.body}
    }

    assert set(actual) == set(EXPECTED_ASPECTS)
    for key, (expected_orb, expected_category) in EXPECTED_ASPECTS.items():
        aspect = actual[key]
        assert round(aspect.orb, 2) == expected_orb
        assert aspect.category is expected_category
        assert aspect.applying is None


def test_natal_aspects_include_current_selena_method_but_not_geocult_exact() -> None:
    chart = _reference_chart()
    selena_aspects = [
        aspect
        for aspect in chart.aspects or ()
        if "selena" in {aspect.from_point.body, aspect.to_point.body}
    ]

    assert len(selena_aspects) == 1
    aspect = selena_aspects[0]
    assert _aspect_key(aspect) == _key("chiron", "quincunx", "selena")
    assert round(aspect.orb, 2) == 1.06
    assert aspect.category is AspectCategory.WORKING


def test_natal_aspect_json_contains_category_and_null_applying() -> None:
    chart = _reference_chart()
    payload = chart.model_dump(mode="json")

    assert all("category" in item for item in payload["aspects"])
    assert all(item["applying"] is None for item in payload["aspects"])
    mercury_saturn = next(
        item
        for item in payload["aspects"]
        if {
            item["from_point"]["body"],
            item["to_point"]["body"],
        } == {"mercury", "saturn"}
        and item["aspect_type"] == "square"
    )
    assert mercury_saturn["category"] == "exact"


def test_natal_chart_uses_one_resolvable_point_namespace() -> None:
    chart = _reference_chart()
    available = {(body.chart, name) for name, body in (chart.bodies or {}).items()}
    available.update(("natal", name) for name in (chart.angles or {}))
    references = []

    for name, body in (chart.bodies or {}).items():
        assert body.name == name
    for name, angle in (chart.angles or {}).items():
        assert angle.name == name
    for aspect in chart.aspects or ():
        references.extend((aspect.from_point, aspect.to_point))
    pending = list(chart.configurations or ())
    while pending:
        configuration = pending.pop()
        references.extend(configuration.points.values())
        for aspect in configuration.aspects:
            references.extend((aspect.from_point, aspect.to_point))
        pending.extend(configuration.contains)

    assert references
    assert all((point.chart, point.body) in available for point in references)
    identifiers = {point.body for point in references}
    assert {"true_node", "mean_apog", "pars_fortune"} <= identifiers
    assert {"south_node", "north_node", "lilith", "pars"}.isdisjoint(identifiers)


def test_south_node_remains_a_derived_body_opposite_true_node() -> None:
    chart = _reference_chart()
    true_node = chart.bodies["true_node"]
    south_node = chart.bodies["south_node"]

    assert south_node.source == "derived"
    assert (south_node.longitude - true_node.longitude) % 360.0 == pytest.approx(180.0)


def test_natal_aspects_use_true_node_as_the_only_axis_representative() -> None:
    chart = _reference_chart()
    aspects = chart.aspects or ()

    assert aspects
    assert all(
        "south_node" not in {aspect.from_point.body, aspect.to_point.body}
        for aspect in aspects
    )
    assert any(
        "true_node" in {aspect.from_point.body, aspect.to_point.body}
        for aspect in aspects
    )


@pytest.mark.parametrize(
    "location",
    ("aspect", "configuration_point", "configuration_aspect", "nested_configuration"),
)
def test_natal_chart_rejects_unresolved_point_references(location: str) -> None:
    payload = _reference_chart().model_dump(mode="python")
    missing = {"chart": "natal", "body": "missing_point"}

    if location == "aspect":
        payload["aspects"][0]["from_point"] = missing
    elif location == "configuration_point":
        role = next(iter(payload["configurations"][0]["points"]))
        payload["configurations"][0]["points"][role] = missing
    elif location == "configuration_aspect":
        payload["configurations"][0]["aspects"][0]["to_point"] = missing
    else:
        nested = payload["configurations"][0].copy()
        nested["points"] = dict(nested["points"])
        role = next(iter(nested["points"]))
        nested["points"][role] = missing
        nested["contains"] = []
        payload["configurations"][0]["contains"] = [nested]

    with pytest.raises(ValidationError, match="must resolve"):
        NatalChart.model_validate(payload)


@pytest.mark.parametrize(
    "location",
    ("aspect", "configuration_point", "configuration_aspect", "nested_configuration"),
)
def test_natal_chart_rejects_south_node_in_relations(location: str) -> None:
    payload = _reference_chart().model_dump(mode="python")
    dependent = {"chart": "natal", "body": "south_node"}

    if location == "aspect":
        payload["aspects"][0]["from_point"] = dependent
    elif location == "configuration_point":
        role = next(iter(payload["configurations"][0]["points"]))
        payload["configurations"][0]["points"][role] = dependent
    elif location == "configuration_aspect":
        payload["configurations"][0]["aspects"][0]["to_point"] = dependent
    else:
        nested = payload["configurations"][0].copy()
        nested["points"] = dict(nested["points"])
        role = next(iter(nested["points"]))
        nested["points"][role] = dependent
        nested["contains"] = []
        payload["configurations"][0]["contains"] = [nested]

    with pytest.raises(ValidationError, match="derived lunar-node position south_node"):
        NatalChart.model_validate(payload)


@pytest.mark.parametrize(
    ("collection", "name"),
    (("bodies", "sun"), ("angles", "asc")),
)
def test_natal_chart_rejects_point_name_that_differs_from_mapping_key(
    collection: str,
    name: str,
) -> None:
    payload = _reference_chart().model_dump(mode="python")
    payload[collection][name]["name"] = "different_name"

    with pytest.raises(ValidationError, match="must match"):
        NatalChart.model_validate(payload)


def test_single_set_aspects_are_symmetric_records_not_duplicates() -> None:
    left = PositionedPoint(chart="natal", body="a", longitude=10.0)
    right = PositionedPoint(chart="natal", body="b", longitude=70.0)
    config = AspectConfig.natal(max_orb=7.0)

    forward = find_aspects([left, right], None, config)
    reverse = find_aspects([right, left], None, config)

    assert len(forward) == 1
    assert len(reverse) == 1
    assert _aspect_key(forward[0]) == _aspect_key(reverse[0])


def test_max_orb_boundary_filters_moon_jupiter_sextile() -> None:
    tight_chart = calculate_natal(
        REFERENCE["datetime_utc"],
        REFERENCE["latitude"],
        REFERENCE["longitude"],
        chart_kind="natal",
        house_system=REFERENCE["house_system"],
        aspect_config=AspectConfig.natal(max_orb=1.0),
    )
    loose_chart = calculate_natal(
        REFERENCE["datetime_utc"],
        REFERENCE["latitude"],
        REFERENCE["longitude"],
        chart_kind="natal",
        house_system=REFERENCE["house_system"],
        aspect_config=AspectConfig.natal(max_orb=1.1),
    )

    moon_jupiter = _key("moon", "sextile", "jupiter")
    assert moon_jupiter not in {_aspect_key(aspect) for aspect in tight_chart.aspects or ()}
    assert moon_jupiter in {_aspect_key(aspect) for aspect in loose_chart.aspects or ()}


def test_zero_aries_wrap_uses_shortest_arc() -> None:
    aspects = find_aspects(
        [
            PositionedPoint(chart="natal", body="late_pisces", longitude=359.0),
            PositionedPoint(chart="natal", body="early_aries", longitude=1.0),
        ],
        None,
        AspectConfig.natal(max_orb=3.0),
    )

    assert len(aspects) == 1
    assert aspects[0].aspect_type.value == "conjunction"
    assert aspects[0].orb == pytest.approx(2.0)


def test_same_point_does_not_aspect_itself() -> None:
    aspects = find_aspects(
        [
            PositionedPoint(chart="natal", body="moon", longitude=10.0),
            PositionedPoint(chart="natal", body="moon", longitude=11.0),
        ],
        None,
        AspectConfig.natal(max_orb=7.0),
    )

    assert aspects == []


def test_include_without_aspects_sets_block_to_none() -> None:
    chart = calculate_natal(
        REFERENCE["datetime_utc"],
        REFERENCE["latitude"],
        REFERENCE["longitude"],
        chart_kind="natal",
        house_system=REFERENCE["house_system"],
        include={"positions", "houses", "rulers"},
    )

    assert chart.aspects is None
    assert chart.bodies is not None
    assert chart.cusps is not None
    assert chart.house_rulers is not None


@settings(max_examples=30, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    longitudes=st.lists(
        st.floats(min_value=0.0, max_value=360.0, allow_nan=False, allow_infinity=False),
        min_size=1,
        max_size=8,
    )
)
def test_property_orbs_are_nonnegative_and_within_max(longitudes: list[float]) -> None:
    config = AspectConfig.natal(max_orb=7.0)
    aspects = find_aspects(_points(longitudes), None, config)

    assert all(0.0 <= aspect.orb <= config.active_orbs.max_orb for aspect in aspects)


@settings(max_examples=30, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    longitudes=st.lists(
        st.floats(min_value=0.0, max_value=360.0, allow_nan=False, allow_infinity=False),
        min_size=1,
        max_size=8,
    ),
    shift=st.floats(min_value=0.0, max_value=360.0, allow_nan=False, allow_infinity=False),
)
def test_property_global_longitude_shift_preserves_aspect_set(
    longitudes: list[float],
    shift: float,
) -> None:
    config = AspectConfig.natal(max_orb=7.0)
    original = {_aspect_key(aspect) for aspect in find_aspects(_points(longitudes), None, config)}
    shifted = {
        _aspect_key(aspect)
        for aspect in find_aspects(_points([(longitude + shift) % 360.0 for longitude in longitudes]), None, config)
    }

    assert original == shifted


@settings(max_examples=30, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    longitudes=st.lists(
        st.floats(min_value=0.0, max_value=360.0, allow_nan=False, allow_infinity=False),
        min_size=1,
        max_size=8,
    )
)
def test_property_no_self_aspects(longitudes: list[float]) -> None:
    aspects = find_aspects(_points(longitudes), None, AspectConfig.natal(max_orb=7.0))

    assert all(aspect.from_point != aspect.to_point for aspect in aspects)


def test_property_zero_max_orb_returns_no_aspects() -> None:
    aspects = find_aspects(
        [
            PositionedPoint(chart="natal", body="true_node", longitude=0.0),
            PositionedPoint(chart="natal", body="south_node", longitude=180.0),
        ],
        None,
        AspectConfig.natal(max_orb=0.0),
    )

    assert aspects == []


def test_time_stable_aspect_uses_maximum_orb() -> None:
    snapshots = (
        _snapshot(0.0, 1.5),
        _snapshot(0.0, 2.5),
    )

    stable, excluded = find_time_stable_aspects(snapshots, AspectConfig.natal())

    assert len(stable) == 1
    assert stable[0].aspect_type.value == "conjunction"
    assert stable[0].category is AspectCategory.WORKING
    assert stable[0].orb == pytest.approx(2.5)
    assert stable[0].applying is None
    assert excluded == ()


def test_time_stability_distinguishes_absence_type_and_category_changes() -> None:
    config = AspectConfig.natal()

    stable, excluded = find_time_stable_aspects(
        (_snapshot(0.0, 0.5), _snapshot(0.0, 10.0)),
        config,
    )
    assert stable == ()
    assert excluded[0].reasons == (
        UnstableAspectReason.NOT_PRESENT_FOR_ALL_TIMES,
    )
    assert excluded[0].includes_no_aspect is True

    stable, excluded = find_time_stable_aspects(
        (_snapshot(0.0, 29.5), _snapshot(0.0, 59.5)),
        config,
    )
    assert stable == ()
    assert excluded[0].reasons == (UnstableAspectReason.ASPECT_TYPE_CHANGED,)
    assert tuple(item.value for item in excluded[0].possible_aspect_types) == (
        "semisextile",
        "sextile",
    )

    stable, excluded = find_time_stable_aspects(
        (_snapshot(0.0, 0.5), _snapshot(0.0, 2.5)),
        config,
    )
    assert stable == ()
    assert excluded[0].reasons == (UnstableAspectReason.CATEGORY_CHANGED,)
    assert tuple(item.value for item in excluded[0].possible_categories) == (
        "exact",
        "working",
    )


def test_pair_without_aspect_at_any_time_has_no_diagnostic() -> None:
    snapshots = (_snapshot(0.0, 10.0), _snapshot(0.0, 11.0))

    stable, excluded = find_time_stable_aspects(snapshots, AspectConfig.natal())

    assert stable == ()
    assert excluded == ()


def test_time_stability_is_independent_of_snapshot_order() -> None:
    snapshots = (_snapshot(0.0, 1.5), _snapshot(0.0, 2.5))

    first = find_time_stable_aspects(snapshots, AspectConfig.natal())
    second = find_time_stable_aspects(tuple(reversed(snapshots)), AspectConfig.natal())

    assert first == second


def test_low_level_aspect_finder_remains_name_agnostic() -> None:
    aspects = find_aspects(
        [
            PositionedPoint(chart="natal", body="true_node", longitude=0.0),
            PositionedPoint(chart="natal", body="south_node", longitude=180.0),
        ],
        None,
        AspectConfig.natal(max_orb=7.0),
    )

    assert len(aspects) == 1
    assert aspects[0].aspect_type.value == "opposition"
    assert aspects[0].orb == pytest.approx(0.0)


def _reference_chart():
    return calculate_natal(
        datetime(1985, 9, 1, 20, 45, tzinfo=timezone.utc),
        REFERENCE["latitude"],
        REFERENCE["longitude"],
        chart_kind="natal",
        house_system=REFERENCE["house_system"],
        aspect_config=AspectConfig.natal(max_orb=7.0),
    )


def _points(longitudes: list[float]) -> list[PositionedPoint]:
    return [
        PositionedPoint(chart="natal", body=f"p{index}", longitude=longitude)
        for index, longitude in enumerate(longitudes)
    ]


def _snapshot(left: float, right: float) -> tuple[PositionedPoint, ...]:
    return (
        PositionedPoint(chart="natal", body="p0", longitude=left),
        PositionedPoint(chart="natal", body="p1", longitude=right),
    )


def _aspect_key(aspect) -> tuple[str, str, str]:
    return _key(
        aspect.from_point.body,
        aspect.aspect_type.value,
        aspect.to_point.body,
    )
