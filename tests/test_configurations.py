"""Aspect configuration tests."""

from __future__ import annotations

from datetime import datetime, timezone

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
import pytest
from pydantic import ValidationError

from exact_orb.engine.aspects import (
    Aspect,
    AspectCategory,
    AspectConfig,
    AspectPointRef,
    AspectType,
)
from exact_orb.engine.aspects.types import ASPECT_ANGLES
from exact_orb.engine.configurations import (
    Configuration,
    ConfigurationCategory,
    ConfigurationConfig,
    ConfigurationType,
    find_configurations,
)
from exact_orb.engine.configurations.integrity import validate_configuration_tree
from exact_orb.engine.charts.natal import calculate_natal
from tests.fixtures.natal_1985 import REFERENCE


def _config_key(config_type: str, participants: set[str]) -> tuple[str, frozenset[str]]:
    return config_type, frozenset(participants)


EXPECTED_CONFIGURATIONS: dict[tuple[str, frozenset[str]], float] = {
    _config_key("t_square", {"sun", "uranus", "chiron"}): 5.10,
    _config_key("yod", {"sun", "moon", "jupiter"}): 1.68,
    _config_key("yod", {"moon", "sun", "pluto"}): 6.68,
    _config_key("bisextile", {"moon", "jupiter", "chiron"}): 6.78,
    _config_key("bisextile", {"jupiter", "moon", "uranus"}): 6.34,
    _config_key("trapeze", {"moon", "jupiter", "uranus", "chiron"}): 6.78,
}

EXPECTED_CONFIGURATION_CATEGORIES = {
    _config_key("t_square", {"sun", "uranus", "chiron"}): ConfigurationCategory.LOOSE,
    _config_key("yod", {"sun", "moon", "jupiter"}): ConfigurationCategory.TIGHT,
    _config_key("yod", {"moon", "sun", "pluto"}): ConfigurationCategory.LOOSE,
    _config_key("bisextile", {"moon", "jupiter", "chiron"}): ConfigurationCategory.LOOSE,
    _config_key("bisextile", {"jupiter", "moon", "uranus"}): ConfigurationCategory.LOOSE,
    _config_key("trapeze", {"moon", "jupiter", "uranus", "chiron"}): ConfigurationCategory.LOOSE,
}

EXPECTED_EDGE_COUNTS = {
    ConfigurationType.T_SQUARE: 3,
    ConfigurationType.YOD: 3,
    ConfigurationType.BISEXTILE: 3,
    ConfigurationType.GRAND_CROSS: 6,
    ConfigurationType.GRAND_TRINE: 3,
    ConfigurationType.TRAPEZE: 6,
}


def _aspect_lists():
    return st.lists(_aspect_strategy(), min_size=0, max_size=24)


def _aspect_strategy():
    point_pairs = st.tuples(
        st.integers(min_value=0, max_value=5),
        st.integers(min_value=0, max_value=5),
    ).filter(lambda pair: pair[0] != pair[1])
    return st.builds(
        _make_aspect,
        pair=point_pairs,
        aspect_type=st.sampled_from(tuple(AspectType)),
        orb=st.floats(min_value=0.0, max_value=7.0, allow_nan=False, allow_infinity=False),
    )


def _make_aspect(
    pair: tuple[int, int],
    aspect_type: AspectType,
    orb: float,
) -> Aspect:
    return Aspect(
        from_point=AspectPointRef(chart="natal", body=f"p{pair[0]}"),
        to_point=AspectPointRef(chart="natal", body=f"p{pair[1]}"),
        aspect_type=aspect_type,
        exact_angle=ASPECT_ANGLES[aspect_type],
        orb=orb,
        category=AspectCategory.EXACT,
        applying=None,
    )


def test_natal_configurations_match_reference() -> None:
    chart = _reference_chart()
    actual = {_configuration_key(configuration): configuration for configuration in chart.configurations or ()}

    assert set(actual) == set(EXPECTED_CONFIGURATIONS)
    for key, expected_max_orb in EXPECTED_CONFIGURATIONS.items():
        assert round(actual[key].max_orb, 2) == expected_max_orb
        assert actual[key].category is EXPECTED_CONFIGURATION_CATEGORIES[key]


