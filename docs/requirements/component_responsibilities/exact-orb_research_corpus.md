# exact-orb — требования к блоку Research Corpus

Документ описывает отдельный sibling-компонент `research/`: какие признаки
он принимает, что физически запрещено переносить из `ChartArtifact`, как
устроены идемпотентная запись и поздние quality events и какой остаточный
privacy-риск принят.

Это основной контракт компонента. `exact-orb_session_requirements.md` §6
описывает Research кратко с точки зрения session. При расхождении нормативны
настоящий документ и ADR-0023.

Статус: **P5a реализован.** Contracts, whitelist-проекция и InMemory adapter
готовы и покрыты тестами. P5b — SQLite и аналитически пригодная схема — ещё
не реализован; application wiring выполняется отдельной задачей.

Согласовано 2026-09-06.

---

## 1. Назначение и граница

Сессия живёт по TTL. После её удаления рассчитанная карта и контекст ответа
не восстанавливаются. Чтобы сравнивать версии рецепта и качество ответов по
категориальным признакам карты, разрешён отдельный бессрочный write path.

Research Corpus не является History пользователя и не обслуживает runtime-
чтение. Он принимает одну базовую запись на готовый ответ и отдельные
append-only события качества.

В компонент входят:

- frozen модели закрытых категориальных признаков;
- whitelist-проекция `ChartArtifact -> ChartFeatures`;
- canonical content digest format;
- write-only `ResearchCorpus`;
- реализованный InMemory adapter и запланированный SQLite adapter за общим
  behavioral conformance.

Не входят:

- session lifecycle и `ContextService`;
- query/response text и полный artifact;
- consent, revoke и delete-by-consent;
- application policy write-always/fail-open/outbox;
- online analytics reader, export и History.

## 2. Privacy-модель

### 2.1. Что схема гарантирует

Research record физически не содержит:

- `session_id`, cookie, IP, `run_id`, account ID, `consent_id`;
- `calculation_key` и другой идентификатор, производный от birth data;
- birth input, место, координаты, точное время и `julian_day_ut`;
- градусы, минуты, орбисы, скорости, расстояния и thresholds;
- `ChartSpec`, полный `ChartArtifact`, warnings и ephemeris provenance;
- query text, response text и generic metadata.

Проекция строится по allowlist. Проверка выполняется над моделью и её
сериализацией, а P5b дополнительно проверит физическую SQLite-схему.

### 2.2. Что схема не гарантирует

Полный категориальный вектор карты вместе с UTC hour, selection, model и
recipe version остаётся квазиидентификатором. Обладатель известных birth data
может вычислить ту же карту и искать совпадения. Совпадающие поля двух
корпусов также могут позволить корреляцию без общего ID.

Поэтому Research называется de-identified по явным полям, но не анонимным и
не unlinkable. Предельный риск нового признака не считается нулевым.

Бессрочный retention и отсутствие удаления по кнопке сессии — отдельное
принятое решение ADR-0023. Оно требует повторной оценки перед публичным
развёртыванием и не выводится из отсутствия `session_id`.

### 2.3. Text и consent

Always-on record не содержит query/response text и полный artifact. Их
будущий consented payload не предрешён настоящим документом: точная модель
принимается до реализации freeform/consent-flow.

## 3. Область feature schema v1

### 3.1. Selection

ADR-0015 задаёт продуктовый vocabulary:

```text
topic ∈ {natal, transit}
focus ∈ {general, career, money, love}
```

Research v1 поддерживает только:

```text
topic = natal
focus ∈ {general, career, money, love}
chart_kind ∈ {natal, cosmogram}
```

Transit-запись отложена до модели, которая различает natal base и transit
chart. Одна неуточнённая `ChartFeatures` для transit запрещена.

### 3.2. Закрытые признаки

`ChartFeatures.feature_schema_version = 1` содержит:

```text
BodyFeature          point, sign, house 1..12 | None, retrograde
AngleFeature         point (только asc и mc), sign
AspectFeature        canonical endpoints, type, category
ConfigurationFeature type, category, typed role/point pairs, element, modality
DignityFeature       point, system, status
StrengthFeature      point, category, house_type
BalanceFeature       typed axis/bucket, state
LunarPhaseFeature    phase_number 1..8
chart_kind
```

Знак записывается только для `asc` и `mc`. Шесть вспомогательных углов
(`armc`, `vertex`, `equatorial_ascendant`, `co_ascendant_koch`,
`co_ascendant_munkasey`, `polar_ascendant`) интерпретационно почти не
используются, а их знаки вместе доопределяют время рождения точнее одного
Асцендента. `vertex` остаётся допустимым концом аспекта, поскольку входит в
`AspectConfig.natal_points`, но своего `AngleFeature` не получает.

