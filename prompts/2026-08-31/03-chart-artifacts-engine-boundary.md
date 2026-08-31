# Prompt 3: Calculation Engine Boundary для Chart Artifacts

Задача: реализовать расчётную границу для chart artifacts: `CalculationResult`,
`TechniqueAdapter`, `NatalTechniqueAdapter`, `CalculationEnginePort` и
`EngineService`.

Работай по:

- `docs/requirements/component_responsibilities/exact-orb_chart_artifacts.md`
  §2.3, §3.4, §4, §5.3, §6.2, §7.2, §10
- `docs/requirements/component_responsibilities/exact-orb_build_natal_components.md`
  §4.4
- `docs/architecture/service_ready_architecture.md` — I1, I5, «Известный долг»
- текущим слоям:
  - `src/exact_orb/domain.py`
  - `src/exact_orb/errors.py`
  - `src/exact_orb/run_context.py`
  - `src/exact_orb/calculation/spec.py`
  - `src/exact_orb/calculation/types.py`
  - `src/exact_orb/calculation/cache.py`
  - `src/exact_orb/calculation/codec.py`

Код менять только в рамках этой задачи.

Не реализовывать:

- `ChartArtifactResolver`
- cache get/put orchestration
- single-flight
- Redis
- `CalculationVersion`
- `ChartArtifact` сборку в engine layer
- stale/corrupt cache handling
- tool/application handler wiring

---

## 1. Важное текущее состояние

Сейчас `ChartSpec = NatalChartSpec`.

Не вводить `Annotated[..., Field(discriminator="technique")]` в этом промте.
Тегированный union появится только вместе со второй техникой.

В текущем `NatalChartSpec` есть:

- `technique: Literal["natal"] = "natal"`
- `chart_kind`
- `include`
- `house_system`
- `rulership`
- `near_interception_threshold`

В текущем `NatalChartSpec` нет:

- `selena_method`
- `orb_profile`

Не добавлять эти поля в этом промте.

**Следствие, которое нельзя обойти молча.** Реальная сигнатура `calculate_natal`
принимает 15 параметров; из спеки покрываются 7. Остальные уходят в дефолты,
и один из них принципиален: `selena_method=None` внутри превращается
в `get_selena_method_name(None)`, то есть в чтение процессной конфигурации.
Значит требование §10 `chart_artifacts.md` — «каждый параметр функции ядра
получает значение из спеки, а не дефолт» — в этой задаче **не выполняется
и не может быть выполнено**. Фиксируется в §17, а не обходится расширением спеки.

---

## 2. Добавить `src/exact_orb/calculation/errors.py`

```python
ChartCalculationErrorCode = Literal[
    "SPEC_INVALID",
    "GEOGRAPHY_INVALID",
    "HOUSES_DEGENERATE",
    "ENGINE_UNEXPECTED",
]

CalculationUnavailableErrorCode = Literal[
    "EPHEMERIS_UNAVAILABLE",
]

class ArtifactError(Exception):
    def __init__(self, code: str, *, run_id: str) -> None:
        super().__init__(code)
        self.code = code
        self.run_id = run_id

class ChartCalculationError(ArtifactError):
    code: ChartCalculationErrorCode

class CalculationUnavailableError(ArtifactError):
    code: CalculationUnavailableErrorCode
```

Конструктор задать явно: без него `str(error)` не определён, и требование
ниже не выполняется.

Требования:

* `str(error)` не содержит исходный exception message
* `str(error)` может содержать только code и run_id
* не хранить payload/spec/resolved/chart в ошибке
* не хранить `retryable` в exception; retryability определяется классом
  (§6.2 `chart_artifacts.md`):
   * `ChartCalculationError` -> не retryable
   * `CalculationUnavailableError` -> retryable
* `run_id` — **строка**. `RunContext.run_id` имеет тип `UUID`, поэтому
  конверсия `str(run.run_id)` делается в одном месте, на границе `EngineService`
* не использовать `raise ... from exc` для ошибок, чей исходный текст может
  содержать дату рождения, координаты, путь или warning message
* для mapped engine failures использовать `raise ... from None`
* тестом проверить `error.__cause__ is None` для mapped failures

Модуль обязан остаться контрактным: только `typing` и stdlib, без импортов
`engine`, `config`, `swiss_backend`. Он регистрируется в тесте границ (§16).

---

## 3. Обновить `src/exact_orb/outcomes.py`