def test_configuration_categories_match_reference_distribution() -> None:
    chart = _reference_chart()
    categories = [configuration.category for configuration in chart.configurations or ()]

    assert categories.count(ConfigurationCategory.TIGHT) == 1
    assert categories.count(ConfigurationCategory.MODERATE) == 0
    assert categories.count(ConfigurationCategory.LOOSE) == 5


def test_configuration_json_contains_category() -> None:
    chart = _reference_chart()
    payload = chart.model_dump(mode="json")

    assert all("category" in item for item in payload["configurations"])
    assert {item["category"] for item in payload["configurations"]} == {"tight", "loose"}


def test_configuration_roles_match_reference() -> None:
    chart = _reference_chart()
    by_key = {_configuration_key(configuration): configuration for configuration in chart.configurations or ()}

    assert by_key[_config_key("yod", {"sun", "moon", "jupiter"})].points["apex"].body == "sun"
    assert by_key[_config_key("yod", {"moon", "sun", "pluto"})].points["apex"].body == "moon"


def test_configuration_deduplication_is_independent_of_input_order() -> None:
    chart = _reference_chart()
    aspects = list(chart.aspects or ())
    config = ConfigurationConfig(configuration_max_orb=7.0)

    forward = find_configurations(aspects, config)
    reverse = find_configurations(list(reversed(aspects)), config)

    assert {_configuration_key(item) for item in forward} == {_configuration_key(item) for item in reverse}
    assert len(forward) == len(reverse) == len(EXPECTED_CONFIGURATIONS)


def test_configuration_threshold_is_strict_max_orb() -> None:
    chart = _reference_chart()
    aspects = list(chart.aspects or ())

    strict = find_configurations(aspects, ConfigurationConfig(configuration_max_orb=5.0))
    relaxed = find_configurations(aspects, ConfigurationConfig(configuration_max_orb=5.2))

    assert {_configuration_key(item) for item in strict} == {
        _config_key("yod", {"sun", "moon", "jupiter"})
    }
    assert _config_key("t_square", {"sun", "uranus", "chiron"}) in {
        _configuration_key(item) for item in relaxed
    }
    assert _config_key("yod", {"moon", "sun", "pluto"}) not in {
        _configuration_key(item) for item in strict
    }


def test_empty_configuration_input_returns_empty_list() -> None:
    assert find_configurations([], ConfigurationConfig()) == []


def test_default_configuration_points_use_one_lunar_node_axis_representative() -> None:
    points = ConfigurationConfig().points

    assert points is not None
    assert len(points) == 13
    assert "true_node" in points
    assert "south_node" not in points


def test_configuration_config_rejects_south_node_as_independent_point() -> None:
    points = ConfigurationConfig().points + ("south_node",)

    with pytest.raises(ValidationError, match="derived lunar-node position"):
        ConfigurationConfig(points=points)


def test_points_none_does_not_reenable_south_node_configurations() -> None:
    aspects = [
        _named_aspect("true_node", "south_node", AspectType.OPPOSITION),
        _named_aspect("jupiter", "true_node", AspectType.SQUARE),
        _named_aspect("jupiter", "south_node", AspectType.SQUARE),
        _named_aspect("base_1", "base_2", AspectType.OPPOSITION),
        _named_aspect("apex", "base_1", AspectType.SQUARE),
        _named_aspect("apex", "base_2", AspectType.SQUARE),
    ]

    configurations = find_configurations(
        aspects,
        ConfigurationConfig(points=None, enabled_types=(ConfigurationType.T_SQUARE,)),
    )

    assert len(configurations) == 1
    assert {point.body for point in configurations[0].points.values()} == {
        "apex",
        "base_1",
        "base_2",
    }


def test_include_without_configurations_sets_block_to_none() -> None:
    chart = calculate_natal(
        REFERENCE["datetime_utc"],
        REFERENCE["latitude"],
        REFERENCE["longitude"],
        chart_kind="natal",
        house_system=REFERENCE["house_system"],
        include={"positions", "houses", "rulers", "aspects"},
    )

    assert chart.configurations is None
    assert chart.aspects is not None


