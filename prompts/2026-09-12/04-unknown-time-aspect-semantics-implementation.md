# Реализация устойчивых аспектов космограммы при неизвестном времени

Работай только в ветке `fix/aspect-model-semantics`. Перед изменениями
убедись, что это текущая ветка; если нет — остановись.

Не создавай новую ветку, коммит, push или PR. Сохрани все пользовательские и
несвязанные изменения рабочего дерева.

`prompts/**` — исторический журнал. Не изменяй этот или другие сохранённые
промты.

## Исходное состояние

Предыдущие этапы уже выполнены:

- `229be82` и ADR-0029 ввели канонические идентификаторы точек;
- `3cca8eb` и ADR-0030 сделали `true_node` единственным расчётным
  представителем оси лунных узлов;
- `71df64c` и ADR-0031 закрепили производность materialized-рёбер
  конфигураций от `NatalChart.aspects`;
- `baff28a` и ADR-0032 приняли точный контракт неизвестного времени без
  реализации.

Сейчас `BirthDataResolver` при отсутствующем времени создаёт один технический
полуденный `utc_datetime`, а движок рассчитывает по нему обычные аспекты и
конфигурации. В результате космограмма выдаёт полуденные отношения за факты
неизвестного момента рождения.

ADR-0032 уже принял решение:

- отсутствие `BirthInput.birth_time` означает `cosmogram`;
- любое явно указанное допустимое время, включая `12:00`, означает `natal`;
- домен космограммы — все валидные минуты локальной календарной даты,
  отображённые в UTC;
- публикуются только аспекты, существующие на каждой допустимой минуте с
  неизменными `aspect_type` и `category`;
- опубликованный `Aspect.orb` космограммы — максимальный орбис на домене;
- исключённые кандидаты описываются отдельным типизированным блоком
  `NatalChart.time_uncertainty`;
- конфигурации строятся только из опубликованных устойчивых аспектов.

## Цель

Полностью реализовать ADR-0032 в birth, calculation, engine, artifact/cache,
session и Build Natal путях, закрепить изменение regression-тестами и
синхронизировать актуальную документацию и диаграммы с фактическим кодом.

После изменения:

1. `ResolvedBirthData` однозначно переносит допустимый UTC-домен только для
   неизвестного времени.
2. Расчётный ключ различает домены неизвестного времени.
3. Ни одна новая космограмма не может существовать без применённой проверки
   устойчивости.
4. `NatalChart.aspects` космограммы содержит только доказанные отношения, а
   `configurations` — только производные от них фигуры.
5. Натал с известным временем сохраняет прежние числовые результаты и
   моментальную семантику аспектов.
6. SQLite пишет state payload v2 и продолжает читать frozen payload v1 через
   явную миграцию.
7. Старые cache entries недостижимы по новому ключу, а структурно старый
   cosmogram artifact отклоняется как corrupt.

Это implementation-этап. Не выполняй финальное закрытие finding `002`: после
него останется отдельная сквозная проверка полного DEBUG-payload и ручная
приёмка результата.

## Перед изменениями

1. Выполни:

   ```text
   git branch --show-current
   git status --short
   git log -6 --oneline
   ```

2. Убедись, что текущая ветка — `fix/aspect-model-semantics`, а в истории
   присутствуют `229be82`, `3cca8eb`, `71df64c`, `6dcb63b` и `baff28a`.

3. Зафиксируй исходные результаты ближайших тестов:

   ```text
   pytest tests/test_birth_tz.py tests/test_birth_resolver.py -q
   pytest tests/test_aspects.py tests/test_configurations.py tests/test_natal_include_gating.py -q
   pytest tests/test_calculation_keys.py tests/test_calculation_version.py tests/test_chart_artifact_codec.py -q
   pytest tests/test_calculation_engine.py tests/test_calculation_engine_integration.py tests/test_calculation_block_integration.py -q
   pytest tests/session/test_state.py tests/session/test_sqlite.py -q
   pytest tests/application/test_build_natal_handler.py tests/application/test_build_natal_integration.py -q
   pytest tests/research/test_projection.py tests/research/test_models.py tests/test_tools_natal.py tests/test_module_boundaries.py -q
   ```

   Если baseline уже красный, зафиксируй точные падения и не исправляй
   несвязанные дефекты под видом этой работы.

4. Изучи действующие источники истины:

   - `AGENTS.md`;
   - ADR-0008, ADR-0017, ADR-0024, ADR-0029, ADR-0030, ADR-0031 и ADR-0032;
   - `docs/requirements/decisions/README.md`;
   - `docs/development_approach/problems_detected_by_human/002_full-build-natal-log-exposed-aspect-model-defects.md`;
   - актуальные responsibilities birth, calculation, chart artifacts,
     Build Natal и session;
   - `docs/requirements/handlers/exact_orb_build_natal_handler_requirements.md`;
   - актуальные class/component diagrams и sequence diagrams Build Natal;
   - `src/exact_orb/birth/types.py`, `tz.py`, `resolver.py` и `__init__.py`;
   - `src/exact_orb/calculation/keys.py`, `chart_contract.py`, `engine.py`,
     `artifacts.py`, `types.py`, `codec.py` и `version.py`;
   - `src/exact_orb/engine/charts/natal.py`, aspect/configuration finder и
     типы;
   - `src/exact_orb/session/state.py`, `persistence.py`, SQLite adapter и
     frozen payload v1;
   - `src/exact_orb/application/handlers/build_natal.py`;
   - `src/exact_orb/tools/natal_tool.py` и прямые вызовы
     `calculate_natal()`;
   - ближайшие fixtures, golden и regression-тесты.