```python
class CalculationFailed(BaseModel):
    error_code: str
```

Добавить в `__all__`.

Не реализовывать общий mapper exception -> outcome в этом промте: по §6.2
отображение принадлежит прикладной границе, а `BuildChartHandler` ещё
не существует.

---

## 4. Добавить `src/exact_orb/calculation/engine.py`

Публичный API подмодуля:

* `CalculationResult`
* `CalculationEnginePort`
* `TechniqueAdapter`
* `NatalTechniqueAdapter`
* `EngineService`

`calculation/engine.py` может импортировать engine/native stack транзитивно.
Это не shared-contract модуль.

Package-level `src/exact_orb/calculation/__init__.py` должен остаться
swisseph-free. Не экспортировать из него:

* `CalculationResult`
* `CalculationEnginePort`
* `TechniqueAdapter`
* `NatalTechniqueAdapter`
* `EngineService`

`calculation/errors.py` контрактный и native не тянет, но экспортировать
его из package-level `__init__` в этой задаче тоже не нужно — вызывающих ещё нет.

Импорт только через подмодули:

```python
from exact_orb.calculation.engine import EngineService
from exact_orb.calculation.errors import ChartCalculationError
```

---

## 5. `CalculationResult`

```python
class CalculationResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    chart_kind: ChartKind
    chart: NatalChart
    warnings: tuple[CalculationWarning, ...]
```

Требования:

* frozen top-level model
* `warnings` хранить tuple
* `CalculationResult` не содержит calculation key, calculation version,
  cache payload, `ChartArtifact`
* `CalculationResult` несёт **сырой** `NatalChart`, вместе с `ephemeris.path`
  и `ephemeris.source`. Приведение к artifact-safe форме — работа
  `ChartArtifact` из `calculation/types.py`, и делать её здесь нельзя:
  движок не знает, что его результат поедет в кэш

---

## 6. `CalculationEnginePort`

```python
class CalculationEnginePort(Protocol):
    async def calculate(
        self,
        spec: ChartSpec,
        resolved: ResolvedBirthData,
        *,
        run: RunContext,
    ) -> CalculationResult: ...
```

**Не помечать `@runtime_checkable`.** Единственный член протокола — `calculate`,
поэтому `isinstance` сводится к `hasattr(obj, "calculate")` и возвращает `True`
в том числе для `NatalTechniqueAdapter`, у которого совсем другая сигнатура.
Проверка, которая не отличает порт от адаптера, хуже её отсутствия: она создаёт
ложное покрытие. Соответствие порту — задача аннотаций типов в `bootstrap`,
а не рантайма.

`RunContext` импортировать из реального текущего модуля:

```python
from exact_orb.run_context import RunContext
```

Не создавать второй `RunContext` в `application/` (см. §17).

---

## 7. `TechniqueAdapter`

```python
@runtime_checkable
class TechniqueAdapter(Protocol):
    technique: str

    def calculate(
        self,
        spec: ChartSpec,
        resolved: ResolvedBirthData,
    ) -> CalculationResult: ...
```

Здесь `@runtime_checkable` оправдан: у протокола есть data-член `technique`,
поэтому `isinstance` действительно отсеивает объект без него, и конструктор
`EngineService` использует эту проверку при валидации реестра (§9).

Ограничение, которое надо знать при написании тестов: для протокола
с non-method членами `issubclass()` бросает
`TypeError: Protocols with non-method members don't support issubclass()`.
В тестах использовать только `isinstance`.

Требования:

* метод синхронный
* adapter исполняется внутри `ThreadPoolExecutor`
* adapter не знает про `run`, `run_id`, cache, calculation key,
  calculation version, `ChartArtifact`, resolver
* adapter не логирует operation-level events
* adapter делает только mapping spec/resolved -> функция движка и сборку
  `CalculationResult`

---

## 8. `NatalTechniqueAdapter`

`technique = "natal"`.

Конструктор принимает вычислитель явной зависимостью:

```python
class NatalTechniqueAdapter:
    technique = "natal"

    def __init__(self, *, calculator: Callable[..., NatalChart] = calculate_natal) -> None:
        self._calculator = calculator
```

Инъекция нужна для теста маппинга: он проверяет переданные аргументы,
и подмена через параметр не зависит от того, как именно модуль импортировал
`calculate_natal`. Дефолт — реальная функция, так что production-сборка
ничего не передаёт.

Маппинг:

```
birth_datetime              <- resolved.utc_datetime
latitude                    <- resolved.latitude
longitude                   <- resolved.longitude
chart_kind                  <- spec.chart_kind
house_system                <- normalize_house_system_code(spec.house_system)
rulership                   <- spec.rulership
include                     <- frozenset(spec.include)
near_interception_threshold <- spec.near_interception_threshold
```

Требования:

* `house_system` передавать нормализованной строкой, не bytes: ядро принимает
  `str | bytes`, а `normalize_house_system()` внутри `calc.py` делает
  `.encode("ascii")` самостоятельно
* `include` берётся из `spec.include` как есть, без повторного
  `normalize_include`. Адаптер не чинит спеку: невалидная форма обязана быть
  отвергнута prevalidation (§10)
* `rulership` и `near_interception_threshold` передавать явно
* не передавать `selena_method` и `orb_profile` (§1)
* не менять `calculate_natal` численно
* не менять body ids / ephemeris flags / aspect config / configuration config /
  strength config

После вызова:

```python
return CalculationResult(
    chart_kind=chart.chart_kind,
    chart=chart,
    warnings=chart.warnings,
)
```

---

## 9. `EngineService`

```python
class EngineService:
    def __init__(
        self,
        *,
        executor: ThreadPoolExecutor,
        techniques: Mapping[str, TechniqueAdapter],
        slow_threshold_ms: float,
    ) -> None: ...
```

`slow_threshold_ms` **без дефолта** — как `max_entries` и `ttl_seconds`
у кэша. Значение первой версии (3000 мс, §7 и §8.4 `chart_artifacts.md`)
передаёт `bootstrap.py`. Дефолт в конструкторе создал бы второй источник
истины про конфигурацию.

Требования:

* executor передаётся снаружи
* `EngineService` не создаёт и не shutdown-ит executor
* registry техник копируется в immutable/read-only структуру
* `slow_threshold_ms` должен быть положительным
* текущий обязательный набор техник: `{"natal"}`
* constructor проверяет:
   * registry не пустой
   * registry содержит `"natal"`
   * нет неизвестных техник сверх текущего `ChartSpec`
   * каждый key совпадает с `adapter.technique`
   * каждое значение проходит `isinstance(adapter, TechniqueAdapter)`
   * каждое значение синхронно:
     `not inspect.iscoroutinefunction(adapter.calculate)`
* нарушение любой из проверок реестра — `ValueError` на конструировании,
  а не `ChartCalculationError`: это ошибка сборки приложения, а не расчёта
* при неизвестном `spec.technique` на runtime:
  `ChartCalculationError("SPEC_INVALID", run_id=str(run.run_id))`
* выбор adapter только по `spec.technique`

Про проверку синхронности: `isinstance` для runtime-протокола сводится
к проверке наличия атрибутов и не смотрит на сигнатуру, поэтому адаптер
с `async def calculate` пройдёт её и упадёт уже в worker thread, вернув
корутину вместо `CalculationResult`. Проверка обязана быть отдельной
и выполняться на старте.

**Про параллелизм.** Несколько воркеров в executor не дают параллельного
расчёта: `calculate_natal` заходит в `ephemeris_session()`, а это процессный
`RLock` (`src/exact_orb/ephemeris_runtime.py`). Executor нужен для того, чтобы
не блокировать event loop, а не для ускорения. Записать это комментарием в коде,
иначе будущее увеличение `max_workers` будет выглядеть как способ ускориться.

---

## 10. Prevalidation до executor

Все дешёвые проверки выполнить до `loop.run_in_executor`.

Проверять до отправки в поток:

* `spec.technique == "natal"`
* `normalize_include(spec.chart_kind, spec.include)` не падает
* `spec.include` — итерируемая коллекция строк, **не `None`**
* `normalize_house_system_code(spec.house_system)` не падает
* `RulershipScheme(spec.rulership)` не падает
* `near_interception_threshold` finite и `>= 0.0`
* `validate_geography(resolved.latitude, resolved.longitude)` не падает

**Импортировать `validate_geography` из `exact_orb.domain`.** Функция с тем же
именем есть и в `engine/ephemeris/calc.py`, но это тонкий делегат к доменной;
брать её оттуда значило бы тянуть native-путь в проверку, которая существует
именно ради того, чтобы его не трогать.

**Prevalidation проверяет, но не чинит.**

