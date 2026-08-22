# Промт: скелет агентной архитектуры (intent / tools / interpretation / orchestration)

Ты работаешь над проектом exact-orb. Детерминированное расчётное ядро
(`ephemeris/`, `charts/`, `aspects/`, `configurations/`, `strength/`) уже
готово и в этой задаче не меняется вообще — ни по содержимому, ни по
расположению. `src/exact_orb/llm/` в проекте пока не существует — это
сознательно: LLM-шлюз в этой задаче не реализуется и не запускается
никаким образом, даже частично.

Сейчас нужен только структурный каркас агентной части системы: слои,
которые в будущем превратят запрос пользователя в план интерпретации,
исполнят его через tools и соберут промпт для LLM. Это первый из серии
промтов — здесь **только контракты (pydantic-модели), интерфейсы
(абстрактные классы) и минимальная инфраструктурная логика реестров**
(`register`/`get`, проверка дублей и пустых имён) — без доменной и
orchestration-логики: реальный `plan()`, реальный `run()` конкретного
tool'а, реальный `select()`, реальный `build()`, реальный `handle()` в
этой задаче не пишутся. Конкретные tool-обёртки над `get_natal()`,
правила определения интента, selector'ы и prompt-рецепты — предмет
отдельных, следующих промтов. Если в процессе хочется «заодно»
реализовать что-то из перечисленного — не делай, оставь как есть.

ВАЖНО — LLM ПОКА НЕ СУЩЕСТВУЕТ
`Orchestrator` по контракту из ТЗ должен вызывать LLM Gateway, но
`llm/gateway.py` не реализуется в этом промте и не будет реализован
сразу после него. Поэтому параметр `llm_complete` в конструкторе
`Orchestrator` типизируется как `Callable[..., Any]`, а не
`Callable[..., LLMResponse]` — никакого импорта из `exact_orb.llm` в
этом промте быть не должно. Когда `llm/gateway.py` появится (отдельным
промтом, в неопределённом будущем), потребуется отдельная маленькая
задача — заменить `Any` на `LLMResponse` и добавить импорт. Это
осознанный технический долг, а не недосмотр: не пытайся закрыть его
заранее и не абстрагируй тип через собственный protocol/интерфейс —
просто `Any`.

ВАЖНО — ГРАНИЦЫ МЕЖДУ СЛОЯМИ
Зависимости между новыми пакетами идут строго в одну сторону, циклов
быть не должно:

```
tools/          -> ничего из новых пакетов не импортирует
intent/         -> может импортировать только из tools/
interpretation/ -> может импортировать из intent/ и tools/
orchestration/  -> может импортировать из intent/, tools/, interpretation/
```

`llm/` пока нет ни в проекте, ни в списке зависимостей `orchestration/`
— это добавится вместе с реальным типом `LLMResponse` в будущей задаче.
`tools/` не должен ничего знать про `intent/`, `interpretation/` или
`orchestration/`. Если для скелета кажется, что нужен импорт в обратную
сторону — это сигнал, что тип определён не в том слое; остановись и
следуй схеме выше, а не изобретай исключение.

Решения, которые уже приняты и не обсуждаются в рамках этого промта:
- один субъект (человек) на запрос — не список; расширение под
  синастрию будет отдельной задачей, когда до неё дойдёт очередь;
- каждый контракт живёт в том слое, к которому относится по смыслу
  (`InterpretationPlan` — в `intent/`, `ToolRequest`/`ToolResult` — в
  `tools/`), а не в едином общем модуле;
- `Orchestrator` собирает весь flow из ТЗ целиком, включая шаг
  селекторов (`tool results -> selectors -> evidence -> prompt_builder`)
  — он получает `data_selector` как отдельную зависимость, а не
  предполагает, что selection происходит где-то ещё;
- `DataSelector` в этом скелете — это фасад/диспетчер: один объект,
  который в будущей реализации сам посмотрит на `plan.data_selectors`
  (список id) и внутри себя решит, какие конкретные селекторы применить
  и как объединить их результат. Отдельные конкретные селекторы
  (по одному на `data_selectors`-id) здесь не создаются — это не
  архитектурная нестыковка «один объект против списка id», это
  сознательный выбор диспетчер-паттерна;
- `api/` и `frontend/` в этой задаче не создаются вообще.

ЧТО ВЕРНУТЬ

```
src/exact_orb/
  intent/
    __init__.py
    types.py
    planner.py
  tools/
    __init__.py
    types.py
    base.py
    registry.py
  interpretation/
    __init__.py
    types.py
    selectors.py
    prompt_builder.py
    prompt_registry.py
  orchestration/
    __init__.py
    types.py
    orchestrator.py
```

**`tools/types.py`**
```python
class ToolRequest(BaseModel):
    tool_name: str
    args: dict[str, Any] = Field(default_factory=dict)

class ToolResult(BaseModel):
    tool_name: str
    data: dict[str, Any]
    warnings: list[str] = Field(default_factory=list)
    meta: dict[str, Any] = Field(default_factory=dict)
```

