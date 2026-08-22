"""Contracts for LLM prompt preparation."""

from __future__ import annotations

from pydantic import BaseModel


class PromptBundle(BaseModel):
    """System and user prompt pair prepared from a recipe."""

    system: str
    user: str
    recipe_id: str