Объект, созданный через `model_construct(...)`, минует валидаторы pydantic,
и нормализаторы, вызванные в prevalidation, свои результаты **выбрасывают** —
они нужны только чтобы упасть на некорректном входе. Отсюда правило: после
prevalidation поля спеки должны быть пригодны к прямому использованию
адаптером, без доработки.

Конкретно для `include`: `normalize_include(chart_kind, None)` возвращает
дефолтный набор и не падает, поэтому объект с `include=None` пройдёт проверку,
а `frozenset(spec.include)` в адаптере бросит `TypeError` уже внутри executor —
и дефект спеки будет диагностирован как `ENGINE_UNEXPECTED`. Поэтому проверка
на `not None` стоит в списке выше отдельной строкой.

`include=None` допустим только при обычном конструировании `NatalChartSpec`,
где `model_validator` подставляет дефолт до того, как объект попадёт сюда.

Маппинг prevalidation failures:

* ошибки спеки -> `ChartCalculationError("SPEC_INVALID")`
* ошибки географии -> `ChartCalculationError("GEOGRAPHY_INVALID")`

Не проверять в этом промте `selena_method` и `orb_profile`: их нет
в текущем `ChartSpec`.

---

## 11. Выполнение в executor

Использовать именно:

```python
loop = asyncio.get_running_loop()
await loop.run_in_executor(self._executor, ...)
```

Не использовать `asyncio.to_thread`. Причина: executor должен контролироваться
bootstrap'ом, а `run_id` должен быть передан в расчётный поток явно.

Сделать sync helper, который реально исполняется в worker thread:

```python
def _calculate_sync(adapter, spec, resolved, run_id: str):
    ...
```

Требования:

* `adapter.calculate` выполняется не в event loop thread
* event loop не блокируется
* `run_id` присутствует в логах, записанных из worker thread
* contextvars не считать достаточными
* не вводить timeout вокруг executor в этом промте
* `calculate_natal` должен быть работоспособен внутри worker thread:
  после `validate_ephemeris_path(...)` повторно применить уже замороженный
  `ephemeris.path` через `swiss_backend.swe.set_ephe_path(...)` внутри
  защищённого расчётного участка, потому что binding может хранить path
  thread-local

---

## 12. Проверка результата

После возврата adapter результата проверить:

```
result.chart_kind == spec.chart_kind
result.chart.chart_kind == spec.chart_kind
```

Двух равенств достаточно: трёхсторонняя сходимость из §10 документа
(`result.chart_kind`, `result.chart.chart_kind`, `spec.chart_kind`) следует
из них.

При нарушении:

```python
ChartCalculationError("ENGINE_UNEXPECTED", run_id=str(run.run_id))
```

Требования:

* не возвращать несовместимый result
* не собирать `ChartArtifact`
* не писать в кэш
* не пытаться чинить `chart_kind`
* ошибка не содержит содержимое chart/spec/resolved

---

## 13. Маппинг ошибок

```
prevalidation spec failure          -> ChartCalculationError("SPEC_INVALID")
prevalidation geography failure     -> ChartCalculationError("GEOGRAPHY_INVALID")
known degenerate houses ValueError  -> ChartCalculationError("HOUSES_DEGENERATE")
other ValueError from engine        -> ChartCalculationError("ENGINE_UNEXPECTED")
EphemerisConfigurationError         -> CalculationUnavailableError("EPHEMERIS_UNAVAILABLE")
EphemerisSessionRequiredError       -> ChartCalculationError("ENGINE_UNEXPECTED")
other unexpected exception          -> ChartCalculationError("ENGINE_UNEXPECTED")
result invariant mismatch           -> ChartCalculationError("ENGINE_UNEXPECTED")
```

**Маппить по явным классам, а не по корню иерархии.** В `exact_orb/errors.py`:

```
EphemerisRuntimeError
├── EphemerisConfigurationError
│   ├── EphemerisNotInitializedError
│   └── EphemerisPathMismatchError
└── EphemerisSessionRequiredError
```

`EphemerisSessionRequiredError` («Swiss Ephemeris calls require an active
`ephemeris_session()`») — дефект программиста, а не отказ развёртывания.
По §5.3 разделяющий признак — «кто чинит»: повтор не поможет, дежурный ничего
не сделает, а `EPHEMERIS_UNAVAILABLE` по §5.3.3 обязан порождать алерт.
Отправлять внутренний баг в retryable-класс нельзя.

Если ловить `EphemerisRuntimeError` целиком, то `EphemerisSessionRequiredError`
обрабатывать **до** него отдельной веткой.

