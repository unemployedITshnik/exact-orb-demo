# Prompt 5: Coverage audit расчётного блока calculation / chart artifacts

Задача: сверить существующее покрытие блока `calculation/` с требованиями и
закрыть **только** реальные пробелы. Основной ожидаемый результат — не новые
unit-тесты (они почти все уже есть), а **сквозной интеграционный тест всего
пути `ChartArtifactResolver → EngineService → NatalTechniqueAdapter` с
подменённым калькулятором**, которого сейчас нет.

Production-код менять только если тест выявил реальный дефект; дефект и правку
описать отдельно. Существующие тесты не переписывать.

---

## 1. Область

Читать и покрывать:

- `src/exact_orb/calculation/__init__.py`
- `src/exact_orb/calculation/spec.py`
- `src/exact_orb/calculation/keys.py`
- `src/exact_orb/calculation/cache.py`
- `src/exact_orb/calculation/types.py`
- `src/exact_orb/calculation/codec.py`
- `src/exact_orb/calculation/errors.py`
- `src/exact_orb/calculation/engine.py`
- `src/exact_orb/calculation/artifacts.py`

Только как вход, не покрывать отдельно: `src/exact_orb/run_context.py`,
`src/exact_orb/birth/types.py`, `src/exact_orb/domain.py`.

Требования, по которым сверяться (полные пути):

- `docs/requirements/component_responsibilities/exact-orb_chart_artifacts.md`
- `docs/requirements/component_responsibilities/exact-orb_build_natal_components.md` §4
- `docs/requirements/component_responsibilities/exact-orb_calculation_requirements.md` §8 (ГРН-1 … ГРН-8), §9 (ДЕТ)
- `docs/requirements/decisions/0017-calculation-cache-and-chartspec.md`
- `prompts/2026-08-30/01-chart-artifacts-input-and-key.md`
- `prompts/2026-08-31/02-chart-artifacts-cache-and-codec.md`
- `prompts/2026-08-31/03-chart-artifacts-engine-boundary.md`
- `prompts/2026-08-31/04-chart-artifact-resolver.md`

Не реализовывать:

- Redis, distributed lock, negative cache, derived charts
- Tool/API handlers, `BuildNatalHandler`, outcome mapper
- `CalculationVersion`, новые техники
- изменение схемы ключа, `SCHEMA_VERSION`, формата артефакта
- известный и уже зафиксированный долг: неполнота `ChartSpec` относительно
  параметров `calculate_natal` (`selena_method`, `body_ids`, флаги, конфиги
  орбисов/фигур/силы) — см. ГРН-7. Тесты, закрепляющие текущее поведение как
  правильное, не писать.

---

## 2. Сначала — аудит существующего

Прочитать целиком:

- `tests/test_calculation_keys.py`
- `tests/test_calculation_cache.py`
- `tests/test_chart_artifact_codec.py`
- `tests/test_calculation_engine.py`
- `tests/test_calculation_engine_integration.py`
- `tests/test_chart_artifact_resolver.py`
- `tests/test_module_boundaries.py`

Аудит — это не самостоятельный прогон `--cov` (`pytest-cov` в зависимостях
нет, ставить его не нужно). Аудит **требованческий**: таблица
«требование → существующий тест → вердикт». Формат:

```text
| Требование / сценарий | Тест | Вердикт |
|---|---|---|
| key не зависит от canonical_place | test_calculation_keys.py::test_projection_ignores_non_key_birth_resolution_fields | covered |
| ...                   | —    | GAP-01  |
```

Вердикты: `covered`, `partial`, `GAP-NN`. Каждый добавленный тест обязан
ссылаться на конкретный `GAP-NN`. Тест без `GAP-NN` — дубль, его не добавлять.

Ожидание по результату аудита: подавляющее большинство пунктов ниже уже
`covered`. Если по разделу не нашлось ни одного пробела — так и написать, а не
добавлять «на всякий случай» ещё один тест.

---

## 3. Чек-лист unit-покрытия (сверить, не переписывать)

### keys / spec

