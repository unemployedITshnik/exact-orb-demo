"""Typed artifact payload models.

This module imports engine result models and therefore also imports the native
calculation stack transitively. Keep contract-only imports on
``exact_orb.calculation`` or its ``spec``, ``keys`` and ``cache`` modules.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from exact_orb.domain import ChartKind
from exact_orb.engine.charts.natal import NatalChart
from exact_orb.engine.ephemeris.types import CalculationWarning

from .keys import KEY_PREFIX
from .spec import ChartSpec


class ArtifactEphemerisStatus(BaseModel):
    """Artifact-safe ephemeris audit data without runtime provenance."""

    mode: Literal["files", "fallback"]
    required_files: tuple[str, ...]
    found_files: tuple[str, ...]
    missing_files: tuple[str, ...]

    @property
    def using_files(self) -> bool:
        return self.mode == "files"


class ArtifactNatalChart(NatalChart):
    """Natal chart payload with artifact-safe ephemeris status."""

    ephemeris: ArtifactEphemerisStatus

    @field_validator("ephemeris", mode="before")
    @classmethod
    def _normalize_ephemeris(cls, value: Any) -> Any:
        if hasattr(value, "model_dump"):
            return value.model_dump(mode="python")
        return value


class ChartArtifact(BaseModel):
    """Serialized chart artifact identity plus deterministic chart payload."""

    model_config = ConfigDict(frozen=True)

    calculation_key: str
    spec: ChartSpec
    calculation_version: str = Field(..., min_length=1)
    chart_kind: ChartKind
    chart: ArtifactNatalChart
    warnings: tuple[CalculationWarning, ...]

    @model_validator(mode="before")
    @classmethod
    def _normalize_chart(cls, data: Any) -> Any:
        if not isinstance(data, Mapping):
            return data

        values = dict(data)
        chart = values.get("chart")
        if isinstance(chart, NatalChart) and not isinstance(chart, ArtifactNatalChart):
            values["chart"] = ArtifactNatalChart.model_validate(chart.model_dump(mode="python"))
        return values

    @field_validator("calculation_key")
    @classmethod
    def _calculation_key_must_have_prefix(cls, value: str) -> str:
        if not value.startswith(KEY_PREFIX):
            raise ValueError(f"calculation_key must start with {KEY_PREFIX!r}")
        return value

    @model_validator(mode="after")
    def _validate_identity(self) -> "ChartArtifact":
        if self.chart_kind != self.spec.chart_kind:
            raise ValueError("chart_kind must match spec.chart_kind")
        if self.chart_kind != self.chart.chart_kind:
            raise ValueError("chart_kind must match chart.chart_kind")
        if self.warnings != self.chart.warnings:
            raise ValueError("warnings must match chart.warnings for natal artifacts")
        return self


__all__ = [
    "ArtifactEphemerisStatus",
    "ArtifactNatalChart",
    "ChartArtifact",
]