Имя фазы Луны не хранится: отображение номер ↔ имя биективно, а сами имена
относятся к слою отображения, и их правка стала бы изменением формата
бессрочного корпуса.

`None` означает «семейство не вычислялось», пустой tuple — «вычислялось,
результатов нет». Эти состояния не схлопываются.

Не входят в v1: house cusps/rulers/interceptions, degree flags, dispositor
chains, mutual receptions, hemisphere balance и house-type balance.

### 3.3. Vocabulary точек

```text
BODY_FEATURE_POINTS = {
  sun, moon, mercury, venus, mars, jupiter, saturn,
  uranus, neptune, pluto, chiron, true_node, mean_apog,
  south_node, pars_fortune, selena
}

ANGLE_FEATURE_POINTS = {asc, mc}

STRENGTH_POINTS = {
  sun, moon, mercury, venus, mars,
  jupiter, saturn, uranus, neptune, pluto
}

RELATIONAL_POINTS = BODY_FEATURE_POINTS ∪ {asc, mc, vertex}
```

`RELATIONAL_POINTS` — закрытый vocabulary persisted schema v1. Активный набор
новых отношений равен `RELATIONAL_POINTS − {south_node}` и в точности
совпадает с `AspectConfig.natal_points`. `south_node` остаётся допустимым enum
value только для чтения исторических v1-записей прежней
`CalculationVersion`.

**У точки ровно одно каноническое написание.** По ADR-0029 машинные ссылки
используют имена `true_node`, `south_node`, `mean_apog`, `pars_fortune` без
alias-преобразования. По ADR-0030 `true_node` является единственным
представителем оси в новых отношениях, а `south_node` переносится только как
`BodyFeature`. `_natal_aspect_points` выпускает имя без преобразования, а
`AspectConfig.point_aliases` отсутствует.

Research projection получает уже канонический `ChartArtifact` и переносит
идентификаторы напрямую. `north_node`, `lilith`, `pars` являются ошибкой
машинного vocabulary, а не альтернативным входным написанием. Поэтому одна
точка не может попасть в бессрочный корпус под двумя именами, и `GROUP BY` по
концу аспекта не разделяет один объект на два значения.

### 3.4. Остальные vocabulary

```text
zodiac = Aries .. Pisces
aspect type = conjunction, semisextile, sextile, square, trine,
              quincunx, opposition
aspect category = exact, working, background
configuration type = t_square, yod, bisextile, grand_cross,
                     grand_trine, trapeze
configuration category = tight, moderate, loose
dignity system = traditional, modern
dignity status = domicile, exaltation, detriment, fall, peregrine
strength category = strong, moderate, weak
house type = angular, succedent, cadent
element = fire, earth, air, water
modality = cardinal, fixed, mutable
balance state = deficit, balanced, excess
quality kind = rating, regenerate, copy, reading_time
```

Vocabulary `quality kind` задаётся `Literal`-дискриминаторами четырёх моделей
событий, а не отдельным enum: discriminated union остаётся единственным
источником истины.

Фаза Луны хранится номером восьмифазной системы Рудьяра: `1..8`. Имена
`PHASE_NAMES` остаются в engine и в корпус не переносятся.

Configuration roles зависят от type:

```text
t_square/yod -> apex, base_1, base_2
bisextile    -> center, wing_1, wing_2
grand_trine  -> point_1, point_2, point_3
grand_cross  -> axis_1_a, axis_1_b, axis_2_a, axis_2_b
trapeze      -> opposition_1, opposition_2, base_1, base_2
```

Модель отвергает неизвестные значения, несовместимые axis/bucket, номер фазы
вне `1..8`, отсутствующие/лишние/повторные roles, повтор одного point в
нескольких ролях, alias вместо канонического имени и point не своего вида.

Все `DignityFeature` одной записи имеют одинаковый `system`:
`NatalStrength.dignity_system` задан на карту целиком, поэтому смешанный набор
engine произвести не может.

## 4. Канонические модели

Все модели используют `ConfigDict(frozen=True, extra="forbid")`; вложенные
коллекции immutable.

```text
ResearchRecord {
    research_id: UUID4
    created_at: aware UTC hour
    calculation_version: bounded ASCII identifier
    chart_features: ChartFeatures
    selection: {topic=natal, focus}
    recipe_version: bounded ASCII identifier
    model: bounded ASCII identifier
    tokens_in, tokens_out: int >= 0 | None
    cost_usd: finite float >= 0 | None
    latency_ms: finite float >= 0
}
```

`feature_schema_version` имеет один источник истины внутри `ChartFeatures`.
P5b вправе денормализовать его в индексируемую колонку, но не добавляет второй
независимый field модели.

