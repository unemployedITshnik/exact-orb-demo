# Промт: ленивые экспорты в tools/__init__.py и ленивый импорт в ToolRegistry.default()

**Контекст.**

После промтов `03` и `04` пакет `tools/` устроен так:

```python
# tools/__init__.py
from .base import Tool
from .natal_tool import NatalTool, NatalToolArgs      # <- (1)
from .registry import DuplicateToolError, InvalidToolError, ToolRegistry, UnknownToolError
from .types import ToolRequest, ToolResult

# tools/registry.py
from .base import Tool
from .natal_tool import NatalTool                     # <- (2)
```

Из-за (1) и (2) любой импорт чего угодно из пакета — даже одного
абстрактного `Tool` или `ToolRequest` — исполняет цепочку:

```
exact_orb.tools
  -> exact_orb.tools.natal_tool
    -> exact_orb.engine.charts.natal
      -> swisseph, aspects, configurations, strength, config...
```

Агентскому слою это не нужно. `Planner`, `Orchestrator` и их будущие
контрактные тесты работают с портом `Tool`, а не с расчётом; сегодня они
платят за Swiss Ephemeris и загрузку эфемеридных файлов на каждом
импорте.

**Важно про (1) и (2): их нельзя чинить по отдельности.** Пока в
`__init__.py` есть строка (1), ленивый импорт в `registry.py` не даёт
ничего — `import exact_orb.tools.registry` сначала исполняет
`__init__.py` пакета. И наоборот: пока в `registry.py` есть строка (2),
ленивый `__init__.py` бесполезен, потому что он импортирует `.registry`.
Поэтому обе правки — в одном промте, и критерий приёмки на них общий.

**Решения, которые уже приняты и не обсуждаются в рамках этого промта:**

- Публичный контракт не меняется: `from exact_orb.tools import NatalTool`
  и `from exact_orb.tools import NatalToolArgs` продолжают работать, и
  оба имени остаются в `__all__`. Меняется только **момент** импорта.
  Промт `04` не отменяется, а уточняется.
- Механизм — модульный `__getattr__` (PEP 562), а не удаление
  экспортов и не строковые заглушки.
- Блок `if TYPE_CHECKING:` обязателен. Без него mypy и IDE перестают
  видеть `NatalTool`/`NatalToolArgs` у пакета — это была бы плата
  лёгким импортом за слепой редактор, а такой размен нам не нужен.
- Лениво выносятся **только конкретные адаптеры** (`NatalTool`,
  `NatalToolArgs`). `Tool`, `ToolRegistry`, `ToolRequest`, `ToolResult`
  и три класса ошибок остаются обычными top-level импортами: они лёгкие
  (`pydantic` и стандартная библиотека) и нужны почти в каждом
  обращении к пакету.

**Задача.**

**1. `src/exact_orb/tools/__init__.py`** — привести к виду:

```python
"""Agent tool contracts and registry."""

from typing import TYPE_CHECKING, Any

from .base import Tool
from .registry import DuplicateToolError, InvalidToolError, ToolRegistry, UnknownToolError
from .types import ToolRequest, ToolResult

if TYPE_CHECKING:
    from .natal_tool import NatalTool, NatalToolArgs

__all__ = [
    "DuplicateToolError",
    "InvalidToolError",
    "NatalTool",
    "NatalToolArgs",
    "Tool",
    "ToolRegistry",
    "ToolRequest",
    "ToolResult",
    "UnknownToolError",
]

_LAZY_NAMES = frozenset({"NatalTool", "NatalToolArgs"})


def __getattr__(name: str) -> Any:
    """Import concrete tool adapters on first access (PEP 562).

    Keeps ``import exact_orb.tools`` free of the calculation engine: the
    agent layer (``Planner``, ``Orchestrator`` and their contract tests)
    only needs ``Tool``/``ToolRegistry``/``ToolRequest``/``ToolResult``,
    while ``NatalTool`` pulls ``engine.charts.natal`` and, through it,
    Swiss Ephemeris. Callers that do want the adapter pay for it at the
    moment they ask for it, with the public name unchanged.
    """

    if name in _LAZY_NAMES:
        from . import natal_tool

        return getattr(natal_tool, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(__all__)
```

