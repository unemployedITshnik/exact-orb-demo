# exact-orb — компоненты пути построения натальной карты

**Статус:** рабочий документ
**Дата:** 2026-08-26
**Область:** application-путь `BuildNatalCommand` — от входа в `Application
Orchestrator` до возврата результата и подтверждённого изменения состояния.
**Основание:** ADR-0002, 0005, 0006, 0007, 0008, 0009, 0012, 0013, 0014, 0015,
0017, 0020; инварианты И-1, И-5, И-7, И-8, И-11, И-12, И-13, И-14.

Документ фиксирует состав компонентов и контрактов до начала реализации. Он
не заменяет ADR: каждое решение здесь либо следует принятому ADR, либо явно
помечено как открытое.

---

## 1. Область

### 1.1. Что входит

Путь одной пользовательской операции:

```text
BuildNatalCommand
    → Application Orchestrator
    → BuildNatalHandler
    → BirthDataResolver
    → ChartArtifactResolver
    → EngineService
    → StateDelta
    → Application Orchestrator
    → ContextService (commit)
    → BuildNatalResult
```

Космограмма при неизвестном времени рассчитывается тем же путём (ADR-0008):
меняется `chart_kind` в `ChartSpec`, не компонент и не handler.

### 1.2. Что не входит

| Компонент | Причина |
|---|---|
| FastAPI, Session Middleware, RateLimiter | транспортный слой, отдельная задача |
| Frontend, Chart Renderer | клиент |
| `Planner`, `ScenarioRegistry`, `ToolExecutor` | agent-путь; build через них не проходит (ADR-0020) |
| `InterpretationService`, LLM Gateway, OutputGuard | интерпретация |
| `CapabilityService`, `PolicyService`, `AdmissionControl` | класс операции `calculation` доступен всем и бюджета не расходует (ADR-0013, ADR-0015) |
| `IntentService`, `InputGuard` | natural-language путь (ADR-0019) |
| Эндпоинт подсказок мест | отдельная операция; на build-пути `AMBIGUOUS` не возникает (ADR-0005) |
| `BuildAttempt`, durable job, polling | отложено (ADR-0012), см. §13 |

### 1.3. Решения, принятые до этого документа

1. **Два уровня оркестрации разведены.** `Application Orchestrator`
   (ADR-0006) и `Agent Runtime` (ADR-0020) — разные компоненты в разных
   пакетах. Build-путь не проходит через второй.

2. **Асинхронность сразу.** Оркестратор, handler и все порты — `async`.
   Обоснование в §5.4: `async def` сам по себе не делает CPU-bound расчёт
   параллельным (ADR-0012), поэтому расчёт уходит в thread executor явно.

3. **Порт без второй реализации — это `typing.Protocol`,** а не ABC плюс
   адаптер плюс фабрика плюс строка конфигурации. ADR-0020 формулирует
   принцип: дорого стоит точка вызова, а не логика за ней. `Protocol` —
   и есть точка вызова, ценой ноль строк.

4. **`EngineService` — локальная реализация `CalculationEnginePort`,**
   а не отдельный слой под адаптером. Отдельного `LocalEngineAdapter`
   не существует, иначе появляется слой без собственной ответственности.

5. **`BuildAttempt` не реализуется.** Актуальность результата обеспечивается
   compare-and-set по `state_version` (ADR-0014). Обоснование в §1.4.

### 1.4. Замер, на который опирается пятое решение

`engine/charts/natal.py` с `pysweph 2.10.3.6` и текущими `ephe/*.se1`,
30 прогонов с разными датами, один процесс, прогретый кэш эфемерид:

```text
natal (полный)   min 11.1 ms   median 18.9 ms   p95 20.3 ms   max 24.9 ms
cosmogram        min 10.3 ms   median 17.4 ms   p95 18.4 ms   max 20.3 ms
```

`BuildAttempt` со статусами `PROCESSING | INTERRUPTED`, счётчиком
`build_revision`, reaper'ом зависших попыток и восстановлением незавершённой
операции после reopen защищает окно длиной двадцать миллисекунд. Пользователь
закрывает вкладку либо до отправки запроса (N1.1 — реагировать не на что),
либо после получения ответа.

Компенсирующий механизм — CAS по `state_version`:

```text
A и B стартуют при state_version = 5, обе expected = 5
B коммитит первой:  CAS(5 → 6) успешно
A коммитит позже:   expected 5 ≠ actual 6 → отказ, outcome Superseded
```

Этим покрываются N6, N10 и все ветки «кто закончил первым». `build_revision`
добавляет только возможность отличить «намерение устарело» от «состояние
изменилось под тобой» — качество диагностики, не корректность.

**Условие возврата к решению:** расчёт, устойчиво превышающий несколько сотен
миллисекунд, либо появление асинхронного build. `calculate_transit` по
overview §4.10 — «самая дорогая операция системы»; не измерена и может стать
таким триггером.

---

## 2. Пакетная структура

```text
src/exact_orb/
    outcomes.py                 общие типизированные исходы

    application/
        __init__.py
        commands.py             BuildNatalCommand
        run_context.py          RunContext
        orchestrator.py         ApplicationOrchestrator
        results.py              BuildNatalResult и приведение исходов
        handlers/
            __init__.py
            build_natal.py      BuildNatalHandler

    birth/
        __init__.py
        types.py                BirthInput, ResolvedBirthData
        places.py               PlaceCatalog, LocalPlaceCatalog
        tz.py                   resolve_historical_tz
        resolver.py             BirthDataResolver

    calculation/
        __init__.py
        spec.py                 ChartSpec
        types.py                ChartArtifact
        version.py              CalculationVersion
        keys.py                 CalculationInput, calculation_key
        cache.py                CalculationCache, InMemoryCalculationCache
        codec.py                ChartArtifact byte codec
        errors.py               ArtifactError, ChartCalculationError, CalculationUnavailableError
        engine.py               CalculationEnginePort, EngineService
        artifacts.py            ChartArtifactResolver

    session/
        __init__.py
        state.py                SessionState, StateDelta, RESET_DELTA, чистые переходы
        outcomes.py             типизированные исходы lifecycle и CAS
        errors.py               SessionPersistenceError, StateReadError, StateWriteError
        store.py                SessionStore (Protocol)
        dialog.py               DialogStore (Protocol)
        persistence.py          SessionSnapshot, SessionPersistence (Protocol)
        context.py              ContextService (P3)
        adapters/
            __init__.py         public concrete adapter exports
            _time.py            internal wrapper над public require_utc для P2/P4
            in_memory.py        P2
            sqlite.py           P4

    bootstrap.py                композиция объектов на старте
```

Имя `calculation` выбрано вместо `charts`, потому что `engine/charts`
уже занят техниками расчёта; путаницы между «спецификация и артефакт» и
«алгоритм техники» быть не должно.

---

## 3. Контракты

### 3.1. `birth/types.py`

#### `BirthInput`

Пользовательский ввод в том виде, в котором он пришёл из формы. Хранится
в `SessionState`, чтобы форму редактирования было чем заполнить (ADR-0009).

```text
BirthInput {
    birth_date: date            обязательно
    birth_time: time | None     необязательно; None = time_unknown
    place_id:   str             обязательно; недоверенный
}
```

Три поля — больше системе для расчёта ничего не нужно, и всё, что нужно,
человек выбрал явно.

`place_id` приходит от клиента и **доверенным не является** (ADR-0005): он
может устареть после обновления каталога или быть подделан. Проверяется
резолвером по каталогу.

Свободный текст места Build API не принимает — форма выбирающая.

**`place_text` убран.** Он был нужен, чтобы показать, что человек написал
до выбора из подсказок. Но форма выбирающая: набранное — это поисковый
префикс, а не данные. Хранить его незачем, а попав в `ResolvedBirthData`,
он ещё и испортил бы `calculation_key` (§4.2).

#### `ResolvedBirthData`

Расчётные параметры и источник для `calculation_key`. Состав зафиксирован
ADR-0005:

```text
ResolvedBirthData {
    utc_datetime:         datetime   tz-aware, UTC     ← расчёт
    latitude:             float                        ← расчёт
    longitude:            float                        ← расчёт

    tz_id:                str                          ← форма, транзиты
    utc_offset_seconds:   int                          ← объяснение
    canonical_place:      str                          ← форма

    time_unknown:         bool                         ← chart_kind
    warnings:             tuple[ResolutionWarning, ...] ← промпт (И-7)
}
```

Поля разделены на три группы намеренно: только первая влияет на числа
расчёта, и только она попадает в `calculation_key` (§4.2).

**Смещение хранится в секундах.** До введения стандартного времени зоны
жили по местному солнечному, и смещения не кратны минуте: `Europe/Moscow`
на 1800 год даёт `+02:30:17`, `Europe/Paris` — `+00:09:21`, `Asia/Tokyo`
на 1880 — `+09:18:59`. Проверено перебором: **399 зон из 498** имеют
нецелые минуты на 1800-01-01, а нижняя граница поддерживаемого диапазона
как раз 1800 год.

В минутах поле «применённое смещение» врало бы для большей части старого
диапазона — `utc_datetime` считался бы верно, а соседнее поле нет.
Единица вынесена в имя, чтобы убрать класс ошибок при чтении кода.

**`place_substituted` и `place_input_text` отменены.** ADR-0005 вводил их
ради предупреждения «при отклонении больше 0.5° по долготе или широте».
Механизм не работает с обоих концов: флаг неоткуда взять — выбор ближайшего
города со стороны backend неотличим от выбора своего, — а порог невычислим,
потому что считается от координат исходного места, которых у системы нет;
в этом и состояла причина подстановки.

Подстановка ближайшего города остаётся, но целиком как текст в форме,
а не поле контракта. Подробный разбор —
`exact-orb_birth_data_resolution.md` §3.4.

**О `utc_datetime` при `time_unknown = true`.** Для долгот тел всё равно нужен
момент. Берётся местный полдень как техническая опорная точка, и результат
обязан нести `time_unknown = true`. ADR-0008 прямо требует: полдень
«не должен создавать видимость известного времени ни в одном поле
результата» — то есть ни `BirthInput.birth_time`, ни какое-либо поле
`Chart DTO` не показывают 12:00 как время рождения.

**Оба представления хранятся в профиле.** Только `birth_input` означал бы,
что обновление `tzdata` молча сдвинет карту; только `birth_resolved` — что
нечего показать в форме (ADR-0009).

```text
ResolutionWarning {
    source:  str        "place" | "time"
    code:    str        проверяемый код, не свободный текст
    message: str
}
```

Обобщённые формулировки запрещены: ADR-0008 требует, чтобы критерий
предупреждения был проверяем тестом.

---

### 3.2. `outcomes.py`

Верхний уровень пакета, потому что `InputRequired` возвращают и
`BirthDataResolver` (наш путь), и `ContractValidator` с `Planner`
(agent-путь) — один тип и одно место его определения (ADR-0007, И-11).

```text
Issue {
    field:       str                 путь в контракт, напр. "birth.place"
    code:        MISSING | AMBIGUOUS | INVALID | UNSUPPORTED
    candidates:  tuple[...] | None
    constraints: dict | None
}

InputRequired {
    issues: tuple[Issue, ...]
}
```

Технические исходы — отдельные типы, чтобы их нельзя было перепутать
с пользовательской ошибкой ввода:

```text
ResolutionUnavailable { error_code: str, retryable: bool }
CalculationFailed     { error_code: str }
StateReadFailed       { error_code: str }
StateCommitFailed     { error_code: str }
SessionCreated        { state: SessionState }
SessionIdConflict     { session_id: str }
Committed             { state_version: int }
AlreadyApplied        { state_version: int }
Superseded            { actual: SessionState }
SessionAbsent         { reason: Literal["expired", "not_found"] }
```

> **Инвариант.** Техническая недоступность никогда не преобразуется
> в `InputRequired`. Проверяется тестом на каждой границе, где это возможно
> перепутать: resolver, engine, store.

`AMBIGUOUS` на build-пути в MVP **не возникает**: форма выбирающая, Build API
принимает только `place_id` (ADR-0005, ADR-0007). Код оставлен в перечислении,
потому что его порождает эндпоинт подсказок и, позже, свободный текст.

---

### 3.3. `calculation/spec.py`

`ChartSpec` — полная спецификация расчёта и **источник восстановления** после
eviction кэша (ADR-0017, И-12). Хранится в сессии, ключ выводится из неё
чистой функцией.

```text
NatalChartSpec {
    technique:                    Literal["natal"]
    chart_kind:                   Literal["natal", "cosmogram"]
    include:                      tuple[str, ...]
    house_system:                 str          "P"
    rulership:                    str          "combined"
    near_interception_threshold:  float
}

ChartSpec = NatalChartSpec
```

Tagged union не вводится, пока у union нет второго члена: сохранённые спеки
уже несут `technique`, а `ChartSpec = NatalChartSpec` валидируется без явной
передачи тега. `TransitChartSpec` добавит union тогда, когда появится сама
транзитная спека.

**`include` по `chart_kind`** (проверяется движком, см. `_validate_chart_kind_include`):

```text
natal      → {positions, houses, rulers, aspects, configurations, strength}
cosmogram  → {positions, aspects, configurations}
```

`strength` в космограмме невозможен: в коде он требует `houses` (ADR-0008).

**`orb_profile` — решение, требующее подтверждения.** `calculate_natal()`
принимает `aspect_config`, `configuration_config` и `strength_config`.
Предлагается не класть их в спеку целиком, а хранить именованную ссылку;
сами значения остаются кодом и покрываются `CalculationVersion`, поэтому
правка орбисов инвалидирует кэш автоматически, а сессия не таскает
развёрнутые настройки. Если конфиги должны быть переопределяемы
пользователем — они обязаны лежать в спеке целиком, и это меняет решение.

**Чего в спеке нет.** `selena_method`, `orb_profile`, `ephemeris_flags`
и `body_ids` зафиксированы кодом или процессной конфигурацией и должны быть
покрыты `CalculationVersion`; `ephemeris_path` — runtime-конфигурация,
а содержимое файлов входит в `CalculationVersion` отпечатком. При появлении
выбора любой из этих величин переезжает в спеку.

---

### 3.4. `calculation/types.py`

```text
ChartArtifact {
    calculation_key:      str
    spec:                 ChartSpec
    calculation_version:  str
    chart_kind:           Literal["natal", "cosmogram"]
    chart:                NatalChart
    warnings:             tuple[CalculationWarning, ...]
}
```

В первой версии `house_system` принимает только каноническое значение `P`
(Плацидус). Поле остаётся частью спеки, сериализации и ключа как точка
расширения, но неподдерживаемый код отклоняется, а не молча заменяется на `P`.

`chart_kind` дублируется на верхнем уровне артефакта сознательно: И-8
запрещает выводить вид карты по отсутствию домов, и потребителю артефакта
не должно требоваться заглядывать внутрь `NatalChart`, чтобы узнать вид.

`warnings` поднимаются на уровень артефакта, потому что по И-7 они обязаны
дойти до промпта, а `InterpretationService` работает с артефактом, не
с внутренностями движка.

---

### 3.5. Контракты `session/`

```text
ChartRef {
    state_version: int            # >= 1
    spec:          ChartSpec
}

SessionState {
    session_id:      str
    birth_input:     BirthInput | None
    birth_resolved:  ResolvedBirthData | None
    state_version:   int
    base_chart:      ChartRef | None
    created_at:      datetime
    expires_at:      datetime
    hard_expires_at: datetime
}

StateDelta {
    birth_input:     BirthInput | None
    birth_resolved:  ResolvedBirthData | None
    base_chart_spec: ChartSpec | None
}

RESET_DELTA = StateDelta(None, None, None)

SessionSnapshot {
    state:  SessionState
    dialog: tuple[DialogTurn, ...]
}
```