ADR-0030 не меняет `feature_schema_version=1`: поля, закрытые enum и digest
format сохранены, а разная методика состава отношений различается
`ResearchRecord.calculation_version`. Исторический relational `south_node`
остаётся валидным v1 value, хотя новая проекция выпускает его только как
`BodyFeature`.

ADR-0032 также не меняет `feature_schema_version=1`: проекция читает только
опубликованные устойчивые `NatalChart.aspects` и производные конфигурации,
не восстанавливает исключённые пары из `bodies` или `time_uncertainty`.
Диагностический блок не входит в `ChartFeatures`; происхождение чисел и
методики различается обязательным `ResearchRecord.calculation_version`.

Bounded identifiers имеют длину `1..128` и соответствуют
`^[A-Za-z0-9][A-Za-z0-9._:/+@~\-]{0,127}$`. `@` и `~` входят в класс потому,
что gateway построен на litellm, а часть провайдерских идентификаторов
содержит `@` (например `text-bison@002`); отвергнутый `model` означал бы, что
успешный ответ не удалось записать.

Нулевые floats нормализуются к `+0.0`; отрицательные, NaN и Infinity
отвергаются.

Feature tuples канонизируются при создании модели, а не только projection:
bodies/angles по point, aspects по полному содержимому после ориентации
endpoints, configuration points по role/point, остальные семейства по полному
категориальному содержимому. Дубликаты aspects/configurations не удаляются.

## 5. Время и идентичность

Caller создаёт `research_id`, `event_id` и исходный timestamp один раз на
logical write. Research не вызывает генераторы ID, системные часы, randomness
и не читает environment.

`floor_to_utc_hour(value)` принимает только aware zero-offset datetime,
нормализует tzinfo к `timezone.utc` и обнуляет minute/second/microsecond.
Ненулевой UTC offset не конвертируется, а отвергается. Timestamp fields уже
должны находиться на границе часа.

## 6. Base record и quality events

Базовая запись создаётся один раз при готовности ответа. Поздние сигналы
качества — discriminated union:

```text
RatingEvent      {kind=rating,       event_id, research_id, observed_at, rating}
RegenerateEvent  {kind=regenerate,   event_id, research_id, observed_at}
CopyEvent        {kind=copy,         event_id, research_id, observed_at}
ReadingTimeEvent {kind=reading_time, event_id, research_id, observed_at,
                  reading_time_ms}
```

`kind` — обязательный discriminator. У события один смысл. Event не меняет
base record и не создаёт отсутствующий parent.

## 7. Content digest format v1

Record digest исключает только `research_id`. Event digest исключает только
`event_id`; `research_id`, `kind`, `observed_at` и payload входят обязательно.

Канонический payload использует Enum value, lowercase hyphenated UUID,
datetime `YYYY-MM-DDTHH:00:00Z`, arrays для tuple и `null` для None.
JSON кодируется UTF-8 с `ensure_ascii=False`, `allow_nan=False`,
`sort_keys=True`, separators `(',', ':')`. Digest — lowercase SHA-256 hex.
Finite float сериализуется стандартным Python `json.dumps` в shortest
round-trip representation: например, `0.1 + 0.2` кодируется как
`0.30000000000000004`. Все адаптеры обязаны использовать общий digest helper,
а не воспроизводить канонизацию самостоятельно.

Публичная константа `RESEARCH_DIGEST_FORMAT_VERSION = 1` обозначает алгоритм,
но не дублируется отдельным полем каждой записи. Точные canonical JSON и
digest фиксируются frozen fixture. Изменение байта —
persisted contract change, требующее решения и P5b migration strategy.

## 8. Проекция

`research.projection.project_chart_features(artifact)` — единственная точка,
где полный `ChartArtifact` входит в компонент. Она явно выбирает разрешённые
поля, не использует whole-object dump с последующим blacklist, не мутирует
artifact и не читает часы.

Неизвестная категория даёт безопасный
`ResearchProjectionError("RESEARCH_PROJECTION_UNSUPPORTED_VALUE")`; значение
и artifact content не входят в exception text. Подавленный `ValidationError`
оставляет одну warning-запись с безопасными `loc` и `type`; входное значение,
сообщение валидатора и содержимое artifact в диагностические поля не входят.

Перед построением моделей проекция переносит канонические имена точек без
alias-преобразования. `south_node` сохраняется в семействе `BodyFeature`, но
новый `NatalChart` не допускает его в аспектах и конфигурациях. Разрешимость и
семантика оси всех `AspectPointRef` уже проверены контрактом `NatalChart`;
закрытые Research enums независимо отклоняют значение вне Research v1.

Poisoned-artifact тест проверяет отсутствие запрещённых sentinels в модели и
сериализации. Отдельный drift suite напрямую сравнивает Research vocabulary с
engine enums, derived points, активным `AspectConfig.natal_points` без
исторически допустимого relational `south_node`, `ConfigurationConfig.points`,
разбиением `ANGLE_INDICES` на включённые
(`asc`, `mc`) и исключённые углы, восьмифазностью `PHASE_NAMES` и
configuration roles; одного rich artifact недостаточно для доказательства
полноты.

