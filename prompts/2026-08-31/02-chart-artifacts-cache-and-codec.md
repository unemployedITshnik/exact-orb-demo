# Prompt 2: CalculationCache и ChartArtifact Codec

Задача: реализовать байтовый `CalculationCache`, `InMemoryCalculationCache`, модель
`ChartArtifact` и codec для сериализации/десериализации `ChartArtifact`.

Работай по:

- `docs/requirements/component_responsibilities/exact-orb_chart_artifacts.md` §3.2, §3.5, §8.4, §8.5, §9, §10
- `docs/requirements/component_responsibilities/exact-orb_build_natal_components.md` §2, §3.4, §4.3
- `docs/requirements/decisions/0017-calculation-cache-and-chartspec.md`
- `docs/architecture/service_ready_architecture.md` — инварианты I1, I3, I4, I6
- уже реализованному слою `calculation/spec.py` и `calculation/keys.py`

Код менять только в рамках этой задачи.

Не реализовывать:

- `ChartArtifactResolver`
- `EngineService`
- `TechniqueAdapter`
- single-flight
- Redis
- `CalculationVersion`
- fail-open orchestration
- `cache_stale/cache_corrupt` логирование в resolver
- взаимодействие с Swiss Ephemeris

---

## 1. Добавить `calculation/types.py`

```python
class ArtifactEphemerisStatus(BaseModel):
    mode: Literal["files", "fallback"]
    required_files: tuple[str, ...]
    found_files: tuple[str, ...]
    missing_files: tuple[str, ...]

    @property
    def using_files(self) -> bool:
        return self.mode == "files"

class ArtifactNatalChart(NatalChart):
    ephemeris: ArtifactEphemerisStatus

class ChartArtifact(BaseModel):
    calculation_key: str
    spec: ChartSpec
    calculation_version: str
    chart_kind: ChartKind
    chart: ArtifactNatalChart
    warnings: tuple[CalculationWarning, ...]
```

Требования:

- `ChartArtifact.model_config = ConfigDict(frozen=True)`
- `calculation_key` должен начинаться с `eo:calc:v1:`
- `calculation_version` не пустая строка
- `chart_kind` — явное поле, не выводить его по отсутствию `houses`
- `spec` хранить как `ChartSpec`
- `chart` хранить как `ArtifactNatalChart`
- `warnings` хранить как `tuple`
- `isinstance(artifact.chart, NatalChart)` должен оставаться `True`
- `ArtifactEphemerisStatus` обязан повторить property `using_files` из `EphemerisStatus`
- проверить инвариант:
  - `chart_kind == spec.chart_kind`
  - `chart_kind == chart.chart_kind`
  - `warnings == chart.warnings`
- нарушение инварианта должно давать pydantic `ValidationError`

Про инвариант `warnings`: равенство `artifact.warnings == chart.warnings` верно
для натальной техники. По §4 `chart_artifacts.md` композитные артефакты
(`ensure_derived`) собирают warnings из нескольких базовых карт, и равенство там
держаться не будет. Записать инвариант как привязанный к технике, чтобы при
появлении композитов его сняли осознанно.

Важно: `calculation/types.py` может импортировать `NatalChart` и
`CalculationWarning` из engine-моделей, потому что это typed artifact payload.
Требование swisseph-free относится к `domain.py`, `calculation/spec.py`,
`calculation/keys.py` и `calculation/cache.py`, но не к typed artifact model/codec.

Известное следствие, которое нужно записать в комментарии модуля, а не обходить:
импорт `calculation.types` тянет `engine`, `config` и `swisseph`. Значит проверить
кэш без native binding можно, а распаковать попадание — нет. Настоящее лекарство —
вынести модели результата (`NatalChart`, `CalculationWarning`, `BodyPosition`) в
swisseph-free модуль; это вне scope этой задачи.

