"""Chart artifact model and codec tests."""

from __future__ import annotations

from datetime import datetime, timezone
import gzip
import json
from typing import Any

import pytest
from pydantic import ValidationError

from exact_orb.calculation.codec import (
    ChartArtifactDecodeError,
    decode_chart_artifact,
    encode_chart_artifact,
)
from exact_orb.calculation.keys import CalculationInput, calculation_key
from exact_orb.calculation.spec import NatalChartSpec
from exact_orb.calculation.types import (
    ArtifactEphemerisStatus,
    ArtifactNatalChart,
    ChartArtifact,
)
from exact_orb.config import EphemerisStatus
from exact_orb.engine.charts.natal import NatalChart
from exact_orb.engine.ephemeris.types import CalculationWarning


pytestmark = pytest.mark.no_ephemeris_autoinit


BASE_UTC = datetime(1990, 9, 2, 10, 30, 45, tzinfo=timezone.utc)
EPHE_FILES = ("sepl_18.se1", "semo_18.se1", "seas_18.se1")
SENSITIVE_WARNING = "sensitive warning for 55.7558 37.6173 at 1990-09-02"


def test_chart_artifact_normalizes_raw_chart_to_artifact_safe_chart() -> None:
    artifact = _artifact()

    assert isinstance(artifact.chart, ArtifactNatalChart)
    assert isinstance(artifact.chart, NatalChart)
    assert isinstance(artifact.chart.ephemeris, ArtifactEphemerisStatus)
    assert artifact.chart.ephemeris.mode == "files"
    assert artifact.chart.ephemeris.required_files == EPHE_FILES
    assert artifact.chart.ephemeris.found_files == EPHE_FILES
    assert artifact.chart.ephemeris.missing_files == ()
    assert artifact.chart.ephemeris.using_files is True
    assert not hasattr(artifact.chart.ephemeris, "path")
    assert not hasattr(artifact.chart.ephemeris, "source")


def test_chart_artifact_accepts_already_normalized_chart() -> None:
    first = _artifact()
    second = _artifact(chart=first.chart)

    assert isinstance(second.chart, ArtifactNatalChart)
    assert second == first


def test_artifact_ephemeris_fields_track_runtime_status_minus_runtime_provenance() -> None:
    assert set(ArtifactEphemerisStatus.model_fields) == set(EphemerisStatus.model_fields) - {
        "path",
        "source",
    }


def test_chart_artifact_top_level_model_is_frozen() -> None:
    artifact = _artifact()

    with pytest.raises(ValidationError):
        artifact.chart_kind = "cosmogram"  # type: ignore[misc]


def test_chart_artifact_validates_identity_fields() -> None:
    chart = _raw_chart()
    warnings = chart.warnings

    with pytest.raises(ValidationError, match="calculation_key"):
        _artifact(chart=chart, key="bad-prefix")

    with pytest.raises(ValidationError, match="calculation_version"):
        _artifact(chart=chart, version="")

    with pytest.raises(ValidationError, match="spec.chart_kind"):
        _artifact(chart=chart, spec=NatalChartSpec(chart_kind="cosmogram"), chart_kind="natal")

    with pytest.raises(ValidationError, match="chart.chart_kind"):
        _artifact(chart=_raw_chart(chart_kind="cosmogram"), spec=NatalChartSpec(chart_kind="natal"), chart_kind="natal")

    with pytest.raises(ValidationError, match="warnings"):
        _artifact(chart=chart, warnings=warnings + (_warning("extra"),))


def test_encode_returns_deterministic_gzip_bytes_with_utf8_json_payload() -> None:
    artifact = _artifact()

    first = encode_chart_artifact(artifact)
    second = encode_chart_artifact(artifact)
    raw = gzip.decompress(first)
    payload = json.loads(raw.decode("utf-8"))

    assert isinstance(first, bytes)
    assert first == second
    assert payload["calculation_key"] == artifact.calculation_key
    assert payload["chart"]["ephemeris"]["mode"] == "files"
    assert "path" not in payload["chart"]["ephemeris"]
    assert "source" not in payload["chart"]["ephemeris"]


def test_codec_round_trip_returns_equal_new_instance() -> None:
    artifact = _artifact()
    encoded = encode_chart_artifact(artifact)

    decoded = decode_chart_artifact(encoded)

    assert decoded == artifact
    assert decoded is not artifact
    assert encode_chart_artifact(decode_chart_artifact(encoded)) == encoded


def test_mutating_decoded_nested_chart_does_not_affect_next_decode() -> None:
    artifact = _artifact()
    encoded = encode_chart_artifact(artifact)
    first = decode_chart_artifact(encoded)

    first.chart.longitude = 0.0
    second = decode_chart_artifact(encoded)

    assert second.chart.longitude == artifact.chart.longitude


