"""Contracts for user requests and interpretation plans."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from exact_orb.tools.types import ToolRequest


class UserRequest(BaseModel):
    """A single-subject user request."""

    text: str
    # Temporary birth-data shape for one subject. It will be refined when the
    # concrete natal tool is designed.
    subject: dict[str, Any] | None = None


class InterpretationPlan(BaseModel):
    """Planner output consumed by orchestration."""

    intent: str
    focus: str | None = None
    required_tools: list[ToolRequest]
    data_selectors: list[str]
    prompt_recipe: str
    missing_slots: list[str] = Field(default_factory=list)
    output_format: str = "prose"
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
