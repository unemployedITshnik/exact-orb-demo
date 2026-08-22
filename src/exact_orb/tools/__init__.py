"""Agent tool contracts and registry."""

from .base import Tool
from .natal_tool import NatalTool, NatalToolArgs
from .registry import DuplicateToolError, InvalidToolError, ToolRegistry, UnknownToolError
from .types import ToolRequest, ToolResult

__all__ = [
    "DuplicateToolError",
    "InvalidToolError",
    "NatalTool",
    "NatalToolArgs",
    "Tool",
    "ToolRegistry",
    "ToolRequest",
    "ToolResult",
    "UnknownToolError",
]