`calculation_input_from` берёт только `utc_datetime`, `latitude`, `longitude`;
key не меняется при смене `canonical_place`, `tz_id`, `utc_offset_seconds`,
`time_unknown`, `warnings`; key стабилен при перестановке `include`; key
меняется при смене `utc_datetime`, `latitude`, `longitude`, `chart_kind`,
`include`, `house_system`, `rulership`, `near_interception_threshold`,
`version`; префикс строго `eo:calc:v1:`; хвост — 64 символа lowercase hex;
canonical payload содержит `technique`; golden key и golden canonical JSON
зафиксированы; ключ не зависит от `PYTHONHASHSEED`.

Дополнительно проверить, что покрыто: обнуление микросекунд; отказ на
не-UTC и наивном `datetime`; квантование координат до 1e-6 `ROUND_HALF_UP`;
нормализация `-0.0 → 0.0` и `+180 → −180`; отказ на `inf`/`nan`;
`CalculationInput` и `NatalChartSpec` frozen; `house_system` «p» и «P» дают
один ключ.

### cache

miss на пустом; put/get байтов; замена по тому же ключу; истечение TTL;
вытеснение по `max_entries`; порядок LRU и обновление свежести при `get`;
повторный put не растит размер; хранение только `bytes` (иначе `TypeError`);
часы по умолчанию — `time.monotonic`; `clear()` и `__len__`; отказ конструктора
на `max_entries <= 0` и `ttl_seconds <= 0`; протокол `runtime_checkable`;
модуль не импортирует `ChartArtifact` и кодек.

### types / codec

`ChartArtifact` frozen; сырой `NatalChart` нормализуется в
`ArtifactNatalChart`; `EphemerisStatus` принимается как объект (через
`model_dump`); `ephemeris.path` и `ephemeris.source` отсутствуют в payload;
инвариант совпадения `chart_kind` спеки/артефакта/карты; инвариант совпадения
`warnings` верхнего уровня и карты; валидатор префикса `calculation_key`;
encode возвращает bytes; gzip детерминирован (`mtime=0`), одинаковый артефакт →
одинаковые байты; round-trip; пустые байты и не-gzip → `reason="gzip"`;
битый deflate и обрезанный gzip → `"gzip"`; не-UTF-8 → `"utf8"`; валидный
UTF-8, но не артефакт → `"validation"`; текст ошибки не раскрывает payload и
детали pydantic.

### engine

`TechniqueAdapter` синхронный и `runtime_checkable`, `CalculationEnginePort` —
нет; валидация реестра (пустой, без `natal`, неизвестная техника, несовпадение
ключа и `adapter.technique`, async-адаптер, неположительный/нефинитный
`slow_threshold_ms`); адаптер выбирается по `spec.technique`; в адаптер не
передаются `run`, ключ, версия, кэш; превалидация выполняется до executor
(executor не трогается вообще); расчёт уходит в executor, event loop не
блокируется; проверка инварианта результата; коды отказов:

| Источник | Тип | Код |
|---|---|---|
| плохая спека | `ChartCalculationError` | `SPEC_INVALID` |
| плохая география | `ChartCalculationError` | `GEOGRAPHY_INVALID` |
| `ValueError` про купола домов | `ChartCalculationError` | `HOUSES_DEGENERATE` |
| `EphemerisNotInitializedError` / `EphemerisPathMismatchError` | `CalculationUnavailableError` | `EPHEMERIS_UNAVAILABLE` |
| `EphemerisSessionRequiredError` | `ChartCalculationError` | `ENGINE_UNEXPECTED` |
| прочее | `ChartCalculationError` | `ENGINE_UNEXPECTED` |

`run_id` проброшен в объект ошибки строкой; `__cause__` не утекает; логи не
содержат даты, координат, места, `warning.message`, traceback.

### resolver

