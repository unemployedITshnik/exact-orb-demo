# Chart Artifacts Prompt 1: input and deterministic key

Задача: реализовать первый слой Chart Artifacts — swisseph-free доменные контракты, ChartSpec, CalculationInput и deterministic calculation_key.

Код менять только в рамках этой задачи. Не реализовывать кэш, кодек, ChartArtifactResolver, EngineService, single-flight, Redis, CalculationVersion и взаимодействие с Swiss Ephemeris.

Цель:
слой ключа должен импортироваться без swisseph, без native binding и без каталога .se1. В обычном pytest-процессе это проверять нельзя: tests/conftest.py импортирует exact_orb.config, а тот тянет swiss_backend. Поэтому import-boundary проверять только чистым subprocess. Unit-тесты этого слоя пометить существующим маркером no_ephemeris_autoinit.

## 1. Создать src/exact_orb/domain.py

Верхнеуровневый swisseph-free модуль без владельца слоя. Его импортируют calculation и engine.

Вынести туда:

- ChartKind = Literal["natal", "cosmogram"]
- IncludeBlock / INCLUDE_BLOCKS
- DEFAULT_INCLUDE_BY_CHART_KIND
- RulershipScheme с теми же значениями, что сейчас
- координатные диапазоны
- normalize_include(chart_kind, include)
- normalize_coordinate helpers
- normalize_house_system_code(house_system) -> str
- validate_geography(latitude, longitude)

Типы зафиксировать явно:

```python
INCLUDE_BLOCKS: frozenset[str]
DEFAULT_INCLUDE_BY_CHART_KIND: Mapping[ChartKind, tuple[str, ...]]
```

Значения:

```python
natal     -> ("aspects", "configurations", "houses", "positions", "rulers", "strength")
cosmogram -> ("aspects", "configurations", "positions")
```

INCLUDE_BLOCKS и DEFAULT_INCLUDE_BY_CHART_KIND не должны быть одним объектом.

Не переносить SelenaMethodName в этом промте.
Не переносить body ids, angle indices и любые значения, требующие Swiss numeric constants.

## 2. Создать src/exact_orb/calculation/

Файлы:

- __init__.py
- spec.py
- keys.py

Публичный API:

- NatalChartSpec
- ChartSpec
- CalculationInput
- canonical_key_payload
- calculation_input_from
- calculation_key

## 3. Реализовать NatalChartSpec

- model_config = ConfigDict(frozen=True)
- technique: Literal["natal"] = "natal"
- chart_kind: ChartKind
- include: tuple[IncludeBlock, ...]
- house_system: str = "P"
- rulership: RulershipScheme = RulershipScheme.COMBINED
- near_interception_threshold: float = 1.0

Не добавлять пока:

- selena_method
- orb_profile

ChartSpec = NatalChartSpec.

Не использовать Annotated/Field(discriminator="technique") в этом промте. Тегированный union вводится только вместе со вторым членом union. Сохранённые спеки останутся совместимы, потому что model_dump уже пишет technique.

## 4. Правила include

include не Optional в финальной модели. Подстановку дефолта сделать через model_validator(mode="before"), потому что дефолт зависит от chart_kind.

Порядок:

- из raw input прочитать chart_kind
- если include отсутствует или None, взять DEFAULT_INCLUDE_BY_CHART_KIND[chart_kind]
- убрать дубли
- проверить неизвестные блоки
- отсортировать
- сохранить tuple
- выполнить проверки совместимости

Правила:

- natal безусловно требует houses
- cosmogram запрещает houses, rulers, strength
- rulers требует houses
- strength требует houses

## 5. house_system

В ChartSpec хранить только str.

normalize_house_system_code:

- принимает str или bytes, если это нужно для совместимости существующего кода
- результат всегда str
- требует ровно один ASCII-символ
- нормализует к uppercase
- whitelist не вводить: текущий код whitelist не имеет, Swiss Ephemeris знает больше систем, чем мы хотим поддерживать вручную

Отдельная bytes-конверсия для вызова движка остаётся на промт взаимодействия с EngineService. Не менять normalize_house_system так, чтобы сломать calculate_houses.

## 6. CalculationInput

- model_config = ConfigDict(frozen=True)
- utc_datetime: datetime
- latitude: float
- longitude: float

utc_datetime:

- naive отвергнуть
- aware, но offset != +00:00, отвергнуть
- не конвертировать
- микросекунды отбросить усечением replace(microsecond=0)
- формат в ключе зафиксировать явным field_serializer, не полагаться на pydantic default
- сериализовать как "YYYY-MM-DDTHH:MM:SSZ"

coordinates:

- NaN/inf/-inf отвергнуть
- latitude в [-90, 90]
- longitude вне [-180, 180] отвергнуть
- 180.0 канонизировать в -180.0
- не делать modulo-нормализацию: 200.0 должен быть ошибкой, а не -160.0
- порядок: finite check -> range check -> Decimal quantize -> boundary normalization -> повторная range check -> -0.0 normalization
- -0.0 нормализовать в 0.0 после квантования и boundary-normalization
- хранить float, не Decimal

Квантование:

- Decimal(str(value)).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
- результат модели — float

## 7. calculation_input_from(resolved)

Разрешён импорт exact_orb.birth.types.ResolvedBirthData. Пакет birth проверен как swisseph-free.

Брать только:

- utc_datetime
- latitude
- longitude

Не брать:

- tz_id
- utc_offset_seconds
- canonical_place
- time_unknown
- warnings

place_id и исходной строки места в ResolvedBirthData нет, не ссылаться на них.

## 8. canonical_key_payload

canonical_key_payload(calc_input, spec, version) -> dict[str, object]

Payload:

```python
{
    "schema_version": "v1",
    "calculation_input": calc_input.model_dump(mode="json"),
    "spec": spec.model_dump(mode="json"),
    "calculation_version": version,
}
```

Проверить тестом, что spec.technique попадает в payload.

## 9. calculation_key

Формат строго:

```text
eo:calc:v1:<sha256-hex>
```

JSON:

- json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
- encode UTF-8
- sha256
- lowercase hex длиной 64

schema_version намеренно дублируется в prefix и внутри hashed payload.

version — opaque string. compute_calculation_version не реализовывать.

## 10. Запреты

В domain.py, spec.py, keys.py запрещены:

- swisseph
- exact_orb.swiss_backend
- exact_orb.engine
- exact_orb.config
- кэш
- runtime settings/env
- чтение файлов
- обращение к часам
- логирование

Разрешён импорт exact_orb.birth.types.ResolvedBirthData в keys.py.

## 11. Обновить существующие импорты

Engine-модули должны импортировать перенесённые swisseph-free значения из exact_orb.domain.

Проверяемое сохранение поведения:

- natal default include посимвольно тот же набор, что был раньше
- RulershipScheme содержит те же значения
- существующие include-gating тесты продолжают проходить с обновлённым контрактом cosmogram default

## 12. Тесты

Все новые обычные pytest-тесты этого слоя пометить no_ephemeris_autoinit.

Добавить unit-тесты на новые случаи:

- ChartSpec валидируется без явной передачи technique
- model_dump содержит technique
- include=None для natal даёт natal-default
- include=None для cosmogram даёт cosmogram-default
- house_system "p" и "P" дают одинаковый canonical value и одинаковый key
- longitude 200.0 отвергается
- 180.0 и -180.0 дают одинаковую canonical longitude
- -0.0 нормализуется и на уровне модели, и на уровне calculation_key
- datetime в payload строго "YYYY-MM-DDTHH:MM:SSZ"
- technique входит в canonical_key_payload
- PYTHONHASHSEED=1 и PYTHONHASHSEED=2 в двух subprocess дают один ключ

Вернуть и сохранить полный контрактный набор тестов:

- projection invariant: изменение canonical_place, tz_id, utc_offset_seconds, time_unknown, warnings не меняет key
- одинаковые смысловые входы дают один key
- изменение utc_datetime меняет key
- изменение latitude меняет key
- изменение longitude меняет key
- изменение chart_kind меняет key
- изменение include меняет key
- изменение house_system меняет key
- изменение rulership меняет key
- изменение near_interception_threshold меняет key
- изменение version меняет key
- перестановка include не меняет key
- prefix ровно eo:calc:v1:
- hash-tail lowercase hex длиной 64
- naive datetime отвергается
- aware datetime с ненулевым offset отвергается
- микросекунды усекаются
- NaN/inf/-inf отвергаются
- latitude вне [-90, 90] отвергается
- неизвестный include отвергается
- natal без houses отвергается
- cosmogram с houses отвергается
- cosmogram с rulers отвергается
- cosmogram со strength отвергается
- rulers без houses отвергается
- strength без houses отвергается
- модели frozen

## 13. Golden test

Конкретный CalculationInput + NatalChartSpec + version = "test-version-1"
-> зафиксированные две константы:

- expected canonical JSON string
- expected calculation key eo:calc:v1:<sha256-hex>

Рядом с константами комментарий:
менять golden-key можно только при осознанном изменении схемы ключа,
bump schema_version или записи в ADR. Нельзя переписывать константу просто
потому, что поменялась сериализация.

## 14. Import-boundary tests

Только subprocess.

Проверить:

```bash
python -c "import exact_orb.domain, exact_orb.calculation.keys, exact_orb.calculation.spec, exact_orb.calculation, sys; assert 'swisseph' not in sys.modules; assert 'exact_orb.swiss_backend' not in sys.modules; assert 'exact_orb.engine' not in sys.modules; assert 'exact_orb.config' not in sys.modules"
```

## 15. Приёмка

- prompt v5 сохранён в prompts/2026-08-30
- src/exact_orb/domain.py создан
- src/exact_orb/calculation/ создан как пакет
- ChartSpec = NatalChartSpec
- ChartSpec валидируется без явной передачи technique
- cosmogram строится с include=None
- DEFAULT_INCLUDE_BY_CHART_KIND разделяет natal и cosmogram
- INCLUDE_BLOCKS остаётся frozenset[str]
- house_system canonical str uppercase
- longitude вне [-180, 180] отвергается, modulo нет
- datetime serialization зафиксирована field_serializer
- calculation_key возвращает eo:calc:v1:<sha256-hex>
- golden-тест есть и содержит правило изменения констант
- golden-тест проверяет canonical JSON и key
- PYTHONHASHSEED проверен subprocess
- import-boundary проверен subprocess
- все новые pytest-тесты помечены no_ephemeris_autoinit
- тесты ключа проходят без каталога ephe
- новые модули не импортируют swisseph/swiss_backend/engine/config
- существующее поведение движка не изменено численно, кроме согласованного cosmogram include=None
- существующие тесты проекта зелёные