**2. `src/exact_orb/tools/registry.py`** — убрать top-level
`from .natal_tool import NatalTool` и импортировать его внутри
`default()`, с комментарием, объясняющим почему (иначе следующий проход
«причешет» импорт обратно наверх как случайность):

```python
    @classmethod
    def default(cls) -> "ToolRegistry":
        """<докстринг остаётся ровно таким, каким его оставил промт 03>"""

        # Imported lazily so that importing ``ToolRegistry`` does not drag the
        # calculation engine (``engine.charts.natal`` -> Swiss Ephemeris) in.
        # Only callers that actually build the default registry pay for it;
        # see tests/test_tools_imports.py, which locks this in.
        from .natal_tool import NatalTool

        registry = cls()
        registry.register(NatalTool())
        return registry
```

**3. `tests/test_tools_imports.py`** — новый файл, два теста.

Проверку «пакет не тянет движок» нужно делать **в отдельном процессе**:
внутри одного прогона pytest другие тесты (`test_tools_natal.py`,
`test_aspects.py`, …) уже импортировали `swisseph`, и проверка
`sys.modules` в общем процессе была бы зелёной всегда, независимо от
того, работает механизм или нет. Тест, который не может упасть, хуже
отсутствующего.

```python
"""Import hygiene for the tools package: the agent layer must not pay for the engine."""

from __future__ import annotations

import subprocess
import sys


def test_tools_package_import_does_not_pull_the_engine() -> None:
    code = (
        "import sys\n"
        "import exact_orb.tools\n"
        "leaked = sorted(m for m in sys.modules if m == 'swisseph' or m.startswith('exact_orb.engine'))\n"
        "assert not leaked, leaked\n"
    )

    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)

    assert result.returncode == 0, result.stdout + result.stderr


def test_natal_tool_is_still_importable_from_the_package() -> None:
    from exact_orb.tools import NatalTool, NatalToolArgs
    from exact_orb.tools.natal_tool import NatalTool as DirectNatalTool

    assert NatalTool is DirectNatalTool
    assert NatalToolArgs.__name__ == "NatalToolArgs"
```

**Ограничения.**

- Не менять состав и порядок `__all__` — он остаётся ровно тем же
  отсортированным списком из промта `04`.
- Не делать ленивыми `Tool`, `ToolRegistry`, `ToolRequest`,
  `ToolResult`, `UnknownToolError`, `DuplicateToolError`,
  `InvalidToolError`.
- Не трогать `tools/base.py`, `tools/types.py`, `tools/natal_tool.py`.
- Не менять докстринг и поведение `ToolRegistry.default()` — только
  место импорта плюс комментарий.
- Не применять тот же приём к `engine/*/__init__.py` — там расчёт и
  есть содержимое пакета, лениться не от чего.
- Не переименовывать и не удалять существующие тесты.

**Критерии приёмки.**

1. `tests/test_tools_imports.py` зелёный, и оба его теста реально
   способны упасть: если временно вернуть top-level импорт в
   `registry.py`, первый тест краснеет.
2. `from exact_orb.tools import NatalTool, NatalToolArgs, ToolRegistry, ToolRequest, ToolResult`
   импортируется без ошибок.
3. `ToolRegistry.default().list_tools() == ["natal"]` — как раньше.
4. `dir(exact_orb.tools)` содержит `"NatalTool"`.
5. `exact_orb.tools.NoSuchName` кидает `AttributeError`, а не
   `ImportError` и не `KeyError`.
6. `tests/test_tools_natal.py` и `tests/test_agent_skeleton.py` проходят
   без единой правки.

**Проверка.**

Запусти и покажи фактический вывод (не описывай ожидаемое):

```bash
python -m pytest tests/test_tools_imports.py tests/test_tools_natal.py tests/test_agent_skeleton.py -v
python -m pytest -q
```

Второй прогон — чтобы убедиться, что ленивый пакет ничего не сломал в
остальных 15 тестовых файлах.