@pytest.mark.parametrize("empty_configurations", (False, True))
def test_natal_chart_requires_canonical_aspects_for_configurations(
    empty_configurations: bool,
) -> None:
    payload = _reference_chart().model_dump(mode="python")
    payload["aspects"] = None
    if empty_configurations:
        payload["configurations"] = []

    with pytest.raises(ValidationError, match=r"configurations.*aspects"):
        type(_reference_chart()).model_validate(payload)


def test_reference_configuration_edges_exactly_materialize_chart_aspects() -> None:
    chart = _reference_chart()
    canonical = chart.aspects or ()
    pending = list(chart.configurations or ())

    assert pending
    while pending:
        configuration = pending.pop()
        assert all(aspect in canonical for aspect in configuration.aspects)
        pending.extend(configuration.contains)


@pytest.mark.parametrize(
    "mutation",
    ("endpoint", "direction", "aspect_type", "exact_angle", "orb", "category", "applying"),
)
def test_natal_chart_rejects_noncanonical_materialized_aspect_field(
    mutation: str,
) -> None:
    payload = _reference_chart().model_dump(mode="python")
    nested = payload["configurations"][0]["aspects"][0]

    if mutation == "endpoint":
        nested["from_point"] = {"chart": "natal", "body": "venus"}
    elif mutation == "direction":
        nested["from_point"], nested["to_point"] = (
            nested["to_point"],
            nested["from_point"],
        )
    elif mutation == "aspect_type":
        nested["aspect_type"] = "conjunction"
    elif mutation == "exact_angle":
        nested["exact_angle"] += 0.5
    elif mutation == "orb":
        nested["orb"] += 0.5
    elif mutation == "category":
        nested["category"] = "background"
    else:
        nested["applying"] = True

    with pytest.raises(
        ValidationError,
        match=r"configurations\[0\]\.aspects\[0\].*exactly materialize",
    ):
        type(_reference_chart()).model_validate(payload)


def test_natal_chart_rejects_configuration_edge_removed_from_canonical_list() -> None:
    payload = _reference_chart().model_dump(mode="python")
    nested = payload["configurations"][0]["aspects"][0]
    payload["aspects"] = tuple(
        aspect for aspect in payload["aspects"] if aspect != nested
    )

    with pytest.raises(
        ValidationError,
        match=r"configurations\[0\]\.aspects\[0\].*NatalChart\.aspects",
    ):
        type(_reference_chart()).model_validate(payload)


@pytest.mark.parametrize("configuration_type", tuple(ConfigurationType))
def test_configuration_integrity_accepts_each_supported_topology(
    configuration_type: ConfigurationType,
) -> None:
    configuration, canonical = _synthetic_configuration(configuration_type)

    validate_configuration_tree(configuration, canonical, "configurations[0]")


@pytest.mark.parametrize("configuration_type", tuple(ConfigurationType))
def test_configuration_integrity_rejects_wrong_role_topology(
    configuration_type: ConfigurationType,
) -> None:
    configuration, canonical = _synthetic_configuration(configuration_type)
    first = canonical[0].model_copy(update={"aspect_type": AspectType.CONJUNCTION})
    corrupted_edges = (first, *canonical[1:])
    corrupted = configuration.model_copy(update={"aspects": corrupted_edges})

    with pytest.raises(ValueError, match=r"aspects.*(topology|opposition)"):
        validate_configuration_tree(
            corrupted,
            corrupted_edges,
            "configurations[0]",
        )


@pytest.mark.parametrize(
    ("mutation", "message"),
    (
        ("missing_role", "roles"),
        ("extra_role", "roles"),
        ("duplicate_point", "distinct"),
        ("point_outside_edges", "endpoints"),
    ),
)
def test_configuration_integrity_rejects_inconsistent_points(
    mutation: str,
    message: str,
) -> None:
    configuration, canonical = _synthetic_configuration(ConfigurationType.T_SQUARE)
    points = dict(configuration.points)

    if mutation == "missing_role":
        points.pop("base_2")
    elif mutation == "extra_role":
        points["extra"] = AspectPointRef(chart="natal", body="p3")
    elif mutation == "duplicate_point":
        points["base_2"] = points["base_1"]
    else:
        points["base_2"] = AspectPointRef(chart="natal", body="p3")

    corrupted = configuration.model_copy(update={"points": points})
    with pytest.raises(ValueError, match=message):
        validate_configuration_tree(corrupted, canonical, "configurations[0]")


