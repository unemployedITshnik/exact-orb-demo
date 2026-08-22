"""Base planner interface."""

from __future__ import annotations

from abc import ABC, abstractmethod

from .types import InterpretationPlan, UserRequest


class Planner(ABC):
    """Build an interpretation plan from a user request."""

    @abstractmethod
    def plan(self, request: UserRequest) -> InterpretationPlan:
        """Return the interpretation plan for ``request``."""

        raise NotImplementedError