5. Через `rg` найди все текущие употребления:

   ```text
   time_unknown
   ResolvedBirthData
   CalculationInput
   calculation_input_from_chart
   calculate_natal(
   chart_kind="cosmogram"
   eo:calc:v1:
   SCHEMA_VERSION
   ENGINE_VERSION
   CALCULATION_VERSION_SCHEMA
   _STATE_PAYLOAD_VERSION
   _DIALOG_PAYLOAD_VERSION
   payload_version
   feature_schema_version
   ```

Не выполняй глобальную замену версий: исторические ADR, старые промты и
frozen payload v1 должны остаться исторически правдивыми.

## 1. Канонический домен допустимых минут

Добавь в contract-only birth-слой неизменяемые типы:

```text
UtcMinuteRange {
    first_utc: datetime
    count: int
}

BirthTimeDomain {
    ranges: tuple[UtcMinuteRange, ...]
}
```

Размести их рядом с birth-контрактами и экспортируй через
`exact_orb.birth`. Они не должны импортировать engine, calculation, session,
Swiss Ephemeris или внешние сервисы.

### `UtcMinuteRange`

- `first_utc` — timezone-aware UTC;
- microseconds равны нулю;
- seconds могут быть ненулевыми: исторические IANA-смещения не всегда кратны
  минуте;
- `count` — настоящий положительный `int`, `bool` недопустим;
- диапазон раскрывается как `first_utc + n * 60 секунд`,
  `0 <= n < count`;
- переполнение `datetime` должно отклоняться валидатором.

### `BirthTimeDomain`

- `ranges` непуст;
- диапазоны строго отсортированы по `first_utc`;
- раскрытые моменты уникальны и не пересекаются;
- два диапазона, между которыми следующий момент равен предыдущему плюс
  60 секунд, неканоничны и должны быть слиты;
- объект предоставляет один детерминированный способ итерировать все UTC-
  моменты без материализации timezone policy;
- сериализация сохраняет tuple-порядок и точные секунды.

Добавь один общий helper `birth_time_domain_digest()`. Не дублируй
канонизацию в resolver, key и chart contract.

Digest вычисляется как lowercase SHA-256 UTF-8 JSON следующей семантической
формы:

```json
{
  "format": "utc-minute-domain-v1",
  "ranges": [
    {"first_utc": "YYYY-MM-DDTHH:MM:SSZ", "count": 1}
  ]
}
```

JSON сортирует ключи, использует separators `(',', ':')`, не содержит
microseconds, локальных дат, `tz_id` или отображаемых сообщений.

## 2. Построение домена в birth/timezone-слое

Добавь один публичный birth-owned helper построения домена по локальной дате
и IANA-зоне. Точное имя согласуй с существующим стилем, но не создавай второй
алгоритм в resolver, session или engine.

Алгоритм должен быть исчерпывающим, а не выборочным:

1. Перебрать ровно 1440 поддерживаемых локальных значений `00:00` … `23:59`
   с `second=microsecond=0`.
2. Для каждой минуты применить ту же IANA policy, что
   `resolve_historical_tz()`.
3. `TzOk` даёт один UTC-момент.
4. `TzNonexistent` не даёт ни одного момента; не нормализуй пропущенную
   пользовательскую минуту вперёд.
5. `TzAmbiguous` даёт оба fold как два разных UTC-момента.
6. Итоговые UTC-моменты дедуплицировать, отсортировать и без потерь сжать в
   канонические `UtcMinuteRange`.
7. Пустой результат означает полностью несуществующую локальную дату и
   должен позволить resolver вернуть существующий
   `InputRequired { birth.date, INVALID }`.

Не вычисляй домен как `00:00Z…24:00Z`, `utc_datetime ± 12 часов` или только
по двум границам суток. Не вызывай сеть и не добавляй timezone dependency:
используй stdlib `zoneinfo` и текущую базу окружения.

Обязательные свойства:

- обычные сутки дают 1440 UTC-моментов;
- весенний gap даёт 1380;
- осенний fold даёт 1500;
- обе реализации повторённой локальной минуты присутствуют;
- ни одна пропущенная минута не присутствует;
- переход со смещением, не кратным минуте, сохраняется разбиением ranges, а
  не округлением секунд;
- `Pacific/Apia` 2011-12-30 и другие уже покрытые полностью пропущенные даты
  остаются invalid date.

