# Prompt 4: ChartArtifactResolver

Задача: реализовать `ChartArtifactResolver` как единственную точку получения
`ChartArtifact`.

Работай по:

- `docs/requirements/component_responsibilities/exact-orb_chart_artifacts.md`
  §1, §2.1, §2.4, §3, §5, §6, §7, §7.1, §8.3, §10
- `docs/requirements/component_responsibilities/exact-orb_build_natal_components.md` §4.5
- `docs/sequence_diagrams/chart_artifacts/*.puml`
- `docs/requirements/decisions/0017-calculation-cache-and-chartspec.md`
- уже реализованным слоям: `calculation/keys.py`, `calculation/types.py`,
  `calculation/codec.py`, `calculation/cache.py`, `calculation/engine.py`,
  `calculation/errors.py`

Не реализовывать:

- Redis
- distributed lock
- negative cache
- derived charts
- Tool/API handlers
- `CalculationVersion`
- новые техники
- изменение численного расчёта Swiss Ephemeris

---

## 1. Модуль

Создать `src/exact_orb/calculation/artifacts.py`:

```python
class ChartArtifactResolver:
    async def ensure_chart(
        self,
        spec: ChartSpec,
        resolved: ResolvedBirthData,
        *,
        run: RunContext,
    ) -> ChartArtifact: ...
```

Сигнатура — дословно из §2.1: `run: RunContext` аргументом, не через
`contextvars` (§2.4).

`ChartArtifactResolver` не экспортировать из
`src/exact_orb/calculation/__init__.py`, чтобы пакетный импорт
`exact_orb.calculation` оставался swisseph-free:

```python
from exact_orb.calculation.artifacts import ChartArtifactResolver
```

---

## 2. Constructor

```python
def __init__(
    self,
    *,
    cache: CalculationCache,
    engine: CalculationEnginePort,
    version: str,
    degraded_log_interval_s: float,
    clock: Callable[[], float] | None = None,
) -> None: ...
```

§2.1 фиксирует три параметра: `cache`, `engine`, `version`.
`degraded_log_interval_s` добавляется потому, что §7.1.6 селит состояние
дросселя именно в резолвер. `clock` — ради тестируемости длительностей.
Больше ничего не добавлять; в частности **не** добавлять `cache_timeout_ms`
в резолвер.

Требования:

- `version` — opaque string, непустая, не меняется в течение жизни объекта (§2.1)
- `degraded_log_interval_s` — положительное число, без дефолта;
  значение первой версии (60 с, §8.4) передаёт composition/bootstrap-слой
- `clock` по умолчанию `time.monotonic`
- `clock` используется только для длительностей, `started_at` в single-flight
  и throttling логов
- wall clock не использовать

**Предусловие на `version`.** `selena_method` влияет на положения, приходит
из `[tool.exact_orb]` и не входит ни в `ChartSpec`, ни в ключ. Пока
`CalculationVersion` не реализован, строка `version`, которую передаёт
вызывающий composition/bootstrap-слой, **обязана включать `selena_method`** —
иначе смена метода не инвалидирует кэш и попадание вернёт карту, посчитанную
по другому правилу.

В этом промте не проверять содержимое `version`, не импортировать `config`,
не импортировать будущий `bootstrap` и не реализовывать `CalculationVersion`.
Резолвер трактует `version` как непрозрачную строку. Этот временный долг
записывается в §13.

---

## 3. Таймаут кэша — в адаптере/settings, не в резолвере

**Protocol `CalculationCache` не менять.** Он остаётся:

```python
class CalculationCache(Protocol):
    async def get(self, key: str) -> bytes | None: ...
    async def put(self, key: str, payload: bytes) -> None: ...
```

`InMemoryCalculationCache` в этом промте тоже не менять: `cache_timeout_ms`
для него no-op и не входит в конструктор согласно
`build_natal_components.md` §4.3.

Обоснование — `chart_artifacts.md` §3.2.1: «Таймаут реализуется **внутри
адаптера кэша**, а не в резолвере: для in-memory он no-op… Резолвер видит
только исключение и обрабатывает его fail-open». Значение
`cache_timeout_ms = 50` из §8.4 относится к будущему Redis-адаптеру или
`CacheSettings`, а не к конструктору резолвера.

Следствия:

- резолвер не знает про таймауты вообще
- резолвер не передаёт `timeout_ms` в `get`/`put`
- резолвер не оборачивает `get`/`put` в `asyncio.wait_for`
- превышение таймаута приходит в резолвер обычным исключением и обрабатывается
  fail-open наравне с любым другим отказом кэша
- будущий Redis-адаптер реализует прерывание внутри себя
- контракт кэша, сознательно суженный в промте 2 до `bytes` и двух операций,
  не расширяется

---

## 4. `ensure_chart` — поток

1. Построить `CalculationInput` через `calculation_input_from(resolved)`.
2. Построить key через `calculation_key(calc_input, spec, self.version)`.
3. `payload = await cache.get(key)`.
4. Если payload не `None` — попытка попадания (§5). При успехе вернуть artifact.
5. Промах, деградация, `stale` или `corrupt` — перейти на single-flight
   miss path (§7).
6. После расчёта собрать `ChartArtifact` в резолвере (§8).
7. `payload = encode_chart_artifact(artifact)`.
8. `await cache.put(key, payload)`.
9. Вернуть artifact **независимо** от исхода `put` (§3.5.1).

Порядок из §3.5.1 обязателен: сначала гарантирован результат, потом
побочный эффект.

---

## 5. Проверки на попадании

`decode_chart_artifact(payload)` уже валидирует формат модели.
Дополнительные defensive-проверки резолвера (§3.2.2):

```text
artifact.calculation_key     == key
artifact.calculation_version == self.version
artifact.spec                == spec
```

Третья проверка сверх §3.2.2 допустима, но понимать её нужно правильно:
`spec` участвует в хэше ключа, поэтому сработать она может ровно на тех же
причинах, что и проверка ключа (дефект построения ключа, коллизия namespace,
подмена значения в хранилище). Отдельного класса отказов она не ловит.

`ChartArtifactDecodeError`:

- логировать `cache_corrupt` с `run_id`, усечённым key и `reason`
- в лог писать **только** `reason`, не `str(error)`, не payload, не traceback
- продолжить как промах

Несовпадение key / version / spec:

- логировать `cache_stale` с `run_id`, усечённым key и **версией записи**
  (`artifact.calculation_version`) — §7 требует её в полях события
- продолжить как промах

Ни `cache_stale`, ни `cache_corrupt` не превращаются в пользовательскую ошибку:
запись отбрасывается, выполнение идёт как при промахе (§3.2.3, §3.2.4).

---

## 6. Деградация кэша

`cache.get` бросил исключение:

- трактовать как промах
- логировать `cache_degraded` с `op="get"` и `reason` (тип исключения)
- продолжить расчёт

`cache.put` бросил исключение:

- логировать `cache_put_failed` с `op="put"` и `reason`
- отметить деградацию `op="put"` для дросселя и последующего `cache_recovered`
- вернуть уже построенный artifact

`encode_chart_artifact` неожиданно упал:

- логировать как отказ пути записи через `cache_put_failed`
- не валить `ensure_chart`
- вернуть уже построенный artifact

Кэш не источник истины: инвариант «после успешного `ensure_chart` запись
существует» **не заявляется и не тестируется** (§3.5.4).

---

## 7. Single-flight

Process-local дедупликация промахов внутри резолвера (§3.3):

```python
@dataclass
class _InFlight:
    task: asyncio.Task[ChartArtifact]
    leader_run_id: str
    started_at: float

_inflight: dict[str, _InFlight]
```

**Блокировки нет.** `asyncio.Lock` вокруг словаря не вводить: проверка
«есть ли ключ» и `create_task` идут без единого `await`, поэтому в одном
event loop они атомарны сами по себе. Лок, случайно удержанный через
`await asyncio.shield(task)`, сериализовал бы **все** ключи и молча сломал бы
требование §10 «параллельные вызовы по разным ключам не сериализуются между
собой резолвером» — без единого падающего теста. Записать комментарием:
между проверкой словаря и созданием задачи не должно быть `await`.

Поведение:

1. Первый промах по ключу создаёт задачу:
   `asyncio.create_task(self._calculate_and_store(key, spec, resolved, run))`.
2. **Все ожидающие, включая лидера, ждут `await asyncio.shield(inflight[key].task)`.**
   Лидер не привилегирован: без `shield` его уход убил бы расчёт
   для присоединившихся.