`HOUSES_DEGENERATE` определять только по известному отказу домов: сообщение
содержит `could not calculate house cusps` или явный признак
`Placidus degenerates`.

Остальные `ValueError` после prevalidation не классифицировать как
`SPEC_INVALID`: если дешёвая проверка пропустила, это уже unexpected defect.

Privacy:

* не включать `str(exc)` исходного исключения в новую ошибку
* не логировать traceback
* не использовать `logger.exception` и `exc_info=True`
* в log можно писать только `exception_type`, но не message

---

## 14. Runtime logging

Добавить logger в `calculation/engine.py`.

События минимум:

```
calculation_started
calculation_finished
calculation_failed
calculation_thread_started
calculation_thread_finished
```

Поля:

* `run_id`
* `technique`
* `chart_kind`
* `duration_ms`
* `slow`
* `code` для отказа
* `failure_class` для отказа — `ChartCalculationError` или
  `CalculationUnavailableError`. §5.3 п. 6 требует в `calculation_failed`
  и код, и класс: алерт настраивается на класс, а не на строку
* `exception_type` для отказа

Не логировать:

* `birth_datetime`, `utc_datetime`, latitude, longitude
* `canonical_place`, `place_id`
* полный `calculation_key`
* `ResolutionWarning.message`, `CalculationWarning.message`
* `NatalChart`, `ResolvedBirthData`, `ChartSpec` целиком
* traceback, исходный exception message

`EngineService` не знает key, version и cache. Не добавлять эти поля в его API
ради логов. Key-level события появятся в `ChartArtifactResolver`.

---

## 15. Почистить существующие runtime-логи расчётного пути

**Входные данные.**

`src/exact_orb/engine/charts/natal.py`:

* start log (строка `calculate_natal start ...`) не должен содержать дату
  рождения и входные координаты
* `ephemeris configured mode=... source=... path=... missing=...` не должен
  содержать absolute path; оставить `mode`, `source`, число missing
* можно оставить `chart_kind`, `house_system`, `rulership`, include blocks

`src/exact_orb/engine/ephemeris/calc.py`:

* `Swiss Ephemeris warning source=%s retflags=%s message=%s` — убрать дословный
  `message`; писать `source`, `retflags`, `message_present=True`
* `CalculationWarning.message` в модели результата сохраняется полностью

`src/exact_orb/engine/ephemeris/selena.py`:

* `Swiss Ephemeris warning source=%s retflags=%s message=%s` — убрать дословный
  `message`; писать `source`, `retflags`, `message_present=True`
* `RuntimeError` при запрете fallback не должен включать дословный warning:
  иначе он снова попадёт в mapped exception/log через traceback или message

**Вычисленные положения не логируются дословно.**

Долготы тел позволяют восстановить момент рождения: пары «Солнце — Луна»
достаточно для даты, а вместе с Асцендентом — и для времени с точностью
до минут. §9.9 считает тройку «дата, широта, долгота» квази-идентификатором,
и вычисленные положения к ней эквивалентны.

Убрать числовые эклиптические координаты из debug-логов расчётного пути
во всех пяти местах:

```
engine/ephemeris/calc.py:119      body_calculated — убрать longitude, latitude, speed;
                                  оставить name, swe_id, house, retflags
engine/charts/natal.py:715        derived_point south_node — убрать longitude
engine/charts/natal.py:728        derived_point pars_fortune — убрать longitude
engine/charts/natal.py:747        derived_point selena — убрать longitude,
                                  оставить method
engine/strength/lunar_phase.py:43 calculate_lunar_phase — убрать sun_longitude,
                                  moon_longitude, elongation; оставить phase
```

Сами записи сохранить: факт вычисления и его результат в виде имени фазы,
дома или метода остаются полезны для диагностики. Числа доступны из результата
и из CLI-вывода, дублировать их в лог незачем.

`engine/strength/degrees.py:51` не трогать: там только счётчики.
`engine/charts/transit.py` таких записей не содержит.

Не менять в этой задаче:

* CLI startup и `cli_call` логи
* стартовый лог `exact_orb.config` («Swiss Ephemeris files found in \<path\>»),
  на который опираются тесты в `test_ephemeris_runtime_config.py`

Это осознанное ограничение области, а не недосмотр: см. §17 п. 5.

---

## 16. Границы модулей — правка существующего теста

`tests/test_module_boundaries.py` уже существует и проверяет инвариант I1.
Отдельные subprocess-проверки не добавлять — тест делает и статический разбор,
и импорт в подпроцессе.

