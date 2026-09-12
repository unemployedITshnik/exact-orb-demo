# exact-orb — требования к `BuildNatalHandler`

**Статус:** функциональная реализация сверена; обязательный import-boundary gate из §10.1 ещё не перенесён в текущую ветку

**Дата:** 2026-09-08

**Ревизия:** 2026-09-09, R5 — документ сверен с реализацией: уточнены планируемый статус `ApplicationResult`, фактические границы валидации вспомогательных моделей и тестирование defense-in-depth; в актуальных диаграммах разграничены реализованный handler и целевой внешний поток

**Область:** изолированное проектирование `BuildNatalHandler`

**Целевой модуль:** `src/exact_orb/application/handlers/build_natal.py`

**Исходная база проектирования:** ветка `main`, commit `c1e407581d51ab71de6ce57d2aebdb2f7098ca3a`

**Сверено с реализацией:** commit `9b7a4179fa10ebda066ff998294b05aaa8930fd2`

**Ограничение:** документ не требует изменения уже реализованных модулей `birth`, `calculation` и `session`.

`ApplicationOrchestrator` и внешний `ApplicationResult` ниже описывают целевую
границу, принятую ADR-0006, но на указанном commit ещё не реализованы. Текущий
реализованный срез заканчивается на `BuildNatalOutcome`.

## 1. Назначение

`BuildNatalHandler` реализует прикладной use case построения базовой карты рождения.

Handler должен:

1. принять структурированные данные рождения;
2. разрешить место, координаты и историческое локальное время;
3. определить вид базовой карты:
   - натальная карта при известном времени рождения;
   - космограмма при неизвестном времени;
4. получить воспроизводимый `ChartArtifact`;
5. сформировать полную замену изменяемой части состояния в виде `StateDelta`;
6. вернуть типизированный исход целевому вызывающему `ApplicationOrchestrator`.

Handler не завершает пользовательскую операцию самостоятельно. Успешный результат handler означает только, что карта рассчитана или восстановлена из кэша, а изменение состояния подготовлено к commit.

Окончательный `Success` сможет вернуть только `ApplicationOrchestrator` после подтверждённого сохранения `StateDelta`.

## 2. Граница модуля

```text
BuildNatalCommand
        ↓
BuildNatalHandler
        ├── BirthDataResolver
        └── ChartArtifactResolver
                ↓
BuildNatalSuccess {
    artifact,
    delta
}
```

Handler является координатором одного конкретного use case, но не глобальным оркестратором системы.

## 3. Зависимости

### 3.1. Структура нового application-пакета

```text
src/exact_orb/application/
    __init__.py
    commands.py
    results.py
    ports.py
    handlers/
        __init__.py
        build_natal.py
```

| Тип | Модуль |
|---|---|
| `Command` | `exact_orb.application.commands` |
| `BuildNatalCommand` | `exact_orb.application.commands` |
| `BuildNatalSuccess` | `exact_orb.application.results` |
| `BuildNatalOutcome` | `exact_orb.application.results` |
| `ApplicationResult` | `exact_orb.application.results` — целевое размещение; тип появится вместе с `ApplicationOrchestrator` и сейчас не реализован |
| `Handler` | `exact_orb.application.ports` |
| `BirthDataResolverPort` | `exact_orb.application.ports` |
| `ChartArtifactPort` | `exact_orb.application.ports` |
| `BuildNatalHandler` | `exact_orb.application.handlers.build_natal` |

Имя `BuildNatalResult` не используется. `BuildNatalSuccess` обозначает успешный внутренний результат handler, а `BuildNatalOutcome` — полный union его исходов.

Существующий `RunContext` остаётся в `exact_orb.run_context`. В пакете `application` второй `RunContext` не создаётся.

`Command` объявляется в `application/commands.py`, а не в `ports.py`. Все application-команды неизменяемы:

```python
class Command(BaseModel):
    model_config = ConfigDict(frozen=True)


class BuildNatalCommand(Command):
    birth_input: BirthInput
```

### 3.2. Порты handler

Handler зависит от структурных портов, а не от конкретных классов соседних модулей:

```python
CommandT = TypeVar("CommandT", bound=Command, contravariant=True)
OutcomeT = TypeVar("OutcomeT", covariant=True)


class Handler(Protocol[CommandT, OutcomeT]):
    async def handle(
        self,
        command: CommandT,
        state: SessionState,
        run: RunContext,
    ) -> OutcomeT:
        ...


class BirthDataResolverPort(Protocol):
    async def resolve(
        self,
        birth_input: BirthInput,
        *,
        run: RunContext | None = None,
    ) -> ResolvedBirthData | InputRequired | ResolutionUnavailable:
        ...


class ChartArtifactPort(Protocol):
    async def ensure_chart(
        self,
        spec: ChartSpec,
        resolved: ResolvedBirthData,
        *,
        run: RunContext,
    ) -> ChartArtifact:
        ...
```

Реализованные `BirthDataResolver` и `ChartArtifactResolver` удовлетворяют этим протоколам структурно и не требуют изменения.

`Handler`, `BirthDataResolverPort` и `ChartArtifactPort` не помечаются `@runtime_checkable`: система не выполняет `isinstance(..., Protocol)`. Совместимость обеспечивается статической проверкой типов и contract-тестами. Это намеренное решение, а не пропущенный декоратор.

Оба порта должны быть защищены денилистом импортов симметрично: прямой импорт как `exact_orb.calculation.artifacts`, так и `exact_orb.birth.resolver` в модуле handler запрещён (§10.1). Фактические импорты handler это ограничение соблюдают, но обязательный regression-тест на сверенном commit отсутствует. Конкретные реализации поступают только через конструктор.

`BuildNatalCommand` наследуется от `Command`. Целевой реестр `ApplicationOrchestrator` будет маршрутизировать команды по типу через `Mapping[type[Command], Handler]`; строковый routing и LLM для выбора handler не используются.

```python
class BuildNatalHandler:
    def __init__(
        self,
        *,
        resolver: BirthDataResolverPort,
        artifacts: ChartArtifactPort,
    ) -> None:
        ...
```

### 3.3. `BirthDataResolver`

Используется для преобразования:

```text
BirthInput
    →
ResolvedBirthData
| InputRequired
| ResolutionUnavailable
```

Handler не должен повторять внутри себя:

- проверку даты;
- проверку `place_id`;
- поиск места;
- определение координат;
- определение `tz_id`;
- расчёт исторического UTC offset;
- обработку ambiguous/nonexistent local time;
- назначение технического полудня при неизвестном времени.

Это ответственность реализованного `BirthDataResolver`.

### 3.4. `ChartArtifactResolver`

Используется как единственная точка получения карты:

```python
await artifacts.ensure_chart(
    spec,
    resolved,
    run=run,
)
```

Handler не должен напрямую обращаться к:

- `CalculationCache`;
- `CalculationEnginePort`;
- `EngineService`;
- `calculate_natal()`;
- Swiss Ephemeris;
- функциям формирования `calculation_key`;
- `CalculationVersion` и отпечатку расчётного окружения;
- codec артефактов.

Cache hit, cache miss, single-flight, восстановление после corrupt/stale cache и fail-open кэша остаются внутри `ChartArtifactResolver`.

## 4. Входной контракт handler

```python
async def handle(
    self,
    command: BuildNatalCommand,
    state: SessionState,
    run: RunContext,
) -> BuildNatalOutcome:
    ...
```

### 4.1. `BuildNatalCommand`

Команда содержит только пользовательское намерение — построить карту по переданному `BirthInput`.

В команду не входят:

- `session_id`;
- `state_version`;
- `run_id`;
- настройки кэша;
- параметры движка;
- технические параметры сохранения состояния.

### 4.2. `SessionState`

Handler получает снимок текущего состояния, но в первой версии:

- не мутирует его;
- не увеличивает `state_version`;
- не формирует `ChartRef`;
- не определяет результат CAS;
- не сохраняет состояние.

Наличие `state` в контракте сохраняет единый интерфейс application handlers и возможность будущих проверок. Текущий build-use-case не использует состояние как источник новых данных: источником является `command.birth_input`.