**`tools/base.py`** — абстрактный класс `Tool` с обязательным атрибутом
`name: str` и абстрактным методом `run(self, request: ToolRequest) ->
ToolResult`.

**`tools/registry.py`** — `ToolRegistry` с методами `register(tool: Tool)
-> None`, `get(name: str) -> Tool`, `list_tools() -> list[str]` (возвращает
имена **отсортированными** — `sorted(...)`, а не в порядке регистрации,
чтобы тесты и будущие потребители не зависели от порядка вызовов
`register()`). Заведи именованные исключения, не общий `Exception`:

```python
class UnknownToolError(KeyError):
    """get() для незарегистрированного имени."""

class DuplicateToolError(ValueError):
    """register() для уже занятого имени — не молчаливая перезапись."""

class InvalidToolError(ValueError):
    """register() для tool с пустым/пробельным name."""
```

`get()` кидает `UnknownToolError`; `register()` для уже занятого `name`
— `DuplicateToolError`; `register()` для tool, у которого `name` пустая
строка или состоит только из пробелов, — `InvalidToolError`.

**`intent/types.py`**
```python
class UserRequest(BaseModel):
    text: str
    subject: dict[str, Any] | None = None
    # Форма subject временная (birth data одного человека). Она будет
    # уточнена, когда появится NatalTool — не проектируй её здесь.

class InterpretationPlan(BaseModel):
    intent: str
    focus: str | None = None
    required_tools: list[ToolRequest]
    data_selectors: list[str]
    prompt_recipe: str
    missing_slots: list[str] = Field(default_factory=list)
    output_format: str = "prose"
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
```

`intent`, `focus`, `prompt_recipe`, `data_selectors`, `output_format`
намеренно остаются строками — не превращай их в enum в этом промте,
раннее ограничение набора значений сделает расширение (соляр, синастрия,
новые фокусы) более трудоёмким, чем оно того стоит сейчас. `confidence`
— единственное поле, где граница значений оправдана уже на этом шаге:
без неё план с `confidence=42` пройдёт валидацию молча.

**`intent/planner.py`** — абстрактный класс `Planner` с абстрактным
методом `plan(self, request: UserRequest) -> InterpretationPlan`. Ни
`RuleBasedPlanner`, ни `LLMPlanner` здесь не появляются.

**`interpretation/types.py`**
```python
class PromptBundle(BaseModel):
    system: str
    user: str
    recipe_id: str
```

**`interpretation/selectors.py`** — абстрактный класс `DataSelector` с
методом `select(self, plan: InterpretationPlan, tool_results:
list[ToolResult]) -> dict[str, Any]`. В докстринге класса зафиксируй его
роль фасада/диспетчера (см. раздел выше про уже принятые решения) —
чтобы при следующем наполнении не возник вопрос, почему их не несколько.

**`interpretation/prompt_builder.py`** — абстрактный класс
`PromptBuilder` с методом `build(self, plan: InterpretationPlan,
evidence: dict[str, Any]) -> PromptBundle`.

**`interpretation/prompt_registry.py`** — класс `PromptRegistry`,
хранящий **только** соответствие `recipe_id: str -> Any` (внутри —
обычный `dict`). Методы и ошибки — по образцу `ToolRegistry`,
симметрично:

```python
class UnknownPromptRecipeError(KeyError):
    """get() для незарегистрированного recipe_id."""

class DuplicatePromptRecipeError(ValueError):
    """register() для уже занятого recipe_id."""

class InvalidPromptRecipeError(ValueError):
    """register() для пустого/пробельного recipe_id."""
```

`register(recipe_id: str, recipe: Any) -> None`, `get(recipe_id: str) ->
Any`. Повторный `register()` того же `recipe_id` — `DuplicatePromptRecipeError`,
`get()` для отсутствующего — `UnknownPromptRecipeError`, `register()`
для `recipe_id`, который пустой или состоит только из пробелов, —
`InvalidPromptRecipeError` (симметрично `InvalidToolError` у
`ToolRegistry`). Явно закомментируй в коде: состав и семантика самого
recipe (`base/rules/focus/style/format`) в этой задаче не
проектируются — тип значения намеренно `Any` до отдельного промта.

**`orchestration/types.py`**
```python
class OrchestrationResponse(BaseModel):
    text: str
    plan: InterpretationPlan
    warnings: list[str] = Field(default_factory=list)
    meta: dict[str, Any] = Field(default_factory=dict)
```

**`orchestration/orchestrator.py`** — класс `Orchestrator`, конструктор
принимает `planner: Planner`, `tool_registry: ToolRegistry`,
`data_selector: DataSelector`, `prompt_builder: PromptBuilder`,
`llm_complete: Callable[..., Any]` (см. раздел «LLM пока не существует»
выше — не импортируй ничего из `exact_orb.llm`). Конструктор только
сохраняет все пять зависимостей в атрибуты (`self.planner = planner` и
т.д.) — ничего не вызывает, ничего не выполняет; пустой `__init__` без
сохранения атрибутов — ошибка, даже если тесты на конструктор формально
пройдут, класс должен быть пригоден для использования в следующем
промте. Метод `handle(self, request: UserRequest) ->
OrchestrationResponse` пока не реализован — тело кидает
`NotImplementedError`, но в docstring зафиксируй по шагам контракт
будущей реализации, дословно опираясь на последовательность из ТЗ и
явно называя, где в ней встаёт `data_selector`:

