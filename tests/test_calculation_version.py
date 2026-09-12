"""Calculation-version fingerprint and cache-invalidation tests."""

from __future__ import annotations

import hashlib
import inspect
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
from typing import Any

import pytest

import exact_orb.calculation.version as version_module
from exact_orb.calculation.artifacts import ChartArtifactResolver
from exact_orb.calculation.cache import InMemoryCalculationCache
from exact_orb.calculation.engine import CalculationResult
from exact_orb.calculation.version import (
    UNRESOLVED_DISTRIBUTION,
    CalculationVersionRecord,
    calculation_version_of,
    compute_calculation_version,
    compute_calculation_version_record,
    log_calculation_version,
)
from exact_orb.engine.charts.natal import calculate_natal
from exact_orb.engine.ephemeris.types import (
    DEFAULT_BODY_IDS,
    DEFAULT_EPHEMERIS_FLAGS,
)
from exact_orb.errors import (
    EphemerisBindingAmbiguousError,
    EphemerisConfigurationError,
)
from tests.fixtures.calculation import (
    calculation_result,
    chart_spec,
    resolved_birth_data,
    run_context,
)


pytestmark = pytest.mark.no_ephemeris_autoinit

EXPECTED_CANONICAL_JSON = (
    '{"body_ids_digest":"eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",'
    '"distribution":"pysweph==2.10.3.6","engine_version":"1",'
    '"ephemeris_files":[["seas_18.se1",'
    '"cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"],'
    '["sepl_18.se1",'
    '"dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd"]],'
    '"ephemeris_flags":2,"native_module_digest":'
    '"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",'
    '"profiles_digest":'
    '"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",'
    '"schema_version":"v1","selena_method":"true_perigee",'
    '"swisseph_version":"2.10.03"}'
)
EXPECTED_VERSION = (
    "eo:calcver:v1:f2f5d3d9975068f9e28c176b18ecafe9b38d2f2b6b076d83a053694e936845cc"
)
# Captured before ADR-0032 and unchanged by ADR-0033; both implementations
# change engine methods, not AspectConfig/ConfigurationConfig/StrengthConfig.
UNCHANGED_PROFILES_DIGEST = (
    "69629f0c755c3a2e74b34d6b9d6b492fba98d2cdfaad00d1b8cb4d89a73dfce7"
)


def test_record_has_stable_golden_fingerprint() -> None:
    record = _sample_record()

    independent_digest = hashlib.sha256(EXPECTED_CANONICAL_JSON.encode("utf-8")).hexdigest()

    assert EXPECTED_VERSION == f"eo:calcver:v1:{independent_digest}"
    assert calculation_version_of(record) == EXPECTED_VERSION
    assert tuple(type(record).model_fields) == (
        "engine_version",
        "profiles_digest",
        "swisseph_version",
        "distribution",
        "native_module_digest",
        "ephemeris_files",
        "selena_method",
        "body_ids_digest",
        "ephemeris_flags",
    )


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("engine_version", "2"),
        ("profiles_digest", "1" * 64),
        ("swisseph_version", "2.10.04"),
        ("distribution", "pyswisseph==2.10.3.2"),
        ("native_module_digest", None),
        ("ephemeris_files", (("seas_18.se1", "9" * 64),)),
        ("selena_method", "mean_perigee"),
        ("body_ids_digest", "8" * 64),
        ("ephemeris_flags", 4),
    ),
)
def test_each_record_component_changes_fingerprint(field: str, value: object) -> None:
    record = _sample_record()
    changed = record.model_copy(update={field: value})

    assert calculation_version_of(changed) != calculation_version_of(record)