Handler также не валидирует состояние и не должен:

- проверять `expires_at` или `hard_expires_at`;
- сравнивать `state_version`;
- сравнивать `state.birth_input` с командой;
- проверять наличие `base_chart`;
- определять эквивалентность команды текущему состоянию;
- самостоятельно возвращать `AlreadyApplied`.

Истечение сессии и конкурентность проверяются `ContextService` и session-слоем.

### 4.3. `RunContext`

Handler обязан передавать тот же `RunContext` в нижележащие операции.

`run_id` используется только как correlation identifier для наблюдаемости и не влияет:

- на вид карты;
- на расчёт;
- на `calculation_key`;
- на содержимое `StateDelta`;
- на идемпотентность операции.

## 5. Выходной контракт handler

```text
BuildNatalOutcome =
      BuildNatalSuccess
    | InputRequired
    | ResolutionUnavailable
    | CalculationFailed
```

Успешный внутренний исход:

```python
class BuildNatalSuccess(BaseModel):
    artifact: ChartArtifact
    delta: StateDelta
```

`BuildNatalSuccess` — внутренний application-результат handler. Он не равен целевому внешнему `Success` из ещё не реализованного `ApplicationResult`.

`BuildNatalSuccess` должен быть frozen Pydantic-моделью с проверкой согласованности application-границы:

```python
@model_validator(mode="after")
def _result_must_be_consistent(self) -> Self:
    delta = self.delta

    # Проверка выполняется первой: успешный build не принимает RESET_DELTA.
    if (
        delta.birth_input is None
        or delta.birth_resolved is None
        or delta.base_chart_spec is None
    ):
        raise ValueError(
            "successful build requires a fully populated StateDelta"
        )

    if self.artifact.spec != delta.base_chart_spec:
        raise ValueError(
            "artifact.spec must equal delta.base_chart_spec"
        )

    expected_chart_kind = (
        "cosmogram" if delta.birth_resolved.time_unknown else "natal"
    )
    if delta.base_chart_spec.chart_kind != expected_chart_kind:
        raise ValueError(
            "delta.base_chart_spec.chart_kind must match "
            "delta.birth_resolved.time_unknown"
        )

    if ((delta.birth_input.birth_time is None)
            != delta.birth_resolved.time_unknown):
        raise ValueError(
            "delta.birth_input.birth_time must match "
            "delta.birth_resolved.time_unknown"
        )

    resolved_input = calculation_input_from(delta.birth_resolved)
    if calculation_input_from_chart(self.artifact.chart) != resolved_input:
        raise ValueError(
            "artifact.chart calculation input must equal "
            "delta.birth_resolved calculation input"
        )

    expected_key = calculation_key(
        resolved_input,
        delta.base_chart_spec,
        self.artifact.calculation_version,
    )
    if self.artifact.calculation_key != expected_key:
        raise ValueError(
            "artifact.calculation_key must match delta birth data, spec, "
            "and calculation version"
        )

    return self
```

Все нарушения поднимаются как `ValueError`, который Pydantic преобразует в
`ValidationError`. Проверка заполненности выполняется до обращения к полям
delta, поэтому пустая дельта не может породить сырой `TypeError`.

Локальные validators `ChartArtifact` уже доказывают соответствие chart/spec и
собственного ключа артефакта. Проверки выше имеют другую ответственность: они
не позволяют соединить локально валидный артефакт с чужими resolved data или
delta (ADR-0027).

Handler не выполняет эти проверки отдельными `if`: валидность обеспечивается выходной моделью. Внутренняя согласованность `ChartArtifact` уже обеспечивается расчётным слоем.

## 6. Основной алгоритм

### Шаг 1. Разрешить данные рождения

```python
resolution = await resolver.resolve(
    command.birth_input,
    run=run,
)
```

Если результат:

- `InputRequired` — немедленно вернуть этот результат;
- `ResolutionUnavailable` — немедленно вернуть этот результат;
- `ResolvedBirthData` — продолжить выполнение.

При неуспешном резолве `ChartArtifactResolver` вызываться не должен.

### Шаг 2. Определить вид карты

```text
resolved.time_unknown == False → chart_kind = "natal"
resolved.time_unknown == True  → chart_kind = "cosmogram"
```

Отсутствие времени рождения является допустимым завершённым вводом и не должно приводить к `InputRequired`.

Handler не должен проверять `command.birth_input.birth_time` повторно. Источником окончательного решения является `resolved.time_unknown`, установленный резолвером.

### Шаг 3. Построить `NatalChartSpec`

Для известного времени результирующая спецификация должна быть эквивалентна:

```python
NatalChartSpec(
    chart_kind="natal",
    include=(
        "aspects",
        "configurations",
        "houses",
        "positions",
        "rulers",
        "strength",
    ),
    house_system="P",
    rulership="combined",
    near_interception_threshold=1.0,
)
```

Для неизвестного времени:

```python
NatalChartSpec(
    chart_kind="cosmogram",
    include=(
        "aspects",
        "configurations",
        "positions",
    ),
    house_system="P",
    rulership="combined",
    near_interception_threshold=1.0,
)
```

Handler не должен дублировать уже реализованные правила нормализации `include`. Допустимо создавать:

```python
NatalChartSpec(chart_kind=chart_kind)
```

если итоговая спецификация получает канонический `include` через существующий контракт `NatalChartSpec` и `DEFAULT_INCLUDE_BY_CHART_KIND`.

Текущий handler не переопределяет остальные параметры модели, поэтому
`near_interception_threshold` всегда получает каноническое значение `1.0`.

Критично проверять итоговое значение спецификации, а не конкретный способ её создания.

### Шаг 4. Получить артефакт

```python
artifact = await artifacts.ensure_chart(
    spec,
    resolved,
    run=run,
)
```

Handler не должен различать cache hit и cache miss: оба являются одинаково успешным получением `ChartArtifact`.

### Шаг 5. Сформировать `StateDelta`

```python
delta = StateDelta(
    birth_input=command.birth_input,
    birth_resolved=resolved,
    base_chart_spec=spec,
)
```

В дельту входит `ChartSpec`, а не:

- `calculation_key`;
- весь `ChartArtifact`;
- рассчитанная карта;
- сериализованные байты кэша;
- новый `state_version`;
- `ChartRef`.

`StateDelta` должна быть сформирована только после успешного получения артефакта.

Предупреждения не преобразуются и не объединяются:

- `ResolvedBirthData.warnings` сохраняются внутри `delta.birth_resolved`;
- `NatalChart.warnings` сохраняются внутри `artifact.chart`;
- отдельное поле `BuildNatalSuccess.warnings` не создаётся;
- handler не удаляет, не переводит и не интерпретирует предупреждения.

### Шаг 6. Вернуть успешный внутренний исход

```python
return BuildNatalSuccess(
    artifact=artifact,
    delta=delta,
)
```

## 7. Обработка исходов резолва

### 7.1. `InputRequired`

Handler должен вернуть полученный от `BirthDataResolver` объект `InputRequired` без:

- изменения типа;
- обобщения кодов;
- замены путей `issues[].field`;
- превращения нескольких issues в одно;
- добавления пользовательских текстов;
- попытки самостоятельно исправить ввод.

Возможные примеры:

```text
birth.date  + UNSUPPORTED
birth.date  + INVALID
birth.place + INVALID
birth.time  + INVALID
birth.time  + AMBIGUOUS
```

Для текущей реализации `BirthDataResolver` достижимы коды `INVALID`, `AMBIGUOUS` и `UNSUPPORTED`. Код `MISSING` остаётся частью общей модели `IssueCode`, но после успешного создания обязательного `BirthInput` текущим резолвером не возвращается.

`AMBIGUOUS` не возникает при разрешении места, потому что Build API принимает выбранный `place_id`. При этом он возникает для удвоенного локального времени. Handler должен пропускать такой исход без изменений.

### 7.2. `ResolutionUnavailable`

Handler должен вернуть `ResolutionUnavailable` без преобразования в `InputRequired`.

Примеры:

```text
PLACE_CATALOG_UNAVAILABLE
UNKNOWN_TIMEZONE
```

Техническая недоступность зависимости не является ошибкой пользователя.

### 7.3. Исключения резолвера

Handler обрабатывает только объявленные результаты `InputRequired` и `ResolutionUnavailable`.

Любое исключение, поднятое `BirthDataResolverPort.resolve`, не перехватывается handler и не преобразуется ни в `InputRequired`, ни в `ResolutionUnavailable`. Это относится, в частности, к нарушению внутреннего контракта резолвера и к необработанным исключениям будущих адаптеров каталога.

Единственное действие handler на этом пути — запись terminal event `build_natal_failed` (§11) с последующим повторным поднятием исключения.

## 8. Обработка ошибок расчётного блока

`ChartArtifactResolver.ensure_chart()` возвращает `ChartArtifact` либо поднимает
`ChartCalculationError` / `CalculationUnavailableError`. Ответственность за преобразование
этих типизированных исключений в `CalculationFailed` располагается в
`BuildNatalHandler`; актуальные sequence-диаграммы отражают именно этот контракт.

`BuildNatalCommand` содержит только `birth_input`. В целевом внешнем orchestration-потоке
`session_id` передаётся `ApplicationOrchestrator` отдельным доверенным аргументом.
`AMBIGUOUS` не возникает при разрешении уже выбранного `place_id`, но остаётся допустимым
исходом для удвоенного локального времени.

### 8.1. `ChartCalculationError`

Handler должен перехватить `ChartCalculationError` и вернуть:

```python
CalculationFailed(error_code=error.code)
```

Текущие коды:

```text
SPEC_INVALID
GEOGRAPHY_INVALID
HOUSES_DEGENERATE
ENGINE_UNEXPECTED
```

### 8.2. `CalculationUnavailableError`

Handler должен перехватить `CalculationUnavailableError` и вернуть:

```python
CalculationFailed(error_code=error.code)
```

Текущий код:

```text
EPHEMERIS_UNAVAILABLE
```

Это сохраняет существующий application-контракт, где отдельного `CalculationUnavailable` пока нет. В дальнейшем можно отдельно решить, нужен ли retryable-признак для недоступности эфемерид. Для первой реализации расширять существующие outcomes не требуется.

### 8.3. Неизвестные исключения

Handler не добавляет собственной маскировки исключений и не должен использовать конструкцию вида:

```python
except Exception:
    return CalculationFailed(...)
```

Неизвестное исключение, непосредственно дошедшее до handler, должно распространяться наверх. Это требование описывает только границу handler.

`EngineService` уже преобразует любые неизвестные исключения внутри расчётного стека в
`ChartCalculationError("ENGINE_UNEXPECTED", run_id="<run_id>")`. Такой результат приходит
в handler как типизированная ошибка и преобразуется в
`CalculationFailed(error_code="ENGINE_UNEXPECTED")`. Handler не может и не должен
восстанавливать первоначальный тип исключения.

`ENGINE_UNEXPECTED` считается замаскированным техническим дефектом, а не штатным предметным исходом:

- `EngineService` сохраняет фактический `exception_type` в событии `calculation_failed`;
- terminal event handler должен иметь уровень `ERROR` (§11).

Требования к алертингу по этому коду лежат вне границы handler и вынесены в §14.4.

Handler не должен подавлять `asyncio.CancelledError`.

### 8.4. Тайм-ауты и семантика отмены

- Handler не устанавливает собственный timeout на `resolve` и `ensure_chart`.
- Timeout policy принадлежит transport/application orchestration либо адаптерам зависимостей.
- `asyncio.CancelledError` распространяется наверх.
- Handler не пытается отменять single-flight leader внутри `ChartArtifactResolver`.
- Продолжение защищённой leader task после отмены отдельного waiter является ожидаемой семантикой `asyncio.shield`, а не утечкой задачи.

## 9. Проверяемые инварианты

| ID | Требование |
|---|---|
| BH-1 | Техническая ошибка не преобразуется в `InputRequired` |
| BH-2 | При неуспешном резолве расчётный блок не вызывается |
| BH-3 | Известное время всегда формирует `chart_kind="natal"` |
| BH-4 | Неизвестное время всегда формирует `chart_kind="cosmogram"` |
| BH-5 | Неизвестное время не вызывает запрос уточнения |
| BH-6 | Вид карты определяется явным `chart_kind`, а не наличием домов в результате |
| BH-7 | `BuildNatalSuccess` требует all-set delta и не допускает расхождения `artifact.spec` и `delta.base_chart_spec` |
| BH-8 | `BuildNatalSuccess` связывает kind/time_unknown, наличие исходного времени, нормализованные время и координаты карты с `birth_resolved`, а `calculation_key` — с delta/spec/version |
| BH-9 | `StateDelta` формируется только после получения артефакта |
| BH-10 | Handler не вызывает `apply_delta`/`touched`, не создаёт производный `SessionState`/`ChartRef` и не передаёт state зависимостям |
| BH-11 | Handler не вычисляет и не увеличивает `state_version` |
| BH-12 | Handler не обращается к Session Store или `ContextService` |
| BH-13 | Handler не обращается напрямую к cache или engine |
| BH-14 | Handler не вызывает Agent Runtime или LLM |
| BH-15 | Один и тот же `RunContext` передаётся в resolver и artifact resolver |
| BH-16 | Полные входы и выходы handler, включая персональные и расчётные данные, разрешены только в `component_message` уровня DEBUG; INFO/WARNING terminal events остаются компактными |
| BH-17 | Cache hit и cache miss дают одинаковый тип успешного результата |
| BH-18 | Handler не реализует собственную дедупликацию или идемпотентность; повторный вызов выполняет тот же use case через воспроизводимый artifact resolver |
| BH-19 | Каждый начатый прогон завершается ровно одним terminal event: `build_natal_completed` либо `build_natal_failed` |

Распределение ответственности за BH-7/BH-8:

- `EngineService` проверяет карту против spec и resolved;
- `ChartArtifact` гарантирует соответствие chart/spec и пересчитывает свой ключ из chart/spec/version;
- `ChartArtifactResolver` не возвращает cache hit с чужими key/spec/version или расчётным входом;
- `BuildNatalSuccess` дополнительно связывает артефакт со всеми расчётно значимыми полями подготовленной handler дельты;
- handler не дублирует эти проверки процедурным кодом.

## 10. Что не входит в ответственность handler

`BuildNatalHandler` не должен:

- создавать или восстанавливать сессию;
- читать `SessionStore`;
- выполнять `ContextService.load`;
- выполнять commit;
- вызывать compare-and-set;
- классифицировать `AlreadyApplied` и `Superseded`;
- определять, истекла ли сессия;
- увеличивать `state_version`;
- создавать `ChartRef`;
- выполнять retry commit;
- управлять `BuildAttempt` или `build_revision`;
- реализовывать `latest request wins`;
- выполнять rate limiting;
- разбирать HTTP-запрос;
- назначать HTTP-коды;
- формировать frontend DTO;
- рендерить карту;
- запускать `Agent Runtime`;
- выбирать tools или сценарии;
- выполнять интерпретацию;
- вызывать LLM;
- управлять подпиской или token budget.

Сценарии `Superseded`, `AlreadyApplied`, `StateCommitFailed` и `SessionAbsent` не являются исходами handler. Они возникают позже — при сохранении дельты оркестратором.

### 10.1. Граница импортов

Запрет применяется к конкретному модулю:

```text
exact_orb.application.handlers.build_natal
```

**Проверка выполняется по списку запрещённых модулей (денилист).** Этот модуль не объявляет прямые импорты из:

```text
exact_orb.birth.resolver
exact_orb.calculation.artifacts
exact_orb.calculation.cache
exact_orb.calculation.codec
exact_orb.calculation.engine
exact_orb.calculation.keys
exact_orb.calculation.version
exact_orb.engine
exact_orb.ephemeris_runtime
exact_orb.swiss_backend
exact_orb.intent
exact_orb.interpretation
exact_orb.llm
exact_orb.orchestration
exact_orb.tools
exact_orb.cli
exact_orb.config
exact_orb.session.store
exact_orb.session.context
exact_orb.session.persistence
exact_orb.session.adapters
```

