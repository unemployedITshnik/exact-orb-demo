"""Deterministic calculation-key projection for Chart Artifacts."""

from __future__ import annotations

from datetime import datetime, timedelta
import hashlib
import json

from pydantic import BaseModel, ConfigDict, field_serializer, field_validator

from exact_orb.birth.types import ResolvedBirthData
from exact_orb.domain import normalize_latitude, normalize_longitude

from .spec import ChartSpec


SCHEMA_VERSION = "v1"
KEY_PREFIX = f"eo:calc:{SCHEMA_VERSION}:"


class CalculationInput(BaseModel):
    """Minimal resolved input projection that participates in calculation keys."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    utc_datetime: datetime
    latitude: float
    longitude: float

    @field_validator("utc_datetime")
    @classmethod
    def _utc_datetime_must_be_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("utc_datetime must be timezone-aware UTC")
        if value.utcoffset() != timedelta(0):
            raise ValueError("utc_datetime must be timezone-aware UTC")
        return value.replace(microsecond=0)

    @field_serializer("utc_datetime")
    def _serialize_utc_datetime(self, value: datetime) -> str:
        return value.strftime("%Y-%m-%dT%H:%M:%SZ")

    @field_validator("latitude", mode="before")
    @classmethod
    def _normalize_latitude(cls, value: object) -> float:
        return normalize_latitude(value)

    @field_validator("longitude", mode="before")
    @classmethod
    def _normalize_longitude(cls, value: object) -> float:
        return normalize_longitude(value)


def calculation_input_from(resolved: ResolvedBirthData) -> CalculationInput:
    return CalculationInput(
        utc_datetime=resolved.utc_datetime,
        latitude=resolved.latitude,
        longitude=resolved.longitude,
    )


def canonical_key_payload(
    calc_input: CalculationInput,
    spec: ChartSpec,
    version: str,
) -> dict[str, object]:
    return {
        "schema_version": SCHEMA_VERSION,
        "calculation_input": calc_input.model_dump(mode="json"),
        "spec": spec.model_dump(mode="json"),
        "calculation_version": version,
    }


def calculation_key(
    calc_input: CalculationInput,
    spec: ChartSpec,
    version: str,
) -> str:
    payload = canonical_key_payload(calc_input, spec, version)
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return f"{KEY_PREFIX}{hashlib.sha256(encoded).hexdigest()}"


__all__ = [
    "CalculationInput",
    "calculation_input_from",
    "calculation_key",
    "canonical_key_payload",
]
