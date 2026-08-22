# Промт: ToolRegistry.default()

**Контекст.**

`src/exact_orb/tools/registry.py` задумывался как чистое хранилище без
единой строчки про конфигурацию:

```python
class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None: ...
    def get(self, name: str) -> Tool: ...
    def list_tools(self) -> list[str]: ...
```

Если первая редакция этого промта уже применена, в рабочей копии там
дополнительно лежит метод `from_config()` — тогда см. «Историю имени»
ниже и п. 4 задачи, промт рассчитан на оба состояния.

Наполняется реестр сейчас только вручную, в тестах (`registry.register(DummyTool("natal"))`).
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

> **История имени.** Первая редакция этого промта называла метод
> `from_config()`. По итогам ревью имя заменено на `default()`: метод не
> читает никакой конфигурации, и знак, указывающий не туда, дороже
> экономии на будущем переименовании. Если промт уже был применён под
> старым именем — этот промт заодно переименовывает метод в коде и в
> тестах (см. «Задача», п. 4).

**Решения, которые уже приняты и не обсуждаются в рамках этого промта:**

- Фабрика — это **classmethod на самом `ToolRegistry`**
  (`ToolRegistry.default()`), а не отдельный модуль вроде
  `tools/factory.py`. Да, это значит, что `ToolRegistry` начинает знать
  про конкретный `NatalTool` — это осознанный выбор ради меньшего числа
  файлов на этом шаге, не архитектурная случайность. ADR-0002 требует
  «фабрику», а не «отдельный модуль»; classmethod — это factory method.
- Имя — `default()`, а не `from_config()` и не `with_default_tools()`.
  Смысл: «реестр, который получается, когда конфигурации нет».
  Когда конфигурация появится, она приедет **отдельным** методом
  `from_config(config)`, и оба имени останутся честными.
- Маршрутизация через конфиг («local vs remote») в этот промт не входит.
  `RemoteTool` не строится, и рассуждать о том, где будет жить конфиг
  (`config.py`, env-переменная, `pyproject.toml`), здесь не нужно —
  сейчас единственный существующий тул жёстко привязан к своему
  локальному вызову.
- Импорт `NatalTool` в `registry.py` — **обычный, на уровне модуля**.
  Ленивый импорт внутри метода и связанная с ним «лёгкость»
  `import exact_orb.tools` — предмет отдельного промта `08` в этой же
  папке; по отдельности, без ленивых экспортов в `tools/__init__.py`,
  ленивый импорт здесь не даёт ничего (пакетный `__init__` всё равно
  тянет `natal_tool`) и выглядит в коде как случайность. Не забегай
  вперёд.

**Задача.**

В `src/exact_orb/tools/registry.py`:

1. Добавить импорт `from .natal_tool import NatalTool`.
2. Добавить `classmethod`:

```python
@classmethod
def default(cls) -> "ToolRegistry":
    """Build the default registry with every tool wired to its adapter.

    Every tool is a ``LocalTool`` today: ``get_natal()`` runs in-process,
    there is nothing to route to yet. ADR-0002 also describes a
    ``RemoteTool`` adapter and a per-tool config value
    (``natal = local | http://...``) to choose between them; that
    routing layer is deliberately deferred until a second adapter
    actually exists to choose between. When it arrives it will land in a
    separate config-reading classmethod next to this one — neither
    ``ToolRegistry`` itself, nor ``Orchestrator``, nor ``Planner`` has to
    change for that.
    """

    registry = cls()
    registry.register(NatalTool())
    return registry
```

3. Не менять сигнатуры и поведение `register`, `get`, `list_tools`,
   `UnknownToolError`, `DuplicateToolError`, `InvalidToolError` — они
   остаются ровно такими, какие есть.
4. Если в рабочей копии метод уже существует под именем `from_config()`
   (первая редакция промта), вычистить старое имя. В `src/` и `tests/`
   оно встречается ровно в **трёх** местах:
   - сам метод в `src/exact_orb/tools/registry.py` — переименовать в
     `default()`;
   - докстринг `NatalTool` в `src/exact_orb/tools/natal_tool.py`
     («only the registration in ``ToolRegistry.from_config()``») —
     заменить упоминание на `ToolRegistry.default()`;
   - тест `test_tool_registry_from_config_registers_natal` в
     `tests/test_tools_natal.py` — переименовать в
     `test_tool_registry_default_registers_natal` и поправить вызов.

   Больше нигде `from_config` не встречается: `cli.py`, `Planner` и
   `Orchestrator` реестр пока не используют вовсе. Алиас для обратной
   совместимости **не нужен** — внешних вызовов нет.

**Ограничения.**

- Не добавлять чтение конфигурации (env, `pyproject.toml`) ни в этот
  файл, ни в `config.py`.
- Не создавать `RemoteTool`.
- Не создавать `from_config()` — ни как метод, ни как алиас. Не
  упоминать эту строку и в исходниках, докстринги включительно: будущий
  метод в комментариях называется описательно («a separate
  config-reading classmethod»), а его конкретное имя зафиксировано
  здесь, в промте, — так критерий 5 остаётся однозначным и не ломается
  от «улучшения» докстринга.
- Не менять `tools/base.py`, `tools/types.py`; в `tools/natal_tool.py`
  допустима ровно одна правка — упоминание имени метода в докстринге.
- Не переносить `default()` в отдельный модуль — именно classmethod
  на классе.
- Не делать импорт `NatalTool` ленивым (см. промт `08`).

**Критерии приёмки.**

1. `ToolRegistry.default().list_tools() == ["natal"]`.
2. `isinstance(ToolRegistry.default().get("natal"), NatalTool)` — `True`.
3. `hasattr(ToolRegistry, "from_config")` — `False`.
4. Прямое использование `ToolRegistry()` (без `default()`) и ручная
   регистрация тулов продолжают работать как раньше — существующий тест
   `test_agent_skeleton.py::test_tool_registry_registers_and_retrieves_tools`
   проходит без изменений.
5. В `src/` и `tests/` не осталось ни одного вхождения подстроки
   `from_config` — ни в коде, ни в докстрингах, ни в именах тестов.
   Проверяется любым поиском по проекту; переносимый вариант, не
   зависящий от наличия `grep`/`rg` в Windows-оболочке:

   ```bash
   python -c "import pathlib; hits=[f'{p}:{i}' for p in list(pathlib.Path('src').rglob('*.py'))+list(pathlib.Path('tests').rglob('*.py')) for i,l in enumerate(p.read_text(encoding='utf-8').splitlines(),1) if 'from_config' in l]; print(hits or 'clean'); raise SystemExit(1 if hits else 0)"
   ```

   Файлы промтов под `prompts/` из проверки исключены намеренно: там
   старое имя обязано остаться — это история решения.

**Проверка.**

Запусти и покажи фактический вывод (не описывай ожидаемое):

```bash
python -m pytest tests/test_tools_natal.py tests/test_agent_skeleton.py -v
```
