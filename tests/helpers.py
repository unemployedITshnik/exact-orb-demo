"""Test helpers for angular calculations."""

from __future__ import annotations


def angular_delta_degrees(left: float, right: float) -> float:
    delta = abs((left - right) % 360.0)
    if delta > 180.0:
        return 360.0 - delta
    return delta


def assert_longitude_close(
    actual: float,
    expected: float,
    *,
    tolerance_degrees: float,
    label: str,
) -> None:
    delta = angular_delta_degrees(actual, expected)
    assert delta <= tolerance_degrees, (
        f"{label}: actual={actual:.9f} expected={expected:.9f} "
        f"delta={delta * 3600.0:.3f} arcsec"
    )