3. Уход всех ожидающих не отменяет задачу; результат доходит до кэша.
4. Результат или исключение получают все ожидающие.
5. **Запись `_inflight` удаляется в `finally` внутри `_calculate_and_store`**,
   до того как задача помечена `done`. Не в done-callback: колбэк планируется
   через `call_soon`, и между завершением задачи и его выполнением остаётся
   окно, в котором упавшая задача ещё лежит в словаре. Пришедший в это окно
   вызывающий присоединится к мёртвой задаче и получит старое исключение
   вместо нового расчёта, ломая требование «после ошибки следующий вызов снова
   идёт в engine».
6. Удаление проверяет identity по текущей task, чтобы не снести новую запись
   по тому же ключу:

```python
current = asyncio.current_task()
entry = self._inflight.get(key)
if entry is not None and entry.task is current:
    del self._inflight[key]
```

7. Задача обязана забрать своё исключение, иначе Python напишет
   «Task exception was never retrieved» (§3.3.6). **Наивная форма
   `add_done_callback(lambda t: t.exception())` неверна:** `Task.exception()`
   на отменённой задаче бросает `CancelledError`, и она вылетает из колбэка
   как «Exception in callback». Ожидающие задачу не отменяют, но при остановке
   loop'а pending-задачи отменяются штатно, так что ветка достижима.
   Правильная форма:

```python
def _drain(task: asyncio.Task) -> None:
    if not task.cancelled():
        task.exception()
```

8. `leader_run_id` нужен присоединившимся для события `singleflight_join` (§5.6);
   `started_at` даёт длительность ожидания.
9. Ошибки расчёта не кэшируются; после отказа следующий вызов снова идёт
   в движок (§5.3).

---

## 8. Расчёт и сборка `ChartArtifact`

Внутри `_calculate_and_store`:

```python
result = await self.engine.calculate(spec, resolved, run=run)
artifact = ChartArtifact(
    calculation_key=key,
    calculation_version=self.version,
    spec=spec,
    chart_kind=result.chart_kind,
    chart=result.chart,
    warnings=result.warnings,
)
```

Резолвер не знает сигнатуру `calculate_natal` и не выбирает функцию ядра
(§4, §11 `chart_artifacts.md`: артефакт собирает резолвер, а не движок).

**Здесь происходит превращение в artifact-safe форму.** `result.chart` —
сырой `NatalChart` с `ephemeris.path` и `ephemeris.source`; before-валидатор
`ChartArtifact` (промт 2, §2) нормализует его в `ArtifactNatalChart` и срезает
оба поля. Резолвер ничего не вырезает сам. Именно благодаря этому артефакт,
собранный на промахе, и артефакт, раскодированный из кэша, сравнимы
на равенство.

Если сборка `ChartArtifact` падает на инварианте или pydantic-валидации:

- мапить в `ChartCalculationError("ENGINE_UNEXPECTED", run_id=str(run.run_id))`
- `raise ... from None` — текст `ValidationError` содержит входные значения
- не писать в кэш
- не логировать сырой `ValidationError`

`RunContext.run_id` имеет тип `UUID`, а `ArtifactError.run_id` объявлен `str`:
конверсия `str(run.run_id)` делается здесь и в любом другом месте, где
резолвер создаёт ошибку или пишет `run_id` в лог. То же для `leader_run_id`.

`EngineService` уже проверяет result invariant (промт 3, §12), но резолвер
остаётся защитной границей перед записью артефакта.

---

## 9. Ошибки расчёта

`ChartCalculationError` и `CalculationUnavailableError`:

- проходят наружу без преобразования (§6.2: отображение в исход — работа
  прикладной границы)
- получают все ожидающие single-flight
- не кэшируются
- `_inflight` очищается

`CancelledError` вызывающего не отменяет общую задачу благодаря `shield`
(§3.3.2).

---

## 10. Логи и privacy

Runtime-логи резолвера не должны содержать (§7.2):

- дату рождения, `utc_datetime`
- latitude, longitude
- место, `canonical_place`, `place_id`
- **полный** `calculation_key`
- payload bytes, JSON артефакта
- `ResolvedBirthData`, `ChartArtifact`, `NatalChart`
- дословный `warning.message`
- сырой текст `ValidationError`

Разрешено:

- `run_id`, `leader_run_id`
- усечённый key
- `chart_kind`, `technique`
- коды ошибок, machine-readable `reason`
- `source` и `code` предупреждений
- длительности и счётчики

Усечение ключа: первые 12 hex-символов хвоста после префикса `eo:calc:v1:`.
Полный хэш в логах не нужен, а в связке с прочими полями квази-идентифицирует
данные рождения (§7.2).

Запрет действует и на уровне `DEBUG`: уровень включают на проде именно тогда,
когда что-то сломалось.

Не использовать `logger.exception` и `exc_info=True`. Для ошибок кэша и decode
логировать только тип или код причины.

---

## 11. События и уровни

Во всех событиях поле `key` означает **усечённый key**, не полный
`calculation_key`.

| Событие | Поля | Уровень |
|---|---|---|
| `cache_hit` | `run_id`, key, `chart_kind` | DEBUG |
| `cache_miss` | `run_id`, key, `chart_kind` | DEBUG |
| `cache_stale` | `run_id`, key, версия записи | **WARNING — алерт** (§3.2.3) |
| `cache_corrupt` | `run_id`, key, `reason` | **WARNING — алерт** (§3.2.4) |
| `cache_degraded` | `run_id`, `op`, `reason`, `suppressed?` | **WARNING**, дросселируется |
| `cache_recovered` | `run_id`, `op`, `degraded_ms`, `suppressed` | INFO |
| `singleflight_join` | `run_id`, `leader_run_id`, key, `waited_ms` | DEBUG |
| `cache_put_ok` | `run_id`, key | DEBUG |
| `cache_put_failed` | `run_id`, key, `reason` | WARNING |

Уровни — часть контракта, а не деталь реализации: алерт настраивается
по паре «уровень + код события».

`calculation_started`, `calculation_finished`, `calculation_failed` принадлежат
`EngineService` (промт 3, §14) и в резолвере не дублируются. См. §13 п. 2.

---

## 12. Дросселирование и счётчики

Состояние дросселя живёт в `ChartArtifactResolver`, не в кэше (§7.1.6).
Состояние вести отдельно по `op`: `"get"` и `"put"`. `reason` хранить как
`last_reason`, но восстановление (`cache_recovered`) тоже считается по `op`:
успешный `put` не лечит деградацию `get`, и наоборот.

Правила (§7.1):

1. Первый отказ после периода нормальной работы пишется **немедленно**,
   уровень `WARNING`, с `run_id`, `op` и `reason`.
2. Далее записи подавляются, счётчик подавленных растёт.
3. **По истечении `degraded_log_interval_s` пишется одна сводная запись**
   `cache_degraded(run_id, op, reason, suppressed=N)`. Без неё при часовой
   деградации в журнале останется одна строка на весь час.
4. При первой успешной операции того же `op` после отказов пишется
   `cache_recovered(run_id, op, degraded_ms, suppressed)`.
5. Дросселирование логов не меняет фактическое поведение `ensure_chart`.

**Счётчики обязательны, полноценный metrics-стек — нет.** §7.4 требует, чтобы
`hit_ratio` и число промахов были доступны наружу, §8.3 строит на них учёт
абьюза, а §7.1.5 отдельно оговаривает, что метрика **не дросселируется**
в отличие от лога. Достаточно целочисленных полей на резолвере, доступных
для чтения:

```text
hits, misses, stale, corrupt, put_ok, put_failed, cache_errors_total
```

Семантика счётчиков:

- `hits` увеличивается только при валидном cache hit, который возвращён
  без вызова движка
- `misses` увеличивается один раз на каждый `ensure_chart`, который не смог
  вернуть валидное попадание до single-flight: `payload is None`, отказ `get`,
  `cache_stale`, `cache_corrupt`
- `stale` и `corrupt` увеличиваются дополнительно к `misses`
- `put_ok` увеличивается только после успешного `cache.put`
- `put_failed` увеличивается при отказе `cache.put` или `encode_chart_artifact`
- `cache_errors_total` увеличивается на каждом отказе cache adapter
  (`get`/`put` exception), включая подавленные в журнале
- `put_failed` и `cache_errors_total` сами по себе не увеличивают `misses`

Опционально добавить read-only property `hit_ratio`; если добавляется, считать
как `hits / (hits + misses)`, а при нулевом знаменателе возвращать `None`.
Интеграция с внешней системой метрик — не эта задача.