Все модели frozen. У `SessionState` и `StateDelta` три nullable-поля либо
целиком пусты, либо целиком заполнены. `SessionSnapshot` — согласованный
persistence-снимок состояния и диалога, а не ещё одна сохраняемая сущность.
`SessionContext` и `current_build` не вводятся — см. §1.4 и §13.

`ChartRef` несёт версию, при которой карта получена. Инвариант
`chart.state_version == state.state_version` — единственная проверка
валидности ссылки (ADR-0014).

---

### 3.6. `application/commands.py` и `application/run_context.py`

```text
BuildNatalCommand {
    birth_input: BirthInput
}

RunContext {
    run_id:     UUID
    started_at: datetime
}
```

**Ни `session_id`, ни `run_id` в команду не входят.** Команда описывает
только пользовательское намерение. `session_id` разрешается API из
защищённой cookie и передаётся оркестратору отдельным доверенным контекстом;
значение из тела/query/path не принимается. `run_id` предметного смысла не несёт — две
одинаковые команды с разными `run_id` остаются одним намерением. Смешав
их, мы протащим телеметрию через сигнатуры всех будущих команд.

Раньше у `run_id` была вторая роль: он входил в `BuildAttempt` как часть
состояния попытки. После отказа от `BuildAttempt` (§1.4) эта роль исчезла,
и он остался чистым correlation identifier — **не** хэндлом возобновления
(ADR-0006, ADR-0012).

**Создаёт его транспорт, а не оркестратор.** ADR-0006 перечисляет
correlation scope, начиная с `API request`; резолв cookie, rate limit
и разбор тела запроса происходят до оркестратора, и именно там живут
отказы, которые труднее всего связать с прогоном. Поэтому `run_id`
рождается в Session Middleware и приходит в оркестратор отдельным
аргументом.

Формулировка ADR-0006 «создаёт **или** принимает» читается так: принимает
от транспорта в обычном режиме, создаёт сам, когда транспорта нет — CLI,
тест, будущая scheduled-операция.

---

## 4. Компоненты расчёта и артефактов

### 4.1. `CalculationVersion` — `calculation/version.py`

**Назначение.** Отпечаток версии расчёта, входящий в `calculation_key`.
Без него обновление эфемерид не инвалидирует кэш: golden-тесты останутся
зелёными, потому что считают заново, а пользователи будут видеть карты,
посчитанные по прежней базе (ADR-0017).

**Контракт.**

```text
compute_calculation_version(ephemeris_path: str) -> str
CALCULATION_VERSION: str        вычисляется один раз при старте
```

**Состав отпечатка.**

1. версия расчётного кода — явная константа `ENGINE_VERSION` в
   `engine/__init__.py`, поднимаемая при изменении алгоритма или дефолтных
   конфигов;
2. версия содержимого профилей, влияющих на числа расчёта:
   orb/aspect/configuration/strength;
3. `swisseph.version`;
4. имя и версия установленного дистрибутива, который предоставляет модуль
   `swisseph`;
5. sha256 нативного модуля `swisseph`, если путь к файлу доступен;
6. sha256 содержимого всех `*.se1` в каталоге эфемерид, имена отсортированы.

Если модуль `swisseph` невозможно однозначно сопоставить с установленным
дистрибутивом или найдено несколько дистрибутивов, претендующих на этот
модуль, приложение не стартует. Это ошибка окружения, а не runtime-выбор
«первого подходящего» биндинга.

**Чего не делает.** Не включает версию `tzdata`: она влияет на `utc_datetime`
внутри `ResolvedBirthData`, который уже входит в ключ (ADR-0017).

**Тесты.** Подмена байта в `.se1` меняет отпечаток; порядок файлов на
отпечаток не влияет; отсутствие каталога даёт явную ошибку, а не пустой
отпечаток.

---

### 4.2. `calculation_key` — `calculation/keys.py`

**Назначение.** Вывести воспроизводимый ключ кэша из спецификации.

**Контракт.**

```text
CalculationInput {
    utc_datetime: datetime   UTC, секунды; микросекунды отброшены
    latitude:     float      квантование до 6 знаков
    longitude:    float      квантование до 6 знаков
}

calculation_input_from(resolved: ResolvedBirthData) -> CalculationInput

calculation_key(
    calc_input: CalculationInput,
    spec:       ChartSpec,
    version:    str,
) -> str
```

Чистые функции без обращений к состоянию (ADR-0017). Реализация ключа —
sha256 канонической JSON-сериализации трёх аргументов с сортировкой ключей.

**Ключ строится из проекции, а не из всего `ResolvedBirthData`.**
Наивная сериализация объекта целиком затягивает в ключ `canonical_place`
и `warnings`, и это ломает кэш дважды:

```text
GeoNames переименовал «Москва» → «Moscow» при пересборке каталога
    → canonical_place изменился → ключ изменился
    → весь кэш этих карт промахивается
    → а CalculationVersion не менялся: расчёт тот же самый

обновление tzdata добавило pre_1970_offset_unverified
    → warnings изменились
    → та же карта получает вторую запись в кэше
```

Ни в одном случае числа не изменились. `CalculationVersion` — единственный
законный механизм инвалидации (§4.1) — оказывается обойдён сбоку.

Проекция живёт в `calculation/keys.py`, а не методом на `ResolvedBirthData`:
так зависимость направлена от `calculation` к `birth`, а не обратно.

**Ответственности.** Канонизация: одинаковые по смыслу входы обязаны давать
один ключ независимо от порядка полей и представления множеств. `include`
сериализуется отсортированным кортежем. Квантование координат и отбрасывание
микросекунд нужны не для красоты: без них ключ зависит от того, как
конкретная реализация сериализует `float`, а это изменится при выносе
расчёта в отдельный сервис.

**`tz_id` в ключ не входит.** Смещение уже применено, а два разных места
не дают одинаковых координат. Включив его, мы связали бы ключ с версией
`tzdata` через чёрный ход.

**Чего не делает.** Не обращается к кэшу, конфигурации, часам и окружению.

**Тесты.** Идемпотентность на повторном вызове; независимость от порядка
элементов `include`; различие ключа при изменении любого поля спеки, любого
поля `CalculationInput` и версии; **совпадение ключа при изменении
`canonical_place` или `warnings`** — прямая проверка того, что проекция
работает; **совпадение ключа, полученного
`BuildNatalHandler` и `NatalTool`** для одной и той же спеки — это проверка
инварианта «один deterministic path» (ADR-0002, ADR-0020).

---

### 4.3. `CalculationCache` — `calculation/cache.py`

**Назначение.** Кэш, а не репозиторий: любой объект можно удалить без потери
пользовательских данных, после eviction результат считается заново (ADR-0017,
И-12).

**Контракт.**

```text
class CalculationCache(Protocol):
    async def get(self, key: str) -> bytes | None: ...
    async def put(self, key: str, payload: bytes) -> None: ...

class InMemoryCalculationCache:
    def __init__(
        self,
        *,
        max_entries: int,
        ttl_seconds: float | None,
        clock: Callable[[], float] | None = None,
    ) -> None: ...
```

Именно `get` / `put`, а не `save` / `load` — семантика удаляемости должна быть
видна в коде (ADR-0017).

**Ответственности.** Хранение opaque bytes, вытеснение, TTL. Формат payload
принадлежит artifact-слою: `calculation/codec.py` кодирует `ChartArtifact` как
gzip-6 от UTF-8 JSON и выполняет обратную валидацию на чтение. TTL кэша расчётов
**не обязан** превосходить TTL сессии: промах восстановим.

`cache_timeout_ms` — параметр будущего Redis-адаптера или `CacheSettings`.
Для in-memory реализации это no-op и не входит в конструктор.

**Чего не делает.** Не знает, чей это результат; не участвует в решении
об актуальности; не является пользовательским состоянием — даже устаревший
для конкретной сессии, но корректный payload артефакта класть в кэш можно.

**Тесты.** Промах на пустом кэше; попадание после `put`; вытеснение по
`max_entries`; истечение TTL; полная очистка не ломает последующий расчёт;
кэш не импортирует `ChartArtifact`.

---

### 4.4. `CalculationEnginePort` и `EngineService` — `calculation/engine.py`