Что изменить:

1. Добавить новый контрактный модуль в `CONTRACT_MODULES`:

```python
CONTRACT_MODULES = (
    "exact_orb.domain",
    "exact_orb.errors",
    "exact_orb.outcomes",
    "exact_orb.calculation.spec",
    "exact_orb.calculation.keys",
    "exact_orb.calculation.cache",
    "exact_orb.calculation.errors",   # новое
    "exact_orb.birth.types",
)
```

2. В комментарии перечислить модули пакета `calculation`, выведенные
   из контрактного слоя сознательно: `types`, `codec`, `engine`.

3. Сохранить проверку, что импорт пакета `exact_orb.calculation`
   в подпроцессе не загружает `swisseph`, `exact_orb.swiss_backend`,
   `exact_orb.engine`, `exact_orb.config`.

`calculation.engine` дополнительно не должен импортировать:

* `calculation.cache`
* `calculation.codec`
* `calculation.types` (в частности `ChartArtifact`)
* resolver, tools, orchestration, llm

Эту проверку добавить статическим разбором импортов `calculation/engine.py`
в том же тесте.

---

## 17. Расхождения с документами — зафиксировать

Не «исправить молча», а записать: строкой в docstring модуля либо в разделе
«Известный долг» `docs/architecture/service_ready_architecture.md`.

1. **Полный маппинг спеки не выполняется.** §10 `chart_artifacts.md` требует,
   чтобы каждый параметр `calculate_natal` приходил из спеки. Адаптер передаёт
   7 из 15; `selena_method` остаётся дефолтным и читается из процессной
   конфигурации. Блокировано отсутствием `selena_method` и `orb_profile`
   в `ChartSpec`; связано с долгом по `CalculationVersion` (ADR-0017).
   Тест из §10 документа до закрытия этого пункта зелёным быть не может.
2. `RunContext` находится в `exact_orb/run_context.py`, а §2 и §3.6 помещают его
   в `application/run_context.py`. Используется реальный модуль.
3. `EngineService.__init__` принимает `techniques` и `slow_threshold_ms`
   без дефолтов; §4.4 документа сигнатуру не фиксирует в этом виде.
4. **Область чистки логов ограничена расчётным путём.** После этой задачи
   `calculate_natal` и ephemeris-путь чисты, но `cli_call` по-прежнему пишет
   `input='<дата время таймзона>'` и полный traceback
   (`tests/test_logging.py`), а `exact_orb.config` пишет абсолютный путь
   к каталогу эфемерид. Требование §10 «логи не содержат даты рождения
   ни на одном уровне логирования» выполняется для calculation-слоя,
   а не для процесса целиком.

---

## 18. Документы

Если требуется, обновить:

* `docs/requirements/component_responsibilities/exact-orb_build_natal_components.md`
  §2:
   * добавить `calculation/errors.py` в пакетную структуру
   * сохранить `calculation/engine.py` вне shared-contract списка
* `docs/requirements/component_responsibilities/exact-orb_build_natal_components.md`
  §4.4:
   * constructor `EngineService` принимает `techniques` и `slow_threshold_ms`
   * `ChartSpec` сейчас не содержит `selena_method` / `orb_profile`
   * полный mapping относится к текущим spec-owned полям
* `docs/architecture/service_ready_architecture.md` — пункты §17 в «Известный долг»

Не переписывать unrelated sections.

---

## 19. Тесты

**Разделить unit и integration по двум файлам.** Маркер `no_ephemeris_autoinit`
ставится на файл целиком, поэтому общий `pytestmark` сломал бы integration smoke,
которому нужны реальные эфемериды:

```
tests/test_calculation_engine.py             unit; pytestmark = pytest.mark.no_ephemeris_autoinit
tests/test_calculation_engine_integration.py integration; маркер НЕ ставить
```

Integration-файлу ничего вызывать вручную не нужно: autouse-фикстура
в `tests/conftest.py` сама выполняет `configure_ephemeris(REPO_ROOT / "ephe")`
для тестов без маркера.

### Типы и registry

* `CalculationResult` frozen
* `CalculationResult.warnings` хранится tuple
* `TechniqueAdapter` runtime-checkable: объект с `technique` и `calculate`
  проходит `isinstance`, объект без `technique` — не проходит