Сохрани существующую отдельную policy технического полудня:
`resolve_anomaly()` по-прежнему используется только для системного anchor.
Она не должна добавлять нормализованный момент за каждую пропущенную минуту
в домен.

## 3. `ResolvedBirthData` и resolver

Добавь в `ResolvedBirthData` обязательное поле без неявного default:

```text
birth_time_domain: BirthTimeDomain | None
```

Инварианты модели:

- `time_unknown=false` требует `birth_time_domain=None`;
- `time_unknown=true` требует непустой `BirthTimeDomain`;
- при неизвестном времени `utc_datetime` технического anchor обязан входить
  в раскрытый домен;
- known-time объект старой формы без явного `birth_time_domain=None` не
  считается новым валидным payload;
- произвольный старый unknown-time объект без домена также не принимается
  обычным `ResolvedBirthData.model_validate`; совместимость принадлежит
  versioned session migration.

Измени `BirthDataResolver`:

- при явно указанном времени передавай `birth_time_domain=None`;
- при отсутствующем времени строй домен после проверки даты и timezone;
- сохраняй прежний технический полуденный anchor и прежние warnings о noon
  anomaly;
- если дата не даёт ни одного допустимого момента, возвращай существующий
  `birth.date + INVALID`;
- неизвестная IANA-зона по-прежнему даёт `ResolutionUnavailable` с текущим
  кодом;
- не меняй координаты, каталог мест, поддерживаемый диапазон дат и правила
  explicit ambiguous/nonexistent time.

Убедись тестом, что отсутствующее время и явно введённые `12:00` могут иметь
одинаковый `utc_datetime`, но первое несёт домен и ведёт к `cosmogram`, а
второе несёт `None` и ведёт к `natal`.

## 4. Расчётный ключ v2

Измени `CalculationInput`:

```text
birth_time_domain_digest: str | None
```

Поле обязательно у прямого конструктора:

- для known-time/natal — явный `None`;
- для unknown-time/cosmogram — ровно 64 lowercase hex-символа SHA-256
  канонического `BirthTimeDomain`;
- digest строится только общим `birth_time_domain_digest()`.

`calculation_input_from(resolved)` обязан проецировать digest из resolved
domain. `calculation_input_from_chart(chart)` обязан получать тот же digest
из `chart.time_uncertainty.domain`; не добавляй домен в `ChartSpec`.

Увеличь:

```text
calculation/keys.py::SCHEMA_VERSION  "v1" → "v2"
KEY_PREFIX                            eo:calc:v1: → eo:calc:v2:
```

`canonical_key_payload()` / `calculation_key()` должны отклонять
несогласованность `spec.chart_kind` и digest:

- `natal` не допускает digest;
- `cosmogram` требует digest.

Обнови golden canonical JSON и key только после структурных assertions.
Обязательные доказательства:

- одинаковый домен независимо от порядка исходного fold-разрешения даёт один
  digest и ключ;
- изменение хотя бы одного range/count меняет digest и ключ;
- display-only `tz_id`, `canonical_place`, offset и warnings по-прежнему не
  меняют ключ при неизменном resolved domain;
- весь namespace, включая natal, использует `eo:calc:v2:`;
- `_short_key()` продолжает показывать первые 12 символов hash-части, а не
  создаёт второй ключ.

## 5. Типизированная неопределённость результата

Добавь закрытые неизменяемые модели, не используя `dict[str, Any]`:

```text
UnstableAspectReason =
    not_present_for_all_times
    | aspect_type_changed
    | category_changed

UnstableAspect {
    from_point: AspectPointRef
    to_point: AspectPointRef
    reasons: tuple[UnstableAspectReason, ...]
    includes_no_aspect: bool
    possible_aspect_types: tuple[AspectType, ...]
    possible_categories: tuple[AspectCategory, ...]
}

CosmogramTimeUncertainty {
    domain: BirthTimeDomain
    excluded_aspects: tuple[UnstableAspect, ...] | None
}
```

Предпочтительно держать chart-specific модели в соседнем модуле
`engine/charts`, а не добавлять timezone policy в универсальный aspect
finder. Экспортируй только действительно публичные типы.

Инварианты `UnstableAspect`:

- концы различны и ориентированы тем же стабильным порядком, что обычная
  пара аспектной сетки;
- причины непусты, уникальны и канонически отсортированы;
- `not_present_for_all_times` присутствует тогда и только тогда, когда
  `includes_no_aspect=true`;
- `aspect_type_changed` присутствует тогда и только тогда, когда возможно
  более одного фактического `AspectType`;
- `category_changed` присутствует тогда и только тогда, когда возможно более
  одной фактической `AspectCategory`;
- possible-наборы непусты, уникальны и отсортированы по действующим
  `ASPECT_PRIORITY` и порядку категорий;
- одна и та же пара не может одновременно быть опубликованным аспектом и
  excluded candidate.

Добавь в `NatalChart` обязательное поле:

```text
time_uncertainty: CosmogramTimeUncertainty | None
```

Инварианты агрегата:

- `chart_kind="natal"` требует `time_uncertainty=None`;
- `chart_kind="cosmogram"` требует `time_uncertainty`;
- если `aspects is None`, то `excluded_aspects is None`;
- если `aspects` рассчитаны, `excluded_aspects` — tuple, возможно пустой;
- все diagnostic endpoints разрешаются в тех же `bodies`/`angles`, что и
  аспекты, и не используют `south_node`;
- diagnostics уникальны, канонически отсортированы и не дублируют
  опубликованные пары;
- error содержит путь нарушившего поля.

`ArtifactNatalChart` наследует этот контракт. Старый вручную собранный
cosmogram payload без `time_uncertainty` должен отклоняться кодеком по
существующему validation/corrupt пути. Не вводи отдельную
`artifact_schema_version`.

## 6. Исчерпывающий расчёт устойчивости

Расширь `calculate_natal()` явным keyword-only аргументом:

```text
birth_time_domain: BirthTimeDomain | None = None
```

Сохрани совместимость прямых natal-вызовов через default `None`, но запрети
нечестный cosmogram:

- `chart_kind="natal"` + домен — validation error до обращения к Swiss
  Ephemeris;
- `chart_kind="cosmogram"` без домена — validation error до обращения к
  Swiss Ephemeris;
- cosmogram anchor обязан входить в домен.

`NatalTechniqueAdapter` передаёт `resolved.birth_time_domain`. Engine не
восстанавливает локальную дату, не импортирует `ZoneInfo`, не читает `tz_id`
и не применяет fold/gap policy.

### Алгоритм

Для текущего конечного minute-domain используй точный перебор всех раскрытых
UTC-моментов. Не применяй sampling, только начало/полдень/конец, произвольный
часовой шаг или недоказанный root-finding.

Для каждой минуты:

1. Рассчитай положения тел с теми же `body_ids`, ephemeris flags и Selena
   method, что у технического anchor.
2. Не рассчитывай дома и углы: космограмма их запрещает.
3. Примени действующие derived body rules и сформируй точки через один
   `AspectConfig.natal_points`.
4. Вызови неизменённый универсальный `find_aspects()` с текущими профилями,
   приоритетами, orb limits и category thresholds.
5. Сопоставь состояние каждой канонической неупорядоченной пары. Отсутствие
   записи на минуте означает `no aspect`.

Затем для каждой пары:

- если аспекта нет на всех минутах — не публикуй и не создавай diagnostic;
- если один тип и одна категория присутствуют на всех минутах — опубликуй
  один `Aspect` с максимальным наблюдавшимся `orb`, соответствующим
  `exact_angle` и `applying=None`;
- иначе не публикуй аспект и создай один `UnstableAspect` с точным набором
  причин и возможных значений.

Используй один общий детерминированный sort для обычного finder и итогового
списка; не копируй несовпадающие sort keys по слоям. Orientation пары должна
следовать порядку активной сетки, а не зависеть от порядка dict или момента.

Не добавляй специальное условие по имени `moon`: правило действует ко всем
парам. Не меняй `find_aspects()` знанием о cosmogram или timezone. Чистую
агрегацию снимков вынеси так, чтобы её можно было полноценно проверить
синтетическими `PositionedPoint` без Swiss Ephemeris.

Основные `NatalChart.bodies`, `datetime_utc` и `julian_day_ut` космограммы
остаются значениями технического anchor. Минутные снимки нужны только для
вывода отношений и не сериализуются как второй набор позиций.

Если `aspects` не включены:

- не выполняй минутный aspect scan;
- верни `time_uncertainty.domain` и `excluded_aspects=None`.

Если `aspects` включены:

- выполни scan один раз;
- передай тот же tuple устойчивых аспектов в configuration finder;
- `excluded_aspects` должен быть tuple, даже если пуст.

Не вызывай полную component boundary отдельно для каждой минуты и не помещай
в DEBUG-payload 1440/1500 полных snapshots. Сохрани существующее полное
DEBUG-логирование публичных границ и добавь только агрегированную диагностику:
число моментов, пар, устойчивых/исключённых отношений и duration. Не скрывай
итоговый `NatalChart` в full payload.

## 7. Конфигурации

ADR-0031 остаётся без изменений:

- `NatalChart.aspects` — единственный канонический список рёбер;
- configuration finder получает только устойчивые аспекты;
- исключённое ребро не может появиться в `Configuration.aspects` или
  рекурсивных `contains`;
- `Configuration.max_orb` вычисляется из уже консервативных максимальных
  орбисов рёбер;
- category, topology, роли и materialized-инварианты используют существующие
  правила;
- отдельный минутный configuration scanner не вводится.

Не меняй состав configuration patterns, allowlist, семантику оси узлов или
`normalize_include`. Знаки в `point_signs` продолжают относиться к
техническому anchor; не вводи в этом этапе отдельную интервальную модель
знаков или конфигураций.

