"""Reference and behavior checks for transit-to-natal aspect calculation.

The primary reference is a special case: transits calculated *at the exact
natal moment*. At that instant every transiting classical-planet longitude
is numerically identical to its natal counterpart, so the resulting
transit-to-natal aspect grid must reduce to the already-verified natal
aspect grid in ``tests/test_aspects.py`` (see ``EXPECTED_ASPECTS`` there),
restricted to the ten bodies exact-orb treats as "transiting"
(``TRANSIT_BODY_IDS``: Sun through Pluto, no Chiron/nodes/Lilith/Selena/
Vertex/angles as *sources*; ``true_node`` is the sole lunar-node-axis target).

This gives us an externally-verified oracle for ``calculate_transit`` without
needing a second geocult.ru date: every orb below was cross-checked against
the natal reference table for
``1985-09-01 20:45 UTC, Moscow (55.7522N, 37.6155E), Placidus``
https://geocult.ru/natalnaya-karta-onlayn-raschet?fd=2&fm=9&fy=1985&fh=0&fmn=45&ttz=4&lt=55.7522&ln=37.6155&hs=P&sb=1

A few of geocult's natal aspects exceed exact-orb's transit orb ceiling
(``_default_transit_orbs`` hard-caps every transit aspect at 6 degrees,
tighter still for fictitious points), so they are intentionally absent from
``calculate_transit`` output; ``test_self_transit_orb_hard_capped_below_six_degrees``
documents that on purpose instead of leaving it as an unexplained gap.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import inspect
import json

import pytest
from pydantic import ValidationError
import swisseph as swe

from exact_orb.birth.types import BirthTimeDomain, UtcMinuteRange
from exact_orb.config import configure_ephemeris
from exact_orb.engine.aspects import AspectConfig
from exact_orb.engine.charts import transit as transit_calc
from exact_orb.engine.charts.transit import (
    RelativeExactWindow,
    TransitChart,
    TransitDateRange,
    TransitLocation,
    calculate_transit,
)
from exact_orb.engine.charts.natal import NatalChart, calculate_natal
from exact_orb.engine.ephemeris.runtime import ephemeris_session
from exact_orb.engine.ephemeris.types import DEFAULT_EPHEMERIS_FLAGS
from tests.conftest import REPO_ROOT
from tests.fixtures.natal_1985 import REFERENCE


TRANSIT_BODIES = (
    "sun",
    "moon",
    "mercury",
    "venus",
    "mars",
    "jupiter",
    "saturn",
    "uranus",
    "neptune",
    "pluto",
)

# baseline: canonical point identifiers from ADR-0029 + vendored ephe/*.se1;
# recalculate only for an intentional transit-result contract or ephemeris update.
TRANSIT_RANGE_BASELINE_SHA256 = "b6a5ab95f915ca46abd24b7896b4d7fa007091b790deb87900a4e8ced6543df0"


def _key(transit_body: str, aspect_type: str, natal_target: str) -> tuple[str, str, str]:
    return transit_body, aspect_type, natal_target


def _transit_payload_digest(chart: TransitChart) -> str:
    payload = chart.model_dump(mode="json")
    payload["ephemeris"].pop("path", None)
    payload["ephemeris"].pop("source", None)
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return sha256(canonical).hexdigest()


# (transit_body, aspect_type, natal_target) -> orb, restricted to the 10
# classical bodies exact-orb transits and rounded to geocult's 2 decimals.
EXPECTED_TRANSIT_ASPECTS: dict[tuple[str, str, str], float] = {
    _key("sun", "quincunx", "moon"): 1.68,
    _key("sun", "quincunx", "jupiter"): 0.66,
    _key("sun", "square", "uranus"): 4.66,
    _key("sun", "square", "chiron"): 5.10,
    _key("sun", "trine", "mean_apog"): 0.86,
    _key("sun", "trine", "true_node"): 1.57,
    _key("sun", "square", "pars_fortune"): 0.44,
    _key("sun", "sextile", "asc"): 1.24,

    _key("moon", "quincunx", "sun"): 1.68,
    _key("moon", "trine", "venus"): 2.07,
    _key("moon", "sextile", "jupiter"): 1.02,
    _key("moon", "quincunx", "pluto"): 5.01,
    _key("moon", "square", "asc"): 0.44,

    _key("mercury", "conjunction", "mars"): 2.70,
    _key("mercury", "square", "saturn"): 0.52,
    _key("mercury", "square", "vertex"): 2.36,
    _key("mercury", "opposition", "mc"): 5.53,

    _key("venus", "trine", "moon"): 2.07,
    _key("venus", "opposition", "jupiter"): 3.08,
    _key("venus", "quincunx", "neptune"): 4.72,
    _key("venus", "square", "pluto"): 2.94,

    _key("mars", "conjunction", "mercury"): 2.70,
    _key("mars", "square", "saturn"): 2.18,
    _key("mars", "opposition", "mc"): 2.84,

    _key("jupiter", "quincunx", "sun"): 0.66,
    _key("jupiter", "sextile", "moon"): 1.02,
    _key("jupiter", "opposition", "venus"): 3.08,
    _key("jupiter", "sextile", "uranus"): 5.32,
    _key("jupiter", "trine", "chiron"): 5.76,
    _key("jupiter", "square", "mean_apog"): 1.52,
    _key("jupiter", "square", "true_node"): 2.23,
    _key("jupiter", "sextile", "pars_fortune"): 1.10,
    _key("jupiter", "quincunx", "asc"): 0.58,

    _key("saturn", "square", "mercury"): 0.52,
    _key("saturn", "square", "mars"): 2.18,
    _key("saturn", "conjunction", "vertex"): 2.88,
    _key("saturn", "square", "mc"): 5.01,

    _key("uranus", "square", "sun"): 4.66,
    _key("uranus", "sextile", "jupiter"): 5.32,
    _key("uranus", "opposition", "chiron"): 0.44,
    _key("uranus", "quincunx", "asc"): 5.90,

    _key("neptune", "quincunx", "venus"): 4.72,
    _key("neptune", "sextile", "pluto"): 1.78,
    _key("neptune", "sextile", "mc"): 3.25,

    _key("pluto", "quincunx", "moon"): 5.01,
    _key("pluto", "square", "venus"): 2.94,
    _key("pluto", "sextile", "neptune"): 1.78,
    _key("pluto", "trine", "asc"): 5.45,
    _key("pluto", "trine", "mc"): 5.03,
}

# Real natal aspects (see tests/test_aspects.py::EXPECTED_ASPECTS) whose orb
# exceeds the 6-degree transit ceiling baked into
# exact_orb.engine.aspects.types._default_transit_orbs. calculate_transit must not
# report these, regardless of the max_orb argument passed in, because the
# per-aspect/per-body caps in that default table are independent of the
# top-level ceiling.
EXCLUDED_BY_TRANSIT_ORB_CAP = (
    ("sun", "sextile", "pluto", 6.68),
    ("pluto", "sextile", "sun", 6.68),
    ("moon", "trine", "uranus", 6.34),
    ("uranus", "trine", "moon", 6.34),
    ("moon", "square", "neptune", 6.79),
    ("neptune", "square", "moon", 6.79),
    ("moon", "sextile", "chiron", 6.78),
)


@pytest.fixture(scope="module")
def natal_chart() -> NatalChart:
    # Module-scoped: it is built before the function-scoped autouse fixture, so
    # it configures the same repo ephemeris explicitly instead of relying on cwd.
    configure_ephemeris(REPO_ROOT / "ephe")
    return calculate_natal(
        REFERENCE["datetime_utc"],
        REFERENCE["latitude"],
        REFERENCE["longitude"],
        chart_kind="natal",
        house_system=REFERENCE["house_system"],
        aspect_config=AspectConfig.natal(max_orb=7.0),
    )


@pytest.fixture(scope="module")
def self_transit(natal_chart: NatalChart) -> TransitChart:
    """Transits calculated at the exact natal moment (see module docstring)."""

    return calculate_transit(
        natal_chart,
        REFERENCE["datetime_utc"],
        max_orb=10.0,
        exact_window=RelativeExactWindow(months=0),
    )


def _lookup(chart: TransitChart) -> dict[tuple[str, str, str], float]:
    return {
        (aspect.from_point.body, aspect.aspect.value, aspect.to.body): aspect.orb
        for aspect in chart.aspects
    }


@pytest.mark.parametrize(
    ("transit_body", "aspect_type", "natal_target", "expected_orb"),
    [(*key, orb) for key, orb in EXPECTED_TRANSIT_ASPECTS.items()],
    ids=[f"{t}-{a}-{n}" for t, a, n in EXPECTED_TRANSIT_ASPECTS],
)
def test_self_transit_matches_geocult_reference(
    self_transit: TransitChart,
    transit_body: str,
    aspect_type: str,
    natal_target: str,
    expected_orb: float,
) -> None:
    lookup = _lookup(self_transit)
    key = (transit_body, aspect_type, natal_target)

    assert key in lookup, f"expected aspect {key} missing from calculate_transit() output"
    assert round(lookup[key], 2) == expected_orb


def test_self_transit_orb_hard_capped_below_six_degrees(
    self_transit: TransitChart,
    natal_chart: NatalChart,
) -> None:
    lookup = _lookup(self_transit)

    for from_body, aspect_type, to_body, natal_orb in EXCLUDED_BY_TRANSIT_ORB_CAP:
        # Sanity check the premise: the aspect genuinely exists in the natal
        # chart at (about) this orb, it is just outside what a transit is
        # allowed to report.
        assert any(
            {aspect.from_point.body, aspect.to_point.body} == {from_body, to_body}
            and aspect.aspect_type.value == aspect_type
            and round(aspect.orb, 2) == natal_orb
            for aspect in natal_chart.aspects
        ), f"fixture assumption broken: natal {from_body}-{to_body} {aspect_type} not found"

        assert (from_body, aspect_type, to_body) not in lookup


def test_transiting_body_is_conjunct_its_own_natal_position(
    self_transit: TransitChart,
) -> None:
    lookup = _lookup(self_transit)

    for body in TRANSIT_BODIES:
        key = (body, "conjunction", body)
        assert key in lookup
        assert lookup[key] == pytest.approx(0.0, abs=1e-6)
        self_conjunction = next(
            aspect
            for aspect in self_transit.aspects
            if aspect.from_point.body == body
            and aspect.aspect.value == "conjunction"
            and aspect.to.body == body
        )
        assert self_conjunction.exact_dates == (REFERENCE["datetime_utc"],)


def test_transits_use_true_node_as_the_only_lunar_node_axis_target(
    self_transit: TransitChart,
) -> None:
    targets = {aspect.to.body for aspect in self_transit.aspects}

    assert "true_node" in targets
    assert "south_node" not in targets


def test_station_aspects_use_the_same_filtered_natal_points(
    natal_chart: NatalChart,
) -> None:
    natal_points = transit_calc._natal_points(natal_chart)
    station_aspects = transit_calc._station_aspects(
        natal_chart.bodies["sun"].longitude,
        natal_points,
        1.0,
    )

    assert "south_node" not in natal_points
    assert any(
        aspect.to.body == "sun" and aspect.aspect.value == "conjunction"
        for aspect in station_aspects
    )
    assert all(aspect.to.body != "south_node" for aspect in station_aspects)


def test_transit_chart_rejects_south_node_aspect_target(
    self_transit: TransitChart,
) -> None:
    payload = self_transit.model_dump(mode="python")
    payload["aspects"][0]["to"]["body"] = "south_node"

    with pytest.raises(ValidationError, match=r"aspects\[0\]\.to"):
        TransitChart.model_validate(payload)


def test_transit_chart_rejects_south_node_station_target(
    self_transit: TransitChart,
) -> None:
    payload = self_transit.model_dump(mode="python")
    position = next(iter(payload["positions"].values()))
    payload["stations"] = [
        {
            "chart": "transit",
            "body": "jupiter",
            "type": "retrograde",
            "datetime_utc": payload["moment_utc"],
            "longitude": position["longitude"],
            "longitude_speed": -0.1,
            "zodiac": position["zodiac"],
            "natal_aspects": [
                {
                    "to": {"chart": "natal", "body": "south_node"},
                    "aspect": "conjunction",
                    "aspect_angle": 0.0,
                    "orb": 0.0,
                }
            ],
        }
    ]

    with pytest.raises(ValidationError, match=r"stations\[0\]\.natal_aspects\[0\]\.to"):
        TransitChart.model_validate(payload)


def test_calculate_transit_requires_natal_houses() -> None:
    bare_natal = calculate_natal(
        REFERENCE["datetime_utc"],
        REFERENCE["latitude"],
        REFERENCE["longitude"],
        chart_kind="cosmogram",
        house_system=REFERENCE["house_system"],
        include={"positions"},
        birth_time_domain=BirthTimeDomain(
            ranges=(
                UtcMinuteRange(
                    first_utc=REFERENCE["datetime_utc"].replace(
                        hour=0,
                        minute=0,
                        second=0,
                        microsecond=0,
                    ),
                    count=1440,
                ),
            )
        ),
    )

    with pytest.raises(ValueError, match="natal chart must include houses"):
        calculate_transit(bare_natal, REFERENCE["datetime_utc"])


def test_calculate_transit_rejects_negative_max_orb(natal_chart: NatalChart) -> None:
    with pytest.raises(ValueError, match="max_orb must be non-negative"):
        calculate_transit(natal_chart, REFERENCE["datetime_utc"], max_orb=-1.0)


def test_calculate_transit_rejects_negative_station_aspect_orb(natal_chart: NatalChart) -> None:
    with pytest.raises(ValueError, match="station_aspect_orb must be non-negative"):
        calculate_transit(natal_chart, REFERENCE["datetime_utc"], station_aspect_orb=-1.0)


def test_relative_exact_window_rejects_negative_months() -> None:
    with pytest.raises(ValidationError):
        RelativeExactWindow(months=-1)


def test_calculate_transit_with_location_returns_transit_houses(natal_chart: NatalChart) -> None:
    chart = calculate_transit(
        natal_chart,
        REFERENCE["datetime_utc"],
        location=(REFERENCE["latitude"], REFERENCE["longitude"], REFERENCE["house_system"]),
        max_orb=10.0,
        exact_window=RelativeExactWindow(months=0),
    )

    assert chart.houses is not None
    assert len(chart.houses) == 12
    assert chart.angles is not None
    assert "asc" in chart.angles


def test_calculate_transit_without_location_omits_transit_houses(natal_chart: NatalChart) -> None:
    chart = calculate_transit(
        natal_chart,
        REFERENCE["datetime_utc"],
        max_orb=10.0,
        exact_window=RelativeExactWindow(months=0),
    )

    assert chart.houses is None
    assert chart.angles is None


# --------------------------------------------------------------------------
# Exact-date / closest-approach root finding: self-consistency check.
#
# There is no independent external reference for exact transit dates, so
# this test checks the root finder against itself: any date it reports as
# "exact" must reproduce (close to) a zero orb when the aspect is
# recomputed at that date, and the reported closest approach must land
# inside the search window and be at least as tight as the orb we already
# know is active at the natal moment.
# --------------------------------------------------------------------------


def test_exact_dates_and_closest_approach_are_self_consistent(natal_chart: NatalChart) -> None:
    chart = calculate_transit(
        natal_chart,
        REFERENCE["datetime_utc"],
        body_ids={"sun": swe.SUN},  # keep the scan to one fast body
        station_body_ids={"jupiter": swe.JUPITER},  # keep the station scan cheap
        max_orb=1.0,
        exact_window=RelativeExactWindow(months=1),
    )

    sun_pars = next(
        aspect
        for aspect in chart.aspects
        if aspect.from_point.body == "sun" and aspect.to.body == "pars_fortune"
    )

    assert sun_pars.aspect.value == "square"
    assert round(sun_pars.orb, 2) == 0.44

    natal_pars_longitude = natal_chart.bodies["pars_fortune"].longitude

    # Every returned "exact" date should reproduce a near-zero orb when the
    # aspect is independently recomputed at that instant.
    with ephemeris_session():
        for exact_date in sun_pars.exact_dates:
            longitude, _, _, _ = transit_calc._body_longitude_speed(
                exact_date,
                swe.SUN,
                natal_chart.ephemeris_flags,
            )
            recomputed_orb = transit_calc._aspect_orb(
                longitude,
                natal_pars_longitude,
                sun_pars.aspect_angle,
            )
            assert recomputed_orb < 1e-3

    # The Sun moves about 1 degree/day and the orb is already 0.44 degrees
    # at the window's midpoint, so an exact hit and a very tight closest
    # approach are both expected well inside a one-month window.
    assert len(sun_pars.exact_dates) >= 1
    assert sun_pars.closest_approach.orb < 0.2
    window_start = transit_calc._add_months(REFERENCE["datetime_utc"], -1)
    window_end = transit_calc._add_months(REFERENCE["datetime_utc"], 1)
    assert window_start <= sun_pars.closest_approach.datetime_utc <= window_end


# --------------------------------------------------------------------------
# Pure-function unit tests: no Swiss Ephemeris calls, so these are fast and
# pin down the arithmetic that the root finder above depends on.
# --------------------------------------------------------------------------


def test_add_months_clamps_day_to_shorter_month() -> None:
    result = transit_calc._add_months(datetime(2024, 1, 31, tzinfo=timezone.utc), 1)
    assert result == datetime(2024, 2, 29, tzinfo=timezone.utc)


def test_add_months_handles_year_rollover() -> None:
    result = transit_calc._add_months(datetime(1985, 12, 15, tzinfo=timezone.utc), 2)
    assert result == datetime(1986, 2, 15, tzinfo=timezone.utc)


def test_add_months_handles_negative_months() -> None:
    result = transit_calc._add_months(datetime(1985, 1, 15, tzinfo=timezone.utc), -1)
    assert result == datetime(1984, 12, 15, tzinfo=timezone.utc)


def test_aspect_targets_conjunction_returns_single_target() -> None:
    assert transit_calc._aspect_targets(100.0, 0.0) == (100.0,)


def test_aspect_targets_opposition_returns_single_target() -> None:
    assert transit_calc._aspect_targets(100.0, 180.0) == (280.0,)


def test_aspect_targets_other_aspects_return_two_symmetric_targets() -> None:
    assert set(transit_calc._aspect_targets(100.0, 90.0)) == {190.0, 10.0}


def test_signed_delta_wraps_across_zero_aries() -> None:
    assert transit_calc._signed_delta(1.0, 359.0) == pytest.approx(2.0)
    assert transit_calc._signed_delta(359.0, 1.0) == pytest.approx(-2.0)


def test_aspect_orb_picks_the_nearer_of_two_symmetric_targets() -> None:
    assert transit_calc._aspect_orb(190.5, 100.0, 90.0) == pytest.approx(0.5)
    assert transit_calc._aspect_orb(9.5, 100.0, 90.0) == pytest.approx(0.5)


def test_has_root_between_detects_sign_change() -> None:
    assert transit_calc._has_root_between(-1.0, 1.0) is True
    assert transit_calc._has_root_between(1.0, 2.0) is False


def test_has_root_between_treats_near_zero_as_root() -> None:
    assert transit_calc._has_root_between(1e-8, 5.0) is True


def test_has_root_between_ignores_circle_wraparound() -> None:
    # A same-magnitude jump larger than 180 degrees is the angle wrapping
    # around the circle, not a genuine sign change/root.
    assert transit_calc._has_root_between(170.0, -170.0) is False


def test_normalize_location_accepts_lat_lon_tuple() -> None:
    location = transit_calc._normalize_location((55.75, 37.62))
    assert location.latitude == 55.75
    assert location.longitude == 37.62
    assert location.house_system == "P"


def test_normalize_location_accepts_lat_lon_house_system_tuple() -> None:
    location = transit_calc._normalize_location((55.75, 37.62, "K"))
    assert location.house_system == "K"


def test_normalize_location_accepts_mapping() -> None:
    location = transit_calc._normalize_location({"latitude": 1.0, "longitude": 2.0})
    assert location == TransitLocation(latitude=1.0, longitude=2.0)


def test_normalize_location_rejects_wrong_length_sequence() -> None:
    with pytest.raises(ValueError, match="location must be"):
        transit_calc._normalize_location((1.0,))


def test_normalize_exact_window_uses_independent_moment_and_explicit_range() -> None:
    moment_input = datetime(1985, 8, 1, tzinfo=timezone.utc)
    start = datetime(1985, 9, 1, tzinfo=timezone.utc)
    end = datetime(1985, 10, 1, tzinfo=timezone.utc)

    moment, window_start, window_end = transit_calc._normalize_exact_window(
        moment_input,
        TransitDateRange(start=start, end=end),
    )

    assert moment == moment_input
    assert window_start == start
    assert window_end == end


def test_normalize_exact_window_rejects_end_before_start() -> None:
    moment = datetime(1985, 9, 1, tzinfo=timezone.utc)
    start = datetime(1985, 9, 2, tzinfo=timezone.utc)
    end = datetime(1985, 9, 1, tzinfo=timezone.utc)

    with pytest.raises(ValueError, match="end must be after start"):
        transit_calc._normalize_exact_window(
            moment,
            TransitDateRange(start=start, end=end),
        )


def test_normalize_exact_window_rejects_zero_length_explicit_range() -> None:
    moment = REFERENCE["datetime_utc"]
    exact_window = TransitDateRange(start=moment, end=moment)

    with pytest.raises(ValueError, match="end must be after start"):
        transit_calc._normalize_exact_window(moment, exact_window)


def test_normalize_exact_window_builds_symmetric_relative_window() -> None:
    moment_input = datetime(1985, 9, 1, 20, 45, tzinfo=timezone.utc)

    moment, window_start, window_end = transit_calc._normalize_exact_window(
        moment_input,
        RelativeExactWindow(months=3),
    )

    assert moment == moment_input
    assert window_start == transit_calc._add_months(moment_input, -3)
    assert window_end == transit_calc._add_months(moment_input, 3)
    assert transit_calc._normalize_exact_window(
        moment_input,
        RelativeExactWindow(months=0),
    ) == (moment_input, moment_input, moment_input)


@pytest.mark.parametrize(
    "exact_window",
    [
        RelativeExactWindow(months=1),
        TransitDateRange(
            start=REFERENCE["datetime_utc"],
            end=REFERENCE["datetime_utc"] + timedelta(days=1),
        ),
    ],
)
def test_exact_window_variants_are_frozen(
    exact_window: RelativeExactWindow | TransitDateRange,
) -> None:
    with pytest.raises(ValidationError, match="frozen"):
        setattr(
            exact_window,
            "months" if isinstance(exact_window, RelativeExactWindow) else "start",
            2 if isinstance(exact_window, RelativeExactWindow) else REFERENCE["datetime_utc"],
        )


def test_migrated_explicit_range_matches_pre_refactor_baseline(natal_chart: NatalChart) -> None:
    start = REFERENCE["datetime_utc"]
    end = start + timedelta(days=2)

    chart = calculate_transit(
        natal_chart,
        start,
        exact_window=TransitDateRange(start=start, end=end),
        body_ids={"sun": swe.SUN},
        station_body_ids={"jupiter": swe.JUPITER},
        max_orb=1.0,
    )

    assert (len(chart.positions), len(chart.aspects), len(chart.stations)) == (1, 4, 0)
    assert _transit_payload_digest(chart) == TRANSIT_RANGE_BASELINE_SHA256


def test_absolute_exact_window_may_be_after_moment(natal_chart: NatalChart) -> None:
    moment = REFERENCE["datetime_utc"]
    start = moment + timedelta(days=10)
    end = start + timedelta(days=2)

    chart = calculate_transit(
        natal_chart,
        moment,
        exact_window=TransitDateRange(start=start, end=end),
        body_ids={"sun": swe.SUN},
        station_body_ids={"jupiter": swe.JUPITER},
        max_orb=1.0,
    )

    assert chart.moment_utc == moment
    assert (chart.window_start_utc, chart.window_end_utc) == (start, end)


@pytest.mark.parametrize(
    "moment",
    [
        (REFERENCE["datetime_utc"], REFERENCE["datetime_utc"] + timedelta(days=1)),
        TransitDateRange(
            start=REFERENCE["datetime_utc"],
            end=REFERENCE["datetime_utc"] + timedelta(days=1),
        ),
    ],
)
def test_calculate_transit_rejects_non_datetime_moment_before_session(
    moment: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected_session() -> None:
        raise AssertionError("ephemeris_session must not be acquired")

    monkeypatch.setattr(transit_calc, "ephemeris_session", unexpected_session)

    with pytest.raises(TypeError, match="moment must be a datetime"):
        calculate_transit(None, moment)  # type: ignore[arg-type]


def test_calculate_transit_rejects_invalid_exact_window_before_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected_session() -> None:
        raise AssertionError("ephemeris_session must not be acquired")

    monkeypatch.setattr(transit_calc, "ephemeris_session", unexpected_session)

    with pytest.raises(
        TypeError,
        match="exact_window must be a RelativeExactWindow or TransitDateRange",
    ):
        calculate_transit(
            None,  # type: ignore[arg-type]
            REFERENCE["datetime_utc"],
            exact_window=object(),  # type: ignore[arg-type]
        )


def test_calculate_transit_uses_shared_default_ephemeris_flags() -> None:
    default = inspect.signature(calculate_transit).parameters["ephemeris_flags"].default

    assert default == DEFAULT_EPHEMERIS_FLAGS
