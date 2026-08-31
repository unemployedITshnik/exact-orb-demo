"""Chart specification models used to build deterministic calculation keys."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from exact_orb.domain import (
    ChartKind,
    IncludeBlock,
    RulershipScheme,
    normalize_house_system_code,
    normalize_include,
)


class NatalChartSpec(BaseModel):
    """Backend-free specification for natal and natal-like chart calculations."""

    model_config = ConfigDict(frozen=True)

    technique: Literal["natal"] = "natal"
    chart_kind: ChartKind
    include: tuple[IncludeBlock, ...]
    house_system: str = "P"
    rulership: RulershipScheme = RulershipScheme.COMBINED
    near_interception_threshold: float = Field(default=1.0, ge=0.0)

    @model_validator(mode="before")
    @classmethod
    def _default_and_normalize_include(cls, data: Any) -> Any:
        if isinstance(data, cls):
            return data
        if not isinstance(data, Mapping):
            return data

        values = dict(data)
        chart_kind = values.get("chart_kind")
        if chart_kind is not None:
            values["include"] = normalize_include(chart_kind, values.get("include"))
        return values

    @field_validator("house_system", mode="before")
    @classmethod
    def _normalize_house_system(cls, value: str | bytes) -> str:
        return normalize_house_system_code(value)


ChartSpec = NatalChartSpec


__all__ = [
    "ChartSpec",
    "NatalChartSpec",
]
