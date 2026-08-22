"""Tests for the natal tool adapter and its registration in ToolRegistry."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from exact_orb.tools import NatalTool, NatalToolArgs, ToolRegistry, ToolRequest

from .fixtures.natal_1985 import EXPECTED_BODY_LONGITUDES, REFERENCE


def _reference_args() -> dict[str, object]:
    return {
        "birth_datetime": REFERENCE["datetime_utc"],
        "latitude": REFERENCE["latitude"],
        "longitude": REFERENCE["longitude"],
        "house_system": REFERENCE["house_system"],
    }


def test_natal_tool_args_requires_timezone_aware_datetime() -> None:
    with pytest.raises(ValidationError, match="timezone-aware"):
        NatalToolArgs(
            birth_datetime=datetime(1985, 9, 1, 20, 45, 0),
            latitude=REFERENCE["latitude"],
            longitude=REFERENCE["longitude"],
        )


def test_natal_tool_args_defaults() -> None:
    args = NatalToolArgs(
        birth_datetime=datetime(1985, 9, 1, 20, 45, 0, tzinfo=timezone.utc),
        latitude=REFERENCE["latitude"],
        longitude=REFERENCE["longitude"],
    )

    assert args.house_system == "P"
    assert args.rulership == "combined"
    assert args.include is None


def test_natal_tool_run_rejects_incomplete_args() -> None:
    tool = NatalTool()

    with pytest.raises(ValidationError):
        tool.run(ToolRequest(tool_name="natal", args={"latitude": 55.75}))


def test_natal_tool_run_rejects_wrong_tool_name() -> None:
    tool = NatalTool()

    with pytest.raises(ValueError, match="expected tool_name"):
        tool.run(ToolRequest(tool_name="solar", args=_reference_args()))


def test_natal_tool_run_returns_tool_result() -> None:
    tool = NatalTool()
    request = ToolRequest(tool_name="natal", args=_reference_args())

    result = tool.run(request)

    assert result.tool_name == "natal"
    assert result.meta == {"chart_kind": "natal"}
    assert isinstance(result.warnings, list)
    assert "bodies" in result.data
    assert "cusps" in result.data


def test_natal_tool_run_matches_known_sun_longitude() -> None:
    """Round-trip through ToolRequest/ToolResult must not alter the calculation."""

    tool = NatalTool()
    request = ToolRequest(tool_name="natal", args=_reference_args())

    result = tool.run(request)

    sun_longitude = result.data["bodies"]["sun"]["longitude"]
    assert sun_longitude == pytest.approx(EXPECTED_BODY_LONGITUDES["sun"], abs=1e-3)


def test_natal_tool_run_with_reduced_include_produces_cosmogram() -> None:
    """ADR-0008 cosmogram: dropping "houses"/"rulers" from include nulls
    cusps/angles/house_rulers/interceptions in the chart, and chart_kind
    must follow that rather than staying hardcoded."""

    tool = NatalTool()
    args = _reference_args()
    args["include"] = ("positions", "aspects", "configurations", "strength")
    request = ToolRequest(tool_name="natal", args=args)

    result = tool.run(request)

    assert result.meta == {"chart_kind": "cosmogram"}
    assert result.data["cusps"] is None
    assert result.data["angles"] is None
    assert result.data["house_rulers"] is None
    assert result.data["interceptions"] is None
    # Positions are unaffected — the Sun's longitude must still match the
    # known reference regardless of which blocks were requested.
    sun_longitude = result.data["bodies"]["sun"]["longitude"]
    assert sun_longitude == pytest.approx(EXPECTED_BODY_LONGITUDES["sun"], abs=1e-3)


def test_tool_registry_from_config_registers_natal() -> None:
    registry = ToolRegistry.from_config()

    assert registry.list_tools() == ["natal"]
    assert isinstance(registry.get("natal"), NatalTool)