`frozen=True` здесь означает верхнеуровневую заморозку модели. Глубокая
неизменяемость вложенного `NatalChart` не обещается: текущий `NatalChart` не
frozen. Безопасность кэша обеспечивается тем, что cache хранит bytes, а decode
каждого попадания создаёт новый объект. Это должно быть покрыто тестом (§10).

---

## 2. Artifact-safe ephemeris status

`ChartArtifact` — это artifact payload, а не сырая копия результата движка.

`NatalChart.ephemeris.path` содержит runtime filesystem path и может раскрывать:

- имя пользователя на машине разработчика или сервера
- layout файловой системы
- различия между инстансами

`NatalChart.ephemeris.source` описывает, как конкретный процесс нашёл каталог:
`argument`, `environment`, `pyproject`, `default`. Это runtime provenance, а не
свойство расчёта.

Абсолютный путь и `source` не должны попадать в artifact payload.

Artifact-safe форма сохраняет:

- `mode`
- `required_files`
- `found_files`
- `missing_files`

Artifact-safe форма не сохраняет:

- `path`
- `source`

`mode` влияет на числа (`files` против `fallback`), поэтому остаётся. Три списка
файлов — audit-информация: они помогают понять, чем считалось попадание, но не
являются идентичностью артефакта. Идентичность набора эфемерид принадлежит
`CalculationVersion` (ADR-0017: fingerprint файлов `ephe/*.se1`), а не payload.

Не формулировать требований вида «один и тот же расчёт на разных машинах даёт
одинаковые bytes»: смена pydantic, zlib, схемы модели или набора файлов эфемерид
может законно изменить bytes (§7).

Реализация:

- добавить `ArtifactEphemerisStatus` внутри `calculation/types.py`
- добавить `ArtifactNatalChart(NatalChart)`, переопределяющий только поле `ephemeris`
- `ChartArtifact.chart` типизировать как `ArtifactNatalChart`
- не менять runtime-модель `EphemerisStatus` в `config.py`
- не менять `NatalChart`
- не менять поведение `calculate_natal`
- путь и `source` удаляются только на границе artifact payload

Нормализация:

- нормализация происходит при конструировании `ChartArtifact`, не в codec
- codec ничего не вырезает и не знает про `path/source`
- преобразование raw `NatalChart -> ArtifactNatalChart` делать в `ChartArtifact`
  через `model_validator(mode="before")`
- не делать это через `model_validator(mode="after")`: `ChartArtifact` frozen,
  и after-validator не должен присваивать поля
- условие в валидаторе:
  `isinstance(value, NatalChart) and not isinstance(value, ArtifactNatalChart)`
- before-validator обязателен по существу: pydantic отвергает подстановку
  родительской модели в поле дочернего типа с ошибкой `model_type`

Механизм отрезания держится на неявном `extra="ignore"`. `path` и `source`
исчезают потому, что `ArtifactEphemerisStatus` игнорирует лишние ключи из
`model_dump()`. Поэтому обязателен drift-guard тест:

```python
set(ArtifactEphemerisStatus.model_fields) == set(EphemerisStatus.model_fields) - {"path", "source"}
```

---

## 3. Добавить `calculation/cache.py`

```python
@runtime_checkable
class CalculationCache(Protocol):
    async def get(self, key: str) -> bytes | None: ...
    async def put(self, key: str, payload: bytes) -> None: ...
```

Требования:

- API сразу async, даже для in-memory (§9.1 `chart_artifacts.md`)
- `CalculationCache` пометить `@runtime_checkable`
- `get` возвращает `bytes | None`
- `put` принимает только `bytes`
- `put` обязан проверять `isinstance(payload, bytes)` и отвергать любой не-`bytes`
  payload через `TypeError`
- расширение до `bytearray | memoryview` не вводить: `encode_chart_artifact`
  возвращает `bytes`, Redis отдаёт `bytes`, других вызывающих нет
- имена операций только `get` / `put`, не `save`, не `load` (ADR-0017)
- cache не импортирует `ChartArtifact`
- cache не импортирует `engine`
- cache не знает формат payload
- cache не делает gzip/json/model_validate
- cache не логирует payload и не логирует key-input данные
- cache не реализует fail-open: исключения обрабатывает будущий resolver

