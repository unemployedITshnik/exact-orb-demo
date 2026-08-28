"""Telemetry context shared by application-level operations."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from pydantic import BaseModel, field_validator


class RunContext(BaseModel):
    """Correlation scope of one user operation. Telemetry, not domain data."""

    run_id: UUID
    started_at: datetime

    @classmethod
    def new(cls) -> "RunContext":
        return cls(run_id=uuid4(), started_at=datetime.now(timezone.utc))

    @field_validator("started_at")
    @classmethod
    def _started_at_must_be_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != timedelta(0):
            raise ValueError("started_at must be timezone-aware UTC")
        return value


__all__ = ["RunContext"]