## 8. Calculation boundary, artifact и cache

Расширь сквозные проверки:

- `NatalTechniqueAdapter` передаёт домен без копирования timezone policy;
- `_prevalidate()` отклоняет рассогласование `spec.chart_kind`,
  `resolved.time_unknown` и наличия домена до запуска worker;
- `validate_chart_against_resolved()` требует равенство полного chart domain
  resolved domain, а не только совпадение digest;
- `validate_chart_against_calculation_input()` проверяет новый key input;
- `ChartArtifact` восстанавливает тот же key из своего chart;
- foreign или вручную собранный artifact с другим доменом отклоняется;
- новый key v2 делает старые v1 cache entries недостижимыми;
- payload по найденному v2 key без обязательного uncertainty block считается
  corrupt, после чего применяется существующий пересчёт.

Не добавляй таблицу совместимости cache payload, второй cache key, fallback к
v1 или удаление старых записей. Кэш остаётся opaque `bytes`.

## 9. Прямые вызывающие и Build Natal

Обнови все прямые cosmogram-вызовы `calculate_natal()` так, чтобы они явно
передавали уже готовый `BirthTimeDomain`.

`BuildNatalHandler` продолжает выбирать вид только по
`resolved.time_unknown`; он не сравнивает время с `12:00`, не строит домен и
не открывает timezone.

Добавь сквозные позитивные проверки:

- отсутствие времени → resolver создаёт domain → handler выбирает
  cosmogram → adapter передаёт domain → artifact содержит uncertainty;
- явно введённые `12:00` → resolver оставляет domain `None` → handler
  выбирает natal → chart uncertainty `None`;
- одинаковый технический UTC anchor в этих двух случаях не делает ключи или
  результаты взаимозаменяемыми.

`NatalTool` сейчас позволяет прямой cosmogram, но не владеет IANA policy.
Сохрани этот путь только с явным типизированным `birth_time_domain` в
`NatalToolArgs`; tool валидирует согласованность с `chart_kind` и передаёт
домен дальше. Не вычисляй домен из одного `birth_datetime` и fixed offset.
Обычный CLI в `src/exact_orb/cli.py` строит только natal и не должен получать
новый пользовательский timezone-интерфейс в этой работе.

## 10. SQLite state payload v2 и чтение v1

Увеличь только:

```text
_STATE_PAYLOAD_VERSION  1 → 2
```

`_DIALOG_PAYLOAD_VERSION` остаётся `1`. SQL schema version и таблицы не
меняются: колонка `payload_version` уже допускает значения `>=1`.

Новые записи состояния всегда сериализуют полный `ResolvedBirthData` с
`birth_time_domain` и имеют payload version 2.

### Decoder

- v2 валидируется напрямую строгими актуальными моделями;
- v1 остаётся читаемым;
- неизвестная версия по-прежнему даёт существующий
  `SESSION_SQLITE_PAYLOAD_UNSUPPORTED`;
- повреждённый JSON/shape остаётся `SESSION_SQLITE_DATA_CORRUPT`;
- неудача именно v1→v2 migration даёт существующий
  `SESSION_SQLITE_MIGRATION_FAILED`;
- relational metadata сверяется с итоговым migrated `SessionState` так же
  строго, как сейчас.

Для v1 known-time записи миграция явно добавляет
`birth_time_domain=None`, не меняя остальные поля.

Для v1 unknown-time записи:

- домен строится из сохранённых `birth_input.birth_date` и
  `birth_resolved.tz_id` текущей birth/IANA policy;
- технический anchor и `utc_offset_seconds` пересчитываются той же current
  noon policy, если legacy значения не входят в новый minute-domain;
- остальные resolved-поля и существующие warnings сохраняются;
- домен не расширяется произвольным legacy timestamp только ради прохождения
  validator.

Frozen `tests/session/golden/session_sqlite_payload_v1.json` намеренно
содержит unknown-time anchor с microseconds. Не ослабляй новые инварианты и не
правь v1 fixture: migration должна нормализовать такой legacy anchor к
актуальному техническому полудню и проверяемому домену. Ожидаемый объект после
чтения теперь является migrated v2-семантикой, а не byte-for-byte моделью
старого JSON.

При следующей публичной мутации прочитанная v1 запись записывается с payload
version 2. Добавь отдельный frozen/golden v2 для новых записей; не превращай
существующий v1 fixture в v2 и не удаляй тест чтения v1.

### Граница session → birth

`tests/test_module_boundaries.py` сейчас намеренно запрещает session adapter
импортировать `exact_orb.birth` и timezone policy. Не ослабляй запрет до
широкого разрешения и не прячь импорт внутри функции.

Используй узкий синхронный migration seam:

- session contract объявляет минимальный protocol/callback, который по
  `birth_date` и `tz_id` возвращает актуальный unknown-time anchor, offset и
  `BirthTimeDomain`;
- SQLite persistence получает реализацию dependency injection при открытии;
- конкретная реализация живёт в birth/timezone-слое и переиспользует его
  канонический алгоритм;