---

## 4. Реализовать `InMemoryCalculationCache`

```python
class InMemoryCalculationCache:
    def __init__(
        self,
        *,
        max_entries: int,
        ttl_seconds: float | None,
        clock: Callable[[], float] | None = None,
    ) -> None: ...
```

Дефолтов у `max_entries` и `ttl_seconds` нет. Значения первой версии
(`max_entries = 1000`, `ttl_seconds = 3600`, §8.4) передаёт будущий bootstrap.
Дефолт в конструкторе создал бы второй источник истины про конфигурацию.

Требования:

- хранит именно `bytes`
- `max_entries` должен быть положительным
- `ttl_seconds`:
  - `None` означает без TTL
  - иначе должен быть положительным
- `clock` должен быть монотонным; по умолчанию `time.monotonic`
- не использовать `time.time` / wall clock для TTL
- `cache_timeout_ms` в in-memory не добавлять
- timeout появится вместе с будущим Redis-адаптером или `CacheSettings`
- вытеснение предсказуемое: LRU
- `get` по существующему ключу обновляет recency
- `put` по существующему ключу обновляет payload, expiry и recency
- повторный `put` по тому же key не увеличивает число записей (§3.5.3)
- просроченная запись на `get` удаляется и возвращается `None`
- `__len__()` возвращает физическое число записей
- добавить `clear()` — удобный метод in-memory реализации, не в Protocol
- добавить `__len__()` для тестов, не в Protocol

Порядок `put`:

1. удалить expired entries
2. записать/обновить key
3. пока `len > max_entries` — вытеснять LRU

Реализация:

- использовать stdlib, предпочтительно `collections.OrderedDict`
- не использовать фоновые задачи/reaper; lazy expiration достаточно
- не использовать threading locks, пока in-memory cache живёт внутри одного event loop

---

## 5. Добавить `calculation/codec.py`

Публичный API:

- `encode_chart_artifact(artifact: ChartArtifact) -> bytes`
- `decode_chart_artifact(payload: bytes) -> ChartArtifact`
- `ChartArtifactDecodeError`
- `ChartArtifactDecodeReason`

Формат записи:

```text
ChartArtifact.model_dump_json()
→ UTF-8 bytes
→ gzip.compress(..., compresslevel=6, mtime=0)
```

Формат чтения:

```text
gzip.decompress(payload)
→ explicit bytes.decode("utf-8")
→ ChartArtifact.model_validate_json(json_text)
```

Требования:

- gzip level строго 6
- `mtime=0` — детерминизм encode внутри процесса
- JSON encoding строго UTF-8
- не передавать bytes напрямую в `model_validate_json`
- codec возвращает/принимает bytes
- codec не знает про cache
- codec не знает про resolver
- codec не реализует fail-open
- codec не проверяет `calculation_key == key`: key известен resolver
- codec не проверяет `calculation_version == current_version`: она известна resolver
- codec не логирует
- codec не вырезает `path/source`; он работает только с уже artifact-safe `ChartArtifact`

---

## 6. Ошибка decode

```python
ChartArtifactDecodeReason = Literal["gzip", "utf8", "validation"]

class ChartArtifactDecodeError(ValueError):
    reason: ChartArtifactDecodeReason
```

Полный маппинг:

```text
пустой payload (b"")        -> "gzip"
gzip.BadGzipFile            -> "gzip"
EOFError                    -> "gzip"
zlib.error                  -> "gzip"
UnicodeDecodeError          -> "utf8"
pydantic ValidationError    -> "validation"
```

Наивная реализация промахнётся в двух местах:

- `gzip.decompress(b"")` не бросает исключение и возвращает `b""`; пустой payload
  проверять явно до распаковки
- битый gzip может проявиться как `BadGzipFile`, `EOFError` или `zlib.error`