@pytest.mark.parametrize(
    ("payload", "reason"),
    (
        (b"", "gzip"),
        (b"not gzip", "gzip"),
    ),
)
def test_decode_reports_gzip_reason_for_empty_or_non_gzip_payloads(
    payload: bytes,
    reason: str,
) -> None:
    with pytest.raises(ChartArtifactDecodeError) as exc_info:
        decode_chart_artifact(payload)

    assert exc_info.value.reason == reason


def test_decode_reports_gzip_reason_for_truncated_gzip() -> None:
    payload = encode_chart_artifact(_artifact())[:8]

    with pytest.raises(ChartArtifactDecodeError) as exc_info:
        decode_chart_artifact(payload)

    assert exc_info.value.reason == "gzip"


def test_decode_reports_gzip_reason_for_corrupt_deflate_body() -> None:
    payload = bytes.fromhex("1f8b0800000000000003") + b"bad-deflate" + (b"\x00" * 8)

    with pytest.raises(ChartArtifactDecodeError) as exc_info:
        decode_chart_artifact(payload)

    assert exc_info.value.reason == "gzip"


def test_decode_reports_utf8_reason_for_non_utf8_uncompressed_payload() -> None:
    payload = gzip.compress(b"\xff", compresslevel=6, mtime=0)

    with pytest.raises(ChartArtifactDecodeError) as exc_info:
        decode_chart_artifact(payload)

    assert exc_info.value.reason == "utf8"


@pytest.mark.parametrize(
    "raw",
    (
        b"not json",
        b"{}",
    ),
)
def test_decode_reports_validation_reason_for_utf8_payloads_that_are_not_artifacts(
    raw: bytes,
) -> None:
    payload = gzip.compress(raw, compresslevel=6, mtime=0)

    with pytest.raises(ChartArtifactDecodeError) as exc_info:
        decode_chart_artifact(payload)

    assert exc_info.value.reason == "validation"


def test_decode_validation_error_text_does_not_expose_payload_or_pydantic_details() -> None:
    artifact = _artifact()
    payload = _decoded_json_payload(artifact)
    del payload["calculation_version"]
    corrupt = gzip.compress(
        json.dumps(payload, separators=(",", ":")).encode("utf-8"),
        compresslevel=6,
        mtime=0,
    )

    with pytest.raises(ChartArtifactDecodeError) as exc_info:
        decode_chart_artifact(corrupt)

    error = exc_info.value
    text = str(error)
    assert error.reason == "validation"
    assert error.__cause__ is None
    assert "1990-09-02" not in text
    assert "55.7558" not in text
    assert "37.6173" not in text
    assert SENSITIVE_WARNING not in text
    assert "calculation_version" not in text
    assert "ValidationError" not in text


def _raw_chart(
    *,
    chart_kind: str = "natal",
    warnings: tuple[CalculationWarning, ...] | None = None,
    path: str = r"C:\Users\KateUser\secret\ephe",
    source: str = "argument",
) -> NatalChart:
    return NatalChart(
        chart_kind=chart_kind,
        datetime_utc=BASE_UTC,
        julian_day_ut=2448136.0,
        latitude=55.7558,
        longitude=37.6173,
        house_system="P",
        ephemeris_flags=0,
        ephemeris=EphemerisStatus(
            path=path,
            source=source,
            mode="files",
            required_files=EPHE_FILES,
            found_files=EPHE_FILES,
            missing_files=(),
        ),
        selena_method="true_perigee",
        bodies={},
        cusps=None,
        angles=None,
        house_rulers=None,
        interceptions=None,
        aspects=None,
        configurations=None,
        strength=None,
        warnings=warnings if warnings is not None else (_warning(SENSITIVE_WARNING),),
    )


def _artifact(
    *,
    chart: NatalChart | ArtifactNatalChart | None = None,
    spec: NatalChartSpec | None = None,
    chart_kind: str | None = None,
    warnings: tuple[CalculationWarning, ...] | None = None,
    key: str | None = None,
    version: str = "test-version-1",
) -> ChartArtifact:
    chart = chart or _raw_chart()
    spec = spec or NatalChartSpec(chart_kind=chart.chart_kind)
    chart_kind = chart_kind or chart.chart_kind
    warnings = warnings if warnings is not None else chart.warnings
    key = key or calculation_key(
        CalculationInput(
            utc_datetime=chart.datetime_utc,
            latitude=chart.latitude,
            longitude=chart.longitude,
        ),
        spec,
        version,
    )
    return ChartArtifact(
        calculation_key=key,
        spec=spec,
        calculation_version=version,
        chart_kind=chart_kind,
        chart=chart,
        warnings=warnings,
    )


def _warning(message: str) -> CalculationWarning:
    return CalculationWarning(source="fixture", message=message, retflags=None)


def _decoded_json_payload(artifact: ChartArtifact) -> dict[str, Any]:
    return json.loads(gzip.decompress(encode_chart_artifact(artifact)).decode("utf-8"))
