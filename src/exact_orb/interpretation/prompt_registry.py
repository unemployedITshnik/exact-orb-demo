"""Registry for prompt recipe placeholders."""

from __future__ import annotations

from typing import Any


class UnknownPromptRecipeError(KeyError):
    """Raised when a requested prompt recipe id is not registered."""


class DuplicatePromptRecipeError(ValueError):
    """Raised when registering a prompt recipe id that is already occupied."""


class InvalidPromptRecipeError(ValueError):
    """Raised when registering an empty or blank prompt recipe id."""


class PromptRegistry:
    """Minimal storage for prompt recipe placeholders keyed by id."""

    def __init__(self) -> None:
        self._recipes: dict[str, Any] = {}

    def register(self, recipe_id: str, recipe: Any) -> None:
        """Register a recipe placeholder under a non-empty id."""

        if not recipe_id.strip():
            raise InvalidPromptRecipeError("prompt recipe id must be a non-empty string")
        if recipe_id in self._recipes:
            raise DuplicatePromptRecipeError(
                f"prompt recipe {recipe_id!r} is already registered"
            )
        # The recipe shape and base/rules/focus/style/format semantics are
        # intentionally left as Any until a dedicated prompt recipe task.
        self._recipes[recipe_id] = recipe

    def get(self, recipe_id: str) -> Any:
        """Return a registered recipe placeholder by id."""

        try:
            return self._recipes[recipe_id]
        except KeyError as exc:
            raise UnknownPromptRecipeError(
                f"unknown prompt recipe {recipe_id!r}"
            ) from exc