- adapter координирует versioned JSON migration, но не знает fold/gap/IANA
  правил;
- отсутствие обязательного migrator при чтении v1 unknown-time payload даёт
  типизированный migration failure, а не молчаливый `None`;
- если в проекте есть фактический composition root SQLite persistence,
  подключи birth-owned реализацию там; не создавай новый сервис только ради
  будущего wiring.

Обнови module-boundary tests позитивным контролем внедряемого seam, не
разрешая adapter прямые imports `birth`, `ZoneInfo`, engine или application.

Не добавляй миграцию in-memory adapter: он хранит актуальные `SessionState`, а
не versioned JSON.

## 11. Версии и схемы

Одновременно измени:

```text
ENGINE_VERSION                  "3" → "4"
calculation key SCHEMA_VERSION "v1" → "v2"
state payload version           1  → 2
```

Не меняй:

- `CALCULATION_VERSION_SCHEMA="v1"`;
- форму `CalculationVersionRecord`;
- `AspectConfig`, `ConfigurationConfig` и `StrengthConfig`;
- `profiles_digest`;
- `ChartSpec`;
- dialog payload version;
- Research `feature_schema_version` и digest format;
- `artifact_schema_version` — такого поля не вводится.

Regression-тест `CalculationVersion` должен доказать, что новый fingerprint
отличается от прежнего именно `engine_version`, а `profiles_digest` остался
равен доизменённому значению. Не меняй profile golden случайной строкой.

Normalized artifact JSON изменится из-за обязательного
`time_uncertainty` (`null` у natal, объект у cosmogram) и key v2. Обновляй его
digest только после проверок структуры и смысла.

## 12. Обязательные regression-тесты

Сначала расширяй ближайшие существующие тесты и fixtures. Новый fixture
создавай только для действительно новой payload version или повторно
используемого time-domain сценария.

### Birth/timezone

1. Валидация `UtcMinuteRange` и `BirthTimeDomain`: UTC, microseconds, count,
   overflow, сортировка, overlap, mergeable ranges и round-trip.
2. Digest имеет независимый golden canonical JSON и не зависит от порядка
   получения исходных минут.
3. Обычные сутки дают 1440 уникальных UTC-моментов.
4. Управляемые реальные IANA даты дают 1380 и 1500 моментов; fold содержит
   обе UTC-реализации одной локальной минуты.
5. Полностью пропущенная дата остаётся invalid.
6. Исторический секундный offset не округляется; range splitting доказан
   конкретным fixture.
7. Resolver выдаёт domain только при `birth_time is None`; explicit `12:00`
   остаётся known-time.
8. Noon nonexistent/ambiguous anchor входит в итоговый домен после текущей
   детерминированной policy.

### Чистая аспектная агрегация

9. Один тип и категория на всех snapshots публикуются с максимальным orb, а
   не с первым/полуденным/минимальным.
10. Пара без аспекта на всём домене не даёт ни aspect, ни diagnostic.
11. Аспект только на части домена даёт `not_present_for_all_times`.
12. Смена типа, категории и их комбинация дают точные канонические reasons и
    possible-наборы.
13. Синтетическая быстрая пара служит негативным случаем, а независимая
    медленная пара в том же тесте — позитивным контролем, что finder
    действительно выполнялся.
14. Перестановка входных dict/snapshots не меняет сериализованный результат;
    изменение хронологического состава домена меняет его там, где должно.
15. Universal `find_aspects()` остаётся name-agnostic и не знает о времени.

### Реальный engine path

16. Натал с известным временем byte-for-byte сохраняет прежние bodies,
    аспекты, конфигурации, категории, орбисы и `applying=None`; добавляется
    только `time_uncertainty=None` в сериализации.
17. Реальная cosmogram сохраняет полуденные bodies, но каждый опубликованный
    аспект удовлетворяет all-minute предикату и его orb равен максимуму.
18. Выбери и зафиксируй реальный контролируемый lunar-сценарий, где конкретная
    пара исключается с ожидаемой причиной; не ограничивай общий алгоритм
    Луной.
19. Реальная медленная пара остаётся опубликованной как позитивный контроль.
20. Ни одна конфигурация и `contains` не содержит исключённого ребра; все
    materialized-рёбра по-прежнему равны элементам `NatalChart.aspects`.
21. Positions-only cosmogram не запускает aspect scan и возвращает
    `excluded_aspects=None`; рассчитанный пустой список исключений — `()`.
22. Несогласованные `chart_kind`/domain отклоняются до первого Swiss-вызова.

Не создавай ожидаемый список реальной космограммы простой копией текущего
output. Сначала докажи предикат структурно и отдельно закрепи несколько
человеком проверяемых позитивных/негативных отношений.

### Key, artifact, cache и application

23. `CalculationInput` требует явный domain digest и валидирует формат.
24. Key v2 различает unknown domains и сохраняет независимость от display
    metadata.
