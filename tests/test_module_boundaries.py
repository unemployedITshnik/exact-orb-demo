"""Границы модулей: контрактный слой не тянет native, runtime и edge.

Инвариант I1 из `docs/architecture/service_ready_architecture.md`
(решение — ADR-0021). Проверка идёт в двух режимах, и нужны оба:

* **статический разбор AST** — какие импорты модуль объявляет. Нарушение
  показывается в том файле, где оно написано;
* **импорт в отдельном процессе** — что реально оказывается в `sys.modules`.
  Ловит транзитивные протечки: контрактный модуль импортирует «чистого»
  соседа, а тот тянет `swisseph`.

Подпроцесс обязателен: `tests/conftest.py` импортирует `exact_orb.config`
и инициализирует эфемериды автоматически, поэтому внутри процесса pytest
`swisseph` уже загружен и `sys.modules` ничего не доказывает.

Смысл инварианта сегодня, а не «когда порежем на сервисы»: ключи кэша
и спецификации карт (ADR-0017) должны строиться и проверяться без биндинга
и без процессной конфигурации — иначе слой, который решает «идти ли
в движок», сам обязан поднять движок.
"""

from __future__ import annotations

import ast
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
PACKAGE_ROOT = SRC_ROOT / "exact_orb"

pytestmark = pytest.mark.no_ephemeris_autoinit


# Контрактный слой (L0): типы и чистые функции, разделяемые обеими
# сторонами потенциального шва. `calculation.types`, `calculation.codec`
# и `calculation.engine` сознательно выведены из контрактного слоя:
# первые два зависят от payload/result-моделей, третий зовёт native engine.
CONTRACT_MODULES: tuple[str, ...] = (
    "exact_orb.domain",
    "exact_orb.errors",
    "exact_orb.outcomes",
    "exact_orb.calculation.spec",
    "exact_orb.calculation.keys",
    "exact_orb.calculation.cache",
    "exact_orb.calculation.errors",
    "exact_orb.birth.types",
)

# Native — потому что ключи и спецификации строятся без биндинга.
# Runtime и edge — потому что контракт не зависит от того, кто его исполняет.
FORBIDDEN_FOR_CONTRACTS: tuple[str, ...] = (
    "swisseph",
    "redis",
    "httpx",
    "litellm",
    "exact_orb.cli",
    "exact_orb.config",
    "exact_orb.engine",
    "exact_orb.ephemeris_runtime",
    "exact_orb.llm",
    "exact_orb.logging_setup",
    "exact_orb.orchestration",
    "exact_orb.swiss_backend",
    "exact_orb.tools",
)

# Единственный модуль, которому разрешён `import swisseph`.
SWISSEPH_SEAM = "exact_orb.swiss_backend"
CALCULATION_ENGINE_FORBIDDEN: tuple[str, ...] = (
    "exact_orb.birth.resolver",
    "exact_orb.calculation.artifacts",
    "exact_orb.calculation.cache",
    "exact_orb.calculation.codec",
    "exact_orb.calculation.types",
    "exact_orb.intent",
    "exact_orb.llm",
    "exact_orb.orchestration",
    "exact_orb.tools",
)


def _iter_source_files() -> list[Path]:
    return sorted(PACKAGE_ROOT.rglob("*.py"))


