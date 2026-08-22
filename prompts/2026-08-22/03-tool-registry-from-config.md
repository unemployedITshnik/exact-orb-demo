# Промт: ToolRegistry.from_config()

**Контекст.**

`src/exact_orb/tools/registry.py` сегодня — чистое хранилище без единой
строчки про конфигурацию:

```python
class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None: ...
    def get(self, name: str) -> Tool: ...
    def list_tools(self) -> list[str]: ...
```

Наполняется он сейчас только вручную, в тестах (`registry.register(DummyTool("natal"))`).
Реального пути, который бы наполнял `ToolRegistry` для боевого запуска,
в проекте нет: `cli.py` по-прежнему вызывает `get_natal()` напрямую,
минуя `Tool`/`ToolRegistry` целиком (это отдельный, уже известный
технический долг, не предмет этого промта).

ADR-0002 описывает целевую картину — `LocalTool`/`RemoteTool` и
конфигурацию вида `natal = local | http://...`, читаемую фабрикой на
старте. Из этого в проекте есть только сам `Tool`-порт; ни `LocalTool`,
ни `RemoteTool` как отдельные классы не существуют — сегодня каждый
конкретный тул (например `NatalTool` из `tools/natal_tool.py`) сам по
себе и есть локальный адаптер.

**Решения, которые уже приняты и не обсуждаются в рамках этого промта:**

- Маршрутизация через конфиг («local vs remote») в этот промт не входит.
  `RemoteTool` не строится, и рассуждать о том, где будет жить конфиг
  (`config.py`, env-переменная, `pyproject.toml`), здесь не нужно —
  сейчас единственный существующий тул жёстко привязан к своему
  локальному вызову.
- Фабрика — это **classmethod на самом `ToolRegistry`**
  (`ToolRegistry.from_config()`), а не отдельный модуль вроде
  `tools/factory.py`. Да, это значит, что `ToolRegistry` начинает знать
  про конкретный `NatalTool` — это осознанный выбор ради меньшего числа
  файлов на этом шаге, не архитектурная случайность.

**Задача.**

В `src/exact_orb/tools/registry.py`:

1. Добавить импорт `from .natal_tool import NatalTool`.
2. Добавить `classmethod`:

```python
@classmethod
def from_config(cls) -> "ToolRegistry":
    """Build the default registry with every tool wired to its adapter.

    Every tool is a ``LocalTool`` today: ``get_natal()`` runs in-process,
    there is nothing to route to yet. ADR-0002 also describes a
    ``RemoteTool`` adapter and a per-tool config value
    (``natal = local | http://...``) to choose between them; that
    routing layer is deliberately deferred until a second adapter
    actually exists to choose between — adding it later only touches
    this method, not ``ToolRegistry``, ``Orchestrator``, or ``Planner``.
    """

    registry = cls()
    registry.register(NatalTool())
    return registry
```

3. Не менять сигнатуры и поведение `register`, `get`, `list_tools`,
   `UnknownToolError`, `DuplicateToolError`, `InvalidToolError` — они
   остаются ровно такими, какие есть.

**Ограничения.**

- Не добавлять чтение конфигурации (env, `pyproject.toml`) ни в этот
  файл, ни в `config.py`.
- Не создавать `RemoteTool`.
- Не менять `tools/base.py`, `tools/types.py`, `tools/natal_tool.py`.
- Не переносить `from_config()` в отдельный модуль — именно classmethod
  на классе.

**Критерии приёмки.**

1. `ToolRegistry.from_config().list_tools() == ["natal"]`.
2. `isinstance(ToolRegistry.from_config().get("natal"), NatalTool)` —
   `True`.
3. Прямое использование `ToolRegistry()` (без `from_config()`) и ручная
   регистрация тулов продолжают работать как раньше — существующий тест
   `test_agent_skeleton.py::test_tool_registry_registers_and_retrieves_tools`
   проходит без изменений.
