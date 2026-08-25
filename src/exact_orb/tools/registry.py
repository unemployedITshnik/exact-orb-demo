"""Registry for executable agent tools."""

from __future__ import annotations

from .base import Tool
from .natal_tool import NatalTool


class UnknownToolError(KeyError):
    """Raised when a requested tool name is not registered."""


class DuplicateToolError(ValueError):
    """Raised when registering a name that is already occupied."""


class InvalidToolError(ValueError):
    """Raised when registering a tool with an empty or blank name."""


class ToolRegistry:
    """Minimal storage for tool instances keyed by name."""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    @classmethod
    def default(cls) -> "ToolRegistry":
        """Build the default registry with every tool wired to its adapter.

        Every tool is a ``LocalTool`` today: ``calculate_natal()`` runs in-process,
        there is nothing to route to yet. ADR-0002 also describes a
        ``RemoteTool`` adapter and a per-tool config value
        (``natal = local | http://...``) to choose between them; that
        routing layer is deliberately deferred until a second adapter
        actually exists to choose between. When it arrives it will land in a
        separate config-reading classmethod next to this one — existing
        registry storage methods, ``Orchestrator``, and ``Planner`` do not
        have to change for that.
        """

        registry = cls()
        registry.register(NatalTool())
        return registry

    def register(self, tool: Tool) -> None:
        """Register a tool instance under its non-empty ``name``."""

        name = tool.name
        if not name.strip():
            raise InvalidToolError("tool name must be a non-empty string")
        if name in self._tools:
            raise DuplicateToolError(f"tool {name!r} is already registered")
        self._tools[name] = tool

    def get(self, name: str) -> Tool:
        """Return a registered tool by name."""

        try:
            return self._tools[name]
        except KeyError as exc:
            raise UnknownToolError(f"unknown tool {name!r}") from exc

    def list_tools(self) -> list[str]:
        """Return registered tool names in deterministic sorted order."""

        return sorted(self._tools)
