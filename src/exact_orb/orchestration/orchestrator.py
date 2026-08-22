"""Top-level agent orchestration skeleton."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from exact_orb.intent.planner import Planner
from exact_orb.intent.types import UserRequest
from exact_orb.interpretation.prompt_builder import PromptBuilder
from exact_orb.interpretation.selectors import DataSelector
from exact_orb.tools.registry import ToolRegistry

from .types import OrchestrationResponse


class Orchestrator:
    """Coordinate future interpretation flow dependencies."""

    def __init__(
        self,
        *,
        planner: Planner,
        tool_registry: ToolRegistry,
        data_selector: DataSelector,
        prompt_builder: PromptBuilder,
        llm_complete: Callable[..., Any],
    ) -> None:
        self.planner = planner
        self.tool_registry = tool_registry
        self.data_selector = data_selector
        self.prompt_builder = prompt_builder
        self.llm_complete = llm_complete

    def handle(self, request: UserRequest) -> OrchestrationResponse:
        """Handle a user request once orchestration logic is implemented.

        Future implementation contract:

        1. plan = planner.plan(request)
        2. check plan.missing_slots
        3. resolve and validate tools through tool_registry by
           plan.required_tools
        4. run tools -> list[ToolResult]
        5. evidence = data_selector.select(plan, tool_results)
        6. bundle = prompt_builder.build(plan, evidence)
        7. response = llm_complete(bundle.user, system=bundle.system, ...)
        8. build and return OrchestrationResponse
        """

        raise NotImplementedError
