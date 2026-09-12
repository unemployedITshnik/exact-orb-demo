"""Deterministic calculation-key projection for Chart Artifacts."""

from __future__ import annotations

from datetime import datetime, timedelta
import hashlib
import json
import re

from pydantic import BaseModel, ConfigDict, field_serializer, field_validator

from exact_orb.birth.types import ResolvedBirthData, birth_time_domain_digest
from exact_orb.domain import normalize_latitude, normalize_longitude

from .spec import ChartSpec


SCHEMA_VERSION = "v2"
KEY_PREFIX = f"eo:calc:{SCHEMA_VERSION}:"


class CalculationInput(BaseModel):
    """Minimal resolved input projection that participates in calculation keys."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    utc_datetime: datetime
    latitude: float
    longitude: float
    birth_time_domain_digest: str | None

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

    @field_validator("birth_time_domain_digest", mode="before")
    @classmethod
    def _validate_birth_time_domain_digest(cls, value: object) -> str | None:
        if value is None:
            return None
        if type(value) is not str or re.fullmatch(r"[0-9a-f]{64}", value) is None:
            raise ValueError(
                "birth_time_domain_digest must be 64 lowercase hexadecimal characters"
            )
        return value


def calculation_input_from(resolved: ResolvedBirthData) -> CalculationInput:
    return CalculationInput(
        utc_datetime=resolved.utc_datetime,
        latitude=resolved.latitude,
        longitude=resolved.longitude,
        birth_time_domain_digest=(
            birth_time_domain_digest(resolved.birth_time_domain)
            if resolved.birth_time_domain is not None
            else None
        ),
    )


def canonical_key_payload(
    calc_input: CalculationInput,
    spec: ChartSpec,
    version: str,
) -> dict[str, object]:
    _validate_time_domain_identity(calc_input, spec)
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


def _validate_time_domain_identity(
    calc_input: CalculationInput,
    spec: ChartSpec,
) -> None:
    if spec.chart_kind == "cosmogram":
        if calc_input.birth_time_domain_digest is None:
            raise ValueError("cosmogram calculation input requires a time-domain digest")
    elif calc_input.birth_time_domain_digest is not None:
        raise ValueError("natal calculation input must not have a time-domain digest")


__all__ = [
    "CalculationInput",
    "calculation_input_from",
    "calculation_key",
    "canonical_key_payload",
]