Попадание возвращает декодированный артефакт, не зовёт движок и не пишет в
кэш; промах зовёт движок один раз и кладёт байты; расхождение ключа, версии
или спеки → `cache_stale` и пересчёт; битый payload → `cache_corrupt` и
пересчёт; исключение `cache.get` — fail-open промах; исключение `cache.put` —
артефакт всё равно возвращается; отказ `encode` — артефакт возвращается без
put; ошибка расчёта пробрасывается наружу и не кэшируется, следующий вызов
повторяет расчёт; `ValidationError` при сборке артефакта → `ENGINE_UNEXPECTED`;
single-flight зовёт движок один раз на ключ; отмена ведомого и отмена лидера не
отменяют общую задачу; отмена всех ожидающих не мешает задаче досчитать и
записать результат; упавший single-flight очищает `_inflight`; разные ключи не
сериализуются; исключения задачи дренируются без шума loop; логи `cache_degraded`
дросселируются по операции, `cache_recovered` — по операции; счётчики `hits`,
`misses`, `stale`, `corrupt`, `put_ok`, `put_failed`, `cache_errors_total` и
свойство `hit_ratio`.

Явно закрепить семантику, чтобы её случайно не «починили» в production:

- два конкурентных вызова по одному ключу дают `misses == 2` при одном вызове
  движка — счётчик считает промахи, а не расчёты;
- отказ `encode` растит только `put_failed`; отказ `cache.put` растит
  `put_failed` **и** `cache_errors_total`.

---

## 4. Главный пробел: интеграция всего блока без Swiss Ephemeris

Сейчас `tests/test_calculation_engine_integration.py` идёт по **реальному**
`calculate_natal` и реальным файлам эфемерид и не касается ни резолвера, ни
кэша. Его не трогать: это оставшийся smoke реального расчёта.

Добавить новый файл `tests/test_calculation_block_integration.py` — весь путь
на реальных компонентах:

- `ChartArtifactResolver`
- `InMemoryCalculationCache`
- `EngineService`
- `NatalTechniqueAdapter(calculator=fake_calculator)`

`EngineService` создавать внутри:

```python
with ThreadPoolExecutor(max_workers=2) as executor:
    ...
```

чтобы тесты не оставляли живые worker threads.

Подменяется **только листовой калькулятор**: `NatalTechniqueAdapter` принимает
`calculator` конструктором. `fake_calculator` возвращает заранее собранный
`NatalChart` с вручную собранным `EphemerisStatus`, принимает `*args, **kwargs`
и считает количество вызовов.

Ограничение формулируется точно: **никаких вызовов `swe.*`, никакого
`ephemeris_session()`, `configure_ephemeris()` и файлов эфемерид.** Запретить
*импорт* `swisseph` нельзя и не нужно: `calculation.engine` тянет
`engine.charts.natal`, тот — `exact_orb.swiss_backend`, и это штатная граница
(единственная точка импорта, проверяется `test_module_boundaries.py`).

Все тесты файла помечаются:

```python
pytestmark = pytest.mark.no_ephemeris_autoinit
```

Последствие маркера: `_STATE is None`, поэтому любой вызов
`validate_ephemeris_path` упадёт `EphemerisNotInitializedError`. Значит,
`NatalChart` строится напрямую из моделей, а не через расчётный путь.

Вспомогательные фабрики `_raw_chart`, `_artifact`, `_resolved`, `_spec`,
`_run` уже трижды продублированы в `test_calculation_engine.py`,
`test_chart_artifact_codec.py`, `test_chart_artifact_resolver.py`. Четвёртую
копию не плодить: вынести общую фабрику в `tests/fixtures/calculation.py`
(без импорта `tests.fixtures.natal_1985`, который тянет `swisseph`) и
переиспользовать в новом файле. Переписывать существующие файлы под неё в этом
промте не обязательно; если делаешь — отдельным шагом и без изменения
утверждений.

### Сценарии

1. Промах: `resolver → EngineService → NatalTechniqueAdapter(fake) →
   ChartArtifact`, в кэше лежат закодированные байты, декодируемые обратно в
   равный артефакт.
2. Второй такой же запрос — попадание, `fake_calculator` больше не вызывается.
3. Заранее положенный валидный артефакт — попадание без единого вызова
   `fake_calculator`.
4. Заранее положенный артефакт с чужой версией — `cache_stale`, пересчёт,
   байты в кэше заменены. Payload положить **под key текущих**
   `spec/resolved/version`; чужая версия находится внутри payload.