def _module_name(path: Path) -> str:
    parts = list(path.relative_to(SRC_ROOT).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _resolve_relative(module: str, is_package: bool, level: int, name: str | None) -> str:
    parts = module.split(".")
    if not is_package:
        parts = parts[:-1]
    if level > 1:
        parts = parts[: len(parts) - (level - 1)]
    prefix = ".".join(parts)
    if name:
        return f"{prefix}.{name}" if prefix else name
    return prefix


def _declared_imports(path: Path) -> set[str]:
    """Полные имена модулей, импортируемых файлом; относительные разрешены."""

    module = _module_name(path)
    is_package = path.name == "__init__.py"

    names: set[str] = set()
    tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                prefix = _resolve_relative(module, is_package, node.level, node.module)
            else:
                prefix = node.module or ""
            if not prefix:
                continue
            names.add(prefix)
            # `from pkg import mod` неотличим от `from pkg import name`,
            # поэтому кандидат добавляется и проверяется как модуль тоже.
            names.update(f"{prefix}.{alias.name}" for alias in node.names)
    return names


def _violates(imported: str, forbidden: str) -> bool:
    return imported == forbidden or imported.startswith(f"{forbidden}.")


def _contract_source_files() -> list[Path]:
    return [
        path
        for path in _iter_source_files()
        if any(
            _module_name(path) == contract or _module_name(path).startswith(f"{contract}.")
            for contract in CONTRACT_MODULES
        )
    ]


def test_contract_source_files_are_discovered() -> None:
    """Страховка от молчаливо зелёного теста после переезда модулей."""

    modules = {_module_name(path) for path in _contract_source_files()}
    assert {
        "exact_orb.domain",
        "exact_orb.calculation.cache",
        "exact_orb.calculation.errors",
        "exact_orb.calculation.keys",
        "exact_orb.calculation.spec",
    } <= modules


def test_contracts_declare_no_forbidden_imports() -> None:
    """Ни один файл контрактного слоя не объявляет native, runtime или edge импорт."""

    violations = sorted(
        {
            f"{_module_name(path)} -> {forbidden}"
            for path in _contract_source_files()
            for imported in _declared_imports(path)
            for forbidden in FORBIDDEN_FOR_CONTRACTS
            if _violates(imported, forbidden)
        }
    )

    assert not violations, "контрактный слой импортирует запрещённое:\n" + "\n".join(violations)


def test_swisseph_enters_the_process_through_one_module() -> None:
    """`import swisseph` разрешён ровно в одном модуле-шве."""

    importers = sorted(
        _module_name(path)
        for path in _iter_source_files()
        if any(_violates(imported, "swisseph") for imported in _declared_imports(path))
    )

    assert importers == [SWISSEPH_SEAM], (
        f"`import swisseph` разрешён только в {SWISSEPH_SEAM}, найдено: {importers}"
    )


def test_contracts_import_without_pulling_forbidden_modules() -> None:
    """Транзитивная проверка в отдельном процессе."""

    script = "\n".join(
        (
            "import importlib, json, sys",
            f"contracts = {list(CONTRACT_MODULES)!r}",
            f"forbidden = {list(FORBIDDEN_FOR_CONTRACTS)!r}",
            "for name in contracts:",
            "    importlib.import_module(name)",
            "found = sorted(",
            "    name",
            "    for name in sys.modules",
            "    for bad in forbidden",
            "    if name == bad or name.startswith(bad + '.')",
            ")",
            "print(json.dumps(found))",
        )
    )

    env = dict(os.environ)
    env["PYTHONPATH"] = str(SRC_ROOT)
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, (
        "импорт контрактных модулей завершился ошибкой:\n" + completed.stderr
    )

    found = json.loads(completed.stdout.strip().splitlines()[-1])
    assert not found, (
        "импорт контрактного слоя транзитивно загрузил запрещённое: " + ", ".join(found)
    )


def test_calculation_package_import_keeps_artifact_payload_out_of_package_init() -> None:
    """Package-level `calculation` import remains usable without the engine stack."""

    script = "\n".join(
        (
            "import importlib, json, sys",
            "importlib.import_module('exact_orb.calculation')",
            "forbidden = ['swisseph', 'exact_orb.swiss_backend', 'exact_orb.engine', 'exact_orb.config']",
            "found = sorted(",
            "    name",
            "    for name in sys.modules",
            "    for bad in forbidden",
            "    if name == bad or name.startswith(bad + '.')",
            ")",
            "print(json.dumps(found))",
        )
    )

    env = dict(os.environ)
    env["PYTHONPATH"] = str(SRC_ROOT)
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, (
        "импорт exact_orb.calculation завершился ошибкой:\n" + completed.stderr
    )

    found = json.loads(completed.stdout.strip().splitlines()[-1])
    assert not found, (
        "package-level calculation import транзитивно загрузил запрещённое: "
        + ", ".join(found)
    )


def test_calculation_engine_declares_no_artifact_cache_or_edge_imports() -> None:
    """Engine boundary calls the engine, but not artifact/cache orchestration."""

    path = PACKAGE_ROOT / "calculation" / "engine.py"
    violations = sorted(
        {
            forbidden
            for imported in _declared_imports(path)
            for forbidden in CALCULATION_ENGINE_FORBIDDEN
            if _violates(imported, forbidden)
        }
    )

    assert not violations, (
        "calculation.engine импортирует запрещённые соседние слои: "
        + ", ".join(violations)
    )