Сравнение префиксное, поэтому `exact_orb.engine` покрывает `exact_orb.engine.charts`, а `exact_orb.session.adapters` — все адаптеры; отдельные подмодули в список не выносятся.

Список запрещает обе конкретные реализации портов — `exact_orb.birth.resolver` и `exact_orb.calculation.artifacts`. Это принципиально: без первого порт `BirthDataResolverPort` был бы защищён только на бумаге. Обе реализации поступают в handler через конструктор (§3.2).

Заведомо разрешённые контрактные модули — **список неисчерпывающий**, он приведён как ориентир, а не как allowlist:

```text
exact_orb.outcomes            # InputRequired, ResolutionUnavailable, CalculationFailed
exact_orb.run_context         # RunContext
exact_orb.session.state       # SessionState, StateDelta
exact_orb.calculation.spec    # NatalChartSpec, ChartSpec
exact_orb.calculation.errors  # ChartCalculationError, CalculationUnavailableError
exact_orb.application.commands
exact_orb.application.ports
exact_orb.application.results
```

`exact_orb.calculation.types` handler-у прямо не требуется: тип артефакта выводится из `ChartArtifactPort.ensure_chart`. Модуль намеренно не внесён в денилист — его прямой импорт для аннотации допустим, поскольку `application/results.py` импортирует его в любом случае (§10.2) и никакой дополнительной связности это не создаёт.

Проверка в `tests/test_module_boundaries.py` должна быть привязана к `application/handlers/build_natal.py` и использовать отдельную константу:

```text
APPLICATION_BUILD_NATAL_FORBIDDEN_DIRECT_IMPORTS
```

На сверенном commit `9b7a4179fa10ebda066ff998294b05aaa8930fd2` эта константа и
обязательная автоматическая проверка ещё отсутствуют. Прямые импорты handler соответствуют
описанной границе при ручной сверке, но до переноса regression-теста критерий §13 формально
не выполнен.

Общий запрет для всего пакета `application` некорректен: `application/results.py` обязан импортировать `exact_orb.calculation.types`, поскольку Pydantic-модель `BuildNatalSuccess` содержит настоящий `ChartArtifact`.

### 10.2. Неизбежный транзитивный native import

Выбранный выходной контракт образует runtime-цепочку:

```text
BuildNatalSuccess
→ ChartArtifact
→ exact_orb.calculation.types
→ exact_orb.engine.charts.natal
→ swiss_backend
→ swisseph
```

Для Pydantic-модели `ChartArtifact` необходим во время выполнения для построения и валидации схемы, поэтому `TYPE_CHECKING` не устраняет эту цепочку.

Транзитивное присутствие `engine.charts` и `swisseph` в import-графе `application.results` является следствием принятого контракта, а не исключением из требования. Полная изоляция application-слоя от native-стека потребовала бы отдельного application DTO вместо `ChartArtifact`; такое изменение находится вне текущего проектирования.

## 11. Наблюдаемость

Handler должен вести технический журнал операции с использованием `run.run_id`.

Handler пишет три собственных события:

```text
build_natal_started
build_natal_completed
build_natal_failed
```

`build_natal_started` пишется на уровне `DEBUG` перед вызовом resolver. При каждом завершении после него должен быть записан ровно один terminal event:

- `build_natal_completed` — при типизированном `BuildNatalOutcome`;
- `build_natal_failed` — при исключении или отмене.

| Исход | Уровень | `outcome` | `chart_kind` |
|---|---|---|---|
| `BuildNatalSuccess` | `INFO` | `success` | Обязательно |
| `InputRequired` | `INFO` | `input_required` | Отсутствует |
| `ResolutionUnavailable` | `WARNING` | `resolution_unavailable` | Отсутствует |
| `CalculationFailed` кроме `ENGINE_UNEXPECTED` | `WARNING` | `calculation_failed` | Обязательно |
| `CalculationFailed("ENGINE_UNEXPECTED")` | `ERROR` | `calculation_failed` | Обязательно |

Нештатное завершение:

| Ситуация | Событие | Уровень | Дальнейшее действие |
|---|---|---|---|
| Неизвестное исключение на любом шаге handler | `build_natal_failed` | `ERROR` | Повторно поднять исключение |
| `asyncio.CancelledError` | `build_natal_failed` | `WARNING` | Повторно поднять отмену |

**Перехват выполняется через `except BaseException`, а не `except Exception`.** `asyncio.CancelledError` наследуется от `BaseException`, поэтому `except Exception` отмену не поймает и terminal event записан не будет. Повторное поднятие обязательно во всех ветках:

```python
except BaseException as exc:
    self._log_failed(run, stage, exc, started_at)
    raise
```

Это не противоречит §8.3: маскировки нет, исключение уходит наверх неизменным, добавляется только запись в журнал. Обратите внимание, что в `EngineService.calculate` используется `except Exception` — там это осознанное решение, чтобы вовсе не касаться отмены; здесь требование противоположное.

Отмена может быть следствием закрытия клиентского соединения и не считается системной аварией, поэтому журналируется на уровне `WARNING`, а не `ERROR`.

Поля terminal event:

- `run_id` — обязательно;
- `outcome` — обязательно;
- `duration_ms` — обязательно;
- `chart_kind` — только после успешного резолва;
- `calculation_key` — только для `BuildNatalSuccess`, полный ключ;
- `error_code` — только для технического отказа.

Поля `build_natal_failed`:

- `run_id` — обязательно;
- `stage` — `resolve`, `build_spec`, `ensure_chart`, `build_delta` или `build_result`;
- `exception_type` — обязательно;
- `duration_ms` — обязательно;
- `cancelled` — `true` или `false`.

Компактный terminal event `build_natal_failed` не включает `str(exception)`;
текст исключения доступен только в полном DEBUG-событии `component_message`
согласно ADR-0025/0028. Traceback допускается только в защищённом техническом
журнале согласно общей политике observability.

Дополнительно, согласно ADR-0025/0028, публичная граница `handle` пишет на
`DEBUG` ровно два события `component_message`:

- перед началом операции — `direction=in`, `operation=build_natal`,
  `message_type=BuildNatalRequest`; JSON `message` содержит `command` и `run`;
  переданный `SessionState` намеренно не читается и не передаётся даже в
  журнал согласно lifecycle-инварианту handler;
- непосредственно перед возвратом наружу — `direction=out`, тот же `operation`,
  фактический `message_type`, `payload_mode=full` и полный
  `calculation_key`; для успеха JSON содержит полный
  `BuildNatalSuccess`, включая `ChartArtifact`, натальную карту и `StateDelta`;
- при исключении или отмене выходное событие имеет `status=error`,
  `payload_mode=error` и содержит тип и строковое сообщение исключения.

`component_message` намеренно содержит дату и время рождения, `place_id`,
координаты, timezone-данные и полный расчётный результат. Этот payload не
дублируется в terminal event уровня INFO/WARNING. Полный диагностический поток
делает текущий стенд непригодным для публичного развёртывания до отдельного
privacy-hardening решения ADR-0025/0028. Внутренние birth, artifact, engine и
natal-границы также пишут полные входы и фактические выходы на DEBUG: summary-
режима нет. Ниже DEBUG полный payload и logging-проекции не вычисляются.

Cache hit/miss должен журналироваться самим `ChartArtifactResolver`, а не handler.

Текущий `BirthDataResolver` журналирует `tz_id` на уровне `INFO`. Это существующий долг общей политики журналирования birth-блока и не исправляется внутри `BuildNatalHandler` (§14.3).

## 12. Минимальный набор unit-тестов

### 12.1. Успешные сценарии

1. Известное время:
   - вызван resolver;
   - итоговая spec целиком равна канонической `NatalChartSpec(chart_kind="natal")` со всеми default-полями;
   - вызван `ensure_chart`;
   - возвращён `BuildNatalSuccess`;
   - дельта содержит исходный `BirthInput`, `ResolvedBirthData` и построенную spec.

