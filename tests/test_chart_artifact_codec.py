"""Chart artifact model and codec tests."""

from __future__ import annotations

from datetime import datetime, timezone
import gzip
from hashlib import sha256
import json
from typing import Any

import pytest
from pydantic import ValidationError

from exact_orb.birth.types import BirthTimeDomain, UtcMinuteRange
from exact_orb.calculation.chart_contract import calculation_input_from_chart
from exact_orb.calculation.codec import (
    ChartArtifactDecodeError,
    decode_chart_artifact,
    encode_chart_artifact,
)
from exact_orb.calculation.keys import KEY_PREFIX, CalculationInput, calculation_key
from exact_orb.calculation.spec import NatalChartSpec
from exact_orb.calculation.types import (
    ArtifactEphemerisStatus,
    ArtifactNatalChart,
    ChartArtifact,
)
from exact_orb.config import EphemerisStatus, configure_ephemeris
from exact_orb.engine.ephemeris.calc import zodiac_position
from exact_orb.engine.charts.natal import NatalChart, calculate_natal
from exact_orb.engine.charts.uncertainty import CosmogramTimeUncertainty
from exact_orb.engine.ephemeris.types import BodyPosition, CalculationWarning
from tests.conftest import REPO_ROOT
from tests.fixtures.natal_1985 import REFERENCE


pytestmark = pytest.mark.no_ephemeris_autoinit


BASE_UTC = datetime(1990, 9, 2, 10, 30, 45, tzinfo=timezone.utc)
EPHE_FILES = ("sepl_18.se1", "semo_18.se1", "seas_18.se1")
SENSITIVE_WARNING = "sensitive warning for 55.7558 37.6173 at 1990-09-02"

# baseline: normalized ChartArtifact with ADR-0030 lunar-node-axis semantics +
# vendored ephe/*.se1; recalculate only for an intentional contract update.
NATAL_ARTIFACT_JSON_BASELINE_SHA256 = "0ccf14194eeca69de00582b74d51d5c0d9bf2c5d22208d5de58d539a69374c71"


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
        artifact.calculation_version = "other-version"  # type: ignore[misc]


def test_chart_artifact_has_only_canonical_top_level_fields() -> None:
    artifact = _artifact()

    assert set(ChartArtifact.model_fields) == {
        "calculation_key",
        "spec",
        "calculation_version",
        "chart",
    }
    assert not hasattr(artifact, "chart_kind")
    assert not hasattr(artifact, "warnings")
    assert artifact.chart.chart_kind == "natal"
    assert artifact.chart.warnings == (_warning(SENSITIVE_WARNING),)


def test_chart_artifact_rejects_unknown_top_level_fields() -> None:
    payload = _artifact().model_dump(mode="python")
    payload["unexpected"] = "value"

    with pytest.raises(ValidationError, match="unexpected"):
        ChartArtifact.model_validate(payload)


def test_artifact_chart_identity_is_frozen() -> None:
    artifact = _artifact()

    with pytest.raises(ValidationError):
        artifact.chart.longitude = 0.0  # type: ignore[misc]


def test_chart_artifact_validates_identity_fields() -> None:
    chart = _raw_chart()

    with pytest.raises(ValidationError, match="calculation_key"):
        _artifact(chart=chart, key="bad-prefix")

    with pytest.raises(ValidationError, match="calculation_key"):
        _artifact(chart=chart, key=KEY_PREFIX + "f" * 64)

    with pytest.raises(ValidationError, match="calculation_version"):
        _artifact(chart=chart, version="")

    with pytest.raises(ValidationError, match="chart.chart_kind"):
        _artifact(
            chart=chart,
            spec=NatalChartSpec(chart_kind="cosmogram", include=("positions",)),
            key=KEY_PREFIX + "0" * 64,
        )

    with pytest.raises(ValidationError, match="chart block 'positions'"):
        _artifact(chart=chart.model_copy(update={"bodies": None}))

    with pytest.raises(ValidationError, match="house_system"):
        _artifact(chart=chart.model_copy(update={"house_system": "K"}))


def test_chart_artifact_accepts_key_derived_from_chart_spec_and_version() -> None:
    artifact = _artifact()

    expected = calculation_key(
        calculation_input_from_chart(artifact.chart),
        artifact.spec,
        artifact.calculation_version,
    )

    assert artifact.calculation_key == expected