25. `calculation_input_from(resolved)` и `calculation_input_from_chart()`
    дают равные значения для валидного artifact.
26. `NatalChart` отклоняет missing/foreign uncertainty, неправильное
    `excluded_aspects=None`, unresolved endpoint, duplicate diagnostic и
    diagnostic опубликованной пары.
27. Artifact codec round-trip сохраняет domain и diagnostics; старый
    cosmogram shape без блока отклоняется.
28. Corrupt v2 cache payload пересчитывается существующим путём; v1 key не
    запрашивается.
29. Real Build Natal без времени выдаёт cosmogram с uncertainty и ключом v2;
    повторный тот же запрос даёт cache hit.
30. Real Build Natal с явными `12:00` выдаёт natal с `time_uncertainty=None`.
31. Full DEBUG boundary payload содержит новый resolved domain и итоговый
    uncertainty block, но не массив всех минутных body snapshots.
32. `NatalTool` требует domain только для прямого cosmogram и не строит его
    сам.

### Session v1/v2

33. Новая запись state имеет payload version 2; dialog остаётся version 1.
34. Frozen v1 файл не изменён и читается через публичный port.
35. Known-time v1 мигрирует с domain `None` без иных смысловых изменений.
36. Unknown-time v1 получает domain и нормализованный anchor; legacy
    microseconds не протаскиваются в домен.
37. Первая мутация migrated записи сохраняет v2.
38. Неизвестная IANA-зона или отсутствие injected migrator дают
    `SESSION_SQLITE_MIGRATION_FAILED`; транзакционные rollback/cleanup
    гарантии сохраняются.
39. Unsupported payload version и corrupt payload остаются различимыми.
40. Новый v2 golden проверяет exact JSON/metadata, а v1 golden остаётся
    входным доказательством совместимости.

### Research и presentation

Research schema не расширяется блоком неопределённости. Проекция получает
только уже отфильтрованные `chart.aspects` и не восстанавливает исключённые
пары из bodies или diagnostics.

Если строгий новый `NatalChart` требует обновить cosmogram fixtures Research,
добавь им валидный uncertainty block и один тест, что исключённый кандидат не
попадает в `AspectFeature`. Не меняй `ChartFeatures`,
`FEATURE_SCHEMA_VERSION`, Research golden digests или persisted record shape.

Human CLI golden для natal должен остаться неизменным. Не добавляй в него
`time_uncertainty=None` как пользовательскую строку.

## 13. Актуальная документация и диаграммы

После фактической реализации синхронизируй только затронутые текущие
источники истины:

- ADR-0032: убери пометку об отложенной реализации и добавь краткое
  implementation status без переписывания принятого решения;
- `docs/requirements/decisions/README.md`;
- birth, calculation, chart artifacts, Build Natal и session
  responsibilities: целевые поля становятся действующим контрактом;
- `docs/requirements/handlers/exact_orb_build_natal_handler_requirements.md`:
  новое поле resolved, key v2 и cosmogram uncertainty;
- `docs/architecture/exact_orb_class_diagram.puml`;
- `docs/architecture/exact_orb_architecture.puml`;
- `docs/sequence_diagrams/build_natal/000-build_natal_end_to_end.puml`;
- `docs/sequence_diagrams/build_natal/003-build_cosmogram_time_unknown.puml`;
- README sequence-папки, если описание сценариев меняется.

Диаграммы должны показывать фактический поток:

```text
local date + tz_id
  → birth-owned BirthTimeDomain
  → ResolvedBirthData
  → digest in CalculationInput/key v2
  → engine all-minute aspect evaluation
  → NatalChart.time_uncertainty
  → ChartArtifact/cache
```

Не добавляй Research diagram или high-level roadmap только ради нового поля,
если их действующая абстракция не раскрывает payload-модели. Не редактируй
исторические review-документы и ADR-0008 так, будто interval semantics была
реализована изначально.

Finding
`002_full-build-natal-log-exposed-aspect-model-defects.md` пока не помечай
«устранено»: финальный этап ещё должен проверить реальный полный DEBUG-payload
и закрыть весь finding целиком.

Если доступен локальный PlantUML, проверь изменённые `.puml`. Новые
зависимости и Java для этого не скачивай; `.png` вручную не редактируй.

## 14. Жёсткие ограничения

- Не меняй вид карты по значению часов: explicit `12:00` всегда natal.
- Не оставляй cosmogram fallback без domain или с полуденными аспектами.
- Не проверяй только Луну и не удаляй все аспекты Луны без вычисления.
- Не используй sampling нескольких моментов вместо всех поддерживаемых минут.
- Не округляй исторические UTC-секунды до минуты.
- Не нормализуй nonexistent пользовательские минуты в допустимый домен.
- Не восстанавливай timezone policy в engine, handler, calculation key или
  session adapter.
- Не ослабляй module-boundary tests и не прячь запрещённые imports.
- Не сериализуй все минутные positions в artifact, session или Research.
- Не превращай diagnostics во второй список обычных аспектов.
- Не меняй `find_aspects()` специальными именами или chart-kind условиями.
- Не вводи отдельную устойчивость конфигураций, расходящуюся с
  `NatalChart.aspects`.