---

## 13. Расхождения с документами — зафиксировать

Записать строкой в docstring модуля либо в «Известный долг»
`docs/architecture/service_ready_architecture.md`:

1. **`selena_method` не в ключе.** Пока `CalculationVersion` не реализован,
   корректность попаданий держится на том, что вызывающий composition/bootstrap
   слой передал `version`, уже включающую `selena_method` (§2). Это временная
   мера, а не решение. В этом промте содержимое `version` не проверять и
   `config` не импортировать.
2. **`key` отсутствует в `calculation_*` событиях.** §7 приписывает
   `calculation_started` поля `run_id`, `key`, `technique`, но по решению
   промпта 3 `EngineService` ключа не знает, а дублировать эти события
   в резолвере запрещено (§11). Корреляция сохраняется через `run_id`.
3. **`cache_timeout_ms` не параметр резолвера.** §8.4 перечисляет его
   в конфигурации первой версии; он должен жить в будущем Redis-адаптере
   или `CacheSettings` согласно §3.2.1, а не в конструкторе резолвера.
4. **`calculation/artifacts.py` есть в §2 `build_natal_components.md`** —
   в отличие от `codec.py` и `errors.py`, здесь расхождения нет; накопленный
   список модулей вне §2 остаётся прежним.

---

## 14. Тесты

Добавить `tests/test_chart_artifact_resolver.py`, unit, с моками кэша
и движка. Реальный Swiss Ephemeris не вызывать:

```python
pytestmark = pytest.mark.no_ephemeris_autoinit
```

**Попадание**

- hit возвращает decoded artifact
- hit не вызывает engine
- hit не вызывает `put`
- artifact, собранный на промахе, равен артефакту, раскодированному из кэша
  на следующем вызове

**Промах и запись**

- miss вызывает engine ровно один раз
- miss собирает `ChartArtifact` с ожидаемыми `calculation_key`
  и `calculation_version`
- `artifact.chart` — `ArtifactNatalChart`; `ephemeris.path` и `source`
  отсутствуют, хотя движок вернул сырой `NatalChart` с ними
- успешный miss вызывает `cache.put`
- смена `version` даёт промах на тех же данных

**Stale и corrupt**

- чужой `calculation_key` -> `cache_stale` и расчёт
- чужая `calculation_version` -> `cache_stale` и расчёт
- чужая `spec` -> `cache_stale` и расчёт
- `cache_stale` содержит версию записи
- битые gzip bytes -> `cache_corrupt(reason="gzip")` и расчёт
- пустой payload -> `cache_corrupt(reason="gzip")` и расчёт
- не-UTF8 payload -> `cache_corrupt(reason="utf8")` и расчёт
- невалидный JSON/model -> `cache_corrupt(reason="validation")` и расчёт
- `cache_stale` и `cache_corrupt` пишутся на уровне WARNING

**Fail-open**

- `cache.get` бросает -> промах, `cache_degraded(op="get")`, расчёт идёт
- `cache.put` бросает -> результат возвращается, `cache_put_failed`
- `encode_chart_artifact` бросает -> результат возвращается
- ни один из трёх случаев не превращается в исключение наружу

**Single-flight**

- N параллельных промахов по одному ключу вызывают engine ровно один раз
- все получают равные артефакты
- отмена присоединившегося не отменяет общую задачу
- отмена лидера не отменяет общую задачу
- уход **всех** ожидающих не отменяет задачу, и результат оказывается в кэше
- параллельные вызовы по **разным** ключам не сериализуются между собой
  (например: два ключа, движок с искусственной задержкой, оба заходят
  в `engine.calculate` до release)
- ошибка движка доходит до всех ожидающих
- `_inflight` пуст после успеха
- `_inflight` пуст после ошибки
- следующий после ошибки вызов снова идёт в движок
- упавшая задача без ожидающих не оставляет
  «Task exception was never retrieved»
- отменённая задача не приводит к «Exception in callback»: done-callback
  проверяет `task.cancelled()` перед `task.exception()`

**Дроссель и счётчики**

- первый `cache_degraded` пишется сразу
- повторные отказы внутри интервала подавляются
- по истечении интервала пишется сводная запись с `suppressed=N`
- `cache_recovered` пишется после первой успешной операции того же `op`
  и содержит `op`, `degraded_ms`, `suppressed`