Privacy:

- `ChartArtifactDecodeError` несёт только `reason`
- не добавлять в текст ошибки исходный payload
- не добавлять в текст ошибки исходный `ValidationError`
- в ветке `validation` использовать `raise ... from None`, а не `from exc`
- в ветках `gzip` и `utf8` цепочку `from exc` можно сохранить
- `ChartArtifactDecodeError` нельзя логировать через `logger.exception(...)` или с `exc_info=True`
- будущий resolver логирует только `reason`, `run_id`, `key`
- будущий resolver никогда не логирует `str(error)`, traceback, исходный payload или `ValidationError`

---

## 7. Round-trip и стабильность bytes

Round-trip invariant — равенство моделей:

```python
decode_chart_artifact(encode_chart_artifact(artifact)) == artifact
```

Байтовая стабильность проверяется только внутри одного процесса и одной версии
моделей:

```python
encode_chart_artifact(artifact) == encode_chart_artifact(artifact)
```

Не вводить golden-test на bytes codec payload. Причин три: смена pydantic,
изменение `NatalChart` или artifact schema законно меняют bytes; байт OS в
gzip-заголовке ставит zlib из своей сборки, и он различается между платформами;
набор установленных файлов эфемерид различается между машинами (§2).

---

## 8. Экспорты

`src/exact_orb/calculation/__init__.py` должен оставаться swisseph-free.

Экспортировать из package-level `__init__` только:

- существующие типы Prompt 1
- `CalculationCache`
- `InMemoryCalculationCache`

Не экспортировать из `exact_orb.calculation.__init__`:

- `ChartArtifact`, `ArtifactNatalChart`, `ArtifactEphemerisStatus`
- `encode_chart_artifact`, `decode_chart_artifact`
- `ChartArtifactDecodeError`, `ChartArtifactDecodeReason`
- codec-модуль целиком

Их импортировать только по подмодулям: `exact_orb.calculation.types`,
`exact_orb.calculation.codec`.

---

## 9. Тесты cache

`tests/test_calculation_cache.py`. Проверить:

- пустой cache даёт `None`
- `put` затем `get` возвращает те же bytes
- cache хранит bytes, не model object
- `put` отвергает `bytearray`, `memoryview`, `str`, arbitrary object через `TypeError`
- повторный `put` по тому же key не увеличивает размер
- повторный `put` обновляет payload
- `get` обновляет LRU recency
- при `max_entries` вытесняется least-recently-used key
- при `put` сначала удаляются expired entries, затем вытесняется LRU
- `put` вытесняет в цикле: при вставке в переполненный cache размер приходит к `max_entries`
- expired entry возвращает `None`
- expired entry удаляется из физического размера cache после `get`
- `ttl_seconds=None` отключает expiration
- `clear()` очищает cache
- невалидные `max_entries`, `ttl_seconds` отвергаются
- `CalculationCache` помечен `@runtime_checkable`
- `isinstance(cache, CalculationCache)` возвращает `True`

Тесты используют fake clock, не `sleep`.

Тест дефолтного clock:

- надёжных вариантов ровно два — `assert cache._clock is time.monotonic` либо
  `monkeypatch.setattr(<модуль кэша>.time, "monotonic", fake)` до конструирования
  объекта
- подмена `time.time` ничего не доказывает

Тест «у in-memory нет `cache_timeout_ms`» не писать: это негативный тест на
отсутствие будущего параметра. Решение зафиксировано в документах.

---

## 10. Тесты codec/types

`tests/test_chart_artifact_codec.py`. Проверить:

**Модель и нормализация**

- `ChartArtifact` из raw `NatalChart` нормализует `chart` в `ArtifactNatalChart`
- `isinstance(artifact.chart, NatalChart)` остаётся `True`
- повторная нормализация уже нормализованного chart не ломается
- artifact ephemeris сохраняет `mode`, `required_files`, `found_files`, `missing_files`
- `artifact.chart.ephemeris.using_files` работает
- artifact ephemeris не содержит `path` и не содержит `source`
- отсутствие `path/source` проверяется по разобранному JSON
- drift-guard: `set(ArtifactEphemerisStatus.model_fields)` равно
  `set(EphemerisStatus.model_fields) - {"path", "source"}`
