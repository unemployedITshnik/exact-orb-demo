"""Shared typed outcomes returned by deterministic application components."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel


IssueCode = Literal["MISSING", "AMBIGUOUS", "INVALID", "UNSUPPORTED"]


class Issue(BaseModel):
    """One actionable problem in an input contract."""

    field: str
    code: IssueCode
    candidates: tuple[Any, ...] | None = None
    constraints: dict[str, Any] | None = None


class InputRequired(BaseModel):
    """User input must be corrected or completed before processing can continue."""

    issues: tuple[Issue, ...]


class ResolutionUnavailable(BaseModel):
    """A technical dependency made deterministic resolution unavailable."""

    error_code: str
    retryable: bool = True


__all__ = [
    "InputRequired",
    "Issue",
    "IssueCode",
    "ResolutionUnavailable",
]