2. Неизвестное время:
   - итоговая spec целиком равна канонической `NatalChartSpec(chart_kind="cosmogram")`;
   - `InputRequired` не формируется;
   - техническое полуденное время не попадает обратно в `BirthInput`;
   - возвращён `BuildNatalSuccess`.

3. Cache hit:
   - handler получает готовый артефакт;
   - возвращается обычный `BuildNatalSuccess`;
   - поведение handler не отличается от cache miss.

### 12.2. Резолв

4. `InputRequired` возвращается тем же объектом или с полностью сохранённым содержимым.
5. `ResolutionUnavailable` возвращается без преобразования.
6. При обоих исходах `ensure_chart` не вызывается.
7. Исключение из resolver не преобразуется в типизированный исход handler.
8. `InputRequired` с пустым `issues` проходит наверх без изменений: handler не подставляет issue, не заменяет тип и не поднимает ошибку.

### 12.3. Расчёт

9. `ChartCalculationError("HOUSES_DEGENERATE", run_id="<run_id>")` преобразуется в `CalculationFailed(error_code="HOUSES_DEGENERATE")`.
10. `CalculationUnavailableError("EPHEMERIS_UNAVAILABLE", run_id="<run_id>")` через stub порта преобразуется в `CalculationFailed(error_code="EPHEMERIS_UNAVAILABLE")`.
11. Неизвестное исключение stub-а не маскируется самим handler.
12. Неизвестное исключение resolver или artifact port создаёт `build_natal_failed` уровня `ERROR` и повторно поднимается.
13. Отмена корутины создаёт `build_natal_failed` уровня `WARNING` с `cancelled=true`, не подавляется, а handler не устанавливает собственный timeout. Тест обязан проверять именно `asyncio.CancelledError`: реализация с `except Exception` его не поймает.
14. Преобразование `EphemerisConfigurationError` в `EPHEMERIS_UNAVAILABLE` уже покрыто параметризованным тестом `tests/test_calculation_engine.py::test_engine_error_mapping_does_not_expose_source_messages`; дублировать этот тест в handler-наборе не требуется.

### 12.4. Журнал

15. Каждый типизированный исход даёт ровно один `build_natal_completed` с уровнем и полем `outcome` по таблице §11: `success` → `INFO`, `input_required` → `INFO`, `resolution_unavailable` → `WARNING`, `calculation_failed` → `WARNING`.
16. `CalculationFailed("ENGINE_UNEXPECTED")` даёт `build_natal_completed` уровня `ERROR`.
17. За каждым `build_natal_started` следует ровно один terminal event — во всех ветках, включая исключение и отмену (BH-19).
18. Каждый вызов handler даёт парные `component_message direction=in|out` с
    одинаковым `run_id`. В success-ветке вход содержит полный
    `BuildNatalRequest`, выход — полный `BuildNatalSuccess` с артефактом и
    натальной картой; выходной envelope также содержит полный
    `calculation_key`. При исключении выход имеет `status=error` и
    `payload_mode=error`.
    Компактный `build_natal_failed` по-прежнему не содержит `str(exception)`.

### 12.5. Границы ответственности

19. Именно модуль `exact_orb.application.handlers.build_natal` не объявляет прямые импорты из списка §10.1. Проверка анализирует объявления импортов в одном модуле, а не весь runtime-граф. Используется `APPLICATION_BUILD_NATAL_FORBIDDEN_DIRECT_IMPORTS`.
20. Handler не вызывает `apply_delta`/`touched`, не создаёт новый `SessionState`/`ChartRef` и не передаёт state зависимостям.
21. Handler не назначает новую версию состояния.
22. Валидатор `BuildNatalSuccess` отклоняет `RESET_DELTA`, несовпадающую spec,
    несогласованные kind/time_unknown и наличие исходного времени, чужие
    время/координаты карты и ключ, не соответствующий delta/spec/version.
    Каждый сценарий даёт `ValidationError`; при сборке handler журналирует
    его как `build_natal_failed stage=build_result` и не возвращает success.
23. В успешном сценарии resolver и artifact port получают тот же объект `RunContext`: `resolver.received_run is run` и `artifacts.received_run is run`.
24. В short-circuit-сценарии resolver получает тот же объект `RunContext`, а artifact port не вызывается.
25. `Command` и `BuildNatalCommand` frozen; попытка изменить поле команды отклоняется `ValidationError` с `type="frozen_instance"`.
26. Порты не являются runtime-checkable: `isinstance(obj, ChartArtifactPort)` поднимает `TypeError`, контракт не использует `isinstance(..., Protocol)`.

## 13. Критерии готовности

Модуль считается спроектированным и реализованным корректно, если:

- все четыре исхода `BuildNatalOutcome` достижимы и покрыты тестами;
- natal и cosmogram строятся одним handler;
- handler использует только два прикладных порта: `BirthDataResolverPort` и `ChartArtifactPort`;
- существующие birth/calculation/session-модули не требуют изменения контрактов;
- handler возвращает полную `StateDelta`, но не сохраняет её;
- ошибки расчётного слоя корректно переводятся из существующих исключений в `CalculationFailed`;
- `BuildNatalSuccess` отклоняет связь артефакта с чужими resolved data, spec
  или ключом до возврата результата;
- каждый начатый прогон имеет terminal event: `build_natal_completed` либо `build_natal_failed`;
- полный input/output handler пишется только в DEBUG `component_message`, а
  ниже DEBUG не сериализуется;
- отмена перехватывается через `except BaseException` и повторно поднимается;
- прямые импорты `build_natal.py` соответствуют отдельной application-границе;
- модуль не зависит от transport, persistence, agent и LLM-слоёв.

Главная предметная ответственность `BuildNatalHandler` — выбрать между натальной картой и космограммой и собрать согласованный результат `{artifact, StateDelta}`. Все вычислительные, кэшовые и сессионные механизмы остаются за границей handler.

На сверенном commit функциональные критерии handler покрыты, но обязательный
import-boundary regression-тест из §10.1 отсутствует. Поэтому формальная готовность всего
перечня выше остаётся неполной до интеграции этого теста.

## 14. Открытые вопросы вне handler

### 14.1. `AlreadyApplied` при различающемся `BirthInput`

Текущая функция `session.state.matches_intent` сравнивает только:

```text
birth_resolved
base_chart_spec
```

`birth_input` в сравнение не входит. Поэтому новая команда, которая содержит другой `BirthInput`, но приводит к тем же `ResolvedBirthData` и `ChartSpec`, может быть классифицирована оркестратором как `AlreadyApplied`. В этом случае сохранённый presentation-only `birth_input` не обновится, хотя handler включил новое значение в дельту.

Handler не должен исправлять это самостоятельно: он всегда формирует полную дельту с `command.birth_input`. Требуется отдельное решение в сессионной модели о том, должен ли `AlreadyApplied` обновлять presentation-only ввод.

### 14.2. Retryable-семантика эфемерид

`CalculationUnavailableError("EPHEMERIS_UNAVAILABLE", run_id="<run_id>")` в текущем application-контракте преобразуется в `CalculationFailed`, который не содержит `retryable`. Если клиенту понадобится различать повторяемую недоступность расчётной зависимости и окончательный отказ расчёта, потребуется отдельное изменение outcome-контракта.

### 14.3. Общая политика геоданных в журнале

Handler не журналирует `tz_id`, но текущий `BirthDataResolver` уже пишет его на уровне `INFO`. Нужно отдельно решить, признаётся ли `tz_id` допустимым техническим измерением или удаляется из журналов birth-блока.

### 14.4. Алертинг по `ENGINE_UNEXPECTED`

`ENGINE_UNEXPECTED` — замаскированный технический дефект (§8.3), и одного уровня `ERROR` для его обнаружения мало: нужен алерт по коду. Выделенного observability-слоя в проекте пока нет, поэтому требование не входит в критерии готовности handler и должно быть решено вместе с общей политикой мониторинга. Handler со своей стороны обязан только обеспечить уровень `ERROR` и наличие `error_code` в terminal event.

---

# Контракты `BuildNatalHandler`