- `ChartArtifact` frozen на верхнем уровне
- `ChartArtifact` проверяет prefix `calculation_key`
- `ChartArtifact` отвергает пустой `calculation_version`
- `ChartArtifact` отвергает mismatch `chart_kind != spec.chart_kind`
- `ChartArtifact` отвергает mismatch `chart_kind != chart.chart_kind`
- `ChartArtifact` отвергает mismatch `warnings != chart.warnings`

**Codec**

- `encode_chart_artifact` возвращает bytes
- bytes реально gzip
- два encode одного artifact дают одинаковые bytes
- gzip payload содержит UTF-8 JSON
- gzip payload не содержит `["chart"]["ephemeris"]["path"]` и `["source"]`
- round-trip: `decode_chart_artifact(encode_chart_artifact(artifact)) == artifact`
- decode возвращает новый экземпляр, не тот же объект
- повторный round-trip стабилен
- мутация возвращённого объекта не влияет на следующий decode

**Ошибки decode**

- пустой payload `b""` → `reason="gzip"`
- не gzip bytes → `reason="gzip"`
- обрезанный gzip → `reason="gzip"`
- gzip с испорченным deflate-телом → `reason="gzip"`
- не UTF-8 после распаковки → `reason="utf8"`
- валидный UTF-8, но не JSON → `reason="validation"`
- валидный JSON, не проходящий модель → `reason="validation"`

**Privacy**

Сценарий с реальным PII в payload:

1. взять валидный artifact JSON после gzip-decode — он содержит `datetime_utc`,
   `latitude`, `longitude` и тексты `warnings`
2. удалить обязательное поле, например `calculation_version`
3. снова gzip-compress с `compresslevel=6, mtime=0`
4. вызвать decode
5. проверить `ChartArtifactDecodeError(reason="validation")`
6. проверить, что `str(error)` не содержит datetime, latitude, longitude,
   дословных текстов предупреждений, исходного JSON и pydantic `ValidationError` details
7. проверить `error.__cause__ is None`

Для artifact fixture:

- построить минимальный валидный `NatalChart` вручную, без вызова `calculate_natal`
- не вызывать Swiss Ephemeris
- тесты codec/cache пометить `no_ephemeris_autoinit`

---

## 11. Границы модулей и архитектурный документ

Обновить `docs/architecture/service_ready_architecture.md`:

- заменить `calculation.*` в Shared contracts / I1 на явный список:
  `calculation.spec`, `calculation.keys`, `calculation.cache`
- отдельно записать, что `calculation.types` и `calculation.codec` относятся к
  artifact payload layer, а не к shared contract layer, пока result-модели движка
  не вынесены из engine

Обновить `tests/test_module_boundaries.py`:

1. В `CONTRACT_MODULES` заменить `"exact_orb.calculation"` на явный список:

```python
CONTRACT_MODULES = (
    "exact_orb.domain",
    "exact_orb.errors",
    "exact_orb.outcomes",
    "exact_orb.calculation.spec",
    "exact_orb.calculation.keys",
    "exact_orb.calculation.cache",
    "exact_orb.birth.types",
)
```

2. Добавить отдельную проверку package-level `__init__`: импорт пакета
   `exact_orb.calculation` в подпроцессе не должен загружать `swisseph`,
   `exact_orb.swiss_backend`, `exact_orb.engine`, `exact_orb.config`.

3. В комментарии к `CONTRACT_MODULES` записать цену изменения: `calculation`
   перестал быть пакетом с одним правилом, правило стало помодульным и требует
   поддерживаемого списка. `calculation.types` и `calculation.codec` выведены из
   контрактного слоя сознательно (§1).

---

