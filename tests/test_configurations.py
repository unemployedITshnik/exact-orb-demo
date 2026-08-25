"""Aspect configuration tests."""

from __future__ import annotations

from datetime import datetime, timezone

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from exact_orb.engine.aspects import (
    Aspect,
    AspectCategory,
    AspectConfig,
    AspectPointRef,
    AspectType,
)
from exact_orb.engine.aspects.types import ASPECT_ANGLES
from exact_orb.engine.configurations import (
    ConfigurationCategory,
    ConfigurationConfig,
    ConfigurationType,
    find_configurations,
)
from exact_orb.engine.charts.natal import calculate_natal
from tests.fixtures.natal_1985 import REFERENCE


def _config_key(config_type: str, participants: set[str]) -> tuple[str, frozenset[str]]:
    return config_type, frozenset(participants)


EXPECTED_CONFIGURATIONS: dict[tuple[str, frozenset[str]], float] = {
    _config_key("t_square", {"sun", "uranus", "chiron"}): 5.10,
    _config_key("t_square", {"jupiter", "lilith", "south_node"}): 2.23,
    _config_key("t_square", {"jupiter", "north_node", "south_node"}): 2.23,
    _config_key("yod", {"sun", "moon", "jupiter"}): 1.68,
    _config_key("yod", {"moon", "sun", "pluto"}): 6.68,
    _config_key("bisextile", {"moon", "jupiter", "chiron"}): 6.78,
    _config_key("bisextile", {"jupiter", "moon", "uranus"}): 6.34,
    _config_key("trapeze", {"moon", "jupiter", "uranus", "chiron"}): 6.78,
}

EXPECTED_CONFIGURATION_CATEGORIES = {
    _config_key("t_square", {"sun", "uranus", "chiron"}): ConfigurationCategory.LOOSE,
    _config_key("t_square", {"jupiter", "lilith", "south_node"}): ConfigurationCategory.TIGHT,
    _config_key("t_square", {"jupiter", "north_node", "south_node"}): ConfigurationCategory.TIGHT,
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

    assert categories.count(ConfigurationCategory.TIGHT) == 3
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
    assert by_key[_config_key("t_square", {"jupiter", "lilith", "south_node"})].points["apex"].body == "jupiter"
    assert by_key[_config_key("t_square", {"jupiter", "north_node", "south_node"})].points["apex"].body == "jupiter"


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

    strict = find_configurations(aspects, ConfigurationConfig(configuration_max_orb=2.0))
    relaxed = find_configurations(aspects, ConfigurationConfig(configuration_max_orb=2.3))

    assert {_configuration_key(item) for item in strict} == {
        _config_key("yod", {"sun", "moon", "jupiter"})
    }
    assert _config_key("t_square", {"jupiter", "lilith", "south_node"}) in {
        _configuration_key(item) for item in relaxed
    }
    assert _config_key("t_square", {"jupiter", "north_node", "south_node"}) in {
        _configuration_key(item) for item in relaxed
    }
    assert _config_key("yod", {"moon", "sun", "pluto"}) not in {
        _configuration_key(item) for item in strict
    }


def test_empty_configuration_input_returns_empty_list() -> None:
    assert find_configurations([], ConfigurationConfig()) == []


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