def test_collector_populates_all_components(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    ephe = _ephemeris_directory(
        tmp_path / "ephe",
        {"sepl_18.se1": b"planet", "seas_18.se1": b"asteroid"},
    )
    native = _stable_runtime(monkeypatch, tmp_path)

    record = compute_calculation_version_record(
        ephemeris_path=ephe,
        selena_method="true_perigee",
        body_ids={"sun": 0, "moon": 1},
        ephemeris_flags=DEFAULT_EPHEMERIS_FLAGS,
    )

    assert record.engine_version == version_module.engine_module.ENGINE_VERSION
    assert len(record.profiles_digest) == 64
    assert record.swisseph_version == "2.10.03"
    assert record.distribution == "pysweph==2.10.3.6"
    assert record.native_module_digest == _file_digest(native)
    assert record.ephemeris_files == (
        ("seas_18.se1", _bytes_digest(b"asteroid")),
        ("sepl_18.se1", _bytes_digest(b"planet")),
    )
    assert record.selena_method == "true_perigee"
    assert record.body_ids_digest == _json_digest([["moon", 1], ["sun", 0]])
    assert record.ephemeris_flags == DEFAULT_EPHEMERIS_FLAGS


def test_collector_reads_engine_version_at_call_time(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    ephe = _ephemeris_directory(tmp_path / "ephe", {})
    _stable_runtime(monkeypatch, tmp_path)
    before = _record_for(ephe)

    changed_engine_version = f"{before.engine_version}-changed"
    monkeypatch.setattr(
        version_module.engine_module,
        "ENGINE_VERSION",
        changed_engine_version,
    )
    after = _record_for(ephe)

    assert after.engine_version == changed_engine_version
    assert calculation_version_of(after) != calculation_version_of(before)


def test_adr_0033_bumps_only_engine_version_without_changing_profiles(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    ephe = _ephemeris_directory(tmp_path / "ephe", {})
    _stable_runtime(monkeypatch, tmp_path)

    record = _record_for(ephe)
    previous = record.model_copy(update={"engine_version": "4"})

    assert record.engine_version == "5"
    assert record.profiles_digest == UNCHANGED_PROFILES_DIGEST
    assert record.model_dump(exclude={"engine_version"}) == previous.model_dump(
        exclude={"engine_version"}
    )
    assert calculation_version_of(record) != calculation_version_of(previous)


def test_collector_reads_default_profiles_at_call_time(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    ephe = _ephemeris_directory(tmp_path / "ephe", {})
    _stable_runtime(monkeypatch, tmp_path)
    before = _record_for(ephe)
    changed_natal = version_module.AspectConfig.natal(max_orb=6.5)

    monkeypatch.setattr(
        version_module.AspectConfig,
        "natal",
        classmethod(lambda cls, **kwargs: changed_natal),
    )
    after = _record_for(ephe)

    assert after.profiles_digest != before.profiles_digest
    assert calculation_version_of(after) != calculation_version_of(before)


def test_default_ephemeris_flags_are_the_calculate_natal_default() -> None:
    default = inspect.signature(calculate_natal).parameters["ephemeris_flags"].default

    assert DEFAULT_EPHEMERIS_FLAGS == version_module.swiss_backend.swe.FLG_SWIEPH
    assert default == DEFAULT_EPHEMERIS_FLAGS


def test_same_collector_input_is_deterministic_in_one_process(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    ephe = _ephemeris_directory(tmp_path / "ephe", {"sepl_18.se1": b"same"})
    _stable_runtime(monkeypatch, tmp_path)

    first = _version_for(ephe)
    second = _version_for(ephe)

    assert first == second


def test_body_id_insertion_order_does_not_change_fingerprint(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    ephe = _ephemeris_directory(tmp_path / "ephe", {})
    _stable_runtime(monkeypatch, tmp_path)

    first = _version_for(ephe, body_ids={"sun": 0, "moon": 1})
    reordered = _version_for(ephe, body_ids={"moon": 1, "sun": 0})
    changed = _version_for(ephe, body_ids={"moon": 1, "sun": 10})

    assert reordered == first
    assert changed != first


def test_file_byte_change_changes_fingerprint(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    ephe = _ephemeris_directory(tmp_path / "ephe", {"sepl_18.se1": b"before"})
    _stable_runtime(monkeypatch, tmp_path)
    before = _version_for(ephe)

    (ephe / "sepl_18.se1").write_bytes(b"beFore")

    assert _version_for(ephe) != before


def test_creation_order_and_directory_path_do_not_change_fingerprint(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    first = _ephemeris_directory(
        tmp_path / "first",
        {"sepl_18.se1": b"planet", "seas_18.se1": b"asteroid"},
    )
    second = _ephemeris_directory(
        tmp_path / "second",
        {"seas_18.se1": b"asteroid", "sepl_18.se1": b"planet"},
    )
    _stable_runtime(monkeypatch, tmp_path)

    assert _version_for(second) == _version_for(first)


def test_ephemeris_suffix_matching_is_case_insensitive(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    ephe = _ephemeris_directory(
        tmp_path / "ephe",
        {"sepl_18.se1": b"planet", "SEMO_18.SE1": b"moon"},
    )
    _stable_runtime(monkeypatch, tmp_path)

    record = _record_for(ephe)

    assert record.ephemeris_files == (
        ("SEMO_18.SE1", _bytes_digest(b"moon")),
        ("sepl_18.se1", _bytes_digest(b"planet")),
    )


def test_added_ephemeris_file_changes_fingerprint(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    ephe = _ephemeris_directory(tmp_path / "ephe", {"sepl_18.se1": b"planet"})
    _stable_runtime(monkeypatch, tmp_path)
    before = _version_for(ephe)

    (ephe / "extra.se1").write_bytes(b"extra")

    assert _version_for(ephe) != before


def test_empty_directory_has_valid_distinct_fingerprint(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    empty = _ephemeris_directory(tmp_path / "empty", {})
    populated = _ephemeris_directory(tmp_path / "populated", {"sepl_18.se1": b"x"})
    _stable_runtime(monkeypatch, tmp_path)

    empty_record = _record_for(empty)
    populated_record = _record_for(populated)

    assert empty_record.ephemeris_files == ()
    assert calculation_version_of(empty_record) != calculation_version_of(populated_record)


@pytest.mark.parametrize("kind", ("missing", "file"))
def test_invalid_ephemeris_directory_is_typed(
    kind: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _stable_runtime(monkeypatch, tmp_path)
    path = tmp_path / kind
    if kind == "file":
        path.write_bytes(b"not a directory")

    with pytest.raises(EphemerisConfigurationError, match="existing directory"):
        _record_for(path)


def test_ephemeris_read_error_is_typed_without_absolute_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    ephe = _ephemeris_directory(tmp_path / "secret" / "ephe", {"broken.se1": b"x"})
    _stable_runtime(monkeypatch, tmp_path)
    original_open = Path.open

    def failing_open(path: Path, *args: Any, **kwargs: Any) -> Any:
        if path.name == "broken.se1":
            raise OSError("simulated read failure")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", failing_open)

    with pytest.raises(EphemerisConfigurationError) as exc_info:
        _record_for(ephe)

    assert "broken.se1" in str(exc_info.value)
    assert str(ephe) not in str(exc_info.value)
    assert not isinstance(exc_info.value, OSError)


def test_fingerprint_is_independent_of_python_hash_seed(tmp_path: Path) -> None:
    ephe = _ephemeris_directory(
        tmp_path / "ephe",
        {"sepl_18.se1": b"planet", "seas_18.se1": b"asteroid"},
    )
    source_root = Path(__file__).resolve().parents[1] / "src"
    script = (
        "from exact_orb.calculation.version import compute_calculation_version; "
        f"print(compute_calculation_version(ephemeris_path={str(ephe)!r}, "
        "selena_method='true_perigee', body_ids={'sun': 0, 'moon': 1}, "
        "ephemeris_flags=2))"
    )

    versions = []
    for seed in ("1", "2"):
        env = os.environ.copy()
        env["PYTHONPATH"] = str(source_root)
        env["PYTHONHASHSEED"] = seed
        completed = subprocess.run(
            [sys.executable, "-c", script],
            check=True,
            capture_output=True,
            text=True,
            env=env,
        )
        versions.append(completed.stdout.strip())

    assert versions[0] == versions[1]


def test_ambiguous_distribution_is_fail_fast(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    ephe = _ephemeris_directory(tmp_path / "ephe", {})
    _stable_runtime(monkeypatch, tmp_path)
    monkeypatch.setattr(
        version_module.metadata,
        "packages_distributions",
        lambda: {"swisseph": ["pysweph", "pyswisseph"]},
    )

    with pytest.raises(EphemerisBindingAmbiguousError, match="pysweph.*pyswisseph"):
        _record_for(ephe)


def test_duplicate_distribution_name_is_not_ambiguous(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    ephe = _ephemeris_directory(tmp_path / "ephe", {})
    _stable_runtime(monkeypatch, tmp_path)
    monkeypatch.setattr(
        version_module.metadata,
        "packages_distributions",
        lambda: {"swisseph": ["pysweph", "pysweph"]},
    )

    assert _record_for(ephe).distribution == "pysweph==2.10.3.6"


def test_missing_distribution_mapping_uses_stable_marker(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    ephe = _ephemeris_directory(tmp_path / "ephe", {})
    _stable_runtime(monkeypatch, tmp_path)
    monkeypatch.setattr(version_module.metadata, "packages_distributions", lambda: {})

    assert _record_for(ephe).distribution == UNRESOLVED_DISTRIBUTION


def test_broken_distribution_version_metadata_is_typed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    ephe = _ephemeris_directory(tmp_path / "ephe", {})
    _stable_runtime(monkeypatch, tmp_path)

    def missing_version(name: str) -> str:
        raise version_module.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(version_module.metadata, "version", missing_version)

    with pytest.raises(EphemerisConfigurationError, match="pysweph"):
        _record_for(ephe)


@pytest.mark.parametrize("value", (None, "", "   ", 21003))
def test_invalid_swisseph_version_is_typed(
    value: object,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    ephe = _ephemeris_directory(tmp_path / "ephe", {})
    _stable_runtime(monkeypatch, tmp_path)
    monkeypatch.setattr(version_module.swiss_backend.swe, "version", value)

    with pytest.raises(EphemerisConfigurationError, match="library version"):
        _record_for(ephe)


def test_missing_swisseph_version_is_typed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    ephe = _ephemeris_directory(tmp_path / "ephe", {})
    _stable_runtime(monkeypatch, tmp_path)
    monkeypatch.delattr(version_module.swiss_backend.swe, "version")

    with pytest.raises(EphemerisConfigurationError, match="library version"):
        _record_for(ephe)


def test_native_module_byte_change_changes_fingerprint(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    ephe = _ephemeris_directory(tmp_path / "ephe", {})
    native = _stable_runtime(monkeypatch, tmp_path)
    before = _record_for(ephe)

    native.write_bytes(b"native-v2")
    after = _record_for(ephe)

    assert after.native_module_digest != before.native_module_digest
    assert calculation_version_of(after) != calculation_version_of(before)


@pytest.mark.parametrize("origin", (None, "swisseph.py"))
def test_unavailable_or_non_native_module_weakens_record(
    origin: str | None,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    ephe = _ephemeris_directory(tmp_path / "ephe", {})
    _stable_runtime(monkeypatch, tmp_path)
    monkeypatch.setattr(version_module.swiss_backend.swe, "__file__", origin)

    assert _record_for(ephe).native_module_digest is None


def test_unreadable_native_module_weakens_record(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    ephe = _ephemeris_directory(tmp_path / "ephe", {})
    native = _stable_runtime(monkeypatch, tmp_path)
    original_open = Path.open

    def failing_open(path: Path, *args: Any, **kwargs: Any) -> Any:
        if path == native:
            raise OSError("simulated native read failure")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", failing_open)

    assert _record_for(ephe).native_module_digest is None


def test_compute_functions_do_not_log(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    ephe = _ephemeris_directory(tmp_path / "ephe", {})
    _stable_runtime(monkeypatch, tmp_path)
    caplog.set_level(logging.DEBUG, logger=version_module.__name__)

    record = _record_for(ephe)
    calculation_version_of(record)
    _version_for(ephe)

    assert caplog.records == []


def test_startup_log_contains_record_without_paths(
    caplog: pytest.LogCaptureFixture,
) -> None:
    record = _sample_record()
    caplog.set_level(logging.INFO, logger=version_module.__name__)

    log_calculation_version(record)

    records = [item for item in caplog.records if item.name == version_module.__name__]
    assert len(records) == 1
    assert records[0].levelno == logging.INFO
    message = records[0].getMessage()
    assert "calculation_version_computed" in message
    assert EXPECTED_VERSION in message
    for field in type(record).model_fields:
        assert f"{field}=" in message
    assert "C:\\secret\\ephe" not in message
    assert "/secret/ephe" not in message


def test_weakened_startup_log_is_explicit(
    caplog: pytest.LogCaptureFixture,
) -> None:
    record = _sample_record().model_copy(update={"native_module_digest": None})
    caplog.set_level(logging.INFO, logger=version_module.__name__)

    log_calculation_version(record)

    records = [item for item in caplog.records if item.name == version_module.__name__]
    assert [item.levelno for item in records] == [logging.INFO, logging.WARNING]
    assert "calculation_version_weakened" in records[1].getMessage()
    assert "reason=native_module_digest_unavailable" in records[1].getMessage()


async def test_ephemeris_change_misses_shared_cache_with_two_resolvers(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    first_ephe = _ephemeris_directory(tmp_path / "first", {"sepl_18.se1": b"old"})
    second_ephe = _ephemeris_directory(tmp_path / "second", {"sepl_18.se1": b"new"})
    _stable_runtime(monkeypatch, tmp_path)
    old_version = _version_for(first_ephe)
    new_version = _version_for(second_ephe)
    cache = InMemoryCalculationCache(max_entries=10, ttl_seconds=None)
    engine = CountingEngine()
    old_resolver = ChartArtifactResolver(
        cache=cache,
        engine=engine,
        version=old_version,
        degraded_log_interval_s=60.0,
    )
    new_resolver = ChartArtifactResolver(
        cache=cache,
        engine=engine,
        version=new_version,
        degraded_log_interval_s=60.0,
    )
    spec = chart_spec()
    resolved = resolved_birth_data()

    old_artifact = await old_resolver.ensure_chart(spec, resolved, run=run_context())
    new_artifact = await new_resolver.ensure_chart(spec, resolved, run=run_context())

    assert old_version != new_version
    assert old_artifact.calculation_version == old_version
    assert new_artifact.calculation_version == new_version
    assert old_artifact.calculation_key != new_artifact.calculation_key
    assert engine.calls == 2
    assert await cache.get(old_artifact.calculation_key) is not None
    assert await cache.get(new_artifact.calculation_key) is not None


class CountingEngine:
    def __init__(self) -> None:
        self.calls = 0

    async def calculate(self, spec: Any, resolved: Any, *, run: Any) -> CalculationResult:
        self.calls += 1
        return calculation_result(chart_kind=spec.chart_kind)


def _sample_record() -> CalculationVersionRecord:
    return CalculationVersionRecord(
        engine_version="1",
        profiles_digest="a" * 64,
        swisseph_version="2.10.03",
        distribution="pysweph==2.10.3.6",
        native_module_digest="b" * 64,
        ephemeris_files=(
            ("seas_18.se1", "c" * 64),
            ("sepl_18.se1", "d" * 64),
        ),
        selena_method="true_perigee",
        body_ids_digest="e" * 64,
        ephemeris_flags=2,
    )


def _stable_runtime(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    native = tmp_path / "swisseph.pyd"
    native.write_bytes(b"native-v1")
    monkeypatch.setattr(version_module.swiss_backend.swe, "version", "2.10.03")
    monkeypatch.setattr(version_module.swiss_backend.swe, "__file__", str(native))
    monkeypatch.setattr(
        version_module.metadata,
        "packages_distributions",
        lambda: {"swisseph": ["pysweph"]},
    )
    monkeypatch.setattr(version_module.metadata, "version", lambda name: "2.10.3.6")
    return native


def _ephemeris_directory(path: Path, files: dict[str, bytes]) -> Path:
    path.mkdir(parents=True)
    for name, content in files.items():
        (path / name).write_bytes(content)
    return path


def _record_for(
    ephemeris_path: Path,
    *,
    body_ids: dict[str, int] | None = None,
) -> CalculationVersionRecord:
    return compute_calculation_version_record(
        ephemeris_path=ephemeris_path,
        selena_method="true_perigee",
        body_ids=body_ids or {"sun": 0, "moon": 1},
        ephemeris_flags=DEFAULT_EPHEMERIS_FLAGS,
    )


def _version_for(
    ephemeris_path: Path,
    *,
    body_ids: dict[str, int] | None = None,
) -> str:
    return compute_calculation_version(
        ephemeris_path=ephemeris_path,
        selena_method="true_perigee",
        body_ids=body_ids or {"sun": 0, "moon": 1},
        ephemeris_flags=DEFAULT_EPHEMERIS_FLAGS,
    )


def _file_digest(path: Path) -> str:
    return _bytes_digest(path.read_bytes())


def _bytes_digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _json_digest(payload: object) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