def test_configuration_integrity_rejects_duplicate_or_extra_pair() -> None:
    configuration, canonical = _synthetic_configuration(ConfigurationType.T_SQUARE)
    duplicate = canonical[0].model_copy(update={"aspect_type": AspectType.TRINE})
    corrupted_edges = (*canonical, duplicate)
    corrupted = configuration.model_copy(update={"aspects": corrupted_edges})

    with pytest.raises(ValueError, match="duplicates a participant pair"):
        validate_configuration_tree(
            corrupted,
            corrupted_edges,
            "configurations[0]",
        )


def test_configuration_integrity_validates_max_orb_and_chart() -> None:
    configuration, canonical = _synthetic_configuration(ConfigurationType.YOD)

    with pytest.raises(ValueError, match="max_orb"):
        validate_configuration_tree(
            configuration.model_copy(update={"max_orb": configuration.max_orb + 0.1}),
            canonical,
            "configurations[0]",
        )
    with pytest.raises(ValueError, match=r"\.chart"):
        validate_configuration_tree(
            configuration.model_copy(update={"chart": "mixed"}),
            canonical,
            "configurations[0]",
        )

    mixed, mixed_canonical = _synthetic_configuration(
        ConfigurationType.T_SQUARE,
        chart_by_role={"apex": "transit"},
    )
    assert mixed.chart == "mixed"
    validate_configuration_tree(mixed, mixed_canonical, "configurations[0]")


def test_configuration_integrity_does_not_invent_missing_config_provenance() -> None:
    configuration, canonical = _synthetic_configuration(ConfigurationType.GRAND_TRINE)
    variant = configuration.model_copy(
        update={
            "category": ConfigurationCategory.MODERATE,
            "element": "water",
            "modality": "mutable",
        }
    )

    validate_configuration_tree(variant, canonical, "configurations[0]")


def test_configuration_integrity_accepts_nested_t_square_in_grand_cross() -> None:
    parent, nested, canonical = _grand_cross_with_nested_t_square()

    validate_configuration_tree(parent, canonical, "configurations[0]")

    assert nested.type is ConfigurationType.T_SQUARE


def test_configuration_integrity_rejects_contains_on_non_grand_cross() -> None:
    parent, canonical = _synthetic_configuration(ConfigurationType.T_SQUARE)
    corrupted = parent.model_copy(update={"contains": (parent,)})

    with pytest.raises(ValueError, match="only valid for grand_cross"):
        validate_configuration_tree(corrupted, canonical, "configurations[0]")


def test_configuration_integrity_rejects_wrong_nested_type() -> None:
    parent, nested, canonical = _grand_cross_with_nested_t_square()
    wrong = nested.model_copy(update={"type": ConfigurationType.YOD})
    corrupted = parent.model_copy(update={"contains": (wrong,)})

    with pytest.raises(ValueError, match=r"contains\[0\]\.type"):
        validate_configuration_tree(corrupted, canonical, "configurations[0]")


def test_configuration_integrity_rejects_nested_participant_outside_parent() -> None:
    parent, _, canonical = _grand_cross_with_nested_t_square()
    outside, outside_edges = _synthetic_configuration(
        ConfigurationType.T_SQUARE,
        body_prefix="outside_",
    )
    corrupted = parent.model_copy(update={"contains": (outside,)})

    with pytest.raises(ValueError, match="strict subset"):
        validate_configuration_tree(
            corrupted,
            (*canonical, *outside_edges),
            "configurations[0]",
        )


def test_configuration_integrity_rejects_nested_edge_not_materialized_by_parent() -> None:
    parent, nested, canonical = _grand_cross_with_nested_t_square()
    changed_edge = nested.aspects[0].model_copy(update={"orb": 0.25})
    changed_edges = (changed_edge, *nested.aspects[1:])
    changed_nested = nested.model_copy(
        update={
            "aspects": changed_edges,
            "max_orb": max(aspect.orb for aspect in changed_edges),
        }
    )
    corrupted = parent.model_copy(update={"contains": (changed_nested,)})

    with pytest.raises(
        ValueError,
        match=r"contains\[0\]\.aspects\[0\].*configurations\[0\]\.aspects",
    ):
        validate_configuration_tree(
            corrupted,
            (*canonical, changed_edge),
            "configurations[0]",
        )


