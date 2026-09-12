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


SESSION_CONTRACT_MODULES: tuple[str, ...] = (
    "exact_orb.session.errors",
    "exact_orb.session.state",
    "exact_orb.session.outcomes",
    "exact_orb.session.dialog",
    "exact_orb.session.store",
    "exact_orb.session.persistence",
)

SESSION_ADAPTER_MODULES: tuple[str, ...] = (
    "exact_orb.session.adapters",
    "exact_orb.session.adapters._time",
    "exact_orb.session.adapters.in_memory",
    "exact_orb.session.adapters.sqlite",
)

SESSION_SERVICE_MODULES: tuple[str, ...] = (
    "exact_orb.session.context",
)

RESEARCH_PACKAGE_MODULE = "exact_orb.research"

RESEARCH_CONTRACT_MODULES: tuple[str, ...] = (
    "exact_orb.research.errors",
    "exact_orb.research.models",
    "exact_orb.research.outcomes",
    "exact_orb.research.corpus",
)

RESEARCH_PROJECTION_MODULES: tuple[str, ...] = (
    "exact_orb.research.projection",
)

RESEARCH_ADAPTER_MODULES: tuple[str, ...] = (
    "exact_orb.research.adapters",
    "exact_orb.research.adapters.in_memory",
)

RESEARCH_RUNTIME_BASE_REQUIRED: frozenset[str] = frozenset(
    {"exact_orb", RESEARCH_PACKAGE_MODULE, *RESEARCH_CONTRACT_MODULES}
)

# Eager imports below calculation.types are existing transitive engine fallout.
# They are an upper bound, not permission for Research contracts or adapters.
RESEARCH_PROJECTION_RUNTIME_KNOWN_TRANSITIVE_DEBT: frozenset[str] = frozenset(
    {
        "exact_orb.birth",
        "exact_orb.birth.places",
        "exact_orb.birth.resolver",
        "exact_orb.birth.types",
        "exact_orb.birth.tz",
        "exact_orb.calculation",
        "exact_orb.calculation.cache",
        "exact_orb.calculation.keys",
        "exact_orb.calculation.spec",
        "exact_orb.config",
        "exact_orb.domain",
        "exact_orb.engine.aspects",
        "exact_orb.engine.aspects.categories",
        "exact_orb.engine.aspects.finder",
        "exact_orb.engine.aspects.orbs",
        "exact_orb.engine.aspects.types",
        "exact_orb.engine.charts",
        "exact_orb.engine.charts.natal",
        "exact_orb.engine.configurations",
        "exact_orb.engine.configurations.finder",
        "exact_orb.engine.configurations.integrity",
        "exact_orb.engine.configurations.patterns",
        "exact_orb.engine.configurations.patterns.bisextile",
        "exact_orb.engine.configurations.patterns.common",
        "exact_orb.engine.configurations.patterns.grand_cross",
        "exact_orb.engine.configurations.patterns.grand_trine",
        "exact_orb.engine.configurations.patterns.t_square",
        "exact_orb.engine.configurations.patterns.trapeze",
        "exact_orb.engine.configurations.patterns.yod",
        "exact_orb.engine.configurations.types",
        "exact_orb.engine.ephemeris",
        "exact_orb.engine.ephemeris.calc",
        "exact_orb.engine.ephemeris.runtime",
        "exact_orb.engine.ephemeris.types",
        "exact_orb.engine.strength",
        "exact_orb.engine.strength.accidental",
        "exact_orb.engine.strength.balance",
        "exact_orb.engine.strength.degrees",
        "exact_orb.engine.strength.dignities",
        "exact_orb.engine.strength.dispositors",
        "exact_orb.engine.strength.lunar_phase",
        "exact_orb.engine.strength.types",
        "exact_orb.ephemeris_runtime",
        "exact_orb.errors",
        "exact_orb.outcomes",
        "exact_orb.run_context",
    }
)

RESEARCH_FORBIDDEN_IMPORTS: tuple[str, ...] = (
    "sqlite3",
    "exact_orb.session",
    "exact_orb.application",
    "exact_orb.agent",
    "exact_orb.orchestration",
    "exact_orb.tools",
    "exact_orb.llm",
    "exact_orb.cli",
    "exact_orb.edge",
)

SESSION_RUNTIME_BASE_REQUIRED: frozenset[str] = frozenset(
    {
        "exact_orb",
        "exact_orb.session",
        "exact_orb.session.errors",
        "exact_orb.session.state",
        "exact_orb.session.outcomes",
        "exact_orb.session.dialog",
        "exact_orb.session.store",
        "exact_orb.session.persistence",
        "exact_orb.birth",
        "exact_orb.birth.types",
        "exact_orb.calculation",
        "exact_orb.calculation.spec",
        "exact_orb.domain",
    }
)

