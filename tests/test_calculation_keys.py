"""Calculation key contract tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys

import pytest
from pydantic import ValidationError

from exact_orb.birth.types import ResolutionWarning, ResolvedBirthData
from exact_orb.calculation import (
    CalculationInput,
    NatalChartSpec,
    calculation_input_from,
    calculation_key,
    canonical_key_payload,
)
from exact_orb.domain import DEFAULT_INCLUDE_BY_CHART_KIND, INCLUDE_BLOCKS, RulershipScheme


pytestmark = pytest.mark.no_ephemeris_autoinit

VERSION = "test-version-1"
BASE_UTC = datetime(1990, 9, 2, 10, 30, 45, tzinfo=timezone.utc)
BASE_LATITUDE = 55.755825
BASE_LONGITUDE = 37.617299

# Change these constants only for an intentional key-schema change, together
# with a schema_version bump or an ADR note. Do not refresh them for incidental
# serializer drift.
EXPECTED_CANONICAL_JSON = (
    '{"calculation_input":{"latitude":55.755825,"longitude":37.617299,'
    '"utc_datetime":"1990-09-02T10:30:45Z"},"calculation_version":"test-version-1",'
    '"schema_version":"v1","spec":{"chart_kind":"natal","house_system":"P",'
    '"include":["aspects","configurations","houses","positions","rulers","strength"],'
    '"near_interception_threshold":1.0,"rulership":"combined","technique":"natal"}}'
)
EXPECTED_CALCULATION_KEY = (
    "eo:calc:v1:6080cb27406936a135d6c767e099e2670b1262cb2ad73fdcf7ebc980f4da0e4b"
)


def test_chart_spec_defaults_and_technique_are_serialized() -> None:
    spec = NatalChartSpec(chart_kind="natal")

    assert spec.technique == "natal"
    assert spec.include == DEFAULT_INCLUDE_BY_CHART_KIND["natal"]
    assert spec.model_dump(mode="json")["technique"] == "natal"


def test_cosmogram_default_include_is_cosmogram_specific() -> None:
    spec = NatalChartSpec(chart_kind="cosmogram")

    assert spec.include == DEFAULT_INCLUDE_BY_CHART_KIND["cosmogram"]
    assert "houses" not in spec.include


def test_include_contract_types_are_stable() -> None:
    assert isinstance(INCLUDE_BLOCKS, frozenset)
    assert DEFAULT_INCLUDE_BY_CHART_KIND["natal"] == (
        "aspects",
        "configurations",
        "houses",
        "positions",
        "rulers",
        "strength",
    )
    assert DEFAULT_INCLUDE_BY_CHART_KIND["cosmogram"] == (
        "aspects",
        "configurations",
        "positions",
    )


def test_canonical_payload_contains_technique() -> None:
    payload = canonical_key_payload(_base_input(), NatalChartSpec(chart_kind="natal"), VERSION)

    assert payload["spec"]["technique"] == "natal"


def test_projection_ignores_non_key_birth_resolution_fields() -> None:
    base = ResolvedBirthData(
        utc_datetime=BASE_UTC,
        latitude=BASE_LATITUDE,
        longitude=BASE_LONGITUDE,
        tz_id="UTC",
        utc_offset_seconds=0,
        canonical_place="Reference place",
        time_unknown=False,
    )
    changed = ResolvedBirthData(
        utc_datetime=BASE_UTC,
        latitude=BASE_LATITUDE,
        longitude=BASE_LONGITUDE,
        tz_id="Europe/Moscow",
        utc_offset_seconds=10800,
        canonical_place="Another displayed place",
        time_unknown=True,
        warnings=(
            ResolutionWarning(
                source="place",
                code="ambiguous",
                message="Different resolution metadata must not affect the key",
            ),
        ),
    )
    spec = NatalChartSpec(chart_kind="natal")

    assert calculation_input_from(base) == calculation_input_from(changed)
    assert _key(calculation_input_from(base), spec) == _key(calculation_input_from(changed), spec)


def test_same_semantic_inputs_produce_same_key() -> None:
    first_input = CalculationInput(
        utc_datetime=BASE_UTC.replace(microsecond=999999),
        latitude=10.0000004,
        longitude=-4e-7,
    )
    second_input = CalculationInput(
        utc_datetime=BASE_UTC,
        latitude=10.0,
        longitude=0.0,
    )
    first_spec = NatalChartSpec(
        chart_kind="natal",
        include=("strength", "rulers", "positions", "houses", "configurations", "aspects", "positions"),
        house_system="p",
    )
    second_spec = NatalChartSpec(chart_kind="natal", house_system="P")

    assert _key(first_input, first_spec) == _key(second_input, second_spec)


def test_semantic_changes_change_key() -> None:
    base_input = _base_input()
    base_spec = NatalChartSpec(chart_kind="natal")
    base_key = _key(base_input, base_spec)

    cases = [
        (CalculationInput(utc_datetime=BASE_UTC + timedelta(seconds=1), latitude=BASE_LATITUDE, longitude=BASE_LONGITUDE), base_spec, VERSION),
        (CalculationInput(utc_datetime=BASE_UTC, latitude=BASE_LATITUDE + 0.000001, longitude=BASE_LONGITUDE), base_spec, VERSION),
        (CalculationInput(utc_datetime=BASE_UTC, latitude=BASE_LATITUDE, longitude=BASE_LONGITUDE + 0.000001), base_spec, VERSION),
        (base_input, NatalChartSpec(chart_kind="cosmogram"), VERSION),
        (base_input, NatalChartSpec(chart_kind="natal", include=("houses", "positions")), VERSION),
        (base_input, NatalChartSpec(chart_kind="natal", house_system="K"), VERSION),
        (base_input, NatalChartSpec(chart_kind="natal", rulership=RulershipScheme.MODERN), VERSION),
        (base_input, NatalChartSpec(chart_kind="natal", near_interception_threshold=2.0), VERSION),
        (base_input, base_spec, "test-version-2"),
    ]

    for calc_input, spec, version in cases:
        assert calculation_key(calc_input, spec, version) != base_key


def test_include_permutation_does_not_change_key() -> None:
    first = NatalChartSpec(
        chart_kind="natal",
        include=("strength", "rulers", "positions", "houses", "configurations", "aspects"),
    )
    second = NatalChartSpec(
        chart_kind="natal",
        include=("aspects", "configurations", "houses", "positions", "rulers", "strength"),
    )

    assert _key(_base_input(), first) == _key(_base_input(), second)


def test_house_system_case_is_canonical_for_value_and_key() -> None:
    lower = NatalChartSpec(chart_kind="natal", house_system="p")
    upper = NatalChartSpec(chart_kind="natal", house_system="P")

    assert lower.house_system == "P"
    assert _key(_base_input(), lower) == _key(_base_input(), upper)


def test_key_format_is_exact() -> None:
    key = _key(_base_input(), NatalChartSpec(chart_kind="natal"))

    assert key.startswith("eo:calc:v1:")
    assert re.fullmatch(r"eo:calc:v1:[0-9a-f]{64}", key)


def test_datetime_must_be_utc_and_microseconds_are_truncated() -> None:
    with pytest.raises(ValidationError, match="timezone-aware UTC"):
        CalculationInput(utc_datetime=datetime(1990, 9, 2, 10, 30), latitude=0.0, longitude=0.0)

    with pytest.raises(ValidationError, match="timezone-aware UTC"):
        CalculationInput(
            utc_datetime=datetime(
                1990,
                9,
                2,
                10,
                30,
                tzinfo=timezone(timedelta(hours=3)),
            ),
            latitude=0.0,
            longitude=0.0,
        )

    calc_input = CalculationInput(
        utc_datetime=BASE_UTC.replace(microsecond=123456),
        latitude=0.0,
        longitude=0.0,
    )

    assert calc_input.utc_datetime == BASE_UTC
    assert canonical_key_payload(calc_input, NatalChartSpec(chart_kind="natal"), VERSION)[
        "calculation_input"
    ]["utc_datetime"] == "1990-09-02T10:30:45Z"


def test_coordinate_half_up_quantization_uses_decimal_string_semantics() -> None:
    calc_input = CalculationInput(
        utc_datetime=BASE_UTC,
        latitude=0.1234565,
        longitude=2.6754995,
    )

    assert calc_input.latitude == 0.123457
    assert calc_input.longitude == 2.6755


def test_coordinates_reject_non_finite_and_out_of_range_values() -> None:
    for value in (float("nan"), float("inf"), -float("inf"), -90.000001, 90.000001):
        with pytest.raises(ValidationError):
            CalculationInput(utc_datetime=BASE_UTC, latitude=value, longitude=0.0)

    for value in (float("nan"), float("inf"), -float("inf"), -180.000001, 180.000001, 200.0):
        with pytest.raises(ValidationError):
            CalculationInput(utc_datetime=BASE_UTC, latitude=0.0, longitude=value)


def test_longitude_boundary_is_canonical_without_modulo() -> None:
    east = CalculationInput(utc_datetime=BASE_UTC, latitude=0.0, longitude=180.0)
    west = CalculationInput(utc_datetime=BASE_UTC, latitude=0.0, longitude=-180.0)

    assert east.longitude == -180.0
    assert _key(east, NatalChartSpec(chart_kind="natal")) == _key(west, NatalChartSpec(chart_kind="natal"))


def test_negative_zero_is_normalized_in_model_and_key() -> None:
    negative_zero = CalculationInput(utc_datetime=BASE_UTC, latitude=-4e-7, longitude=-0.0)
    zero = CalculationInput(utc_datetime=BASE_UTC, latitude=0.0, longitude=0.0)

    assert negative_zero.latitude == 0.0
    assert math.copysign(1.0, negative_zero.latitude) == 1.0
    assert negative_zero.longitude == 0.0
    assert math.copysign(1.0, negative_zero.longitude) == 1.0
    assert _key(negative_zero, NatalChartSpec(chart_kind="natal")) == _key(zero, NatalChartSpec(chart_kind="natal"))


def test_include_validation_and_chart_kind_gating() -> None:
    with pytest.raises(ValidationError, match="unknown include"):
        NatalChartSpec(chart_kind="natal", include=("houses", "positions", "bogus"))

    with pytest.raises(ValidationError, match=r"chart_kind.*houses"):
        NatalChartSpec(chart_kind="natal", include=("positions",))

    with pytest.raises(ValidationError, match=r"cosmogram.*houses"):
        NatalChartSpec(chart_kind="cosmogram", include=("positions", "houses"))

    with pytest.raises(ValidationError, match=r"rulers.*houses"):
        NatalChartSpec(chart_kind="cosmogram", include=("positions", "rulers"))

    with pytest.raises(ValidationError, match=r"strength.*houses"):
        NatalChartSpec(chart_kind="cosmogram", include=("positions", "strength"))

    with pytest.raises(ValidationError, match=r"rulers.*houses"):
        NatalChartSpec(chart_kind="natal", include=("positions", "rulers"))

    with pytest.raises(ValidationError, match=r"strength.*houses"):
        NatalChartSpec(chart_kind="natal", include=("positions", "strength"))


def test_models_are_frozen() -> None:
    calc_input = _base_input()
    spec = NatalChartSpec(chart_kind="natal")

    with pytest.raises(ValidationError):
        calc_input.latitude = 0.0
    with pytest.raises(ValidationError):
        spec.house_system = "K"


def test_golden_canonical_json_and_key_are_stable() -> None:
    calc_input = _base_input()
    spec = NatalChartSpec(chart_kind="natal", house_system="p")
    canonical_json = json.dumps(
        canonical_key_payload(calc_input, spec, VERSION),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )

    assert canonical_json == EXPECTED_CANONICAL_JSON
    assert calculation_key(calc_input, spec, VERSION) == EXPECTED_CALCULATION_KEY


def test_python_hash_seed_does_not_affect_key(tmp_path: Path) -> None:
    assert _subprocess_key("1", tmp_path) == _subprocess_key("2", tmp_path)


def test_key_layer_imports_without_swiss_backend_or_upper_layers(tmp_path: Path) -> None:
    script = (
        "import json, sys; "
        "import exact_orb.domain, exact_orb.calculation.keys, exact_orb.calculation.spec, exact_orb.calculation; "
        "forbidden = ['swisseph', 'exact_orb.swiss_backend', 'exact_orb.engine', 'exact_orb.config']; "
        "loaded = [name for name in forbidden if name in sys.modules]; "
        "print(json.dumps(loaded)); "
        "raise SystemExit(1 if loaded else 0)"
    )
    completed = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        env=_subprocess_env(),
        cwd=tmp_path,
        check=False,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert completed.stdout.strip() == "[]"


def test_key_can_be_computed_without_ephe_directory(tmp_path: Path) -> None:
    completed = subprocess.run(
        [sys.executable, "-c", _key_script()],
        capture_output=True,
        text=True,
        env=_subprocess_env(),
        cwd=tmp_path,
        check=False,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert completed.stdout.strip().startswith("eo:calc:v1:")


def _base_input() -> CalculationInput:
    return CalculationInput(
        utc_datetime=BASE_UTC,
        latitude=BASE_LATITUDE,
        longitude=BASE_LONGITUDE,
    )


def _key(calc_input: CalculationInput, spec: NatalChartSpec) -> str:
    return calculation_key(calc_input, spec, VERSION)


def _subprocess_key(seed: str, cwd: Path) -> str:
    env = _subprocess_env()
    env["PYTHONHASHSEED"] = seed
    completed = subprocess.run(
        [sys.executable, "-c", _key_script()],
        capture_output=True,
        text=True,
        env=env,
        cwd=cwd,
        check=False,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    return completed.stdout.strip()


def _key_script() -> str:
    return (
        "from datetime import datetime, timezone; "
        "from exact_orb.calculation import CalculationInput, NatalChartSpec, calculation_key; "
        "calc_input = CalculationInput("
        "utc_datetime=datetime(1990, 9, 2, 10, 30, 45, tzinfo=timezone.utc), "
        "latitude=55.755825, longitude=37.617299); "
        "spec = NatalChartSpec(chart_kind='natal', "
        "include={'strength', 'rulers', 'positions', 'houses', 'configurations', 'aspects'}, "
        "house_system='p'); "
        "print(calculation_key(calc_input, spec, 'test-version-1'))"
    )


def _subprocess_env() -> dict[str, str]:
    src = Path(__file__).resolve().parents[1] / "src"
    env = os.environ.copy()
    env["PYTHONPATH"] = str(src) + os.pathsep + env.get("PYTHONPATH", "")
    return env