- успешный `put` не пишет recovery для `get`, и наоборот
- `cache_errors_total` растёт на каждом отказе adapter, включая подавленные
  в логе
- `hits` / `misses` / `stale` / `corrupt` / `put_ok` / `put_failed`
  доступны для чтения и соответствуют семантике выше

Тесты дросселя используют fake clock, не `sleep`.

**Privacy**

Прогнать сценарии hit / miss / stale / corrupt / degraded с реальными
значениями в данных (`1990-09-02`, `55.7558`, `37.6173`, `Moscow`, текст
предупреждения) и проверить, что ни одна запись логгеров
`exact_orb.calculation.*` на уровне DEBUG не содержит:

- этих значений
- полного `calculation_key`
- payload bytes и JSON артефакта
- дословного `warning.message`
- текста `ValidationError`
- traceback

Искать значения, не имена полей.

---

## 15. Границы модулей

Обновить `tests/test_module_boundaries.py`:

- `calculation.artifacts` добавить в комментарий к списку модулей пакета
  `calculation`, выведенных из контрактного слоя: `types`, `codec`, `engine`,
  `artifacts`
- статической проверкой запретить `calculation/artifacts.py` импортировать
  `exact_orb.tools`, `exact_orb.orchestration`, `exact_orb.llm`,
  `exact_orb.cli`, `exact_orb.interpretation`
- импорты `calculation.cache`, `calculation.codec`, `calculation.types`,
  `calculation.engine`, `calculation.errors`, `calculation.keys` для него
  законны — это его работа
- сохранить проверку, что импорт пакета `exact_orb.calculation`
  в подпроцессе не загружает `swisseph`, `exact_orb.swiss_backend`,
  `exact_orb.engine`, `exact_orb.config`

---

## 16. Приёмка

**Модуль и контракт**

- `src/exact_orb/calculation/artifacts.py` создан
- `ensure_chart(spec, resolved, *, run)` реализован по §2.1
- конструктор — `cache`, `engine`, `version`, `degraded_log_interval_s`, `clock`;
  `cache_timeout_ms` в нём отсутствует
- Protocol `CalculationCache` не изменён
- `InMemoryCalculationCache` не меняет сигнатуру конструктора
- резолвер не оборачивает `get`/`put` в `asyncio.wait_for`

**Поток**

- key строится через `calculation_input_from` + `calculation_key`
- попадание проходит decode и проверки key / version / spec
- `cache_stale` и `cache_corrupt` ведут к расчёту, а не к ошибке
- отказ `get` даёт fail-open промах
- отказ `put` и отказ `encode` не валят результат
- артефакт возвращается независимо от исхода записи
- артефакт собирается резолвером; artifact-safe нормализация выполняется
  before-валидатором `ChartArtifact`, резолвер ничего не вырезает

**Single-flight**

- реализован через process-local `asyncio.Task` без `asyncio.Lock`
- `asyncio.shield` используется всеми ожидающими, включая лидера
- `_inflight` очищается в `finally` внутри задачи, с проверкой identity
  по `asyncio.current_task()`
- done-callback вычитывает исключение только при `not task.cancelled()`
- ошибки расчёта не кэшируются, все ожидающие получают один исход
- вызовы по разным ключам не сериализуются

**Ошибки и логи**

- `run_id` во всех событиях, строкой, через `str(run.run_id)`
- сборка артефакта падает в `ChartCalculationError("ENGINE_UNEXPECTED")`
  с `from None`
- полный `calculation_key` и персональные данные не пишутся ни на одном
  уровне логирования
- уровни событий заданы; `cache_stale` и `cache_corrupt` — WARNING
- `cache_stale` несёт версию записи
- дроссель ведётся отдельно по `op`, пишет первое событие сразу, сводку
  раз в интервал и `cache_recovered` при восстановлении того же `op`
- счётчики `hits`, `misses`, `stale`, `corrupt`, `put_ok`, `put_failed`,
  `cache_errors_total` доступны наружу и не дросселируются

**Границы и долг**

- package-level `exact_orb.calculation` остаётся swisseph-free
- `tests/test_module_boundaries.py` обновлён
- четыре пункта §13 записаны, включая требование к составу `version`

**Прогон**

- новые unit-тесты проходят без реального Swiss Ephemeris
- полный `pytest` зелёный