def test_encode_returns_deterministic_gzip_bytes_with_utf8_json_payload() -> None:
    artifact = _artifact()

    first = encode_chart_artifact(artifact)
    second = encode_chart_artifact(artifact)
    raw = gzip.decompress(first)
    payload = json.loads(raw.decode("utf-8"))

    assert isinstance(first, bytes)
    assert first == second
    assert payload["calculation_key"] == artifact.calculation_key
    assert "chart_kind" not in payload
    assert "warnings" not in payload
    assert payload["chart"]["ephemeris"]["mode"] == "files"
    assert "path" not in payload["chart"]["ephemeris"]
    assert "source" not in payload["chart"]["ephemeris"]


def test_reference_natal_artifact_json_matches_normalized_schema_baseline() -> None:
    artifact = _reference_artifact()

    digest = sha256(artifact.model_dump_json().encode("utf-8")).hexdigest()

    assert digest == NATAL_ARTIFACT_JSON_BASELINE_SHA256


def test_reference_artifact_serializes_only_canonical_point_references() -> None:
    payload = _reference_artifact().model_dump(mode="json")
    references = [
        point
        for aspect in payload["chart"]["aspects"]
        for point in (aspect["from_point"], aspect["to_point"])
    ]
    identifiers = {point["body"] for point in references}

    assert "south_node" in payload["chart"]["bodies"]
    assert {"true_node", "mean_apog", "pars_fortune"} <= identifiers
    assert {"south_node", "north_node", "lilith", "pars"}.isdisjoint(identifiers)


def test_decode_rejects_unresolved_chart_point_reference() -> None:
    payload = _reference_artifact().model_dump(mode="json")
    payload["chart"]["aspects"][0]["from_point"]["body"] = "missing_point"
    encoded = gzip.compress(
        json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8"),
        mtime=0,
    )

    with pytest.raises(ChartArtifactDecodeError) as exc_info:
        decode_chart_artifact(encoded)

    assert exc_info.value.reason == "validation"


def test_decode_rejects_south_node_as_relational_endpoint() -> None:
    payload = _reference_artifact().model_dump(mode="json")
    payload["chart"]["aspects"][0]["from_point"]["body"] = "south_node"
    encoded = gzip.compress(
        json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8"),
        mtime=0,
    )

    with pytest.raises(ChartArtifactDecodeError) as exc_info:
        decode_chart_artifact(encoded)

    assert exc_info.value.reason == "validation"


@pytest.mark.parametrize("mutation", ("edge", "point", "max_orb"))
def test_decode_rejects_inconsistent_materialized_configuration(
    mutation: str,
) -> None:
    payload = _reference_artifact().model_dump(mode="json")
    configuration = payload["chart"]["configurations"][0]

    if mutation == "edge":
        configuration["aspects"][0]["orb"] += 0.01
    elif mutation == "point":
        role = next(iter(configuration["points"]))
        configuration["points"][role] = {"chart": "natal", "body": "venus"}
    else:
        configuration["max_orb"] += 0.01

    encoded = gzip.compress(
        json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8"),
        mtime=0,
    )

    with pytest.raises(ChartArtifactDecodeError) as exc_info:
        decode_chart_artifact(encoded)

    assert exc_info.value.reason == "validation"


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

    first.chart.warnings[0].message = "changed"
    second = decode_chart_artifact(encoded)

    assert second.chart.warnings[0].message == SENSITIVE_WARNING


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


def test_decode_rejects_legacy_cosmogram_without_time_uncertainty() -> None:
    artifact = _artifact(chart=_raw_chart(chart_kind="cosmogram"))
    payload = _decoded_json_payload(artifact)
    del payload["chart"]["time_uncertainty"]
    legacy = gzip.compress(
        json.dumps(payload, separators=(",", ":")).encode("utf-8"),
        compresslevel=6,
        mtime=0,
    )

    with pytest.raises(ChartArtifactDecodeError) as exc_info:
        decode_chart_artifact(legacy)

    assert exc_info.value.reason == "validation"


def test_cosmogram_rejects_unresolved_uncertainty_endpoint_with_path() -> None:
    payload = _cosmogram_payload_with_diagnostic()
    payload["time_uncertainty"]["excluded_aspects"][0]["to_point"][
        "body"
    ] = "missing"

    with pytest.raises(
        ValidationError,
        match=r"time_uncertainty\.excluded_aspects\[0\]\.to_point",
    ):
        NatalChart.model_validate(payload)


def test_cosmogram_rejects_duplicate_uncertainty_pair() -> None:
    payload = _cosmogram_payload_with_diagnostic()
    diagnostic = payload["time_uncertainty"]["excluded_aspects"][0]
    payload["time_uncertainty"]["excluded_aspects"].append(diagnostic)

    with pytest.raises(ValidationError, match="duplicates an excluded pair"):
        NatalChart.model_validate(payload)