* `issubclass` с `TechniqueAdapter` не использовать (бросает `TypeError`)
* `CalculationEnginePort` не runtime-checkable — соответствие проверяется
  аннотацией, а не рантайм-проверкой
* constructor отвергает пустой registry
* constructor отвергает отсутствие `"natal"`
* constructor отвергает key != `adapter.technique`
* constructor отвергает неизвестную технику сверх текущего `{"natal"}`
* constructor отвергает значение, не удовлетворяющее `TechniqueAdapter`
* constructor отвергает адаптер с `async def calculate` и `technique="natal"`
  (`ValueError`); адаптер с корректной синхронной сигнатурой принимается
* constructor отвергает неположительный `slow_threshold_ms`

### NatalTechniqueAdapter mapping

Передавать fake-вычислитель через `NatalTechniqueAdapter(calculator=fake)`,
без monkeypatch импортов. Fake записывает полученные аргументы и возвращает
заранее собранный `NatalChart`.

Проверить, что adapter передаёт:

* `resolved.utc_datetime`, `resolved.latitude`, `resolved.longitude`
* `chart_kind=spec.chart_kind`
* `house_system="P"` для `spec.house_system == "P"` (строка, не bytes)
* `rulership=spec.rulership`
* `include=frozenset(spec.include)`
* `near_interception_threshold=spec.near_interception_threshold`

Проверить:

* adapter не получает `run` — прямая проверка набора переданных аргументов
* adapter не создаёт `ChartArtifact`
* cosmogram получает cosmogram-default include
* adapter возвращает `CalculationResult`
* `result.chart is chart`
* `result.warnings == chart.warnings`

### Executor

* adapter выполняется в другом thread, не в event loop thread
* event loop остаётся живым во время расчёта
* `run_id` есть в логах из worker thread
* `asyncio.to_thread` не используется
* prevalidation failure не вызывает executor
* **prevalidation failure не захватывает `ephemeris_session()`** — проверка
  по счётчику захватов лока, как требует §10 документа. Это более точная
  проверка, чем «executor не вызван»: она подтверждает, что невалидная спека
  отвергается до глобального лока

### Prevalidation

Через `model_construct(...)` создать некорректные объекты и проверить:

* natal без houses -> `SPEC_INVALID`
* cosmogram с houses/rulers/strength -> `SPEC_INVALID`
* invalid house_system -> `SPEC_INVALID`
* invalid rulership -> `SPEC_INVALID`
* negative или non-finite `near_interception_threshold` -> `SPEC_INVALID`
* `NatalChartSpec.model_construct(chart_kind="natal", include=None)`
  -> `SPEC_INVALID`, executor не вызван, `ephemeris_session()` не захвачен
* latitude/longitude вне диапазона или NaN/inf -> `GEOGRAPHY_INVALID`
* неизвестный `spec.technique` -> `SPEC_INVALID`

### Error mapping

* known houses `ValueError` -> `ChartCalculationError("HOUSES_DEGENERATE")`
* other `ValueError` -> `ChartCalculationError("ENGINE_UNEXPECTED")`
* `EphemerisNotInitializedError` -> `CalculationUnavailableError("EPHEMERIS_UNAVAILABLE")`
* `EphemerisPathMismatchError` -> `CalculationUnavailableError("EPHEMERIS_UNAVAILABLE")`
* **`EphemerisSessionRequiredError` -> `ChartCalculationError("ENGINE_UNEXPECTED")`**,
  а не в retryable класс
* arbitrary `RuntimeError` -> `ChartCalculationError("ENGINE_UNEXPECTED")`
* result invariant mismatch -> `ChartCalculationError("ENGINE_UNEXPECTED")`
* mapped error text не содержит source exception message
* mapped error `__cause__ is None`
* `error.run_id` — `str`, а не `UUID`

### Logging privacy

С fake adapter, который падает с сообщением, содержащим `1990-09-02`,
`55.7558`, `37.6173`, `Moscow` и warning-like текст. Проверить:

* logs содержат `run_id`, `code`, `failure_class`, `exception_type`
* logs не содержат перечисленные **значения**
* logs не содержат traceback

Искать в логах именно значения, а не имена полей: подстрока `latitude`
легитимно встречается в записи `body_calculated`, и поиск по имени даст
ложное срабатывание.

Отдельно проверить runtime-логи расчётного пути:

* `calculate_natal` debug logs не содержат дату рождения и входные координаты
* `ephemeris configured` не содержит absolute path
* ephemeris warning log не содержит дословный `warning.message`
* warning log в `engine/ephemeris/selena.py` не содержит дословный
  `warning.message`, а RuntimeError при запрете fallback не включает warning
