"""Shared contracts for executable agent tools."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ToolRequest(BaseModel):
    """A request for one registered tool."""

    tool_name: str
    args: dict[str, Any] = Field(default_factory=dict)


class ToolResult(BaseModel):
    """A normalized result returned by one tool."""

    tool_name: str
    data: dict[str, Any]
    warnings: list[str] = Field(default_factory=list)
    meta: dict[str, Any] = Field(default_factory=dict)
