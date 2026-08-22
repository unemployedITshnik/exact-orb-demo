"""Base evidence selector interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from exact_orb.intent.types import InterpretationPlan
from exact_orb.tools.types import ToolResult


class DataSelector(ABC):
    """Facade for selecting evidence based on ``plan.data_selectors``.

    This skeleton exposes one dispatcher object. Future concrete
    implementations will inspect selector ids in the plan, apply the matching
    selectors, and merge their evidence into one payload.
    """

    @abstractmethod
    def select(
        self,
        plan: InterpretationPlan,
        tool_results: list[ToolResult],
    ) -> dict[str, Any]:
        """Select LLM evidence from tool results."""

        raise NotImplementedError