- Не пересматривай ADR-0030/0031, canonical point ids и запрет
  `south_node` в отношениях.
- Не меняй `applying=None`.
- Не меняй aspect/configuration/strength profiles и `profiles_digest`.
- Не удаляй `house_system="P"`.
- Не добавляй domain в `ChartSpec`.
- Не удаляй `artifact.spec` или `delta.base_chart_spec` и не ослабляй их
  равенство.
- Не вводи `artifact_schema_version`, новую форму CalculationVersionRecord
  или новый Research schema version.
- Не меняй dialog payload version.
- Не редактируй frozen session payload v1, исторические `prompts/**`, старые
  ADR и review-документы.
- Не выполняй попутный рефакторинг, migration framework «на будущее», новые
  сервисы, зависимости или сетевые вызовы.
- Не меняй полное DEBUG-логирование component boundaries и не возвращайся к
  отложенному privacy finding.
- Не реализуй транзиты поверх космограммы и предупреждения ADR-0008 о смене
  знака/направления, если они не требуются для устойчивости аспектов.
- Не запускай платные или сетевые smoke-тесты.
- Не создавай коммит, push или PR.

## 15. Проверки

Запускай поэтапно и сообщай только фактические результаты.

### 1. Целевые

```text
pytest tests/test_birth_tz.py tests/test_birth_resolver.py -q
pytest tests/test_aspects.py tests/test_natal_include_gating.py tests/test_configurations.py -q
pytest tests/test_calculation_keys.py tests/test_calculation_version.py -q
pytest tests/test_chart_artifact_codec.py tests/test_chart_artifact_resolver.py -q
pytest tests/test_calculation_engine.py tests/test_calculation_engine_integration.py -q
pytest tests/session/test_state.py tests/session/test_sqlite.py -q
pytest tests/application/test_build_natal_handler.py tests/application/test_build_natal_integration.py -q
pytest tests/test_tools_natal.py -q
```

### 2. Связанные

```text
pytest tests/test_calculation_block_integration.py tests/test_calculation_cache.py -q
pytest tests/research/test_projection.py tests/research/test_models.py -q
pytest tests/test_module_boundaries.py -q
pytest tests/test_cli_render.py tests/test_ephemeris.py tests/test_ephemeris_runtime_concurrency.py -q
```

### 3. Полный набор

```text
pytest -q
git diff --check
```

### 4. Статические проверки

Через `rg` классифицируй все оставшиеся:

- `eo:calc:v1:` — допустим только в исторических ADR/prompts, frozen v1 и
  явных compatibility assertions;
- `_STATE_PAYLOAD_VERSION = 1` — недопустим; dialog version 1 допустим;
- `birth_time_domain` / `time_uncertainty` — должны проходить через все
  владельческие границы, но не появляться в `ChartSpec` или Research schema;
- `ZoneInfo` / `resolve_historical_tz` — не должны импортироваться engine,
  calculation, handler или session adapter;
- прямые `chart_kind="cosmogram"` — каждый production/test caller обязан
  передавать валидный domain;
- `time_unknown=True` test fixtures — не должны обходить validation через
  `model_copy(update=...)` без домена.

Проверь, что `profiles_digest`, `CALCULATION_VERSION_SCHEMA`, dialog payload
version, Research versions и `ChartSpec` действительно не изменились.

Просмотри полный `git diff` и `git status --short`. В diff не должно быть
несвязанных пользовательских файлов или изменений старых промтов.

## Итоговый отчёт

Начни с результата, затем кратко укажи:

- как строится и канонизируется `BirthTimeDomain`, включая counts 23/24/25-
  часовых суток и исторические секунды;
- как различаются неизвестное время и explicit `12:00`;
- точную форму domain digest и key v2;
- как engine исчерпывающе выводит устойчивые аспекты и максимальный orb;
- какие реальные аспекты сохранены и исключены в regression-сценарии;
- как diagnostics отличаются от опубликованных аспектов;
- почему конфигурации не требуют отдельного scan;
- какие агрегатные инварианты добавлены в `ResolvedBirthData`, `NatalChart`
  и `ChartArtifact`;
- как читается frozen state payload v1, что нормализуется у legacy unknown
  anchor и как пишется v2;
- как сохранена session/birth module boundary;
- изменения `ENGINE_VERSION`, key schema и state payload version;
- подтверждение неизменности profiles, CalculationVersion schema, ChartSpec,
  dialog и Research versions;
- фактически изменённые production-файлы, тесты, golden, требования и
  диаграммы;
- точные команды и реальные результаты целевых, связанных и полного pytest;
- результат `git diff --check`, статического поиска и PlantUML-проверки;
- что не проверялось;
- что finding `002` остаётся открытым до следующего финального промта:
  сквозной проверки реального полного DEBUG-payload и ручной приёмки.
