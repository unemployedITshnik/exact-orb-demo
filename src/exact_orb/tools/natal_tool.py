"""Local adapter for the natal chart tool (ADR-0002)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, field_validator

from exact_orb.engine.charts.natal import get_natal

from .base import Tool
from .types import ToolRequest, ToolResult


class NatalToolArgs(BaseModel):
    """Validated arguments for the ``natal`` tool.

    This is the schema contract a ``Planner`` must satisfy when it builds a
    ``ToolRequest`` for a natal-chart scenario. It intentionally exposes only
    the inputs today's scenarios need. Engine-level tuning knobs
    (``aspect_config``, ``configuration_config``, ``strength_config``) stay at
    their ``get_natal()`` defaults until a recipe actually needs to override
    them — adding a field here later is a small, additive change.

    ``include`` is forwarded as-is so a cosmogram recipe (ADR-0008) can pass
    a reduced block set. ``get_natal()`` does honor this at the output level:
    passing ``include`` without ``"houses"`` sets ``NatalChart.cusps`` and
    ``.angles`` to ``None`` (and without ``"rulers"``, ``.house_rulers``/
    ``.interceptions`` to ``None``). Until the engine exposes an explicit
    ``chart_kind`` field, ``NatalTool.run`` derives a temporary proxy from
    that output shape.
    """

    birth_datetime: datetime
    latitude: float
    longitude: float
    house_system: str = "P"
    rulership: str = "combined"
    include: tuple[str, ...] | None = None

    @field_validator("birth_datetime")
    @classmethod
    def _require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("birth_datetime must be timezone-aware")
        return value


class NatalTool(Tool):
    """In-process (``LocalTool``) adapter that calls ``get_natal()`` directly.

    This satisfies the ``LocalTool`` half of ADR-0002: validation and the
    calculation call happen in-process, with no network involved. A future
    ``RemoteTool`` would satisfy the same ``Tool`` interface over HTTP;
    neither ``ToolRegistry`` nor ``Orchestrator`` would need to change for
    that swap — only the registration in ``ToolRegistry.from_config()``.
    """

    name = "natal"

    def run(self, request: ToolRequest) -> ToolResult:
        """Validate ``request.args`` and run the natal chart calculation."""

        if request.tool_name != self.name:
            raise ValueError(f"expected tool_name {self.name!r}, got {request.tool_name!r}")

        args = NatalToolArgs.model_validate(request.args)
        chart = get_natal(
            birth_datetime=args.birth_datetime,
            latitude=args.latitude,
            longitude=args.longitude,
            house_system=args.house_system,
            rulership=args.rulership,
            include=set(args.include) if args.include is not None else None,
        )
        # Temporary proxy until the engine returns an explicit chart_kind.
        chart_kind = "natal" if chart.cusps is not None else "cosmogram"
        return ToolResult(
            tool_name=self.name,
            data=chart.model_dump(mode="json"),
            warnings=[f"{warning.source}: {warning.message}" for warning in chart.warnings],
            meta={"chart_kind": chart_kind},
        )
