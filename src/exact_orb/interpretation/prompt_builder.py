"""Base prompt builder interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from exact_orb.intent.types import InterpretationPlan

from .types import PromptBundle


class PromptBuilder(ABC):
    """Build a prompt bundle from a plan and selected evidence."""

    @abstractmethod
    def build(
        self,
        plan: InterpretationPlan,
        evidence: dict[str, Any],
    ) -> PromptBundle:
        """Build a prompt bundle for the LLM gateway."""

        raise NotImplementedError