**Назначение.** Прикладная граница расчётного ядра и единственное место
в системе, знающее сигнатуру `calculate_natal()`.

**Контракт.**

```text
CalculationResult {
    chart_kind: Literal["natal", "cosmogram"]
    chart:      NatalChart
    warnings:   tuple[CalculationWarning, ...]
}

class CalculationEnginePort(Protocol):
    async def calculate(
        self,
        spec:     ChartSpec,
        resolved: ResolvedBirthData,
        *,
        run:      RunContext,
    ) -> CalculationResult: ...

class EngineService(CalculationEnginePort):
    def __init__(
        self,
        *,
        executor: ThreadPoolExecutor,
        techniques: Mapping[str, TechniqueAdapter],
        slow_threshold_ms: float,
    ) -> None: ...
```

**Ответственности.**

1. Маппинг `ChartSpec + ResolvedBirthData` в аргументы `calculate_natal()`:

```text
birth_datetime               ← resolved.utc_datetime
latitude, longitude          ← resolved.latitude, resolved.longitude
chart_kind                   ← spec.chart_kind
house_system                 ← spec.house_system
rulership                    ← spec.rulership
include                      ← set(spec.include)
near_interception_threshold  ← spec.near_interception_threshold
```

`house_system` проходит натальную P-only валидацию до executor. Подделанная
спека с другим кодом даёт `ChartCalculationError(SPEC_INVALID)` и не доходит
до Swiss Ephemeris.

Текущий `ChartSpec` ещё не содержит `selena_method` и `orb_profile`, поэтому
полный mapping §10 `chart_artifacts.md` выполняется только для уже
spec-owned полей. Остальные параметры остаются известным долгом до появления
`CalculationVersion` и расширения спеки.

2. Исполнение вне event loop:

```text
await loop.run_in_executor(executor, _calculate_sync, ...)

def _calculate_sync(...):
    return adapter.calculate(...)
```

`ephemeris_session()` — process-wide `threading.RLock`. Захват из корутины
напрямую заблокировал бы event loop на всё время расчёта; `async def` сам
по себе CPU-bound работу не распараллеливает (ADR-0012).

**Корреляция в поток не проходит сама.** `loop.run_in_executor` контекст
не копирует, в отличие от `asyncio.to_thread`. Если `run_id` жил бы
в `contextvars`, записи журнала из расчётного потока остались бы без него
ровно там, где он нужнее всего — при разборе медленного или упавшего
расчёта. Поэтому `run_id` передаётся явным аргументом, а не через контекст.

3. Возврат `CalculationResult`, а не `ChartArtifact`. Движок не знает
   `calculation_key` и `calculation_version`; эти поля добавляет
   `ChartArtifactResolver`.

4. Проверка инварианта результата:
   `result.chart_kind == spec.chart_kind` и
   `result.chart.chart_kind == spec.chart_kind`. Несовпадение — дефект
   адаптера, оно приводится к `ChartCalculationError(ENGINE_UNEXPECTED)`
   и не попадает в кэш.

5. Приведение `ValueError` движка к `ChartCalculationError` с кодом. Пример
   из кода: Placidus вырождается на высоких широтах и `calculate_houses`
   поднимает `ValueError` — это `ChartCalculationError(HOUSES_DEGENERATE)`,
   а не `InputRequired`.

**Чего не делает.** Не обращается к кэшу; не знает про сессию; не строит
ключ; не собирает `ChartArtifact`; не решает, нужен ли расчёт вообще.

**Тесты.** Маппинг всех текущих полей спеки; космограмма получает суженный `include`;
расчёт не выполняется в event loop (проверка по идентификатору потока);
вырожденный Placidus даёт `ChartCalculationError(HOUSES_DEGENERATE)`;
адаптер возвращает `CalculationResult`, а не `ChartArtifact`.

---

### 4.5. `ChartArtifactResolver` — `calculation/artifacts.py`

**Назначение.** `get → miss → calculate → put`. Единственная точка, через
которую артефакт получают и UI-driven, и agent-driven пути (ADR-0002,
ADR-0020).

**Контракт.**

```text
class ChartArtifactResolver:
    def __init__(
        self,
        *,
        cache:   CalculationCache,
        engine:  CalculationEnginePort,
        version: str,
    ) -> None: ...

    async def ensure_chart(
        self,
        spec:     ChartSpec,
        resolved: ResolvedBirthData,
        *,
        run:      RunContext,
    ) -> ChartArtifact: ...
```

Скелет без веток отказов, stale/corrupt и single-flight:

```text
key = calculation_key(calculation_input_from(resolved), spec, self.version)
payload = await self.cache.get(key)
if payload is not None:
    return decode_chart_artifact(payload)
result = await self.engine.calculate(spec, resolved, run=run)
artifact = ChartArtifact(
    calculation_key=key,
    calculation_version=self.version,
    spec=spec,
    chart_kind=result.chart_kind,
    chart=result.chart,
    warnings=result.warnings,
)
await self.cache.put(key, encode_chart_artifact(artifact))
return artifact
```

**Чего не делает.** Не пишет в сессию; не знает `state_version`; не решает,
становится ли карта активной.

**Тесты.** Промах вызывает движок ровно один раз; попадание не вызывает его
вовсе; смена `CALCULATION_VERSION` даёт промах на тех же данных; два вызова
с одинаковыми аргументами возвращают равные артефакты; нечитаемые байты
отбрасываются как corrupt cache и ведут к новому расчёту.

---

### 4.6. Предупреждения космограммы — `engine/charts/natal.py`

**Пробел в текущем коде.** ADR-0008 требует предупреждений «при смене знака
Луны или знака либо директности быстрого объекта в пределах суток» и прямо
запрещает обобщённую формулировку. В коде `CalculationWarning` сегодня
порождается только блоком `include` и Swiss Ephemeris; проверки суточной
неоднозначности нет.

**Что реализовать.** При `chart_kind == "cosmogram"` — расчёт положений на
границах суток и сравнение: смена знака Луны; смена знака или направления
движения быстрого объекта. Предупреждение несёт проверяемый код и конкретный
объект, а не общую фразу.

Место — движок, а не handler: это предметное знание, и по И-8 вид карты
проставляет движок.

**Тесты.** Дата с известным переходом Луны через знак даёт предупреждение
с кодом и именем объекта; дата без переходов не даёт ни одного; натальная
карта не порождает эти предупреждения вовсе.

---

## 5. Компоненты резолва

### 5.1. `PlaceCatalog` — `birth/places.py`

**Назначение.** Превратить `place_id` в канонические координаты и `tz_id`.
Единственное место, знающее «локально или по сети» (И-5, ADR-0005).

**Контракт.**

```text
PlaceResolution = Resolved | NotFound

Resolved {
    place_id:        str
    canonical_name:  str
    latitude:        float
    longitude:       float
    tz_id:           str
}

class PlaceCatalog(Protocol):
    async def lookup(self, place_id: str) -> PlaceResolution: ...

class LocalPlaceCatalog(PlaceCatalog):
    @classmethod
    def from_file(cls, path: str) -> "LocalPlaceCatalog": ...
```

**`Candidates` в этот контракт не входит.** На build-пути `AMBIGUOUS`
не возникает: Build API принимает только `place_id` (ADR-0005). Поиск по
строке с несколькими кандидатами — отдельная операция эндпоинта подсказок,
и её контракт определяется вместе с ней.

**Ответственности.** Загрузка каталога на старте, дальше только чтение (И-9).
Проверка недоверенного `place_id`: неизвестный идентификатор — `NotFound`,
который резолвер превращает в `InputRequired` с кодом `INVALID`.

**Чего не делает.** Не подбирает ближайший город: координат исходного места
у системы нет, выбор делает человек в форме (ADR-0005).

**Тесты.** Известный `place_id` резолвится; неизвестный даёт `NotFound`;
каталог не перечитывается после старта.

---

### 5.2. `resolve_historical_tz` — `birth/tz.py`

