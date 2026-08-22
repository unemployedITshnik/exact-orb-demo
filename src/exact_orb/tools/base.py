"""Base interface for agent tools."""

from __future__ import annotations

from abc import ABC, abstractmethod

from .types import ToolRequest, ToolResult


class Tool(ABC):
    """Executable tool interface used by orchestration."""

    name: str

    @abstractmethod
    def run(self, request: ToolRequest) -> ToolResult:
        """Run the tool for a validated request."""

        raise NotImplementedError