def test_cosmogram_rejects_diagnostic_for_published_pair() -> None:
    payload = _cosmogram_payload_with_diagnostic()
    payload["aspects"] = [
        {
            "from_point": {"chart": "natal", "body": "sun"},
            "to_point": {"chart": "natal", "body": "moon"},
            "aspect_type": "conjunction",
            "exact_angle": 0.0,
            "orb": 0.5,
            "category": "exact",
            "applying": None,
        }
    ]

    with pytest.raises(ValidationError, match="duplicates a published aspect pair"):
        NatalChart.model_validate(payload)


def test_decode_rejects_legacy_duplicate_top_level_fields() -> None:
    artifact = _artifact()
    payload = _decoded_json_payload(artifact)
    payload["chart_kind"] = artifact.chart.chart_kind
    payload["warnings"] = [warning.model_dump(mode="json") for warning in artifact.chart.warnings]
    legacy = gzip.compress(
        json.dumps(payload, separators=(",", ":")).encode("utf-8"),
        compresslevel=6,
        mtime=0,
    )

    with pytest.raises(ChartArtifactDecodeError) as exc_info:
        decode_chart_artifact(legacy)

    assert exc_info.value.reason == "validation"


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
        cusps=() if chart_kind == "natal" else None,
        angles={} if chart_kind == "natal" else None,
        house_rulers=None,
        interceptions=None,
        aspects=None,
        configurations=None,
        strength=None,
        time_uncertainty=(
            CosmogramTimeUncertainty(
                domain=BirthTimeDomain(
                    ranges=(UtcMinuteRange(first_utc=BASE_UTC, count=1),)
                ),
                excluded_aspects=None,
            )
            if chart_kind == "cosmogram"
            else None
        ),
        warnings=warnings if warnings is not None else (_warning(SENSITIVE_WARNING),),
    )


def _artifact(
    *,
    chart: NatalChart | ArtifactNatalChart | None = None,
    spec: NatalChartSpec | None = None,
    key: str | None = None,
    version: str = "test-version-1",
) -> ChartArtifact:
    chart = chart or _raw_chart()
    spec = spec or NatalChartSpec(
        chart_kind=chart.chart_kind,
        include=("houses", "positions") if chart.chart_kind == "natal" else ("positions",),
    )
    key = key or calculation_key(
        calculation_input_from_chart(chart),
        spec,
        version,
    )
    return ChartArtifact(
        calculation_key=key,
        spec=spec,
        calculation_version=version,
        chart=chart,
    )


def _reference_artifact() -> ChartArtifact:
    configure_ephemeris(REPO_ROOT / "ephe", selena_method="true_perigee")
    chart = calculate_natal(
        REFERENCE["datetime_utc"],
        REFERENCE["latitude"],
        REFERENCE["longitude"],
        chart_kind="natal",
        house_system=REFERENCE["house_system"],
    )
    spec = NatalChartSpec(chart_kind="natal")
    version = "baseline-version"
    return ChartArtifact(
        calculation_key=calculation_key(
            calculation_input_from_chart(chart),
            spec,
            version,
        ),
        spec=spec,
        calculation_version=version,
        chart=chart,
    )


def _warning(message: str) -> CalculationWarning:
    return CalculationWarning(source="fixture", message=message, retflags=None)


def _cosmogram_payload_with_diagnostic() -> dict[str, Any]:
    payload = _raw_chart(chart_kind="cosmogram").model_dump(mode="json")
    payload["bodies"] = {
        "sun": _body("sun", 10.0).model_dump(mode="json"),
        "moon": _body("moon", 10.5).model_dump(mode="json"),
    }
    payload["aspects"] = []
    payload["configurations"] = []
    payload["time_uncertainty"]["excluded_aspects"] = [
        {
            "from_point": {"chart": "natal", "body": "sun"},
            "to_point": {"chart": "natal", "body": "moon"},
            "reasons": ["not_present_for_all_times"],
            "includes_no_aspect": True,
            "possible_aspect_types": ["conjunction"],
            "possible_categories": ["exact"],
        }
    ]
    return payload


def _body(name: str, longitude: float) -> BodyPosition:
    return BodyPosition(
        name=name,
        chart="natal",
        source="swisseph",
        swe_id=0,
        longitude=longitude,
        latitude=0.0,
        distance=1.0,
        longitude_speed=1.0,
        latitude_speed=0.0,
        distance_speed=0.0,
        retrograde=False,
        house=None,
        zodiac=zodiac_position(longitude),
        retflags=0,
    )


def _decoded_json_payload(artifact: ChartArtifact) -> dict[str, Any]:
    return json.loads(gzip.decompress(encode_chart_artifact(artifact)).decode("utf-8"))