**Назначение.** Историческое смещение на конкретную дату. Наивное «GMT+3»
для Москвы 02.09.1990 даёт ошибку в час и сдвинутый Ascendant (ADR-0005).

**Контракт.**

```text
TzResolution = TzOk | TzNonexistent | TzAmbiguous

TzOk          { utc_datetime: datetime, utc_offset_seconds: int,
                warnings: tuple[ResolutionWarning, ...] }
TzNonexistent { local_datetime, tz_id, normalized }       весенний переход
TzAmbiguous   { local_datetime, tz_id,
                offsets: tuple[int, int] }                осенний переход

resolve_historical_tz(
    local_datetime: datetime,
    tz_id:          str,
) -> TzResolution

resolve_anomaly(
    anomaly: TzNonexistent | TzAmbiguous,
) -> TzOk
```

**Почему на входе `tz_id`, а не координаты.** Это две разные задачи:

```text
координаты → tz_id              географическая: в каком полигоне точка;
                                требует данных границ зон

(локальное время, tz_id) → UTC  временная: какие правила действовали;
                                требует базы IANA — это stdlib
```

Приняв координаты, функция потребовала бы данных границ — ровно ту
зависимость, которая не нужна, потому что каталог отдаёт `tz_id` готовым.
`PlaceCatalog` владеет «где», включая зону; эта функция — «когда».

**Почему на входе готовый `datetime`, а не дата и опциональное время.**
Подстановка полудня при неизвестном времени — предметная конвенция
ADR-0008, и вторая половина того же решения (`time_unknown = true`)
принимается в резолвере. Разнеся их по модулям, мы получили бы половину
решения во временной функции. Кроме того, для момента транзита неизвестного
времени не бывает, и функция должна переиспользоваться там без оговорок.

**Ответственности.** Три явных исхода. Несуществующее и удвоенное локальное
время **не исправляется молча** (ADR-0005) и не поднимает исключение.

**Порядок проверок обязателен: несуществующее время первым.**

По PEP 495 для несуществующего времени `fold=0` и `fold=1` тоже дают разные
`utcoffset()` — ровно как для удвоенного. Проверка «смещения различаются»,
выполненная первой, классифицирует несуществующее время как удвоенное.

```python
tz = ZoneInfo(tz_id)
dt = local_datetime.replace(tzinfo=tz)

# 1. несуществующее — round-trip через UTC не возвращает то же локальное время
back = dt.astimezone(timezone.utc).astimezone(tz).replace(tzinfo=None)
if back != local_datetime:
    return TzNonexistent(local_datetime, tz_id, normalized=back)

# 2. удвоенное — round-trip корректен, но смещения при fold 0/1 различаются
o0 = dt.replace(fold=0).utcoffset()
o1 = dt.replace(fold=1).utcoffset()
if o0 != o1:
    return TzAmbiguous(
        local_datetime=local_datetime,
        tz_id=tz_id,
        offsets=(int(o0.total_seconds()), int(o1.total_seconds())),
    )

return TzOk(...)
```

Проверено на `zoneinfo`: `2011-03-27 02:30 Europe/Moscow` при обратном
порядке даёт `AMBIGUOUS`, при правильном — `NONEXISTENT` с нормализацией
в 03:30.

**Аномалия зоны: виновато время или дата.**

Подставленный полдень может оказаться несуществующим — это не гипотеза.
Перебор всех зон IANA по точкам переходов TZif за 1900–2030 даёт
семнадцать случаев, и они распадаются надвое:

```text
дата цела, пропущен только час — 12 зон
  Africa/Casablanca 1967-06-03 → 13:00
  America/Havana    1925-07-19 → 12:29

дня не существовало вовсе — 5 зон
  Pacific/Apia       2011-12-30 → 2011-12-31
  Pacific/Kiritimati 1994-12-31 → 1995-01-01
  Pacific/Kwajalein  1993-08-21 → 1993-08-22
```

**Первый случай — подставленный полдень**, и здесь `InputRequired
{ birth.time }` был бы просьбой исправить поле, которое человек намеренно
оставил пустым и исправить не может (ADR-0008: «вопрос о времени
не задаётся»).

```text
time_unknown = false → TzNonexistent / TzAmbiguous → InputRequired
time_unknown = true  → детерминированное разрешение + warning
                       несуществующее → нормализованный момент,
                                        код noon_anchor_adjusted
                       удвоенное      → fold = 0,
                                        код noon_anchor_ambiguous
```

Кодов два, потому что причины разные и рецепту нужно их различать:
удвоение способно развести кандидатов почти на сутки
(`Pacific/Kwajalein 1969-09-30`: `+11:00` и `−12:00`, Луна уходит
из Тельца в Близнецы), а сдвиг якоря на минуты — нет.

**Второй случай — дата, и она ввод пользователя**, а не подставленное
системой значение. Исход `InputRequired { birth.date, INVALID }`
и при неизвестном времени, и при явно указанном: если дня не существовало,
подбирать время бессмысленно.

Детекция — **не** сравнение `normalized.date()` с исходной датой: переходы
в 23:00 нормализуются за полночь при полностью существующем дне,
и таких случаев 1135. Критерий — ненулевая длительность суток:

```python
def local_date_exists(local_date: date, tz_id: str) -> bool:
    start = _utc_of_local_midnight(local_date, tz_id)
    end   = _utc_of_local_midnight(local_date + timedelta(days=1), tz_id)
    return end > start
```

Общее правило шире обоих случаев: **система не спрашивает пользователя
о значении, которое подставила сама, и не переписывает значение, которое
пользователь назвал.**

`resolve_anomaly` остаётся в `birth/tz.py`, чтобы timezone-арифметика
не растекалась в резолвер. Для `TzNonexistent` она повторно резолвит
`normalized`; для `TzAmbiguous` строит момент с `fold = 0`. Коды
предупреждений функция не ставит: причина подстановки известна только
вызывающему резолверу.

**Гарантия tzdb действует только с 1970 года.** `tz_id` из каталога — имя
зоны сегодня, а IANA формирует зоны по согласию часов **после**
1970-01-01 и об остальном высказывается прямо:

> If all clocks in a region have agreed since 1970, give them just one name
> even if some of the clocks disagreed before 1970…
>
> many, perhaps most, of the `tz` database's pre-1970 and future timestamps
> are either wrong or misleading.

Для астрологического продукта это штатный случай, а не экзотика: заметная
доля пользователей родилась раньше. Ошибка в час — сдвиг Ascendant примерно
на пятнадцать градусов, то есть с хорошей вероятностью другой знак
на асценденте и другая интерпретация. Профессиональные астропрограммы
держат для этого отдельные атласы исторических смещений именно потому,
что tzdb такой задачи не решает.

Решение: если **`utc_datetime`** раньше `1970-01-01 00:00 UTC`, исход
`TzOk` несёт предупреждение с кодом `pre_1970_offset_unverified`.
Сравнивается именно момент UTC, а не локальное время: гарантия IANA
сформулирована относительно POSIX epoch. Локальные 02:00 первого января
1970 года в Москве — это 23:00 предыдущего дня по UTC, и предупреждение
там нужно. Оно попадает
в `ResolvedBirthData.warnings` и по И-7 доходит до промпта; рецепт смягчает
утверждения об углах.

Собственный атлас исторических смещений остаётся отдельным решением
(§12); условие возврата — жалобы пользователей на асцендент.

**Чего не делает.** Не решает, что показать пользователю: перевод
`TzNonexistent` и `TzAmbiguous` в `InputRequired` — работа резолвера.
Не подставляет полдень и не знает про `time_unknown`.

**Тесты.** Полный набор контрольных значений — в
`exact-orb_birth_data_resolution.md` §6.5. Обязательный минимум:

```text
1990-09-02 14:30 Europe/Moscow → OK, UTC+4        контроль ADR-0005
1991-12-20 16:25 Europe/Moscow → OK, UTC+2        окно отмены декретного
2013-12-20 16:25 Europe/Moscow → OK, UTC+4        зима на постоянном летнем
2011-03-27 02:30 Europe/Moscow → NONEXISTENT      не AMBIGUOUS
2014-10-26 01:30 Europe/Moscow → AMBIGUOUS
2011-12-30 12:00 Pacific/Apia  → NONEXISTENT      подставленный полдень
1955-03-10 12:00 Europe/Moscow → OK + pre_1970_offset_unverified
```