# These modules are current eager-package fallout, not allowed architectural
# dependencies. Removing any of them is compatible with this upper-bound test.
SESSION_RUNTIME_KNOWN_TRANSITIVE_DEBT: frozenset[str] = frozenset(
    {
        "exact_orb.birth.places",
        "exact_orb.birth.resolver",
        "exact_orb.birth.tz",
        "exact_orb.calculation.cache",
        "exact_orb.calculation.keys",
        "exact_orb.outcomes",
        "exact_orb.run_context",
    }
)

SESSION_ALLOWED_PROJECT_IMPORTS: tuple[str, ...] = (
    "exact_orb.session.",
    "exact_orb.birth.types",
    "exact_orb.calculation.spec",
)

SESSION_FORBIDDEN_AT_RUNTIME: tuple[str, ...] = (
    "exact_orb.session.adapters",
    "exact_orb.session.context",
    "swisseph",
    "sqlite3",
    "aiosqlite",
    "redis",
    "exact_orb.swiss_backend",
    "exact_orb.engine",
    "exact_orb.config",
    "exact_orb.calculation.artifacts",
    "exact_orb.calculation.engine",
    "exact_orb.calculation.types",
    "exact_orb.calculation.codec",
    "exact_orb.application",
    "exact_orb.agent",
    "exact_orb.orchestration",
    "exact_orb.tools",
    "exact_orb.llm",
    "exact_orb.cli",
)

SESSION_ADAPTER_FORBIDDEN_AT_RUNTIME: tuple[str, ...] = tuple(
    module
    for module in SESSION_FORBIDDEN_AT_RUNTIME
    if module != "exact_orb.session.adapters"
)

SESSION_SQLITE_ADAPTER_FORBIDDEN_AT_RUNTIME: tuple[str, ...] = tuple(
    module
    for module in SESSION_ADAPTER_FORBIDDEN_AT_RUNTIME
    if module != "sqlite3"
)

SESSION_SERVICE_FORBIDDEN_AT_RUNTIME: tuple[str, ...] = tuple(
    module
    for module in SESSION_FORBIDDEN_AT_RUNTIME
    if module != "exact_orb.session.context"
)

SESSION_ADAPTER_FORBIDDEN_IMPORTS: tuple[str, ...] = (
    "os",
    "random",
    "time",
    "uuid",
    "exact_orb.birth",
    "exact_orb.calculation",
    "exact_orb.config",
    "exact_orb.application",
    "exact_orb.agent",
    "exact_orb.orchestration",
    "exact_orb.tools",
    "exact_orb.llm",
    "exact_orb.cli",
    "exact_orb.engine",
    "exact_orb.swiss_backend",
)

SESSION_SERVICE_FORBIDDEN_IMPORTS: tuple[str, ...] = (
    "os",
    "random",
    "time",
    "uuid",
    "exact_orb.session.adapters",
    "exact_orb.birth",
    "exact_orb.calculation",
    "exact_orb.config",
    "exact_orb.application",
    "exact_orb.agent",
    "exact_orb.orchestration",
    "exact_orb.tools",
    "exact_orb.llm",
    "exact_orb.cli",
    "exact_orb.engine",
    "exact_orb.swiss_backend",
)


# Контрактный слой (L0): типы и чистые функции, разделяемые обеими
# сторонами потенциального шва. `calculation.types`, `calculation.codec`,
# `calculation.engine` и `calculation.artifacts` сознательно выведены из
# контрактного слоя: первые два зависят от payload/result-моделей, engine
# зовёт native engine, artifacts оркестрирует engine/cache.
CONTRACT_MODULES: tuple[str, ...] = (
    "exact_orb.domain",
    "exact_orb.errors",
    "exact_orb.outcomes",
    "exact_orb.calculation.spec",
    "exact_orb.calculation.keys",
    "exact_orb.calculation.cache",
    "exact_orb.calculation.errors",
    "exact_orb.birth.types",
    *SESSION_CONTRACT_MODULES,
    *RESEARCH_CONTRACT_MODULES,
)

