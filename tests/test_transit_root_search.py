"""Regression tests for inclusive transit root and station searches."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta, timezone

import pytest
import swisseph as swe

from exact_orb.engine.charts import transit as transit_calc
from exact_orb.engine.ephemeris.runtime import ephemeris_session


BASE = datetime(2025, 2, 4, 9, 40, 23, tzinfo=timezone.utc)
TARGET_LONGITUDE = 100.0
FLAGS = swe.FLG_SWIEPH | swe.FLG_SPEED
MotionFunction = Callable[[float], float]


def _install_motion(
    monkeypatch: pytest.MonkeyPatch,
    *,
    longitude_delta: MotionFunction,
    speed: MotionFunction,
) -> None:
    """Replace the leaf ephemeris call with one deterministic motion curve."""

    def fake_body_longitude_speed(
        dt: datetime,
        body_id: int,
        flags: int,
    ) -> tuple[float, float, int, str]:
        _ = body_id
        days = (dt - BASE).total_seconds() / 86_400.0
        return (TARGET_LONGITUDE + longitude_delta(days)) % 360.0, speed(days), flags, ""

    monkeypatch.setattr(transit_calc, "_body_longitude_speed", fake_body_longitude_speed)


def _exact_dates(start: datetime, end: datetime) -> tuple[datetime, ...]:
    return transit_calc._exact_dates_for_aspect(
        swe.JUPITER,
        TARGET_LONGITUDE,
        0.0,
        start,
        end,
        FLAGS,
        "jupiter",
    )


def test_exact_dates_preserves_ordinary_sign_changing_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_motion(
        monkeypatch,
        longitude_delta=lambda days: days,
        speed=lambda days: 1.0,
    )

    roots = _exact_dates(BASE - timedelta(days=1), BASE + timedelta(days=1))

    assert len(roots) == 1
    assert abs(roots[0] - BASE) <= timedelta(seconds=1)


def test_exact_dates_finds_tangency_without_sign_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_motion(
        monkeypatch,
        longitude_delta=lambda days: days**2,
        speed=lambda days: 2.0 * days,
    )
    start = BASE - timedelta(days=1)
    end = BASE + timedelta(days=1)

    roots = _exact_dates(start, end)
    closest = transit_calc._closest_approach_for_aspect(
        swe.JUPITER,
        TARGET_LONGITUDE,
        0.0,
        start,
        end,
        FLAGS,
        "jupiter",
    )

    assert len(roots) == 1
    assert abs(roots[0] - BASE) <= timedelta(seconds=1)
    assert abs(closest.datetime_utc - BASE) <= timedelta(seconds=1)
    assert closest.orb <= transit_calc.ROOT_TOLERANCE_DEGREES


def test_exact_dates_finds_both_crossings_around_station(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_motion(
        monkeypatch,
        longitude_delta=lambda days: days**2 - 0.25,
        speed=lambda days: 2.0 * days,
    )

    roots = _exact_dates(BASE - timedelta(days=1), BASE + timedelta(days=1))

    assert len(roots) == 2
    assert abs(roots[0] - (BASE - timedelta(hours=12))) <= timedelta(seconds=1)
    assert abs(roots[1] - (BASE + timedelta(hours=12))) <= timedelta(seconds=1)


def test_exact_date_dedupe_prefers_refined_tangent_over_coarse_sample(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tangent_offset_days = 30.0 / (24.0 * 60.0)
    tangent = BASE + timedelta(minutes=30)
    scale = 1e-4
    _install_motion(
        monkeypatch,
        longitude_delta=lambda days: scale * (days - tangent_offset_days) ** 2,
        speed=lambda days: 2.0 * scale * (days - tangent_offset_days),
    )

    roots = _exact_dates(BASE, BASE + timedelta(days=2))

    assert len(roots) == 1
    assert abs(roots[0] - tangent) <= timedelta(seconds=1)


def test_exact_dates_rejects_tangent_minimum_above_tolerance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    offset = 2.0 * transit_calc.ROOT_TOLERANCE_DEGREES
    _install_motion(
        monkeypatch,
        longitude_delta=lambda days: offset + days**2,
        speed=lambda days: 2.0 * days,
    )

    roots = _exact_dates(BASE - timedelta(days=1), BASE + timedelta(days=1))

    assert roots == ()


@pytest.mark.parametrize("root_at", ["start", "end"])
def test_exact_dates_includes_window_boundary(
    monkeypatch: pytest.MonkeyPatch,
    root_at: str,
) -> None:
    start = BASE
    end = BASE + timedelta(days=2)
    root = start if root_at == "start" else end
    root_days = (root - BASE).total_seconds() / 86_400.0
    _install_motion(
        monkeypatch,
        longitude_delta=lambda days: days - root_days,
        speed=lambda days: 1.0,
    )

    roots = _exact_dates(start, end)

    assert roots == (root,)


def test_exact_dates_refines_crossing_when_endpoint_is_within_tolerance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    endpoint_orb = 0.9 * transit_calc.ROOT_TOLERANCE_DEGREES
    root_offset_days = 30.0 / (24.0 * 60.0)
    motion_speed = -endpoint_orb / root_offset_days
    _install_motion(
        monkeypatch,
        longitude_delta=lambda days: endpoint_orb + motion_speed * days,
        speed=lambda days: motion_speed,
    )

    roots = _exact_dates(BASE, BASE + timedelta(days=2))

    assert len(roots) == 1
    assert roots[0] > BASE
    refined_orb = abs(motion_speed * (roots[0] - BASE).total_seconds() / 86_400.0 + endpoint_orb)
    assert refined_orb < endpoint_orb


@pytest.mark.parametrize(
    ("offset", "expected"),
    [
        (0.0, (BASE,)),
        (2.0 * transit_calc.ROOT_TOLERANCE_DEGREES, ()),
    ],
    ids=["exact", "not-exact"],
)
def test_exact_dates_checks_point_window_once(
    monkeypatch: pytest.MonkeyPatch,
    offset: float,
    expected: tuple[datetime, ...],
) -> None:
    calls: list[datetime] = []

    def fake_body_longitude_speed(
        dt: datetime,
        body_id: int,
        flags: int,
    ) -> tuple[float, float, int, str]:
        _ = body_id
        calls.append(dt)
        return TARGET_LONGITUDE + offset, 1.0, flags, ""

    monkeypatch.setattr(transit_calc, "_body_longitude_speed", fake_body_longitude_speed)

    assert _exact_dates(BASE, BASE) == expected
    assert calls == [BASE]


def test_exact_dates_ignores_angular_wraparound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_motion(
        monkeypatch,
        longitude_delta=lambda days: 180.0 + 10.0 * days,
        speed=lambda days: 10.0,
    )

    roots = _exact_dates(BASE - timedelta(days=1), BASE + timedelta(days=1))

    assert roots == ()


def test_exact_root_deduplication_keeps_best_candidate_in_transitive_cluster() -> None:
    candidates = [
        (BASE, 8e-8),
        (BASE + timedelta(minutes=50), 0.0),
        (BASE + timedelta(minutes=100), 5e-8),
        (BASE + timedelta(hours=3), 1e-8),
    ]

    assert transit_calc._dedupe_exact_candidates(candidates) == (
        BASE + timedelta(minutes=50),
        BASE + timedelta(hours=3),
    )


def test_station_deduplication_keeps_lowest_speed_candidate() -> None:
    candidates = [
        (BASE, -1.0, 1.0, 5e-9),
        (BASE + timedelta(minutes=30), -1.0, 1.0, 1e-9),
        (BASE + timedelta(hours=2), 1.0, -1.0, 2e-9),
    ]

    assert transit_calc._dedupe_station_dates(candidates) == (
        (BASE + timedelta(minutes=30), -1.0, 1.0),
        (BASE + timedelta(hours=2), 1.0, -1.0),
    )


def test_station_dates_checks_point_window_station(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    residual_speed = 0.5 * transit_calc.STATION_SPEED_TOLERANCE_DEGREES_PER_DAY
    _install_motion(
        monkeypatch,
        longitude_delta=lambda days: 0.5 * days**2 + residual_speed * days,
        speed=lambda days: days + residual_speed,
    )

    stations = transit_calc._station_dates(swe.JUPITER, BASE, BASE, FLAGS, "jupiter")

    assert len(stations) == 1
    station_dt, before_speed, after_speed = stations[0]
    assert station_dt == BASE
    assert before_speed < 0.0 < after_speed


def test_station_dates_rejects_nearby_station_outside_point_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root_offset_days = 3.0 / 24.0
    root = BASE + timedelta(hours=3)
    _install_motion(
        monkeypatch,
        longitude_delta=lambda days: 0.5 * (days - root_offset_days) ** 2,
        speed=lambda days: days - root_offset_days,
    )

    outside = transit_calc._station_dates(swe.JUPITER, BASE, BASE, FLAGS, "jupiter")
    positive_control = transit_calc._station_dates(swe.JUPITER, root, root, FLAGS, "jupiter")

    assert outside == ()
    assert len(positive_control) == 1
    assert positive_control[0][0] == root


def test_station_dates_rejects_near_zero_speed_without_reversal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    residual_speed = 0.5 * transit_calc.STATION_SPEED_TOLERANCE_DEGREES_PER_DAY
    _install_motion(
        monkeypatch,
        longitude_delta=lambda days: residual_speed * days,
        speed=lambda days: residual_speed,
    )

    stations = transit_calc._station_dates(swe.JUPITER, BASE, BASE, FLAGS, "jupiter")

    assert stations == ()


def test_station_dates_rejects_point_speed_above_tolerance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outside_speed = 2.0 * transit_calc.STATION_SPEED_TOLERANCE_DEGREES_PER_DAY
    _install_motion(
        monkeypatch,
        longitude_delta=lambda days: 0.5 * days**2 + outside_speed * days,
        speed=lambda days: days + outside_speed,
    )

    stations = transit_calc._station_dates(swe.JUPITER, BASE, BASE, FLAGS, "jupiter")

    assert stations == ()


@pytest.mark.parametrize(
    ("longitude_delta", "speed", "expected_type"),
    [
        (
            lambda days: 0.5 * days**2,
            lambda days: days,
            "direct",
        ),
        (
            lambda days: -0.5 * days**2,
            lambda days: -days,
            "retrograde",
        ),
    ],
    ids=["direct", "retrograde"],
)
def test_calculate_stations_classifies_both_directions(
    monkeypatch: pytest.MonkeyPatch,
    longitude_delta: MotionFunction,
    speed: MotionFunction,
    expected_type: str,
) -> None:
    _install_motion(monkeypatch, longitude_delta=longitude_delta, speed=speed)

    stations = transit_calc._calculate_stations(
        BASE,
        BASE,
        {"jupiter": swe.JUPITER},
        {},
        FLAGS,
        1.0,
    )

    assert len(stations) == 1
    assert stations[0].datetime_utc == BASE
    assert stations[0].type == expected_type


def test_station_dates_preserves_sign_change_and_direction_probes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root_offset_days = 0.25
    expected_root = BASE + timedelta(hours=6)
    _install_motion(
        monkeypatch,
        longitude_delta=lambda days: 0.5 * (days - root_offset_days) ** 2,
        speed=lambda days: days - root_offset_days,
    )

    stations = transit_calc._station_dates(
        swe.JUPITER,
        BASE - timedelta(days=1),
        BASE + timedelta(days=1),
        FLAGS,
        "jupiter",
    )

    assert len(stations) == 1
    station_dt, before_speed, after_speed = stations[0]
    assert abs(station_dt - expected_root) <= timedelta(seconds=1)
    assert before_speed < 0.0 < after_speed


def test_station_dates_bisects_when_both_endpoint_speeds_are_within_tolerance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scale = 1e-8
    _install_motion(
        monkeypatch,
        longitude_delta=lambda days: 0.5 * scale * (days - 0.5) ** 2,
        speed=lambda days: scale * (days - 0.5),
    )

    stations = transit_calc._station_dates(
        swe.JUPITER,
        BASE,
        BASE + timedelta(days=1),
        FLAGS,
        "jupiter",
    )

    assert len(stations) == 1
    assert abs(stations[0][0] - (BASE + timedelta(hours=12))) <= timedelta(seconds=1)


def test_inclusive_datetimes_handles_point_partial_step_and_exact_end() -> None:
    step = timedelta(hours=2)

    assert transit_calc._inclusive_datetimes(BASE, BASE, step) == (BASE,)
    assert transit_calc._inclusive_datetimes(BASE, BASE + timedelta(hours=5), step) == (
        BASE,
        BASE + timedelta(hours=2),
        BASE + timedelta(hours=4),
        BASE + timedelta(hours=5),
    )
    assert transit_calc._inclusive_datetimes(BASE, BASE + timedelta(hours=4), step) == (
        BASE,
        BASE + timedelta(hours=2),
        BASE + timedelta(hours=4),
    )
    assert transit_calc._inclusive_datetimes(BASE, BASE + timedelta(hours=1), step) == (
        BASE,
        BASE + timedelta(hours=1),
    )


def test_inclusive_datetimes_does_not_overflow_when_step_exceeds_max_range() -> None:
    end = datetime.max.replace(tzinfo=timezone.utc)
    start = end - timedelta(days=1)

    assert transit_calc._inclusive_datetimes(start, end, timedelta(days=2)) == (start, end)


def test_jupiter_station_tangent_is_an_exact_date() -> None:
    start = datetime(2025, 1, 30, 9, 40, 23, tzinfo=timezone.utc)
    end = datetime(2025, 2, 9, 9, 40, 23, tzinfo=timezone.utc)
    expected_station = datetime(2025, 2, 4, 9, 40, 23, tzinfo=timezone.utc)

    with ephemeris_session():
        station = transit_calc._bisect_speed_zero(
            swe.JUPITER,
            datetime(2025, 2, 3, tzinfo=timezone.utc),
            datetime(2025, 2, 5, tzinfo=timezone.utc),
            FLAGS,
        )
        station_longitude = transit_calc._body_longitude_speed(
            station,
            swe.JUPITER,
            FLAGS,
        )[0]
        left_delta = transit_calc._signed_delta_at(
            datetime(2025, 2, 3, 9, 40, 23, tzinfo=timezone.utc),
            swe.JUPITER,
            station_longitude,
            FLAGS,
        )
        right_delta = transit_calc._signed_delta_at(
            datetime(2025, 2, 5, 9, 40, 23, tzinfo=timezone.utc),
            swe.JUPITER,
            station_longitude,
            FLAGS,
        )
        longitude, speed, retflags, _ = transit_calc._body_longitude_speed(
            station,
            swe.JUPITER,
            FLAGS,
        )
        aspects = transit_calc._calculate_transit_aspects(
            station,
            {
                "jupiter": transit_calc.TransitBodyPosition(
                    name="jupiter",
                    swe_id=swe.JUPITER,
                    longitude=longitude,
                    longitude_speed=speed,
                    retrograde=speed < 0.0,
                    natal_house=1,
                    zodiac=transit_calc.zodiac_position(longitude),
                    retflags=retflags,
                )
            },
            {"jupiter": station_longitude},
            start,
            end,
            FLAGS,
            1.0,
        )
        assert len(aspects) == 1
        aspect = aspects[0]
        root_orbs = [
            abs(transit_calc._signed_delta_at(root, swe.JUPITER, station_longitude, FLAGS))
            for root in aspect.exact_dates
        ]

    assert abs(station - expected_station) <= timedelta(minutes=1)
    assert left_delta > 0.0 and right_delta > 0.0
    assert len(aspect.exact_dates) == 1
    assert abs(aspect.exact_dates[0] - station) <= timedelta(minutes=1)
    assert root_orbs[0] <= transit_calc.ROOT_TOLERANCE_DEGREES
    assert abs(aspect.closest_approach.datetime_utc - station) <= timedelta(minutes=1)
    assert aspect.closest_approach.orb <= transit_calc.ROOT_TOLERANCE_DEGREES


def test_saturn_bisection_result_is_detected_in_point_window() -> None:
    expected_station = datetime(2073, 3, 4, 4, 31, 14, tzinfo=timezone.utc)

    with ephemeris_session():
        station = transit_calc._bisect_speed_zero(
            swe.SATURN,
            datetime(2073, 3, 3, tzinfo=timezone.utc),
            datetime(2073, 3, 5, tzinfo=timezone.utc),
            FLAGS,
        )
        speed = transit_calc._body_longitude_speed(station, swe.SATURN, FLAGS)[1]
        before_speed = transit_calc._body_longitude_speed(
            station - timedelta(hours=6),
            swe.SATURN,
            FLAGS,
        )[1]
        after_speed = transit_calc._body_longitude_speed(
            station + timedelta(hours=6),
            swe.SATURN,
            FLAGS,
        )[1]
        stations = transit_calc._station_dates(
            swe.SATURN,
            station,
            station,
            FLAGS,
            "saturn",
        )

    assert abs(station - expected_station) <= timedelta(minutes=1)
    assert abs(speed) <= transit_calc.STATION_SPEED_TOLERANCE_DEGREES_PER_DAY
    assert before_speed * after_speed < 0.0
    assert len(stations) == 1
    assert stations[0][0] == station