Две московские аномалии — окно UTC+2 с 29.09.1991 по 18.01.1992
и постоянное UTC+4 с 27.03.2011 по 25.10.2014 — ломают наивное «зимой
UTC+3, летом UTC+4» и потому обязательны.

`tzdata` объявлена зависимостью явно: на Windows `zoneinfo` без неё пуст.

---

### 5.3. `BirthDataResolver` — `birth/resolver.py`

**Назначение.** Вход — примитивная структура формы, выход — расчётные
параметры либо типизированный исход. Вызывается **до** planning и calculation
(ADR-0005).

**Контракт.**

```text
class BirthDataResolver:
    def __init__(
        self,
        *,
        places:         PlaceCatalog,
        min_birth_date: date,
        max_birth_date: date,
        today_provider: Callable[[], date] = date.today,
    ) -> None: ...

    async def resolve(
        self,
        birth_input: BirthInput,
    ) -> ResolvedBirthData | InputRequired | ResolutionUnavailable: ...
```

**Диапазон дат — параметр конструктора, а не константа модуля.** Он
определяется составом `ephe/*.se1`, то есть конфигурацией развёртывания.
Замер на текущем наборе файлов:

```text
1799  RuntimeError на Хироне
1800 … 2399  OK, без предупреждений
2400  OK, но всё посчитано по Moshier — 12 предупреждений
2500  RuntimeError
```

Поведение на границе несогласованное: часть тел молча деградирует
до меньшей точности, Хирон поднимает исключение. Ни то, ни другое
не является внятным «дата вне поддерживаемого диапазона», поэтому проверка
идёт до расчёта.

**Ответственности.**

Если `min_birth_date > max_birth_date`, конструктор поднимает `ValueError`:
это ошибка конфигурации приложения и должна падать на старте.

1. Проверка `birth_date` по диапазону `[min_birth_date, effective_max]`,
   где `effective_max = min(max_birth_date, today_provider())` →
   `InputRequired` с `field = "birth.date"`, `code = UNSUPPORTED`
   и `constraints = { min, max }`.
2. Проверка `place_id` по каталогу; `NotFound` → `InputRequired`
   с `field = "birth.place"`, `code = INVALID`.
3. `time_unknown = (birth_input.birth_time is None)` и **подстановка
   местного полудня** при неизвестном времени — обе половины одного
   решения ADR-0008 принимаются здесь, рядом друг с другом.
4. Историческое время: `resolve_historical_tz(local_datetime, tz_id)`.
   При `TzNonexistent` **сначала** проверяется
   `local_date_exists(birth_date, tz_id)`: если дня не существовало,
   исход — `InputRequired { birth.date, INVALID }` независимо
   от `time_unknown`. Иначе при `time_unknown = false` —
   `InputRequired` с `field = "birth.time"`, при `time_unknown = true` —
   детерминированное разрешение с warning (§5.2).
5. Сбор `warnings`.

**Issues накапливаются, а не возвращается первая.** `InputRequired.issues` —
список (ADR-0007): неверные дата и место должны подсветиться за один заход.
Границу накопления задаёт зависимость данных — время резолвится только
после места, потому что нужен `tz_id`. Отсюда два этапа: шаги 1–2 вместе,
затем 3–4.

**Чего не делает.** **В состояние не пишет** — результат обрабатывает
вызвавший handler (ADR-0005). Не выбирает `chart_kind`: это решение handler'а,
см. §7.1. Не превращает недоступность зависимости в `InputRequired`.

**Тесты.** Полный тест-пак из двадцати сценариев —
`exact-orb_birth_data_resolution.md` §6. Базовые: валидный ввод даёт полный `ResolvedBirthData`; неизвестный
`place_id` — `InputRequired(INVALID)`; отказ каталога — `ResolutionUnavailable`
с `retryable = true`, и **не** `InputRequired`; пустое время даёт
`time_unknown = true` и заполненный `utc_datetime`; резолвер не обращается
ни к `SessionStore`, ни к кэшу.

---

## 6. Компоненты сессии

### 6.1. `SessionStore` — `session/store.py`

**Назначение.** Read-only lookup и атомарный CAS состояния с TTL.
Долговременного `Profile DB` нет (ADR-0009).

**Контракт.**

```text
@runtime_checkable
class SessionStore(Protocol):
    async def create(
        self, session_id: str, *, now: datetime
    ) -> SessionCreated | SessionIdConflict: ...
    async def get(
        self, session_id: str, *, now: datetime
    ) -> SessionState | SessionAbsent: ...
    async def compare_and_set(
        self,
        session_id: str,
        expected_state_version: int,
        delta: StateDelta,
        *,
        now: datetime,
    ) -> int | VersionConflict | SessionAbsent: ...
```

`create` принимает только свежий серверный идентификатор и `now`, сам строит
начальное состояние и считает коллизией любую уже имеющуюся, включая
истёкшую, строку; никогда её не читает и не перезаписывает. `get` read-only:
он различает `SessionAbsent` и infrastructure failure, но не продлевает TTL.

**Версию присваивает store, а не handler** (ADR-0014): store принимает
дельту, применяет её к актуальному состоянию, инкрементирует версию и пишет
результат одной атомарной операцией. Готовый state-кандидат порт не принимает.
Устаревшая версия возвращает `VersionConflict(actual)`, отсутствие —
`SessionAbsent`, а инфраструктурный отказ — типизированный
`StateReadError`/`StateWriteError` с безопасным `error_code`.

**Чего не делает.** Не знает, что такое карта; не принимает предметных
решений; не генерирует `session_id`; не продлевает TTL на `get`; не логирует
содержимое состояния (И-14).

**Тесты.** CAS с верной версией проходит и возвращает новую; с устаревшей —
`VersionConflict(actual)` и состояние не изменено; два конкурентных CAS с
одной `expected_state_version` — ровно один commit; `get` не меняет сроки;
коллизия create не раскрывает и не заменяет существующую сессию.

---

### 6.2. `DialogStore` и `SessionPersistence`

`DialogStore` атомарно append/read/clear отдельной записи диалога. Его
`read` read-only; `append` применяет пределы 50 ходов, 8 000 символов на ход
и 120 000 суммарно. Усечение выставляет `truncated=True`, не меняя
`status="complete" | "partial"`. Успешные append и clear являются
write-and-renew: append продлевает parent state и dialog одним deadline,
clear продлевает state и удаляет dialog. Предметные поля state и
`state_version` при этом не меняются.

```text
@runtime_checkable
class DialogStore(Protocol):
    async def append(
        self, session_id: str, turn: DialogTurn, *, now: datetime
    ) -> None | SessionAbsent: ...
    async def read(
        self, session_id: str, *, now: datetime
    ) -> tuple[DialogTurn, ...] | SessionAbsent: ...
    async def clear(
        self, session_id: str, *, now: datetime
    ) -> None | SessionAbsent: ...
```

Операции, затрагивающие обе записи, принадлежат агрегату:

```text
@runtime_checkable
class SessionPersistence(Protocol):
    sessions: SessionStore
    dialogs:  DialogStore

    async def touch(
        self, session_id: str, *, now: datetime
    ) -> SessionSnapshot | SessionAbsent: ...
    async def reset(
        self, session_id: str, expected_state_version: int, *, now: datetime
    ) -> int | VersionConflict | SessionAbsent: ...
    async def delete(self, session_id: str) -> None: ...
```

`touch` одним `now` продлевает состояние и существующую запись диалога и
возвращает frozen `SessionSnapshot`. `reset` не является вторым алгоритмом:
семантически это
`sessions.compare_and_set(session_id, expected_state_version, RESET_DELTA,
now=now)`: агрегат только делегирует, а RESET_DELTA-aware facet CAS сам
очищает диалог внутри своей backend-секции. `delete`
атомарно удаляет обе записи. Реализация через два независимых вызова фасетов
недопустима.

