"""Concurrency checks for the Swiss Ephemeris runtime session."""

from __future__ import annotations

from datetime import datetime, timezone
import threading
from typing import Any

import pytest
import swisseph as real_swe

from exact_orb import swiss_backend
from exact_orb.config import configure_ephemeris
from exact_orb.engine.charts import transit as transit_calc
from exact_orb.engine.charts.natal import NatalChart, calculate_natal
from exact_orb.engine.ephemeris.calc import julian_day_ut
from exact_orb.engine.ephemeris.runtime import ephemeris_session
from exact_orb.errors import EphemerisSessionRequiredError
from tests.conftest import REPO_ROOT
from tests.fixtures.natal_1985 import REFERENCE


TIMEOUT_SECONDS = 3.0


class FakeSwe:
    class Error(Exception):
        """Stand-in for swisseph.Error, which derives from Exception."""

    FLG_SWIEPH = real_swe.FLG_SWIEPH
    FLG_SPEED = real_swe.FLG_SPEED
    GREG_CAL = real_swe.GREG_CAL

    SUN = real_swe.SUN
    MOON = real_swe.MOON
    MERCURY = real_swe.MERCURY
    VENUS = real_swe.VENUS
    MARS = real_swe.MARS
    JUPITER = real_swe.JUPITER
    SATURN = real_swe.SATURN
    URANUS = real_swe.URANUS
    NEPTUNE = real_swe.NEPTUNE
    PLUTO = real_swe.PLUTO
    CHIRON = real_swe.CHIRON
    TRUE_NODE = real_swe.TRUE_NODE
    MEAN_APOG = real_swe.MEAN_APOG
    OSCU_APOG = real_swe.OSCU_APOG

    ASC = real_swe.ASC
    MC = real_swe.MC
    ARMC = real_swe.ARMC
    VERTEX = real_swe.VERTEX
    EQUASC = real_swe.EQUASC
    COASC1 = real_swe.COASC1
    COASC2 = real_swe.COASC2
    POLASC = real_swe.POLASC

    def __init__(self) -> None:
        self.active_calls = 0
        self.max_active_calls = 0
        self.calc_entries = 0
        self.raise_on_calc_entry: int | None = None
        self.block_first_calc = False
        self.first_calc_entered = threading.Event()
        self.second_calc_entry_reached = threading.Event()
        self.release_first_calc = threading.Event()
        self._lock = threading.Lock()

    def set_ephe_path(self, path: str) -> None:
        self.ephe_path = path

    def julday(self, year: int, month: int, day: int, hour: float, calendar: int) -> float:
        _ = calendar
        return year * 10000.0 + month * 100.0 + day + hour / 24.0

    def calc_ut(self, jd: float, body_id: int, flags: int) -> tuple[tuple[float, ...], int, str]:
        entry = self._enter_backend(calc_entry=True)
        try:
            if self.raise_on_calc_entry == entry:
                raise self.Error("fake Swiss Ephemeris failure")
            if self.block_first_calc and entry == 1:
                assert self.release_first_calc.wait(TIMEOUT_SECONDS), "timed out waiting to release fake calc_ut"
            longitude = (jd * 0.01 + body_id * 17.0) % 360.0
            speed = ((body_id % 9) - 4) / 10.0 or 0.1
            return (
                (longitude, 0.0, 1.0, speed, 0.0, 0.0),
                flags | self.FLG_SPEED,
                "",
            )
        finally:
            self._exit_backend()

    def houses_ex(
        self,
        jd: float,
        latitude: float,
        longitude: float,
        house_system: bytes,
        flags: int,
    ) -> tuple[tuple[float, ...], tuple[float, ...]]:
        self._enter_backend(calc_entry=False)
        try:
            _ = house_system, flags
            offset = (jd * 0.01 + latitude + longitude) % 30.0
            cusps = (0.0,) + tuple((offset + index * 30.0) % 360.0 for index in range(12))
            ascmc = tuple((offset + index * 45.0) % 360.0 for index in range(8))
            return cusps, ascmc
        finally:
            self._exit_backend()

    def _enter_backend(self, *, calc_entry: bool) -> int:
        with self._lock:
            if calc_entry:
                self.calc_entries += 1
                entry = self.calc_entries
                if entry == 1:
                    self.first_calc_entered.set()
                elif entry == 2:
                    self.second_calc_entry_reached.set()
            else:
                entry = 0
            self.active_calls += 1
            self.max_active_calls = max(self.max_active_calls, self.active_calls)
            return entry

    def _exit_backend(self) -> None:
        with self._lock:
            self.active_calls -= 1