В разделе перечислены контракты, непосредственно участвующие в работе handler. Внутреннее содержимое `NatalChart` не раскрывается полностью: handler воспринимает рассчитанную карту как готовую часть `ChartArtifact` и не работает с её доменными полями.

## Контракт: `Command`

Frozen Pydantic base type для всех application-команд. Собственных атрибутов не содержит. Используется как верхняя граница типа и как ключ registry маршрутизации; `ConfigDict(frozen=True)` наследуется конкретными командами.

| Атрибут | Тип атрибута | Описание | Возможные значения | Пример |
|---|---|---|---|---|
| — | — | Собственных полей нет | — | `BuildNatalCommand(...)` |

## Контракт: `Handler[CommandT, OutcomeT]`

Общий application-порт обработчика команды.

| Атрибут / метод | Тип | Описание | Возможные значения | Пример |
|---|---|---|---|---|
| `handle` | `async (CommandT, SessionState, RunContext) -> OutcomeT` | Выполняет один application use case | Конкретные типы задаются реализацией handler | `BuildNatalHandler.handle(command, state, run)` |

## Контракт: `BirthDataResolverPort`

Структурный порт разрешения данных рождения.

| Атрибут / метод | Тип | Описание | Возможные значения | Пример |
|---|---|---|---|---|
| `resolve` | `async (BirthInput, *, RunContext \| None) -> ResolvedBirthData \| InputRequired \| ResolutionUnavailable` | Преобразует структурированный ввод в расчётные факты либо типизированный исход | Три объявленных типа результата; исключения не преобразуются handler | `await resolver.resolve(birth_input, run=run)` |

## Контракт: `ChartArtifactPort`

Структурный порт получения воспроизводимого расчётного артефакта.

| Атрибут / метод | Тип | Описание | Возможные значения | Пример |
|---|---|---|---|---|
| `ensure_chart` | `async (ChartSpec, ResolvedBirthData, *, RunContext) -> ChartArtifact` | Возвращает валидный артефакт из кэша или расчёта | `ChartArtifact`; typed calculation exceptions | `await artifacts.ensure_chart(spec, resolved, run=run)` |

## Сообщение: `BuildNatalCommand`

Команда на построение базовой карты рождения.

`BuildNatalCommand` наследуется от frozen-типа `Command`; после создания команда не может быть изменена.

| Атрибут | Тип атрибута | Описание | Возможные значения | Пример |
|---|---|---|---|---|
| `birth_input` | `BirthInput` | Структурированные данные рождения, переданные пользователем через форму | Валидный объект `BirthInput` | `{"birth_date":"1985-09-02","birth_time":"00:45:00","place_id":"moscow-ru"}` |

## Сообщение: `BirthInput`

Исходные данные пользователя до backend-резолва места и исторического времени.

| Атрибут | Тип атрибута | Описание | Возможные значения | Пример |
|---|---|---|---|---|
| `birth_date` | `date` | Дата рождения | Любая структурно валидная календарная дата; обязательное поле. Поддерживаемый backend-диапазон проверяет resolver | `"1985-09-02"` |
| `birth_time` | `time \| None` | Локальное время рождения. `None` означает, что время неизвестно | Время без timezone либо `None` | `"00:45:00"` |
| `place_id` | `str` | Идентификатор места из каталога. Считается недоверенным и проверяется backend | Обязательная строка без модельного `min_length`; пустой или неизвестный ID отклоняет resolver | `"moscow-ru"` |

`BirthInput` проверяет структуру и типы, но не принадлежность даты поддерживаемому диапазону
и не существование места. Дата вне диапазона приводит в resolver к `InputRequired` с кодом
`UNSUPPORTED`; пустой или неизвестный `place_id` — к `InputRequired` с кодом `INVALID`.

Пример с известным временем:

```json
{
  "birth_date": "1985-09-02",
  "birth_time": "00:45:00",
  "place_id": "moscow-ru"
}
```

Пример с неизвестным временем:

```json
{
  "birth_date": "1985-09-02",
  "birth_time": null,
  "place_id": "moscow-ru"
}
```

## Сообщение: `RunContext`

Технический контекст одного выполнения операции.

| Атрибут | Тип атрибута | Описание | Возможные значения | Пример |
|---|---|---|---|---|
| `run_id` | `UUID` | Correlation identifier операции | Валидный UUID | `"b3f17834-f7ee-4a88-980c-c184c91555c0"` |
| `started_at` | `datetime` | Момент начала операции | `datetime`, обязательно timezone-aware UTC | `"2026-09-08T18:20:31.125Z"` |

## Сообщение: `SessionState`

Неизменяемый снимок состояния сессии, который целевой `ApplicationOrchestrator` будет
передавать handler. В сверенной реализации сам orchestrator ещё отсутствует.

| Атрибут | Тип атрибута | Описание | Возможные значения | Пример |
|---|---|---|---|---|
| `session_id` | `str` | Серверный идентификатор сессии | Непустая строка | `"session-a934ef"` |
| `birth_input` | `BirthInput \| None` | Последние подтверждённые исходные данные рождения | Объект `BirthInput` либо `None` | `{"birth_date":"1985-09-02","birth_time":"00:45:00","place_id":"moscow-ru"}` |
| `birth_resolved` | `ResolvedBirthData \| None` | Последние подтверждённые разрешённые данные рождения | Объект `ResolvedBirthData` либо `None` | См. контракт `ResolvedBirthData` |
| `state_version` | `int` | Текущая версия состояния для CAS | Целое число `>= 0` | `3` |
| `base_chart` | `ChartRef \| None` | Ссылка на спецификацию активной базовой карты | `ChartRef` либо `None` | `state_version=3` + полный natal-пример `NatalChartSpec` ниже |
| `created_at` | `datetime` | Момент создания сессии | Timezone-aware UTC | `"2026-09-01T10:00:00Z"` |
| `expires_at` | `datetime` | Момент окончания sliding TTL | Timezone-aware UTC | `"2026-09-15T18:20:31Z"` |
| `hard_expires_at` | `datetime` | Абсолютный момент окончания жизни сессии | Timezone-aware UTC | `"2026-10-01T10:00:00Z"` |

Инварианты:

- `birth_input`, `birth_resolved` и `base_chart` либо заполнены одновременно, либо одновременно равны `None`;
- если `base_chart` заполнен, `base_chart.state_version == state_version`;
- `created_at <= expires_at <= hard_expires_at`.

## Сообщение: `ChartRef`

Воспроизводимая ссылка на активную карту в состоянии сессии.

| Атрибут | Тип атрибута | Описание | Возможные значения | Пример |
|---|---|---|---|---|
| `state_version` | `int` | Версия состояния, для которой карта является активной | Целое число `>= 1` | `3` |
| `spec` | `ChartSpec` | Полная спецификация восстановления карты | В MVP — `NatalChartSpec` | См. полный natal-пример в разделе `NatalChartSpec` |

`BuildNatalHandler` не создаёт `ChartRef`. Он возвращает `StateDelta`, а `ChartRef` создаётся при применении дельты.

## Сообщение: `ResolvedBirthData`

Разрешённые backend данные, готовые для расчёта карты.

| Атрибут | Тип атрибута | Описание | Возможные значения | Пример |
|---|---|---|---|---|
| `utc_datetime` | `datetime` | Момент рождения в UTC. При неизвестном времени содержит техническую опорную точку | Только timezone-aware UTC | `"1985-09-01T20:45:00Z"` |
| `latitude` | `float` | Широта разрешённого места | От `-90.0` до `90.0` | `55.7558` |
| `longitude` | `float` | Долгота разрешённого места | От `-180.0` до `180.0` | `37.6173` |
| `tz_id` | `str` | Идентификатор временной зоны IANA | Валидный IANA timezone ID | `"Europe/Moscow"` |
| `utc_offset_seconds` | `int` | Историческое смещение относительно UTC в секундах | Целое количество секунд | `14400` |
| `canonical_place` | `str` | Каноническое название места из backend-каталога | Непустая строка | `"Москва, Россия"` |
| `time_unknown` | `bool` | Признак неизвестного времени рождения | `true`, `false` | `false` |
| `birth_time_domain` | `BirthTimeDomain \| None` | Все допустимые UTC-минуты при неизвестном времени | Непустой домен для `time_unknown=true`, иначе `None` | `null` |
| `warnings` | `tuple[ResolutionWarning, ...]` | Машиночитаемые предупреждения резолва | Пустой tuple или набор предупреждений | `[]` |