Поддерживаемая InMemory-композиция — `InMemorySessionPersistence()`, который
владеет одним private backend и одним `asyncio.Lock`, а стабильные
`.sessions`/`.dialogs` создаёт над ними. Отдельные facet требуют backend
позиционно и не создаются без аргумента. Parent `SessionState` — единственный
источник liveness; private dialog deadline в P2 хранится, но не читается.

Все now-bearing методы InMemory и будущего SQLite используют публичный
контракт `exact_orb.session.require_utc`. Внутренний wrapper
`session/adapters/_time.py` фиксирует для портов имя `now`, не импортируя
приватные имена контрактных модулей. Контракт требует aware UTC offset `0` и
возвращает `ValueError` до lookup/mutation; скрытых clock/config источников у
адаптеров нет.

Чистые `new_session`, `apply_delta`, `touched`, `is_expired` и
`matches_intent` живут в `session/state.py`; отдельный `ProfileService` не
вводится. Store вызывает переход внутри атомарной CAS-секции.

---

### 6.3. `ContextService` — `session/context.py`

**Назначение.** Единственная application-граница блока сессий и владелец
семантической классификации persistence outcomes (ADR-0009, ADR-0014).

`ContextService` получает `SessionPersistence`. Restore/load использует
агрегатный `touch`, а не отдельные `get`, `read` и `touch`. Сохранение
передаёт `SessionStore.compare_and_set` исходные `delta` и
`expected_state_version` без построения кандидата.

```text
int
    -> Committed(state_version)
VersionConflict(actual) + matches_intent(actual, delta)
    -> AlreadyApplied(actual.state_version)
VersionConflict(actual) + not matches_intent(actual, delta)
    -> Superseded(actual)
SessionAbsent
    -> SessionAbsent
StateReadError(error_code)
    -> StateReadFailed(error_code)
StateWriteError(error_code)
    -> StateCommitFailed(error_code)
```

`matches_intent` сравнивает `birth_resolved` и `base_chart_spec`, но не
текстовую форму `birth_input`. `actual` берётся только из атомарного
`VersionConflict`: сервис не делает дополнительный `get`, не выполняет
скрытый retry и не перебазирует операцию на свежую версию.

Полный reset идёт только через `SessionPersistence.reset`; delete — только
через `SessionPersistence.delete`. `scope="dialog"` использует
`DialogStore.clear` и не меняет `state_version`.

**Acceptance P3.** Проверяются все строки таблицы выше, отсутствие второго
чтения после конфликта, сохранение original expected при повторе N7/N8,
точное сохранение `error_code`, а также то, что reset-all и delete вызывают
ровно агрегатные методы.

---

## 7. Координация

### 7.1. `BuildNatalHandler` — `application/handlers/build_natal.py`

**Назначение.** Владеет своим use case: провести операцию от `BirthInput`
до `StateDelta` (ADR-0006).

**Контракт.**

```text
class BuildNatalHandler:
    def __init__(
        self,
        *,
        resolver:  BirthDataResolver,
        artifacts: ChartArtifactResolver,
    ) -> None: ...

    async def handle(
        self,
        command: BuildNatalCommand,
        state:   SessionState,
        run:     RunContext,
    ) -> BuildNatalOutcome: ...
```

где

```text
BuildNatalOutcome =
      BuildNatalSuccess { artifact: ChartArtifact, delta: StateDelta }
    | InputRequired
    | ResolutionUnavailable
    | CalculationFailed
```

**Ответственности.**

1. Резолв данных рождения; `InputRequired` и `ResolutionUnavailable`
   поднимаются наверх **без искажения типа**.

2. **Построение `ChartSpec` — единственное предметное решение на этом пути:**

```text
chart_kind = "cosmogram" if resolved.time_unknown else "natal"

include = {"positions", "aspects", "configurations"}
          if chart_kind == "cosmogram"
          else DEFAULT_INCLUDE
```

Правило из ADR-0008: `дата + место + время → natal`,
`дата + место → cosmogram`. Вопрос о времени не задаётся, пустое поле —
это явное `time_unknown`, а не пробел, требующий уточнения.

3. `artifacts.ensure_chart(spec, resolved, run)`.

4. Сформировать all-set `StateDelta`; новую версию handler не вычисляет.

**Чего не делает.** Не сохраняет состояние сам — возвращает дельту,
сохраняет оркестратор (ADR-0006). Не знает про `run_id` больше, чем нужно
для лога. Не вызывает `Agent Runtime`, `Planner`, `ToolExecutor` и LLM
(ADR-0012, ADR-0020).

**Тесты.** Известное время даёт `chart_kind = natal` и полный `include`;
пустое — `cosmogram` и суженный `include`, без вопроса пользователю;
`InputRequired` от резолвера доходит наверх тем же типом; `ResolutionUnavailable`
не превращается в `InputRequired`; handler не обращается к `SessionStore`
напрямую.

---

### 7.2. `ApplicationOrchestrator` — `application/orchestrator.py`

**Назначение.** Единая точка координации application-flow после транспортного
слоя (ADR-0006).

**Контракт.**

```text
class ApplicationOrchestrator:
    def __init__(
        self,
        *,
        context:  ContextService,
        handlers: Mapping[type[Command], Handler],
    ) -> None: ...

    async def handle(
        self,
        command: Command,
        *,
        session_id: str,
        run:     RunContext | None = None,
    ) -> ApplicationResult: ...
```

**Последовательность.**

```text
1. transport разрешает `session_id` только из cookie; при отсутствии cookie
   генерирует свежий ID и insert-only создаёт сессию
2. run = run or RunContext.new()   — транспорт даёт свой, CLI и тест не дают
3. context.load(session_id) → `SessionSnapshot` либо типизированный отказ
4. сохранить original `expected_state_version` из snapshot
5. выбрать handler по типу команды — словарь, не LLM
6. await handler.handle(command, snapshot.state, run)
7. успех → context.save(session_id, original expected, delta)
8. классифицировать исход и вернуть ApplicationResult
```

**Ответственности.**

- lifecycle операции целиком, включая commit: **успешный расчёт ≠ успешная
  пользовательская операция** (ADR-0006). Success не возвращается до
  подтверждённого state transition;
- маршрутизация по типу команды;
- `run_id` как единый correlation scope: API → handler → engine → cache →
  commit → response;
- приведение исходов компонентов к application-контракту, **без** превращения
  инфраструктурной ошибки в пользовательскую.

**Чего не делает.** Не рассчитывает карту; не содержит астрологической
логики; не знает Swiss Ephemeris; не строит промпты; не вызывает LLM;
не выбирает agent tools и не знает их зависимостей; не мутирует
`SessionState` самостоятельно; не принимает policy-решений. При retry после
неподтверждённого commit повторяет original expected и не выполняет rebase
на свежую версию.

> **Инвариант.** Application Orchestrator знает handlers, но не знает
> topology agent tools (ADR-0006, ADR-0020).

**Тесты.** Отказ `ContextService.save` даёт неуспешную операцию, хотя расчёт
прошёл; устаревшая версия даёт `Superseded`, а не `Success` с неактивной
картой; `run_id` присутствует во всех записях журнала операции; оркестратор
не импортирует ничего из `agent/`, `tools/` и `engine/charts` — проверяется
тестом на импорты.

---

### 7.3. Результат — `application/results.py`

```text
ApplicationResult =
      Success { chart: ChartArtifact, state_version: int }
    | InputRequired
    | AlreadyApplied
    | Superseded
    | SessionAbsent
    | StateReadFailed
    | ResolutionUnavailable
    | CalculationFailed
    | StateCommitFailed
```

Отброшенный результат возвращается клиенту явно как `Superseded`, а не
молчаливым успехом с неактивной картой (ADR-0014).

---

## 8. Сведение с agent-слоем

### 8.1. Переименование `orchestration/` → `agent/`