## 12. Расхождения с документами — зафиксировать

Не исправлять молча:

1. `codec.py` отсутствует в пакетной структуре §2 `build_natal_components.md`.
   Предпочтительно обновить §2 документа и добавить строку `codec.py`.
2. `cache_timeout_ms` не реализован, хотя §8.4 перечисляет его в конфигурации
   первой версии. Основание — §3.2.1: таймаут реализуется внутри адаптера кэша,
   для in-memory он no-op.
3. `ttl_seconds: float | None` вместо `int | None` из §4.3 — ради fake clock.
4. `selena_method` влияет на положения, приходит из `[tool.exact_orb]` и не
   входит ни в `ChartSpec`, ни в перечень `CalculationVersion` (ADR-0017).
   Codec и `ChartArtifact` не подключать к реальному кэш-пути, пока это не закрыто.
5. §10 `chart_artifacts.md` требует, чтобы несовпадение `chart_kind` давало
   `ENGINE_UNEXPECTED`. Здесь механизм — pydantic `ValidationError`. Резолвер
   обязан её ловить и отображать в типизированный отказ; записать как ожидание
   для следующего промпта.

---

## 13. Приёмка

**Модели**

- `calculation/types.py`, `calculation/cache.py`, `calculation/codec.py` созданы
- `ArtifactEphemerisStatus` создан без `path` и без `source`, с property `using_files`
- `ArtifactNatalChart(NatalChart)` создан и переопределяет только `ephemeris`
- `ChartArtifact.chart` типизирован как `ArtifactNatalChart`
- `ChartArtifact` нормализует raw `NatalChart` через before-validator с защитой от повторной нормализации
- `ChartArtifact` frozen на верхнем уровне и проверяет key / version / chart_kind / warnings invariants
- drift-guard на состав полей `ArtifactEphemerisStatus` зелёный
- artifact payload не содержит absolute ephemeris path и ephemeris source

**Кэш**

- `CalculationCache` — async runtime-checkable Protocol с `get` / `put(key, payload: bytes)`
- `InMemoryCalculationCache` хранит opaque bytes, без дефолтов в конструкторе
- `InMemoryCalculationCache` runtime-проверяет `bytes` в `put`
- `InMemoryCalculationCache` использует `time.monotonic` по умолчанию
- `InMemoryCalculationCache` не содержит `cache_timeout_ms`
- LRU eviction предсказуемый, вытеснение в цикле, покрыто тестом
- TTL lazy expiration покрыт тестом с fake clock
- cache не импортирует `ChartArtifact`, `engine`, `config`, `swiss_backend`, `swisseph`
- реализация структурно удовлетворяет Protocol

**Codec**

- `model_dump_json -> UTF-8 -> gzip level 6, mtime=0`
- `gzip -> explicit UTF-8 -> ChartArtifact.model_validate_json`
- decode failures дают `ChartArtifactDecodeError` с typed `reason`
- ветка `validation` использует `raise ... from None`; `__cause__ is None` проверен тестом
- decode error text не содержит payload, PII, текстов предупреждений и `ValidationError` details
- codec не логирует, не делает fail-open, не вырезает `path/source`
- round-trip invariant — равенство моделей, не golden bytes
- тест на мутацию декодированного объекта зелёный

**Границы и документы**

- `docs/architecture/service_ready_architecture.md` обновлён: shared contracts больше не говорят `calculation.*`
- `docs/requirements/component_responsibilities/exact-orb_build_natal_components.md` §2 обновлён строкой `codec.py` или расхождение явно записано
- `tests/test_module_boundaries.py` обновлён, `CONTRACT_MODULES` перечисляет модули явно
- добавлена проверка чистоты package-level `exact_orb.calculation`
- `exact_orb.calculation.__init__` остаётся swisseph-free и не экспортирует `ChartArtifact` и codec
- расхождения §12 зафиксированы в коде или документах

**Прогон**

- релевантные тесты зелёные
- полный `pytest` зелёный