## 9. Порт и outcomes

```text
put_record(record)
  -> ResearchStored | ResearchAlreadyStored | ResearchIdConflict

put_quality_event(event)
  -> QualityStored | QualityAlreadyStored |
     QualityEventIdConflict | ResearchRecordAbsent
```

Все outcomes frozen и содержат только соответствующие record/event IDs.

Record: новый ID сохраняется; тот же ID/digest идемпотентен; другой digest
даёт conflict без перезаписи.

Event classification сначала проверяет `event_id`. Существующий ID даёт
AlreadyStored/Conflict независимо от parent в новом payload. Только для
нового event ID проверяется parent; отсутствие даёт `ResearchRecordAbsent`.

Технические ошибки пересекают порт только как
`ResearchWriteError("RESEARCH_WRITE_FAILED")`; raw backend exception и
данные записи наружу не выходят.

Порт не содержит get/list/delete/touch/TTL/reaper/consent/analytics reader.
Сохранность в conformance доказывается повторными публичными writes, а не
test-only read API.

## 10. Граница пакета

Root `exact_orb.research` экспортирует contracts и чистые time/digest helpers,
но не загружает projection, adapters, `calculation.types`, engine, `sqlite3`
или `swisseph`.

Contracts используют только stdlib, Pydantic и `exact_orb.research.*`.
Adapters не знают `ChartArtifact` и принимают санитизированные модели.
`research.projection` — отдельный artifact-facing модуль: текущий import
`calculation.types` транзитивно загружает engine/swisseph и закреплён positive
control. Это не разрешает contract/adapters импортировать engine.

Session не импортирует Research. `ContextService` остаётся единственной
границей управления сессией.

## 11. Реализации и conformance

P5a реализует InMemory adapter и общий behavioral conformance. P5b обязан
подключить к тому же набору SQLite adapter. Factory возвращает две разные
фасеты `primary is not peer`, разделяющие один backend; иначе cross-handle
race вырождается.

Публичной фабрики backend нет. Чтобы получить две фасеты над одним backend,
InMemory-override в `test_in_memory.py` создаёт private backend напрямую — это
единственное санкционированное обращение к приватному имени, ограниченное
concrete test factory. Общий `conformance.py` не импортирует и не конструирует
адаптеры. SQLite в private backend не нуждается: два handle получаются двумя
вызовами `open()` над одним файлом.

Generic race использует внешний start gate и доказывает полный multiset
outcomes и сохранённое содержимое повторными writes. Он не объявляется
доказательством physical overlap внутри private critical section. InMemory
имеет adapter-specific positive controls; SQLite должен получить их в P5b.

InMemory выполняет проверку ID/digest/parent и вставку в одной backend-scoped
секции без `await` после входа. SQLite atomicity, durable schema, restart и
real worker contention относятся к P5b.

## 12. Проверяемые свойства

1. Модели frozen, deeply immutable, extra-forbid и fail-closed.
2. `topic=transit` не принимается Research v1.
3. Phase, balance, roles и point subsets внутренне согласованы; точка имеет
   одно каноническое написание, alias отвергается.
4. Feature tuples имеют один канонический порядок.
5. None и computed-empty различимы.
6. Forbidden source fields не влияют на projection и не проходят sentinels.
7. Digest byte-for-byte соответствует format v1 и golden fixture.
8. Event digest включает parent ID и kind.
9. UUID/time приходят снаружи; скрытых часов/random/environment нет.
10. Retry отличается от ID collision и не переписывает данные.
11. Event без parent не создаёт parent.
12. Порт остаётся write-only.
13. Primary/peer conformance невырожден.
14. Vocabulary drift ловится до runtime.
15. Root import не поднимает artifact/native/runtime слои.
16. Session lifecycle не изменяет Research rows.

## 13. Отложено

| Отложено | До чего |
|---|---|
| SQLite schema, indexes, restart, benchmark | P5b |
| Consented query/response/artifact payload | отдельное решение до freeform |
| Application write policy и producer wiring | отдельная application-задача |
| Transit + natal base multi-chart record | следующая feature schema |
| Dispositors, mutual receptions, hemisphere/house-type balance | schema v2 |
| Analytics reader/export | отдельная продуктовая потребность |
| `chart_group_id` | явная потребность связывать записи |

## 14. Известное расхождение

Session fixtures используют `focus="relationships"`, тогда как ADR-0015 и
Research v1 фиксируют `love`. `session.Selection` остаётся свободной парой
строк, поэтому session не меняется в P5a; Research значение fail-closed
отвергает.