Текущий `orchestration/orchestrator.py` конструируется из `planner`,
`tool_registry`, `data_selector`, `prompt_builder`, `llm_complete` — по составу
зависимостей это `Agent Runtime`, а называется `Orchestrator`. После ADR-0006
и ADR-0020 это прямое расхождение кода с решениями.

```text
orchestration/orchestrator.py  →  agent/runtime.py      Orchestrator → AgentRuntime
orchestration/types.py         →  agent/types.py
```

Правятся `tests/test_agent_skeleton.py` и импорты.

### 8.2. `NatalTool` на общий расчётный путь

Сегодня `tools/natal_tool.py` вызывает `calculate_natal()` напрямую, минуя
`ChartArtifactResolver`. Это нарушает «один deterministic calculation path»
(ADR-0002, ADR-0020): agent-путь считает мимо кэша и мимо `ChartSpec`.

```text
NatalTool
    → ChartArtifactResolver
    → EngineService
```

`NatalToolArgs` перестаёт быть набором аргументов движка и становится
источником `ChartSpec`. Интерфейс `Tool.run` при этом делается
асинхронным — ADR-0002 называет это единственным необратимым решением.

**Тест инварианта.** Одна и та же спека, полученная через `BuildNatalHandler`
и через `NatalTool`, даёт **один `calculation_key`** и попадание в кэш
на втором вызове.

---

## 9. Инфраструктура

### 9.1. `bootstrap.py`

Одна функция композиции вместо DI-фреймворка:

```text
def build_application(settings) -> ApplicationOrchestrator:
    version   = compute_calculation_version(settings.ephemeris_path)
    cache     = InMemoryCalculationCache(...)
    executor  = ThreadPoolExecutor(max_workers=settings.calc_workers)
    engine    = EngineService(
        executor=executor,
        techniques={"natal": NatalTechniqueAdapter()},
        slow_threshold_ms=settings.calc_slow_threshold_ms,
    )
    artifacts = ChartArtifactResolver(cache=cache, engine=engine, version=version)
    places    = LocalPlaceCatalog.from_file(settings.place_catalog_path)
    resolver  = BirthDataResolver(places=places)
    store     = InMemorySessionStore(ttl_seconds=settings.session_ttl)
    context   = ContextService(store=store)
    handlers  = {BuildNatalCommand: BuildNatalHandler(resolver=resolver,
                                                      artifacts=artifacts)}
    return ApplicationOrchestrator(context=context, handlers=handlers)
```

Реестры наполняются здесь и дальше только читаются (И-9).

### 9.2. Зависимости

```text
pytest-asyncio     тесты async-компонентов
tzdata             на Windows zoneinfo без неё пуст
```

### 9.3. Бенчмарк как проверяемое требование

ADR-0012 требует до перехода к background execution измерить `p50/p95/p99`
для `calculate_natal`, `calculate_cosmogram` и `calculate_transit`, а также
поведение при 1, 2, 5 и 10 одновременных расчётах, с отдельным вниманием
к глобальному состоянию `pyswisseph`.

Первые две строки замерены (§1.4), транзит и конкурентность — нет.
Замер конкурентности заодно подтверждает, что `ephemeris_session()`
действительно сериализует вызовы, а пул потоков не даёт ложного ощущения
параллельности.

---

## 10. Проверяемые инварианты пути

| № | Инвариант | Где проверяется |
|---|---|---|
| B-1 | Техническая ошибка никогда не становится `InputRequired` | resolver, engine, store |
| B-2 | Успех возвращается только после подтверждённого commit | orchestrator |
| B-3 | Устаревший результат не меняет состояние; равное намерение даёт `AlreadyApplied`, иное — `Superseded` | store CAS + ContextService |
| B-4 | `chart_kind` — явное поле, не выводится по отсутствию домов (И-8) | spec, artifact, DTO |
| B-5 | Один `calculation_key` для UI-пути и agent-пути (ADR-0002) | тест на два пути |
| B-6 | Ключ восстановим из `ChartSpec` и `ResolvedBirthData` (И-12) | keys |
| B-7 | Обновление эфемерид инвалидирует кэш | version + artifacts |
| B-8 | Build-путь не импортирует `agent/`, `tools/`, `intent/` | тест на импорты |
| B-9 | Предупреждения расчёта доходят до артефакта (И-7) | engine, artifact |
| B-10 | Персональные данные не попадают в журнал (И-14) | logging |
| B-11 | N7 и N8 повторяют CAS с original expected; скрытого rebase нет | ContextService + application |

B-8 в виде теста на импорты стоит дёшево и ловит самое вероятное нарушение
разделения двух уровней оркестрации.

---

## 11. Порядок реализации

Снизу вверх, чтобы каждый этап тестировался на настоящем предыдущем,
а не на моке.

| Этап | Состав | Готовность |
|---|---|---|
| Э0 | контракты §3, ревизия ADR, зависимости | типы импортируются, тестов поведения нет |
| Э1 | version, keys, cache, engine, artifacts, предупреждения космограммы | расчёт с кэшем работает без сессии и без HTTP |
| Э2 | places, tz, resolver | форма → `ResolvedBirthData`, контрольный кейс 02.09.1990 зелёный |
| Э3 | session ports, persistence, context | CAS, агрегатный lifecycle и отказ stale-записи |
| Э4 | handler, orchestrator, results | сквозной путь `BuildNatalCommand → Success` |
| Э5 | переименование `agent/`, `NatalTool` на резолвер | B-5 зелёный |
| Э6 | бенчмарк конкурентности | требование ADR-0012 закрыто |

---

## 12. Отложено и почему

| Отложено | Основание | Условие возврата |
|---|---|---|
| `BuildAttempt`, `build_revision`, статусы попытки, reaper | ADR-0012; замер §1.4 | расчёт > нескольких сотен мс либо асинхронный build |
| `idempotency_key` | двойной клик закрывается кэшем расчётов и CAS | появление операции с внешним побочным эффектом |
| Восстановление незавершённого build после reopen | ADR-0012: требует durable execution protocol | вместе с `BuildAttempt` |
| `RemoteGeocoder`, `Candidates` на build-пути | ADR-0005: форма выбирающая | эндпоинт подсказок, свободный текст |
| `RemoteTool`, конфигурация `natal = local \| http://…` | ADR-0002: нечего выбирать | появление второй реализации |
| `TransitChartSpec` | ADR-0016 | транзиты |
| Собственный атлас исторических смещений | tzdb гарантирует согласие часов только с 1970 (§5.2); в MVP отдаём предупреждение | жалобы пользователей на неверный асцендент для рождений до 1970 |
| Несколько систем домов (`K`, `O`, `R`, `C`, `E`, `W`) | первая версия поддерживает только `P`; UI выбора отсутствует | UI-селектор, явный backend-allowlist, пользовательские названия, числовые и golden-тесты каждой системы, проверка высоких широт и влияния на ключ/версию |

**Что при этом нужно поправить в документах**, иначе они разойдутся с кодом
молча: `docs/requirements/build_chart/exact_orb_negative_corner_scenarios.md`
§1.1 и §1.3 описывают `BuildAttempt` и `build_revision` как действующий
контракт. Их следует пометить как отложенные со ссылкой на ADR-0012.

---

## 13. Открытые вопросы

1. **`orb_profile` против полных конфигов в `ChartSpec`** (§3.3). Решение
   зависит от того, будут ли орбисы пользовательской настройкой.

2. ~~**Порог `place_substituted`.**~~ **Закрыт:** порог невычислим —
   он считается от координат исходного места, которых у системы нет.
   Атрибут отменён, подстановка остаётся текстом в форме (§3.1
   и `exact-orb_birth_data_resolution.md` §3.4).

3. **`ENGINE_VERSION` — константа или хэш исходников.** Константа требует
   дисциплины при правке алгоритма; хэш меняется от любой правки комментария
   и обнуляет кэш чаще, чем нужно.

4. **Размер пула расчётных потоков.** Смысл имеет только после замера
   конкурентности: при process-wide блокировке несколько воркеров могут
   не давать выигрыша вовсе.

5. **Формат каталога мест** и его объём для MVP — влияет на контракт
   `LocalPlaceCatalog.from_file`.