В `calculation_key` входят `utc_datetime`, `latitude`, `longitude` и digest
`birth_time_domain`; presentation-поля не входят.

## Сообщение: `ResolutionWarning`

Предупреждение, возникшее во время разрешения места или исторического времени.

| Атрибут | Тип атрибута | Описание | Возможные значения | Пример |
|---|---|---|---|---|
| `source` | `Literal["place", "time"]` | Источник предупреждения | `"place"`, `"time"` | `"time"` |
| `code` | `str` | Стабильный машинный код | Например, `pre_1970_offset_unverified`, `noon_anchor_adjusted`, `noon_anchor_ambiguous` | `"pre_1970_offset_unverified"` |
| `message` | `str` | Человекочитаемое описание | Непустой текст | `"Historical timezone data before 1970 may be incomplete"` |

## Сообщение: `Issue`

Описание одной проблемы пользовательского ввода.

| Атрибут | Тип атрибута | Описание | Возможные значения | Пример |
|---|---|---|---|---|
| `field` | `str` | Путь к проблемному полю | `birth.date`, `birth.time`, `birth.place` | `"birth.place"` |
| `code` | `IssueCode` | Машинный тип проблемы | `MISSING`, `AMBIGUOUS`, `INVALID`, `UNSUPPORTED` | `"INVALID"` |
| `candidates` | `tuple[Any, ...] \| None` | Допустимые варианты при неоднозначности | Набор кандидатов либо `None` | `[10800,14400]` |
| `constraints` | `dict[str, Any] \| None` | Ограничения для корректного значения | Словарь либо `None` | `{"min":"1800-01-01","max":"2026-09-08"}` |

### Возможные значения `Issue.code`

| Значение | Описание | Пример |
|---|---|---|
| `MISSING` | Обязательное значение отсутствует | Не передан `place_id` |
| `AMBIGUOUS` | Одному вводу соответствуют несколько допустимых вариантов | Локальное время повторилось при переводе часов |
| `INVALID` | Значение невозможно использовать | `place_id` не найден |
| `UNSUPPORTED` | Значение корректно по форме, но не поддерживается | Дата раньше 1800 года |

Для текущего `BirthDataResolver` достижимы `INVALID`, `AMBIGUOUS` и `UNSUPPORTED`. `MISSING` является общим кодом модели, но текущим build-резолвером после валидации `BirthInput` не возвращается.

## Сообщение: `InputRequired`

Пользователь должен исправить или уточнить ввод.

| Атрибут | Тип атрибута | Описание | Возможные значения | Пример |
|---|---|---|---|---|
| `issues` | `tuple[Issue, ...]` | Проблемы входных данных | Tuple объектов `Issue`; текущий resolver возвращает минимум один, но модель допускает пустой tuple | `[{"field":"birth.place","code":"INVALID"}]` |

Handler возвращает `InputRequired` без изменения даже при пустом `issues`; обеспечение содержательного producer-контракта принадлежит resolver.

Пример неизвестного места:

```json
{
  "issues": [
    {
      "field": "birth.place",
      "code": "INVALID",
      "candidates": null,
      "constraints": null
    }
  ]
}
```

## Сообщение: `ResolutionUnavailable`

Технический отказ разрешения места или временной зоны.

| Атрибут | Тип атрибута | Описание | Возможные значения | Пример |
|---|---|---|---|---|
| `error_code` | `str` | Машинный код технической ошибки | `PLACE_CATALOG_UNAVAILABLE`, `UNKNOWN_TIMEZONE` | `"PLACE_CATALOG_UNAVAILABLE"` |
| `retryable` | `bool` | Имеет ли смысл повторить операцию без изменения ввода | `true`, `false` | `true` |

## Сообщение: `NatalChartSpec`

Спецификация расчёта натальной карты или космограммы. В MVP `ChartSpec = NatalChartSpec`.

| Атрибут | Тип атрибута | Описание | Возможные значения | Пример |
|---|---|---|---|---|
| `technique` | `Literal["natal"]` | Расчётная техника | Только `"natal"` | `"natal"` |
| `chart_kind` | `Literal["natal", "cosmogram"]` | Явный вид карты | `"natal"`, `"cosmogram"` | `"natal"` |
| `include` | `tuple[IncludeBlock, ...]` | Набор рассчитываемых блоков | Зависит от `chart_kind` | `["aspects","configurations","houses","positions","rulers","strength"]` |
| `house_system` | `str` | Система домов | В MVP только `"P"` — Placidus | `"P"` |
| `rulership` | `RulershipScheme` | Схема управителей | `combined`, `modern`, `traditional`; default — `combined` | `"combined"` |
| `near_interception_threshold` | `float` | Порог положения рядом с интерцепцией | Модель принимает `float >= 0.0` и не отсекает `+inf`; эффективный расчётный контракт требует конечное число и проверяется `EngineService` с ошибкой `SPEC_INVALID`. Default — `1.0` | `1.0` |

### Возможные значения `include`

| Значение | Назначение | Допустимость |
|---|---|---|
| `positions` | Позиции небесных тел | Natal и cosmogram |
| `houses` | Куспиды и структура домов | Только natal |
| `rulers` | Управители домов | Только natal; требует `houses` |
| `aspects` | Аспекты | Natal и cosmogram |
| `configurations` | Конфигурации аспектов | Natal и cosmogram |
| `strength` | Расчёт силы | Только natal; требует `houses` |

Полный канонический пример natal:

```json
{
  "technique": "natal",
  "chart_kind": "natal",
  "include": [
    "aspects",
    "configurations",
    "houses",
    "positions",
    "rulers",
    "strength"
  ],
  "house_system": "P",
  "rulership": "combined",
  "near_interception_threshold": 1.0
}
```

Полный канонический пример cosmogram:

```json
{
  "technique": "natal",
  "chart_kind": "cosmogram",
  "include": [
    "aspects",
    "configurations",
    "positions"
  ],
  "house_system": "P",
  "rulership": "combined",
  "near_interception_threshold": 1.0
}
```

## Сообщение: `ChartArtifact`

Готовый воспроизводимый результат расчёта или чтения из Calculation Cache.

| Атрибут | Тип атрибута | Описание | Возможные значения | Пример |
|---|---|---|---|---|
| `calculation_key` | `str` | Детерминированный ключ расчёта | Строка с префиксом `eo:calc:v2:` | `"eo:calc:v2:89f…"` |
| `spec` | `ChartSpec` | Спецификация карты | В MVP — `NatalChartSpec` | См. полный natal-пример в разделе `NatalChartSpec` |
| `calculation_version` | `str` | Отпечаток версии расчётного окружения | Непустая строка | `"sha256:c74a…"` |
| `chart` | `ArtifactNatalChart` | Рассчитанные позиции, дома, аспекты и другие блоки | Валидный результат ядра | `{"chart_kind":"natal","positions":[...],"houses":[...]}` |

Инварианты:

```text
artifact.chart.chart_kind == artifact.spec.chart_kind
artifact.calculation_key == calculation_key(
    calculation_input_from_chart(artifact.chart),
    artifact.spec,
    artifact.calculation_version,
)
```

House system и присутствие всех вычисленных блоков `artifact.chart` также
обязаны соответствовать spec. Верхнеуровневых `chart_kind` и `warnings` у
артефакта нет; предупреждения читаются из `artifact.chart.warnings`.
Неизвестные поля запрещены. Отдельная `artifact_schema_version` не вводится:
несовместимый storage payload отклоняется кодеком и пересчитывается
(ADR-0027).

## Сообщение: `ArtifactNatalChart`

Расчётное содержимое артефакта.