def test_configuration_integrity_rejects_second_contains_level() -> None:
    parent, nested, canonical = _grand_cross_with_nested_t_square()
    recursive_nested = nested.model_copy(update={"contains": (nested,)})
    corrupted = parent.model_copy(update={"contains": (recursive_nested,)})

    with pytest.raises(ValueError, match=r"contains\[0\]\.contains"):
        validate_configuration_tree(corrupted, canonical, "configurations[0]")


@settings(max_examples=30, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(aspects=_aspect_lists())
def test_property_configuration_count_does_not_grow_when_threshold_decreases(
    aspects: list[Aspect],
) -> None:
    high = find_configurations(
        aspects,
        ConfigurationConfig(configuration_max_orb=7.0, include_nested=True, points=None),
    )
    low = find_configurations(
        aspects,
        ConfigurationConfig(configuration_max_orb=3.0, include_nested=True, points=None),
    )

    assert len(low) <= len(high)


@settings(max_examples=30, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(aspects=_aspect_lists())
def test_property_configuration_edge_counts_match_definitions(aspects: list[Aspect]) -> None:
    configurations = find_configurations(
        aspects,
        ConfigurationConfig(configuration_max_orb=7.0, include_nested=True, points=None),
    )

    assert all(len(configuration.aspects) == EXPECTED_EDGE_COUNTS[configuration.type] for configuration in configurations)


@settings(max_examples=30, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(aspects=_aspect_lists())
def test_property_configuration_edges_are_present_in_input(aspects: list[Aspect]) -> None:
    configurations = find_configurations(
        aspects,
        ConfigurationConfig(configuration_max_orb=7.0, include_nested=True, points=None),
    )
    input_edges = {_aspect_edge_key(aspect) for aspect in aspects}

    for configuration in configurations:
        assert {_aspect_edge_key(aspect) for aspect in configuration.aspects}.issubset(input_edges)


def _reference_chart():
    return calculate_natal(
        datetime(1985, 9, 1, 20, 45, tzinfo=timezone.utc),
        REFERENCE["latitude"],
        REFERENCE["longitude"],
        chart_kind="natal",
        house_system=REFERENCE["house_system"],
        aspect_config=AspectConfig.natal(max_orb=7.0),
        configuration_config=ConfigurationConfig(configuration_max_orb=7.0),
    )


def _named_aspect(
    left: str,
    right: str,
    aspect_type: AspectType,
    *,
    orb: float = 1.0,
    left_chart: str = "natal",
    right_chart: str = "natal",
) -> Aspect:
    return Aspect(
        from_point=AspectPointRef(chart=left_chart, body=left),
        to_point=AspectPointRef(chart=right_chart, body=right),
        aspect_type=aspect_type,
        exact_angle=ASPECT_ANGLES[aspect_type],
        orb=orb,
        category=AspectCategory.EXACT,
        applying=None,
    )


_SYNTHETIC_ROLE_EDGES = {
    ConfigurationType.T_SQUARE: (
        ("base_1", "base_2", AspectType.OPPOSITION),
        ("apex", "base_1", AspectType.SQUARE),
        ("apex", "base_2", AspectType.SQUARE),
    ),
    ConfigurationType.YOD: (
        ("base_1", "base_2", AspectType.SEXTILE),
        ("apex", "base_1", AspectType.QUINCUNX),
        ("apex", "base_2", AspectType.QUINCUNX),
    ),
    ConfigurationType.BISEXTILE: (
        ("center", "wing_1", AspectType.SEXTILE),
        ("center", "wing_2", AspectType.SEXTILE),
        ("wing_1", "wing_2", AspectType.TRINE),
    ),
    ConfigurationType.GRAND_TRINE: (
        ("point_1", "point_2", AspectType.TRINE),
        ("point_1", "point_3", AspectType.TRINE),
        ("point_2", "point_3", AspectType.TRINE),
    ),
    ConfigurationType.GRAND_CROSS: (
        ("axis_1_a", "axis_1_b", AspectType.OPPOSITION),
        ("axis_2_a", "axis_2_b", AspectType.OPPOSITION),
        ("axis_1_a", "axis_2_a", AspectType.SQUARE),
        ("axis_1_a", "axis_2_b", AspectType.SQUARE),
        ("axis_1_b", "axis_2_a", AspectType.SQUARE),
        ("axis_1_b", "axis_2_b", AspectType.SQUARE),
    ),
    ConfigurationType.TRAPEZE: (
        ("opposition_1", "opposition_2", AspectType.OPPOSITION),
        ("opposition_1", "base_1", AspectType.TRINE),
        ("opposition_2", "base_2", AspectType.TRINE),
        ("opposition_1", "base_2", AspectType.SEXTILE),
        ("opposition_2", "base_1", AspectType.SEXTILE),
        ("base_1", "base_2", AspectType.SEXTILE),
    ),
}


def _synthetic_configuration(
    configuration_type: ConfigurationType,
    *,
    chart_by_role: dict[str, str] | None = None,
    body_prefix: str = "p",
) -> tuple[Configuration, tuple[Aspect, ...]]:
    role_names = tuple(
        dict.fromkeys(
            role
            for left, right, _ in _SYNTHETIC_ROLE_EDGES[configuration_type]
            for role in (left, right)
        )
    )
    chart_by_role = chart_by_role or {}
    points = {
        role: AspectPointRef(
            chart=chart_by_role.get(role, "natal"),
            body=f"{body_prefix}{index}",
        )
        for index, role in enumerate(role_names)
    }
    aspects = tuple(
        _named_aspect(
            points[left].body,
            points[right].body,
            aspect_type,
            orb=float(index),
            left_chart=points[left].chart,
            right_chart=points[right].chart,
        )
        for index, (left, right, aspect_type) in enumerate(
            _SYNTHETIC_ROLE_EDGES[configuration_type],
            start=1,
        )
    )
    charts = {point.chart for point in points.values()}
    configuration = Configuration(
        type=configuration_type,
        points=points,
        aspects=aspects,
        max_orb=max(aspect.orb for aspect in aspects),
        category=ConfigurationCategory.LOOSE,
        chart=charts.pop() if len(charts) == 1 else "mixed",
    )
    return configuration, aspects


def _grand_cross_with_nested_t_square(
) -> tuple[Configuration, Configuration, tuple[Aspect, ...]]:
    parent, canonical = _synthetic_configuration(ConfigurationType.GRAND_CROSS)
    points = parent.points
    nested_points = {
        "apex": points["axis_2_a"],
        "base_1": points["axis_1_a"],
        "base_2": points["axis_1_b"],
    }
    wanted_pairs = {
        frozenset(
            (
                (nested_points["base_1"].chart, nested_points["base_1"].body),
                (nested_points["base_2"].chart, nested_points["base_2"].body),
            )
        ),
        frozenset(
            (
                (nested_points["apex"].chart, nested_points["apex"].body),
                (nested_points["base_1"].chart, nested_points["base_1"].body),
            )
        ),
        frozenset(
            (
                (nested_points["apex"].chart, nested_points["apex"].body),
                (nested_points["base_2"].chart, nested_points["base_2"].body),
            )
        ),
    }
    nested_aspects = tuple(
        aspect
        for aspect in canonical
        if frozenset(
            (
                (aspect.from_point.chart, aspect.from_point.body),
                (aspect.to_point.chart, aspect.to_point.body),
            )
        )
        in wanted_pairs
    )
    nested = Configuration(
        type=ConfigurationType.T_SQUARE,
        points=nested_points,
        aspects=nested_aspects,
        max_orb=max(aspect.orb for aspect in nested_aspects),
        category=ConfigurationCategory.LOOSE,
        chart="natal",
    )
    return parent.model_copy(update={"contains": (nested,)}), nested, canonical


def _configuration_key(configuration) -> tuple[str, frozenset[str]]:
    return _config_key(
        configuration.type.value,
        {point.body for point in configuration.points.values()},
    )


def _aspect_edge_key(aspect: Aspect) -> tuple[frozenset[tuple[str, str]], str]:
    return (
        frozenset(
            (
                (aspect.from_point.chart, aspect.from_point.body),
                (aspect.to_point.chart, aspect.to_point.body),
            )
        ),
        aspect.aspect_type.value,
    )
