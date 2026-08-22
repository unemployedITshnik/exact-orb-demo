"""Contracts returned by the orchestration layer."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from exact_orb.intent.types import InterpretationPlan


class OrchestrationResponse(BaseModel):
    """Normalized response returned by the future orchestrator."""

    text: str
    plan: InterpretationPlan
    warnings: list[str] = Field(default_factory=list)
    meta: dict[str, Any] = Field(default_factory=dict)
