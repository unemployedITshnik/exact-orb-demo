"""Typed calculation-boundary errors for chart artifacts."""

from __future__ import annotations

from typing import Literal


ChartCalculationErrorCode = Literal[
    "SPEC_INVALID",
    "GEOGRAPHY_INVALID",
    "HOUSES_DEGENERATE",
    "ENGINE_UNEXPECTED",
]

CalculationUnavailableErrorCode = Literal[
    "EPHEMERIS_UNAVAILABLE",
]


class ArtifactError(Exception):
    """Base error crossing artifact-layer service boundaries."""

    def __init__(self, code: str, *, run_id: str) -> None:
        self.code = code
        self.run_id = run_id
        super().__init__(f"{code} run_id={run_id}")


class ChartCalculationError(ArtifactError):
    """Non-retryable calculation failure."""

    code: ChartCalculationErrorCode

    def __init__(self, code: ChartCalculationErrorCode, *, run_id: str) -> None:
        super().__init__(code, run_id=run_id)


class CalculationUnavailableError(ArtifactError):
    """Retryable calculation dependency failure."""

    code: CalculationUnavailableErrorCode

    def __init__(self, code: CalculationUnavailableErrorCode, *, run_id: str) -> None:
        super().__init__(code, run_id=run_id)


__all__ = [
    "ArtifactError",
    "CalculationUnavailableError",
    "CalculationUnavailableErrorCode",
    "ChartCalculationError",
    "ChartCalculationErrorCode",
]