5. Заранее положенные битые байты — `cache_corrupt`, пересчёт, байты заменены.
   Payload положить **под key текущих** `spec/resolved/version`, иначе это будет
   обычный miss, а не corrupt.
6. `fake_calculator` бросает generic `ValueError` с текстом, не похожим на
   house-cusps/Placidus error → наружу `ChartCalculationError` с
   `ENGINE_UNEXPECTED`, в кэш ничего не записано, следующий вызов снова зовёт
   калькулятор.
7. `fake_calculator` бросает `EphemerisNotInitializedError` → наружу
   `CalculationUnavailableError` с `EPHEMERIS_UNAVAILABLE`, в кэш ничего не
   записано. (`EphemerisSessionRequiredError` здесь не подходит: он маппится в
   `ENGINE_UNEXPECTED`.)
8. Корреляция: при `caplog.set_level(logging.DEBUG, logger="exact_orb.calculation")`
   события резолвера и движка в одном прогоне несут один и тот же `run_id`.
9. Privacy: в логах **присутствует** короткий 12-символьный префикс ключа и
   `run_id`, и **отсутствуют** полный ключ, дата из фикстуры, обе координаты
   строкой, `canonical_place`, `warning.message`, `Traceback`. Негативные
   утверждения без позитивных не принимаются: тест обязан доказать, что логи
   вообще писались.

---

## 5. Границы модулей

Проверить, что уже утверждается в `tests/test_module_boundaries.py`, и
дописать только недостающее:

- пакетный импорт `exact_orb.calculation` остаётся без `swisseph`,
  `exact_orb.swiss_backend`, `exact_orb.engine`, `exact_orb.config`;
- контрактные модули не импортируют `swisseph`, `engine`, `config`, `tools`,
  `llm`, `orchestration`;
- `calculation.engine` не импортирует артефакты/кэш/edge-слои;
- `calculation.artifacts` не импортирует tool/UI/LLM/application;
- `calculation.cache` не импортирует `ChartArtifact` и кодек.

Новый `tests/fixtures/calculation.py` не должен ломать первое утверждение:
фикстуры тянут модели карты, поэтому импортировать их только из тестов, а не
из `tests/fixtures/__init__.py`.

---

## 6. Стиль тестов

- Стиль существующих тестов; `asyncio_mode = "auto"` уже включён — не
  добавлять `@pytest.mark.asyncio`.
- Никаких `time.sleep` и реального ожидания: фейковые часы и `asyncio.Event`,
  как в `FakeClock` / `_wait_until` из `test_chart_artifact_resolver.py`.
- `ChartArtifactResolver` требует `degraded_log_interval_s` и принимает
  `clock` — использовать инъекцию, а не подкручивание времени процесса.
- Утверждения о поведении, а не о приватных полях. Приватные допускаются
  только для инвариантов жизненного цикла вроде `_inflight == {}`.
- Не дублировать существующее покрытие.

---

## 7. Отчёт

В ответе:

1. таблица аудита «требование → тест → вердикт» по всем файлам области;
2. список `GAP-NN` с обоснованием, почему это пробел, а не дубль;
3. список добавленных тестов в формате `file::test_name → GAP-NN`;
4. менялся ли production-код, что именно и какой тест это потребовал;
5. точные команды pytest, которые прогонялись, и их результат.

---

## 8. Приёмка

- Аудит проведён по требованиям, не по интуиции; каждый вердикт сослан на
  конкретный тест.
- Новых дублей нет: каждый добавленный тест закрывает `GAP-NN`.
- Появился интеграционный тест всего расчётного пути с подменённым
  калькулятором, все 9 сценариев.
- Новые тесты не вызывают `swe.*`, `ephemeris_session()` и не читают файлы
  эфемерид; помечены `no_ephemeris_autoinit`.
- Тестов на handler/API outcome path не добавлено:
  `ChartCalculationError → CalculationFailed` и
  `CalculationUnavailableError → ResolutionUnavailable` тестируются позже, на
  границе handler/API, когда появится `BuildNatalHandler`.
- Полный `pytest` зелёный; новые тесты не используют `sleep` и не ждут реальное
  время, targeted file проходит быстро.