* `body_calculated` не содержит `longitude`, `latitude`, `speed`
* `derived_point` (south_node, pars_fortune, selena) не содержит `longitude`
* `calculate_lunar_phase` не содержит `sun_longitude`, `moon_longitude`,
  `elongation`
* не добавлять глобальный brittle-тест «все долготы из chart отсутствуют во всех
  `exact_orb.engine.*` логах»: проверять конкретные families/field names
  (`body_calculated`, `derived_point`, `calculate_lunar_phase`) и конкретные
  sensitive input values
* возвращаемый `CalculationWarning.message` сохраняется полностью

### Integration smoke

Файл `tests/test_calculation_engine_integration.py`, без маркера
`no_ephemeris_autoinit`. С реальным `NatalTechniqueAdapter` и существующими
ephemeris fixtures:

* natal считает карту через `EngineService`
* cosmogram считает карту через `EngineService`
* результат совпадает по ключевым числам с прямым `calculate_natal`
* вырожденный Placidus за высокой широтой через `EngineService`
  даёт `HOUSES_DEGENERATE`
* существующие engine/golden тесты не ломаются
* `test_logging.py` и `test_ephemeris_runtime_config.py` остаются зелёными

---

## 20. Приёмка

**Контракты и типы**

* `src/exact_orb/calculation/errors.py` создан, `ArtifactError` имеет явный
  `__init__`, `run_id` — строка
* `CalculationFailed` добавлен в `outcomes.py` и в `__all__`
* `src/exact_orb/calculation/engine.py` создан
* `CalculationResult` frozen, не содержит key/version/cache/artifact
  и несёт сырой `NatalChart`
* `CalculationEnginePort.calculate(spec, resolved, *, run)` реализован
  и **не** помечен `runtime_checkable`
* `TechniqueAdapter` sync, `runtime_checkable`, не знает run/key/version/cache

**Поведение**

* `NatalTechniqueAdapter` принимает `calculator` явной зависимостью
* `NatalTechniqueAdapter` мапит все поля текущего `NatalChartSpec`
  (7 из 15 параметров ядра; остаток зафиксирован в §17 п. 1)
* `house_system` передаётся нормализованной строкой
* `EngineService` выбирает adapter по `spec.technique`
* registry техник валидируется на старте: протокол, совпадение ключа
  и синхронность `calculate`
* конструктор `EngineService` отвергает асинхронный адаптер
* `slow_threshold_ms` без дефолта, передаётся снаружи
* cheap prevalidation выполняется до executor и до `ephemeris_session()`
* prevalidation отвергает `include=None` и не чинит `model_construct`-объекты
* `validate_geography` берётся из `exact_orb.domain`
* `calculate_natal` вызывается через `ThreadPoolExecutor`, event loop
  не блокируется, `run_id` передаётся в worker thread явным аргументом
* result invariant проверяется

**Ошибки**

* ошибки мапятся в typed calculation errors по явным классам исключений
* `EphemerisSessionRequiredError` не попадает в retryable класс
* mapped errors имеют `__cause__ is None`
* `calculation_failed` содержит `code` и `failure_class`

**Приватность (в границах calculation-слоя)**

* mapped errors и логи `calculation/engine.py` не содержат дату рождения,
  координаты, место, `warning.message`, traceback и исходный exception message
* входные данные убраны из `calculate_natal start`, `ephemeris configured`
  и ephemeris warning
* вычисленные эклиптические долготы убраны из debug-логов во всех пяти местах
* ограничение области зафиксировано в §17 п. 4: CLI и стартовый лог
  `exact_orb.config` не тронуты и по-прежнему содержат ввод пользователя
  и абсолютный путь

**Границы**

* `tests/test_module_boundaries.py` обновлён: `calculation.errors` в
  `CONTRACT_MODULES`, `calculation.engine` вне контрактного слоя
* package-level `exact_orb.calculation` остаётся swisseph-free
* `calculation.engine` не импортирует cache/codec/`ChartArtifact`/resolver
* кэширование и `ChartArtifactResolver` не реализованы

**Тесты**

* unit и integration тесты движка разведены по разным файлам
* релевантные тесты зелёные
* полный `pytest` зелёный

**Документы**

* пункты §17 записаны, а пакетная структура `build_natal_components.md`
  обновлена под `calculation/errors.py`