# Native — потому что ключи и спецификации строятся без биндинга.
# Runtime и edge — потому что контракт не зависит от того, кто его исполняет.
FORBIDDEN_FOR_CONTRACTS: tuple[str, ...] = (
    "swisseph",
    "sqlite3",
    "aiosqlite",
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

CALCULATION_ARTIFACTS_FORBIDDEN: tuple[str, ...] = (
    "exact_orb.cli",
    "exact_orb.interpretation",
    "exact_orb.llm",
    "exact_orb.orchestration",
    "exact_orb.tools",
)

CALCULATION_CACHE_FORBIDDEN: tuple[str, ...] = (
    "exact_orb.calculation.artifacts",
    "exact_orb.calculation.codec",
    "exact_orb.calculation.engine",
    "exact_orb.calculation.types",
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


def _session_contract_source_files() -> list[Path]:
    return [
        path
        for path in _iter_source_files()
        if _module_name(path) in SESSION_CONTRACT_MODULES
    ]


def _session_adapter_source_files() -> list[Path]:
    return sorted((PACKAGE_ROOT / "session" / "adapters").rglob("*.py"))


def _session_service_source_files() -> list[Path]:
    contract_modules = {"exact_orb.session", *SESSION_CONTRACT_MODULES}
    return sorted(
        path
        for path in (PACKAGE_ROOT / "session").glob("*.py")
        if _module_name(path) not in contract_modules
    )


def _research_source_files(modules: tuple[str, ...]) -> list[Path]:
    return [path for path in _iter_source_files() if _module_name(path) in modules]


def _find_import_cycle(graph: dict[str, set[str]]) -> tuple[str, ...] | None:
    """Return one declared-import cycle, including its repeated start node."""

    visited: set[str] = set()
    active: list[str] = []
    active_set: set[str] = set()

    def visit(module: str) -> tuple[str, ...] | None:
        if module in active_set:
            start = active.index(module)
            return tuple((*active[start:], module))
        if module in visited:
            return None

        active.append(module)
        active_set.add(module)
        for dependency in sorted(graph[module]):
            cycle = visit(dependency)
            if cycle is not None:
                return cycle
        active.pop()
        active_set.remove(module)
        visited.add(module)
        return None

    for module in sorted(graph):
        cycle = visit(module)
        if cycle is not None:
            return cycle
    return None


def _uses_type_checking_guard(path: Path) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
    return any(
        (isinstance(node, ast.Name) and node.id == "TYPE_CHECKING")
        or (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "typing"
            and node.attr == "TYPE_CHECKING"
        )
        for node in ast.walk(tree)
    )


def test_contract_source_files_are_discovered() -> None:
    """Страховка от молчаливо зелёного теста после переезда модулей."""

    modules = {_module_name(path) for path in _contract_source_files()}
    assert {
        "exact_orb.domain",
        "exact_orb.calculation.cache",
        "exact_orb.calculation.errors",
        "exact_orb.calculation.keys",
        "exact_orb.calculation.spec",
        *SESSION_CONTRACT_MODULES,
        *RESEARCH_CONTRACT_MODULES,
    } <= modules


def test_research_source_groups_are_discovered() -> None:
    assert {
        _module_name(path)
        for path in _research_source_files(RESEARCH_CONTRACT_MODULES)
    } == set(RESEARCH_CONTRACT_MODULES)
    assert {
        _module_name(path)
        for path in _research_source_files(RESEARCH_PROJECTION_MODULES)
    } == set(RESEARCH_PROJECTION_MODULES)
    assert {
        _module_name(path)
        for path in _research_source_files(RESEARCH_ADAPTER_MODULES)
    } == set(RESEARCH_ADAPTER_MODULES)
    assert {
        _module_name(path)
        for path in _research_source_files((RESEARCH_PACKAGE_MODULE,))
    } == {RESEARCH_PACKAGE_MODULE}


def test_research_contracts_only_import_research_project_modules() -> None:
    violations = sorted(
        {
            f"{_module_name(path)} -> {imported}"
            for path in _research_source_files(
                (RESEARCH_PACKAGE_MODULE, *RESEARCH_CONTRACT_MODULES)
            )
            for imported in _declared_imports(path)
            if imported.startswith("exact_orb.")
            and not (
                imported == "exact_orb.research"
                or imported.startswith("exact_orb.research.")
            )
        }
    )
    assert not violations, "Research contracts импортируют вне пакета:\n" + "\n".join(
        violations
    )


def test_research_contract_import_graph_is_acyclic() -> None:
    contract_graph_modules = (RESEARCH_PACKAGE_MODULE, *RESEARCH_CONTRACT_MODULES)
    paths = {
        _module_name(path): path
        for path in _research_source_files(contract_graph_modules)
    }
    graph = {
        module: {
            candidate
            for imported in _declared_imports(path)
            for candidate in contract_graph_modules
            if candidate != module
            and imported == candidate
        }
        for module, path in paths.items()
    }
    cycle = _find_import_cycle(graph)
    assert cycle is None, "Research contract import cycle: " + " -> ".join(cycle or ())


def test_research_projection_declares_only_whitelisted_project_imports() -> None:
    allowed = {
        "exact_orb.calculation.types",
        "exact_orb.research.errors",
        "exact_orb.research.models",
    }
    violations = sorted(
        {
            imported
            for path in _research_source_files(RESEARCH_PROJECTION_MODULES)
            for imported in _declared_imports(path)
            if imported.startswith("exact_orb.")
            and not any(
                imported == item or imported.startswith(f"{item}.")
                for item in allowed
            )
        }
    )
    assert not violations, "Research projection импортирует вне allowlist: " + ", ".join(
        violations
    )


def test_research_adapters_only_import_research_contracts() -> None:
    violations = sorted(
        {
            imported
            for path in _research_source_files(RESEARCH_ADAPTER_MODULES)
            for imported in _declared_imports(path)
            if imported.startswith("exact_orb.")
            and not (
                imported == "exact_orb.research"
                or imported.startswith("exact_orb.research.")
            )
        }
    )
    assert not violations, "Research adapters импортируют вне contracts: " + ", ".join(
        violations
    )
    declared = {
        imported
        for path in _research_source_files(RESEARCH_ADAPTER_MODULES)
        for imported in _declared_imports(path)
    }
    assert not any(
        _violates(imported, "exact_orb.research.projection")
        or _violates(imported, "exact_orb.calculation")
        for imported in declared
    )


def test_research_modules_declare_no_forbidden_edges() -> None:
    modules = (
        RESEARCH_PACKAGE_MODULE,
        *RESEARCH_CONTRACT_MODULES,
        *RESEARCH_PROJECTION_MODULES,
        *RESEARCH_ADAPTER_MODULES,
    )
    violations = sorted(
        {
            f"{_module_name(path)} -> {forbidden}"
            for path in _research_source_files(modules)
            for imported in _declared_imports(path)
            for forbidden in RESEARCH_FORBIDDEN_IMPORTS
            if _violates(imported, forbidden)
        }
    )
    assert not violations, "Research импортирует запрещённые слои:\n" + "\n".join(
        violations
    )


def test_research_consumers_do_not_import_private_contract_names() -> None:
    contract_sources = {RESEARCH_PACKAGE_MODULE, *RESEARCH_CONTRACT_MODULES}
    violations: list[str] = []
    observed: list[str] = []
    for path in _research_source_files(
        (*RESEARCH_PROJECTION_MODULES, *RESEARCH_ADAPTER_MODULES)
    ):
        module = _module_name(path)
        is_package = path.name == "__init__.py"
        tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            source = (
                _resolve_relative(module, is_package, node.level, node.module)
                if node.level
                else node.module or ""
            )
            if source not in contract_sources:
                continue
            observed.extend(f"{source}.{alias.name}" for alias in node.names)
            violations.extend(
                f"{module} -> {source}.{alias.name}"
                for alias in node.names
                if alias.name.startswith("_")
            )
    assert observed, "positive control: Research contract imports were not discovered"
    assert not violations, "Research consumer imports private contract names:\n" + "\n".join(
        violations
    )


def test_research_has_no_hidden_identity_time_randomness_or_environment_inputs() -> None:
    modules = (
        RESEARCH_PACKAGE_MODULE,
        *RESEARCH_CONTRACT_MODULES,
        *RESEARCH_PROJECTION_MODULES,
        *RESEARCH_ADAPTER_MODULES,
    )
    violations: list[str] = []
    for path in _research_source_files(modules):
        tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                rendered = ast.unparse(node.func)
                if rendered in {
                    "uuid4",
                    "datetime.now",
                    "datetime.utcnow",
                    "date.today",
                    "time.time",
                    "os.getenv",
                    "getenv",
                } or rendered.startswith("random."):
                    violations.append(f"{_module_name(path)} -> {rendered}")
            if isinstance(node, ast.Attribute) and ast.unparse(node) == "os.environ":
                violations.append(f"{_module_name(path)} -> os.environ")
    assert not violations, "Research читает скрытые inputs:\n" + "\n".join(
        sorted(violations)
    )


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


def test_session_contracts_only_declare_allowlisted_project_imports() -> None:
    """Session contracts depend only on their package and two shared contracts."""

    violations = sorted(
        {
            f"{_module_name(path)} -> {imported}"
            for path in _session_contract_source_files()
            for imported in _declared_imports(path)
            if imported.startswith("exact_orb.")
            and not any(
                (
                    imported.startswith(allowed)
                    if allowed.endswith(".")
                    else imported == allowed or imported.startswith(f"{allowed}.")
                )
                for allowed in SESSION_ALLOWED_PROJECT_IMPORTS
            )
        }
    )

    assert not violations, "session contracts импортируют вне allowlist:\n" + "\n".join(
        violations
    )


def test_session_contract_import_graph_is_acyclic() -> None:
    """Declared imports must remain acyclic even when hidden behind guards."""

    paths = {_module_name(path): path for path in _session_contract_source_files()}
    assert set(paths) == set(SESSION_CONTRACT_MODULES), (
        "не найдены все session contract modules: "
        f"expected={sorted(SESSION_CONTRACT_MODULES)}, actual={sorted(paths)}"
    )

    graph = {
        module: {
            candidate
            for imported in _declared_imports(path)
            for candidate in SESSION_CONTRACT_MODULES
            if candidate != module
            and (imported == candidate or imported.startswith(f"{candidate}."))
        }
        for module, path in paths.items()
    }
    cycle = _find_import_cycle(graph)

    assert cycle is None, "session contract import cycle: " + " -> ".join(cycle or ())


def test_session_contracts_do_not_hide_imports_behind_type_checking() -> None:
    """TYPE_CHECKING must not be an escape hatch around the dependency graph."""

    violations = sorted(
        _module_name(path)
        for path in _session_contract_source_files()
        if _uses_type_checking_guard(path)
    )

    assert not violations, "TYPE_CHECKING запрещён в session contracts: " + ", ".join(
        violations
    )


def test_session_outcomes_do_not_declare_dialog_import() -> None:
    """Snapshot owns the only state/dialog join, so outcomes cannot depend on dialog."""

    path = PACKAGE_ROOT / "session" / "outcomes.py"
    imports = _declared_imports(path)

    assert not any(
        imported == "exact_orb.session.dialog"
        or imported.startswith("exact_orb.session.dialog.")
        for imported in imports
    ), "session.outcomes must not import session.dialog"


def test_session_adapter_source_files_are_discovered() -> None:
    modules = {_module_name(path) for path in _session_adapter_source_files()}

    assert modules == set(SESSION_ADAPTER_MODULES)


def test_session_adapters_only_import_session_project_modules() -> None:
    violations = sorted(
        {
            f"{_module_name(path)} -> {imported}"
            for path in _session_adapter_source_files()
            for imported in _declared_imports(path)
            if imported.startswith("exact_orb.")
            and not (
                imported == "exact_orb.session"
                or imported.startswith("exact_orb.session.")
            )
        }
    )

    assert not violations, "session adapters импортируют вне allowlist:\n" + "\n".join(
        violations
    )


def test_session_contract_consumers_do_not_import_private_contract_names() -> None:
    violations: list[str] = []
    contract_sources = {"exact_orb.session", *SESSION_CONTRACT_MODULES}

    for consumer_kind, paths in (
        ("adapter", _session_adapter_source_files()),
        ("service", _session_service_source_files()),
    ):
        observed_contract_imports: list[str] = []
        for path in paths:
            module = _module_name(path)
            is_package = path.name == "__init__.py"
            tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.ImportFrom):
                    continue
                source = (
                    _resolve_relative(module, is_package, node.level, node.module)
                    if node.level
                    else node.module or ""
                )
                if source not in contract_sources:
                    continue
                observed_contract_imports.extend(
                    f"{module} -> {source}.{alias.name}" for alias in node.names
                )
                violations.extend(
                    f"{module} -> {source}.{alias.name}"
                    for alias in node.names
                    if alias.name.startswith("_")
                )
        assert observed_contract_imports, (
            f"positive control: {consumer_kind} contract imports were not discovered"
        )

    assert not violations, (
        "session consumers импортируют приватные имена контрактов:\n"
        + "\n".join(sorted(violations))
    )


def test_session_adapters_declare_no_hidden_time_id_or_edge_imports() -> None:
    violations = sorted(
        {
            f"{_module_name(path)} -> {forbidden}"
            for path in _session_adapter_source_files()
            for imported in _declared_imports(path)
            for forbidden in SESSION_ADAPTER_FORBIDDEN_IMPORTS
            if _violates(imported, forbidden)
        }
    )

    assert not violations, "session adapters импортируют запрещённое:\n" + "\n".join(
        violations
    )


def test_session_adapters_do_not_read_wall_or_monotonic_time() -> None:
    violations = sorted(
        f"{_module_name(path)} -> {call}"
        for path in _session_adapter_source_files()
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"), str(path)))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"now", "utcnow", "time", "monotonic"}
        for call in (ast.unparse(node.func),)
    )

    assert not violations, "session adapters читают скрытое время:\n" + "\n".join(
        violations
    )


def test_session_service_source_file_is_discovered() -> None:
    modules = {_module_name(path) for path in _session_service_source_files()}

    assert modules == set(SESSION_SERVICE_MODULES)


def test_session_service_only_imports_session_contracts() -> None:
    violations = sorted(
        {
            f"{_module_name(path)} -> {imported}"
            for path in _session_service_source_files()
            for imported in _declared_imports(path)
            if imported.startswith("exact_orb.")
            and not (
                imported == "exact_orb.session"
                or imported.startswith("exact_orb.session.")
            )
        }
    )

    assert not violations, "session service импортирует вне allowlist:\n" + "\n".join(
        violations
    )


def test_session_service_declares_no_adapter_clock_id_or_edge_imports() -> None:
    violations = sorted(
        {
            f"{_module_name(path)} -> {forbidden}"
            for path in _session_service_source_files()
            for imported in _declared_imports(path)
            for forbidden in SESSION_SERVICE_FORBIDDEN_IMPORTS
            if _violates(imported, forbidden)
        }
    )

    assert not violations, "session service импортирует запрещённое:\n" + "\n".join(
        violations
    )


def test_session_service_does_not_read_wall_or_monotonic_time() -> None:
    violations = sorted(
        f"{_module_name(path)} -> {call}"
        for path in _session_service_source_files()
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"), str(path)))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"now", "utcnow", "time", "monotonic"}
        for call in (ast.unparse(node.func),)
    )

    assert not violations, "session service читает скрытое время:\n" + "\n".join(
        violations
    )


def _assert_session_runtime_module_upper_bound(
    loaded_names: list[str],
    *,
    required: frozenset[str],
) -> None:
    loaded = frozenset(loaded_names)
    missing = sorted(required - loaded)
    unexpected = sorted(
        loaded - required - SESSION_RUNTIME_KNOWN_TRANSITIVE_DEBT
    )
    assert not missing, "session runtime import missing required modules:\n" + "\n".join(
        missing
    )
    assert not unexpected, (
        "session runtime import loaded unexpected modules:\n"
        + "\n".join(unexpected)
    )


def test_session_package_import_keeps_runtime_and_edge_modules_out() -> None:
    """A clean package import exposes the API without loading implementations."""

    required = (
        "SessionState",
        "SessionSnapshot",
        "SessionStore",
        "DialogStore",
        "SessionPersistence",
        "require_utc",
    )
    script = "\n".join(
        (
            "import importlib, json, sys",
            "package = importlib.import_module('exact_orb.session')",
            f"required = {list(required)!r}",
            f"forbidden = {list(SESSION_FORBIDDEN_AT_RUNTIME)!r}",
            "missing = sorted(name for name in required if not hasattr(package, name))",
            "found = sorted(",
            "    name",
            "    for name in sys.modules",
            "    for bad in forbidden",
            "    if name == bad or name.startswith(bad + '.')",
            ")",
            "loaded = sorted(name for name in sys.modules if name == 'exact_orb' or name.startswith('exact_orb.'))",
            "print(json.dumps({'missing': missing, 'found': found, 'loaded': loaded}))",
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
        "импорт exact_orb.session завершился ошибкой:\n" + completed.stderr
    )

    result = json.loads(completed.stdout.strip().splitlines()[-1])
    assert not result["missing"], (
        "package import выполнился, но публичный API неполон: "
        + ", ".join(result["missing"])
    )
    assert not result["found"], (
        "package import транзитивно загрузил запрещённое: "
        + ", ".join(result["found"])
    )
    _assert_session_runtime_module_upper_bound(
        result["loaded"],
        required=SESSION_RUNTIME_BASE_REQUIRED,
    )


def test_session_adapters_import_cleanly_with_positive_controls() -> None:
    script = "\n".join(
        (
            "import importlib, json, sys",
            "package = importlib.import_module('exact_orb.session.adapters')",
            "time_module = importlib.import_module('exact_orb.session.adapters._time')",
            "in_memory = importlib.import_module('exact_orb.session.adapters.in_memory')",
            "required = ['InMemorySessionStore', 'InMemoryDialogStore', 'InMemorySessionPersistence']",
            f"forbidden = {list(SESSION_ADAPTER_FORBIDDEN_AT_RUNTIME)!r}",
            "missing = sorted(name for name in required if not hasattr(package, name))",
            "positive = hasattr(time_module, 'validate_now') and all(hasattr(in_memory, name) for name in required)",
            "sqlite_loaded = 'exact_orb.session.adapters.sqlite' in sys.modules",
            "found = sorted(",
            "    name",
            "    for name in sys.modules",
            "    for bad in forbidden",
            "    if name == bad or name.startswith(bad + '.')",
            ")",
            "loaded = sorted(name for name in sys.modules if name == 'exact_orb' or name.startswith('exact_orb.'))",
            "print(json.dumps({'missing': missing, 'positive': positive, 'sqlite_loaded': sqlite_loaded, 'found': found, 'loaded': loaded}))",
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
        "импорт session adapters завершился ошибкой:\n" + completed.stderr
    )
    result = json.loads(completed.stdout.strip().splitlines()[-1])
    assert result["positive"], "positive control не импортировал adapter symbols"
    assert not result["sqlite_loaded"], "adapter package импортировал SQLite concrete module"
    assert not result["missing"], "adapter package API неполон: " + ", ".join(
        result["missing"]
    )
    assert not result["found"], "adapter import загрузил запрещённое: " + ", ".join(
        result["found"]
    )
    _assert_session_runtime_module_upper_bound(
        result["loaded"],
        required=SESSION_RUNTIME_BASE_REQUIRED
        | frozenset(
            {
                "exact_orb.session.adapters",
                "exact_orb.session.adapters._time",
                "exact_orb.session.adapters.in_memory",
            }
        ),
    )


def test_session_sqlite_adapter_imports_cleanly_with_positive_controls() -> None:
    script = "\n".join(
        (
            "import importlib, json, sys",
            "module = importlib.import_module('exact_orb.session.adapters.sqlite')",
            "required = ['SqliteSessionStore', 'SqliteDialogStore', 'SqliteSessionPersistence']",
            f"forbidden = {list(SESSION_SQLITE_ADAPTER_FORBIDDEN_AT_RUNTIME)!r}",
            "missing = sorted(name for name in required if not hasattr(module, name))",
            "positive = 'sqlite3' in sys.modules and not missing",
            "found = sorted(",
            "    name",
            "    for name in sys.modules",
            "    for bad in forbidden",
            "    if name == bad or name.startswith(bad + '.')",
            ")",
            "loaded = sorted(name for name in sys.modules if name == 'exact_orb' or name.startswith('exact_orb.'))",
            "print(json.dumps({'missing': missing, 'positive': positive, 'found': found, 'loaded': loaded}))",
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
        "импорт session SQLite adapter завершился ошибкой:\n" + completed.stderr
    )
    result = json.loads(completed.stdout.strip().splitlines()[-1])
    assert result["positive"], "positive control не импортировал SQLite symbols"
    assert not result["missing"], "SQLite adapter API неполон: " + ", ".join(
        result["missing"]
    )
    assert not result["found"], "SQLite adapter импорт загрузил запрещённое: " + ", ".join(
        result["found"]
    )
    _assert_session_runtime_module_upper_bound(
        result["loaded"],
        required=SESSION_RUNTIME_BASE_REQUIRED
        | frozenset(
            {
                "exact_orb.session.adapters",
                "exact_orb.session.adapters._time",
                "exact_orb.session.adapters.in_memory",
                "exact_orb.session.adapters.sqlite",
            }
        ),
    )


def test_session_service_imports_cleanly_with_positive_control() -> None:
    script = "\n".join(
        (
            "import importlib, json, sys",
            "module = importlib.import_module('exact_orb.session.context')",
            f"forbidden = {list(SESSION_SERVICE_FORBIDDEN_AT_RUNTIME)!r}",
            "positive = hasattr(module, 'ContextService')",
            "found = sorted(",
            "    name",
            "    for name in sys.modules",
            "    for bad in forbidden",
            "    if name == bad or name.startswith(bad + '.')",
            ")",
            "loaded = sorted(name for name in sys.modules if name == 'exact_orb' or name.startswith('exact_orb.'))",
            "print(json.dumps({'positive': positive, 'found': found, 'loaded': loaded}))",
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
        "импорт session service завершился ошибкой:\n" + completed.stderr
    )
    result = json.loads(completed.stdout.strip().splitlines()[-1])
    assert result["positive"], "positive control не импортировал ContextService"
    assert not result["found"], "service import загрузил запрещённое: " + ", ".join(
        result["found"]
    )
    _assert_session_runtime_module_upper_bound(
        result["loaded"],
        required=SESSION_RUNTIME_BASE_REQUIRED
        | frozenset({"exact_orb.session.context"}),
    )


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


def test_calculation_artifacts_declares_no_edge_imports() -> None:
    """Artifact resolver orchestrates calculation, but not UI/tool/LLM edges."""

    path = PACKAGE_ROOT / "calculation" / "artifacts.py"
    violations = sorted(
        {
            forbidden
            for imported in _declared_imports(path)
            for forbidden in CALCULATION_ARTIFACTS_FORBIDDEN
            if _violates(imported, forbidden)
        }
    )

    assert not violations, (
        "calculation.artifacts импортирует запрещённые edge-слои: "
        + ", ".join(violations)
    )


def test_calculation_cache_declares_no_artifact_payload_imports() -> None:
    """Opaque byte cache must not know the artifact payload format."""

    path = PACKAGE_ROOT / "calculation" / "cache.py"
    violations = sorted(
        {
            forbidden
            for imported in _declared_imports(path)
            for forbidden in CALCULATION_CACHE_FORBIDDEN
            if _violates(imported, forbidden)
        }
    )

    assert not violations, (
        "calculation.cache импортирует payload/artifact слои: "
        + ", ".join(violations)
    )


def _research_import_probe(
    module: str,
    *,
    symbols: tuple[str, ...],
    forbidden: tuple[str, ...],
) -> dict[str, object]:
    script = "\n".join(
        (
            "import importlib, json, sys",
            f"module = importlib.import_module({module!r})",
            f"symbols = {list(symbols)!r}",
            f"forbidden = {list(forbidden)!r}",
            "missing = sorted(name for name in symbols if not hasattr(module, name))",
            "found = sorted(",
            "    name",
            "    for name in sys.modules",
            "    for bad in forbidden",
            "    if name == bad or name.startswith(bad + '.')",
            ")",
            "loaded = sorted(name for name in sys.modules if name == 'exact_orb' or name.startswith('exact_orb.'))",
            "print(json.dumps({'missing': missing, 'found': found, 'loaded': loaded, 'swisseph': 'swisseph' in sys.modules}))",
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
        f"import {module} завершился ошибкой:\n" + completed.stderr
    )
    return json.loads(completed.stdout.strip().splitlines()[-1])


def _assert_research_runtime_upper_bound(
    loaded_names: list[str],
    *,
    required: frozenset[str],
    known_transitive_debt: frozenset[str] = frozenset(),
) -> None:
    loaded = frozenset(loaded_names)
    missing = sorted(required - loaded)
    unexpected = sorted(loaded - required - known_transitive_debt)
    assert not missing, "Research runtime import missing required modules:\n" + "\n".join(
        missing
    )
    assert not unexpected, "Research runtime import loaded unexpected modules:\n" + "\n".join(
        unexpected
    )


def test_research_package_import_is_contract_only_with_positive_controls() -> None:
    forbidden = (
        "exact_orb.research.projection",
        "exact_orb.research.adapters",
        "exact_orb.calculation",
        "exact_orb.engine",
        "exact_orb.config",
        "exact_orb.swiss_backend",
        "swisseph",
        "sqlite3",
        "exact_orb.session",
        "exact_orb.application",
        "exact_orb.llm",
        "exact_orb.orchestration",
        "exact_orb.tools",
        "exact_orb.cli",
        "exact_orb.edge",
    )
    result = _research_import_probe(
        "exact_orb.research",
        symbols=("ResearchRecord", "ResearchCorpus", "record_content_digest"),
        forbidden=forbidden,
    )
    assert not result["missing"], "Research package public API is incomplete"
    assert not result["found"], "Research package import leaked runtime modules"
    assert not result["swisseph"]
    _assert_research_runtime_upper_bound(
        result["loaded"],
        required=RESEARCH_RUNTIME_BASE_REQUIRED,
    )


def test_research_adapters_import_in_memory_without_runtime_stack() -> None:
    result = _research_import_probe(
        "exact_orb.research.adapters",
        symbols=("InMemoryResearchCorpus",),
        forbidden=(
            "exact_orb.research.projection",
            "exact_orb.calculation",
            "exact_orb.engine",
            "exact_orb.config",
            "exact_orb.swiss_backend",
            "swisseph",
            "sqlite3",
            "exact_orb.session",
            "exact_orb.application",
            "exact_orb.llm",
            "exact_orb.edge",
        ),
    )
    assert not result["missing"]
    assert not result["found"]
    assert not result["swisseph"]
    _assert_research_runtime_upper_bound(
        result["loaded"],
        required=RESEARCH_RUNTIME_BASE_REQUIRED
        | frozenset(RESEARCH_ADAPTER_MODULES),
    )


def test_research_projection_explicitly_loads_engine_but_not_edges() -> None:
    result = _research_import_probe(
        "exact_orb.research.projection",
        symbols=("project_chart_features",),
        forbidden=(
            "exact_orb.research.adapters",
            "sqlite3",
            "exact_orb.session",
            "exact_orb.application",
            "exact_orb.llm",
            "exact_orb.orchestration",
            "exact_orb.tools",
            "exact_orb.cli",
            "exact_orb.edge",
        ),
    )
    assert not result["missing"]
    assert not result["found"]
    assert result["swisseph"], "positive control: projection did not load native engine"
    _assert_research_runtime_upper_bound(
        result["loaded"],
        required=RESEARCH_RUNTIME_BASE_REQUIRED
        | frozenset(
            {
                "exact_orb.research.projection",
                "exact_orb.calculation.chart_contract",
                "exact_orb.calculation.types",
                "exact_orb.engine",
                "exact_orb.engine.charts.uncertainty",
                "exact_orb.swiss_backend",
            }
        ),
        known_transitive_debt=RESEARCH_PROJECTION_RUNTIME_KNOWN_TRANSITIVE_DEBT,
    )