| Атрибут | Тип атрибута | Описание | Возможные значения | Пример |
|---|---|---|---|---|
| `chart_kind` | `Literal["natal", "cosmogram"]` | Вид карты внутри результата | `"natal"`, `"cosmogram"` | `"natal"` |
| `ephemeris` | `ArtifactEphemerisStatus` | Безопасная для сохранения информация об эфемеридах | Валидный статус | `{"mode":"files","required_files":["sepl_18.se1"],"found_files":["sepl_18.se1"],"missing_files":[]}` |
| остальные поля `NatalChart` | Доменные поля расчётного результата | Позиции, дома, управители, аспекты, конфигурации, сила и предупреждения | Состав зависит от `spec.include` и `chart_kind` | `{"positions":[...],"houses":[...]}` |

Handler не читает и не изменяет внутренние поля этого объекта.

## Сообщение: `ArtifactEphemerisStatus`

| Атрибут | Тип атрибута | Описание | Возможные значения | Пример |
|---|---|---|---|---|
| `mode` | `Literal["files", "fallback"]` | Режим получения эфемерид | `"files"`, `"fallback"` | `"files"` |
| `required_files` | `tuple[str, ...]` | Файлы, необходимые для расчёта | Набор имён файлов | `["sepl_18.se1"]` |
| `found_files` | `tuple[str, ...]` | Найденные файлы | Набор имён файлов | `["sepl_18.se1"]` |
| `missing_files` | `tuple[str, ...]` | Отсутствующие файлы | Пустой или непустой набор | `[]` |

## Сообщение: `CalculationWarning`

| Атрибут | Тип атрибута | Описание | Возможные значения | Пример |
|---|---|---|---|---|
| Состав определяется реализованной моделью `CalculationWarning` | `CalculationWarning` | Предупреждение о свойствах или ограничениях расчёта | Машиночитаемый код и описание согласно контракту engine | Предупреждение об ограничениях космограммы |

`BuildNatalHandler` не создаёт и не редактирует эти предупреждения. Они
передаются в `ChartArtifact.chart.warnings`.

## Сообщение: `StateDelta`

Полная замена изменяемых полей состояния, подготовленная handler для последующего commit.

| Атрибут | Тип атрибута | Описание | Возможные значения | Пример |
|---|---|---|---|---|
| `birth_input` | `BirthInput \| None` | Исходные данные, которые должны стать подтверждёнными | Для build — `BirthInput`; `None` только при reset | `{"birth_date":"1985-09-02","birth_time":"00:45:00","place_id":"moscow-ru"}` |
| `birth_resolved` | `ResolvedBirthData \| None` | Разрешённые backend данные | Для build — `ResolvedBirthData`; `None` только при reset | `{"utc_datetime":"1985-09-01T20:45:00Z","latitude":55.7558,"longitude":37.6173}` |
| `base_chart_spec` | `ChartSpec \| None` | Спецификация новой базовой карты | Для build — `NatalChartSpec`; `None` только при reset | См. полный natal-пример в разделе `NatalChartSpec` |

Инвариант: три атрибута либо одновременно заполнены, либо одновременно равны `None`. Успешный handler всегда возвращает полностью заполненную дельту.

## Сообщение: `BuildNatalSuccess`

Внутренний успешный результат handler.

| Атрибут | Тип атрибута | Описание | Возможные значения | Пример |
|---|---|---|---|---|
| `artifact` | `ChartArtifact` | Полученная из кэша или рассчитанная карта | Валидный `ChartArtifact` | Ключ `eo:calc:v2:89f…`, полный natal `spec`, `chart.chart_kind="natal"` |
| `delta` | `StateDelta` | Полная дельта для последующего commit | Полностью заполненный `StateDelta` | `BirthInput` + `ResolvedBirthData` + полный natal `NatalChartSpec` |

`BuildNatalSuccess` ещё не означает, что состояние сессии сохранено. Модель frozen. Валидатор выполняет проверки строго в следующем порядке:

1. `delta.birth_input`, `delta.birth_resolved` и `delta.base_chart_spec` не равны `None`;
2. `artifact.spec == delta.base_chart_spec`;
3. `delta.base_chart_spec.chart_kind == "cosmogram"` тогда и только тогда,
   когда `delta.birth_resolved.time_unknown == true`;
4. `delta.birth_input.birth_time is None` тогда и только тогда, когда
   `delta.birth_resolved.time_unknown == true`;
5. `calculation_input_from_chart(artifact.chart) ==
   calculation_input_from(delta.birth_resolved)`;
6. `artifact.calculation_key` равен ключу, заново вычисленному из
   `delta.birth_resolved`, `delta.base_chart_spec` и
   `artifact.calculation_version`.

Каждое нарушение поднимает `ValueError` и становится
`pydantic.ValidationError`. Проверка заполненности выполняется первой, чтобы
`RESET_DELTA` не приводила к обращению `None.chart_kind` и сырому `TypeError`.

## Сообщение: `CalculationFailed`

Типизированный неуспешный результат расчёта на границе handler.

| Атрибут | Тип атрибута | Описание | Возможные значения | Пример |
|---|---|---|---|---|
| `error_code` | `str` | Машинный код отказа расчётного блока | `SPEC_INVALID`, `GEOGRAPHY_INVALID`, `HOUSES_DEGENERATE`, `ENGINE_UNEXPECTED`, `EPHEMERIS_UNAVAILABLE` | `"HOUSES_DEGENERATE"` |

### Преобразование ошибок расчётного слоя

| Ошибка расчётного слоя | Результат handler |
|---|---|
| `ChartCalculationError("SPEC_INVALID", run_id="<run_id>")` | `CalculationFailed(error_code="SPEC_INVALID")` |
| `ChartCalculationError("GEOGRAPHY_INVALID", run_id="<run_id>")` | `CalculationFailed(error_code="GEOGRAPHY_INVALID")` |
| `ChartCalculationError("HOUSES_DEGENERATE", run_id="<run_id>")` | `CalculationFailed(error_code="HOUSES_DEGENERATE")` |
| `ChartCalculationError("ENGINE_UNEXPECTED", run_id="<run_id>")` | `CalculationFailed(error_code="ENGINE_UNEXPECTED")` |
| `CalculationUnavailableError("EPHEMERIS_UNAVAILABLE", run_id="<run_id>")` | `CalculationFailed(error_code="EPHEMERIS_UNAVAILABLE")` |

## Сообщение: `BuildNatalOutcome`

Объединённый выходной контракт handler. Это union без собственных атрибутов.

| Возможный тип результата | Когда возвращается | Пример |
|---|---|---|
| `BuildNatalSuccess` | Данные разрешены, карта получена, дельта сформирована | `{"artifact":{...},"delta":{...}}` |
| `InputRequired` | Пользователь должен исправить дату, время или место | `{"issues":[{"field":"birth.place","code":"INVALID"}]}` |
| `ResolutionUnavailable` | Технически недоступен каталог места или временная зона | `{"error_code":"PLACE_CATALOG_UNAVAILABLE","retryable":true}` |
| `CalculationFailed` | Не удалось получить расчётный артефакт | `{"error_code":"HOUSES_DEGENERATE"}` |

```text
BuildNatalOutcome =
      BuildNatalSuccess
    | InputRequired
    | ResolutionUnavailable
    | CalculationFailed
```

## 15. Сводный поток контрактов

```text
BuildNatalCommand
    └── BirthInput

SessionState
RunContext
    ↓
BuildNatalHandler
    ↓
BirthDataResolver
    ├── ResolvedBirthData
    │       └── ResolutionWarning[]
    ├── InputRequired
    │       └── Issue[]
    └── ResolutionUnavailable
    ↓
NatalChartSpec
    ↓
ChartArtifactResolver
    ├── ChartArtifact
    └── calculation exceptions
            ↓
        CalculationFailed
    ↓
StateDelta
    ↓
BuildNatalSuccess
```

За границей `BuildNatalHandler` остаются контракты commit: `Committed`, `AlreadyApplied`, `Superseded`, `SessionAbsent` и `StateCommitFailed`. Их будет обрабатывать целевой `ApplicationOrchestrator`, поэтому во входные и выходные контракты handler они не входят.
