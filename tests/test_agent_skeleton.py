"""Contract tests for the agent architecture skeleton."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from exact_orb.intent import InterpretationPlan, Planner, UserRequest
from exact_orb.interpretation import (
    DataSelector,
    DuplicatePromptRecipeError,
    InvalidPromptRecipeError,
    PromptBuilder,
    PromptBundle,
    PromptRegistry,
    UnknownPromptRecipeError,
)
from exact_orb.orchestration import OrchestrationResponse, Orchestrator
from exact_orb.tools import (
    DuplicateToolError,
    InvalidToolError,
    Tool,
    ToolRegistry,
    ToolRequest,
    ToolResult,
    UnknownToolError,
)


class DummyTool(Tool):
    """Minimal concrete tool for registry tests."""

    def __init__(self, name: str) -> None:
        self.name = name

    def run(self, request: ToolRequest) -> ToolResult:
        return ToolResult(tool_name=request.tool_name, data={"ok": True})


class DummyPlanner(Planner):
    """Minimal concrete planner for orchestration construction tests."""

    def plan(self, request: UserRequest) -> InterpretationPlan:
        return InterpretationPlan(
            intent="natal_interpretation",
            focus="general",
            required_tools=[ToolRequest(tool_name="natal")],
            data_selectors=["natal.general"],
            prompt_recipe="natal.general.v1",
        )


class DummyDataSelector(DataSelector):
    """Minimal concrete selector for orchestration construction tests."""

    def select(
        self,
        plan: InterpretationPlan,
        tool_results: list[ToolResult],
    ) -> dict[str, Any]:
        return {"facts": []}


class DummyPromptBuilder(PromptBuilder):
    """Minimal concrete prompt builder for orchestration construction tests."""

    def build(
        self,
        plan: InterpretationPlan,
        evidence: dict[str, Any],
    ) -> PromptBundle:
        return PromptBundle(
            system="Use only provided evidence.",
            user="Interpret the natal chart.",
            recipe_id=plan.prompt_recipe,
        )


def test_agent_contract_models_set_fields_and_defaults() -> None:
    tool_request = ToolRequest(tool_name="natal")
    tool_result = ToolResult(tool_name="natal", data={"chart": {"houses": 12}})
    user_request = UserRequest(
        text="Tell me about career in the natal chart.",
        subject={"place": "Moscow"},
    )
    plan = InterpretationPlan(
        intent="natal_interpretation",
        focus="career",
        required_tools=[tool_request],
        data_selectors=["natal.career"],
        prompt_recipe="natal.career.v1",
    )
    response = OrchestrationResponse(text="Career interpretation.", plan=plan)

    assert tool_request.args == {}
    assert tool_result.warnings == []
    assert tool_result.meta == {}
    assert user_request.subject == {"place": "Moscow"}
    assert plan.missing_slots == []
    assert plan.output_format == "prose"
    assert plan.confidence == 1.0
    assert response.warnings == []
    assert response.meta == {}


def test_interpretation_plan_rejects_invalid_confidence() -> None:
    with pytest.raises(ValidationError):
        InterpretationPlan(
            intent="natal_interpretation",
            required_tools=[ToolRequest(tool_name="natal")],
            data_selectors=["natal.general"],
            prompt_recipe="natal.general.v1",
            confidence=42,
        )


@pytest.mark.parametrize(
    "abstract_class",
    [Tool, Planner, DataSelector, PromptBuilder],
)
def test_base_interfaces_are_abstract(abstract_class: type[object]) -> None:
    with pytest.raises(TypeError):
        abstract_class()


def test_tool_registry_registers_and_retrieves_tools() -> None:
    registry = ToolRegistry()
    natal = DummyTool("natal")
    solar = DummyTool("solar")
    transits = DummyTool("transits")

    registry.register(transits)
    registry.register(natal)
    registry.register(solar)

    assert registry.get("natal") is natal
    assert registry.get("solar") is solar
    assert registry.get("transits") is transits
    assert registry.list_tools() == ["natal", "solar", "transits"]

    with pytest.raises(UnknownToolError):
        registry.get("directions")
    with pytest.raises(DuplicateToolError):
        registry.register(DummyTool("natal"))
    with pytest.raises(InvalidToolError):
        registry.register(DummyTool(" "))


def test_prompt_registry_registers_and_retrieves_recipes() -> None:
    registry = PromptRegistry()
    general = {"blocks": ["base", "rules", "focus.general"]}
    career = {"blocks": ["base", "rules", "focus.career"]}

    registry.register("natal.general.v1", general)
    registry.register("natal.career.v1", career)

    assert registry.get("natal.general.v1") is general
    assert registry.get("natal.career.v1") is career

    with pytest.raises(UnknownPromptRecipeError):
        registry.get("transits.general.v1")
    with pytest.raises(DuplicatePromptRecipeError):
        registry.register("natal.general.v1", {})
    with pytest.raises(InvalidPromptRecipeError):
        registry.register("\t", {})


def test_orchestrator_constructs_and_handle_is_not_implemented() -> None:
    planner = DummyPlanner()
    tool_registry = ToolRegistry()
    data_selector = DummyDataSelector()
    prompt_builder = DummyPromptBuilder()

    orchestrator = Orchestrator(
        planner=planner,
        tool_registry=tool_registry,
        data_selector=data_selector,
        prompt_builder=prompt_builder,
        llm_complete=lambda *args, **kwargs: None,
    )

    assert orchestrator.planner is planner
    assert orchestrator.tool_registry is tool_registry
    assert orchestrator.data_selector is data_selector
    assert orchestrator.prompt_builder is prompt_builder

    with pytest.raises(NotImplementedError):
        orchestrator.handle(UserRequest(text="Tell me about my natal chart."))
