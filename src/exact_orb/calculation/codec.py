"""Codec for serialized chart artifact payload bytes."""

from __future__ import annotations

import gzip
from typing import Literal
import zlib

from pydantic import ValidationError

from .types import ChartArtifact


ChartArtifactDecodeReason = Literal["gzip", "utf8", "validation"]


class ChartArtifactDecodeError(ValueError):
    """Machine-readable decode failure without sensitive payload details."""

    def __init__(self, reason: ChartArtifactDecodeReason) -> None:
        self.reason = reason
        super().__init__(reason)


def encode_chart_artifact(artifact: ChartArtifact) -> bytes:
    json_payload = artifact.model_dump_json().encode("utf-8")
    return gzip.compress(json_payload, compresslevel=6, mtime=0)


def decode_chart_artifact(payload: bytes) -> ChartArtifact:
    if payload == b"":
        raise ChartArtifactDecodeError("gzip")

    try:
        compressed = gzip.decompress(payload)
    except (gzip.BadGzipFile, EOFError, zlib.error) as exc:
        raise ChartArtifactDecodeError("gzip") from exc

    try:
        json_payload = compressed.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ChartArtifactDecodeError("utf8") from exc

    try:
        return ChartArtifact.model_validate_json(json_payload)
    except ValidationError:
        raise ChartArtifactDecodeError("validation") from None


__all__ = [
    "ChartArtifactDecodeError",
    "ChartArtifactDecodeReason",
    "decode_chart_artifact",
    "encode_chart_artifact",
]