def test_fake_swe_error_matches_real_exception_hierarchy() -> None:
    assert issubclass(real_swe.Error, Exception)
    assert not issubclass(real_swe.Error, RuntimeError)
    assert issubclass(FakeSwe.Error, Exception)
    assert not issubclass(FakeSwe.Error, RuntimeError)


def test_low_level_swe_call_requires_ephemeris_session() -> None:
    with pytest.raises(EphemerisSessionRequiredError):
        julian_day_ut(datetime(1985, 9, 1, 20, 45, tzinfo=timezone.utc))

    with ephemeris_session():
        assert julian_day_ut(datetime(1985, 9, 1, 20, 45, tzinfo=timezone.utc)) > 0


@pytest.mark.no_ephemeris_autoinit
def test_transit_body_longitude_speed_does_not_wrap_session_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(swiss_backend, "swe", FakeSwe())

    with pytest.raises(EphemerisSessionRequiredError):
        transit_calc._body_longitude_speed(
            datetime(1985, 9, 1, 20, 45, tzinfo=timezone.utc),
            real_swe.SUN,
            real_swe.FLG_SWIEPH,
        )


@pytest.mark.no_ephemeris_autoinit
def test_concurrent_calculations_do_not_enter_backend_simultaneously(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeSwe()
    fake.block_first_calc = True
    monkeypatch.setattr(swiss_backend, "swe", fake)
    configure_ephemeris(REPO_ROOT / "ephe")
    errors: list[BaseException] = []
    second_started = threading.Event()

    def calculate() -> None:
        try:
            _calculate_fake_natal()
        except BaseException as exc:
            errors.append(exc)

    def calculate_second() -> None:
        second_started.set()
        calculate()

    first = threading.Thread(target=calculate)
    second = threading.Thread(target=calculate_second)

    first.start()
    assert fake.first_calc_entered.wait(TIMEOUT_SECONDS), "first calculation did not enter fake backend"
    second.start()
    assert second_started.wait(TIMEOUT_SECONDS), "second calculation thread did not start"
    assert not fake.second_calc_entry_reached.wait(0.2), (
        "second calc_ut call reached before the first calculation released the runtime session"
    )

    fake.release_first_calc.set()
    first.join(TIMEOUT_SECONDS)
    second.join(TIMEOUT_SECONDS)

    assert not first.is_alive()
    assert not second.is_alive()
    assert errors == []
    assert fake.max_active_calls == 1


@pytest.mark.no_ephemeris_autoinit
def test_ephemeris_session_releases_lock_after_backend_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeSwe()
    fake.raise_on_calc_entry = 1
    monkeypatch.setattr(swiss_backend, "swe", fake)
    configure_ephemeris(REPO_ROOT / "ephe")

    with pytest.raises(RuntimeError) as exc_info:
        _calculate_fake_natal()
    assert exc_info.match("could not calculate body")
    assert isinstance(exc_info.value.__cause__, FakeSwe.Error)

    fake.raise_on_calc_entry = None
    result: dict[str, Any] = {}
    thread = threading.Thread(target=lambda: result.setdefault("chart", _calculate_fake_natal()))
    thread.start()
    thread.join(TIMEOUT_SECONDS)

    assert not thread.is_alive()
    assert "chart" in result


@pytest.mark.no_ephemeris_autoinit
def test_sequential_and_concurrent_calculations_match_with_fake_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeSwe()
    monkeypatch.setattr(swiss_backend, "swe", fake)
    configure_ephemeris(REPO_ROOT / "ephe")

    expected = _calculate_fake_natal().model_dump(mode="json")
    results: list[dict[str, Any]] = []
    errors: list[BaseException] = []

    def calculate() -> None:
        try:
            results.append(_calculate_fake_natal().model_dump(mode="json"))
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=calculate) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(TIMEOUT_SECONDS)

    assert all(not thread.is_alive() for thread in threads)
    assert errors == []
    assert results == [expected, expected]
    assert fake.max_active_calls == 1


def _calculate_fake_natal() -> NatalChart:
    return calculate_natal(
        datetime(1985, 9, 1, 20, 45, tzinfo=timezone.utc),
        REFERENCE["latitude"],
        REFERENCE["longitude"],
        chart_kind="natal",
        house_system=REFERENCE["house_system"],
    )