```
1. plan = planner.plan(request)
2. проверить plan.missing_slots
3. резолвнуть и провалидировать tools через tool_registry по plan.required_tools
4. запустить tools -> list[ToolResult]
5. evidence = data_selector.select(plan, tool_results)
6. bundle = prompt_builder.build(plan, evidence)
7. response = llm_complete(bundle.user, system=bundle.system, ...)
8. собрать и вернуть OrchestrationResponse
```

Каждый `__init__.py` реэкспортирует публичные имена своего пакета (по
образцу того, как это уже сделано в `exact_orb/aspects/__init__.py` и
`exact_orb/configurations/__init__.py` — открой их и следуй тому же
подходу, а не придумывай новый стиль).

ПРОВЕРКА

1. Весь существующий `pytest` остаётся зелёным без единого изменения в
   существующих тестах — это подтверждает, что новые пакеты никак не
   задели расчётное ядро.
2. Новый файл `tests/test_agent_skeleton.py`:
   - создать `ToolRequest`, `ToolResult`, `UserRequest`,
     `InterpretationPlan`, `OrchestrationResponse` с правдоподобными
     примерными значениями и проверить, что поля (включая дефолты
     `missing_slots`, `output_format`, `confidence`, `warnings`, `meta`)
     выставились корректно;
   - проверить, что `InterpretationPlan(..., confidence=42)` (или любое
     значение вне `[0.0, 1.0]`) кидает `ValidationError`;
   - убедиться, что `Tool()`, `Planner()`, `DataSelector()`,
     `PromptBuilder()` нельзя инстанцировать напрямую — попытка должна
     кидать `TypeError`. Это работает только если абстрактные методы
     оформлены через `abc.ABC` + `@abstractmethod` (проверь, что не
     забыл унаследоваться от `ABC`); тело самого абстрактного метода —
     `raise NotImplementedError(...)` или пустой `...`, на выбор, это не
     влияет на проверку — важен именно декоратор и базовый класс, а не
     содержимое тела;
   - написать минимальный dummy-наследник `Tool` прямо в тесте,
     зарегистрировать 2-3 таких tool'а с разными именами в
     `ToolRegistry`, получить каждый обратно через `get()`, проверить,
     что `list_tools()` вернул имена отсортированными, что `get()` для
     незарегистрированного имени кидает `UnknownToolError`, повторная
     регистрация того же `name` — `DuplicateToolError`, а регистрация
     tool с пустым/пробельным `name` — `InvalidToolError`;
   - то же самое для `PromptRegistry`: `register`/`get` работают,
     `get()` для отсутствующего `recipe_id` кидает
     `UnknownPromptRecipeError`, повторный `register()` того же
     `recipe_id` — `DuplicatePromptRecipeError`, а `register()` с
     пустым/пробельным `recipe_id` — `InvalidPromptRecipeError`;
   - создать `Orchestrator(...)` с dummy-объектами планера/registry/
     data_selector/prompt_builder и заглушкой `llm_complete` (обычная
     функция или лямбда — не мок `litellm`, он тут не нужен и не должен
     импортироваться), вызвать `handle(...)` и убедиться, что кидается
     именно `NotImplementedError`.
3. Запусти по отдельности `python -c "import exact_orb.orchestration"`
   (и аналогично для `intent`, `tools`, `interpretation`) — все четыре
   должны импортироваться без `ImportError`; это ловит случайные
   циклические импорты между слоями и заодно подтверждает, что никто из
   них не тянет за собой `exact_orb.llm`.

ОГРАНИЧЕНИЯ
Не реализуй `NatalTool`/`TransitsTool`, `RuleBasedPlanner`, никакие
конкретные selector'ы или содержимое prompt recipe — это отдельные
промты. Не создавай `src/exact_orb/llm/`, ничего в него не пиши и
ничего из него не импортируй — это отдельная, сознательно отложенная
задача. Не трогай `ephemeris/`, `charts/`, `aspects/`, `configurations/`,
`strength/`, `cli.py`, `config.py`, `logging_setup.py`, `pyproject.toml`.
Не создавай `api/` и `frontend/`. Не придумывай форму `subject` сверх
`dict[str, Any] | None` и не переводи `UserRequest` на список субъектов
— это сознательно отложено. Не превращай `intent`, `focus`,
`prompt_recipe`, `data_selectors`, `output_format` в enum. Не пиши
реальную логику `plan()`, `run()`, `select()`, `build()`, `handle()` —
только регистры (`ToolRegistry`, `PromptRegistry`) содержат минимальную
рабочую логику хранения и проверки дублей/пустых имён, всё остальное —
интерфейсы с `NotImplementedError`/`abstractmethod`. Если какой-то
контракт кажется неполным для будущих соляра/синастрии или для будущего
`LLMResponse` — не расширяй его сейчас, просто ничего не ломай, чтобы
расширение было дешёвым потом.
