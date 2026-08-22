# Промт: экспорт NatalTool/NatalToolArgs из tools/__init__.py

**Контекст.**

`src/exact_orb/tools/__init__.py` реэкспортирует публичные имена пакета
по уже устоявшемуся в проекте образцу (см. `engine/aspects/__init__.py`,
`engine/configurations/__init__.py`):

```python
"""Agent tool contracts and registry."""

from .base import Tool
from .registry import DuplicateToolError, InvalidToolError, ToolRegistry, UnknownToolError
from .types import ToolRequest, ToolResult

__all__ = [
    "DuplicateToolError",
    "InvalidToolError",
    "Tool",
    "ToolRegistry",
    "ToolRequest",
    "ToolResult",
    "UnknownToolError",
]
```

После появления `tools/natal_tool.py` (`NatalTool`, `NatalToolArgs`) и
`ToolRegistry.default()` (промты `02` и `03` в этой же папке) пакет
`tools/` наружу их не отдаёт — снаружи пакета до них можно достучаться
только через `exact_orb.tools.natal_tool`, что не соответствует стилю
остальных пакетов проекта.

> **Что будет дальше.** Обычный top-level импорт `natal_tool` в этом
> файле означает, что любой `import exact_orb.tools` (даже ради одного
> `Tool`) тянет расчётное ядро и Swiss Ephemeris. Это осознанно
> принимается здесь и пересматривается в промте `08` (ленивые экспорты
> через PEP 562). Публичный контракт — сам факт, что
> `from exact_orb.tools import NatalTool` работает, — промт `08` не
> меняет; меняется только момент импорта.

**Задача.**

Обновить `src/exact_orb/tools/__init__.py`:

1. Добавить `from .natal_tool import NatalTool, NatalToolArgs`.
2. Добавить `"NatalTool"` и `"NatalToolArgs"` в `__all__`, сохранив
   список отсортированным по алфавиту (как сейчас).

Файл целиком:

```python
"""Agent tool contracts and registry."""

from .base import Tool
from .natal_tool import NatalTool, NatalToolArgs
from .registry import DuplicateToolError, InvalidToolError, ToolRegistry, UnknownToolError
from .types import ToolRequest, ToolResult

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
```

**Ограничения.**

- Никаких других изменений в файле.
- Не менять порядок и состав уже существующих экспортов, кроме
  добавления двух новых имён на свои алфавитные места.

**Критерии приёмки.**

1. `from exact_orb.tools import NatalTool, NatalToolArgs, ToolRegistry, ToolRequest, ToolResult` —
   импортируется без ошибок.
2. `__all__` отсортирован по алфавиту.
