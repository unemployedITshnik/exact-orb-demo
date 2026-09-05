# Блок сессий, P1: контракты состояния и порты persistence

Работай в ветке `feat/session-context`.

Ветка одна на все пять промтов блока сессий. Если она уже существует,
продолжай в ней; иначе создай от текущей ветки. Не создавай коммит, не делай
push и не включай изменения из других веток. Сохрани пользовательские и
несвязанные изменения рабочего дерева.

`prompts/**` — исторический журнал. Не редактируй старые промты.

## Контекст серии

| Этап | Содержание |
|---|---|
| P1 | модели, outcomes, ошибки, `Protocol`-порты и чистые правила |
| P2 | InMemory-реализация и общий conformance-набор |
| P3 | `ContextService` |
| P4 | SQLite, схема, транзакции и reaper |
| P5 | `Research Corpus` и проекция признаков |

Application-слой — команды, handlers, `ApplicationOrchestrator`,
`bootstrap.py` и переименование `orchestration/` → `agent/` — отдельная
задача. Граница блока проходит по `ContextService`: session-блок его
предоставляет, application-слой потребляет.

В P1 нет реализаций хранилищ, SQLite и настоящего I/O. Здесь фиксируется
контракт, который без изменений реализуют P2 и P4 и использует P3.

## Цель

- глубоко immutable модели;
- чистые и детерминированные переходы;
- store-owned version increment внутри атомарного CAS;
- общий lifecycle состояния и диалога;
- read failure не маскируется под absence;
- expired session не воскрешается;
- reset state + dialog атомарен;
- retry неподтверждённого commit безопасен без `idempotency_key`;
- один conformance-набор для InMemory и SQLite;
- механически проверяемая изоляция `session/`.

## Перед изменениями

1. Проверь Git и сохрани все пользовательские/несвязанные изменения.
2. Перейди на `feat/session-context` либо создай её.
3. Изучи:
   - `exact-orb_session_requirements.md`;
   - контрактные разделы `exact-orb_build_natal_components.md`;
   - ADR-0009, ADR-0014, ADR-0016, ADR-0021, ADR-0024;
   - N7–N9 и N11 в `exact_orb_negative_corner_scenarios.md`;
   - `sequence_diagrams/session/001`–`005`;
   - `service_ready_architecture.md`;
   - `birth/types.py`, `calculation/spec.py`, `calculation/errors.py`,
     корневой `outcomes.py`, `run_context.py`;
   - `tests/test_module_boundaries.py`.
4. Существенные противоречия не разрешай молча: синхронизируй перечисленные
   ниже источники и назови конфликт в отчёте.
5. Не расширяй задачу на application-слой.

## Создать пакет

```text
src/exact_orb/session/
    __init__.py
    errors.py
    state.py
    outcomes.py
    dialog.py
    store.py
    persistence.py
```

Пакет импортируется без native-стека, конфигурации, SQLite и edge-слоёв.

## Ацикличный граф модулей

`SessionSnapshot` объявляется только в `persistence.py`, потому что только
aggregate `SessionPersistence.touch` производит значение, объединяющее
`SessionState` и `DialogTurn`.

- `outcomes.py` импортирует `SessionState`, но не `dialog.py`;
- `dialog.py` может импортировать `SessionAbsent` из `outcomes.py`;
- `store.py` импортирует `state.py` и `outcomes.py`;
- `persistence.py` импортирует `state.py`, `dialog.py`, `outcomes.py`,
  `store.py`.

Не обходи циклы через `TYPE_CHECKING`, строковые взаимные forward references,
локальные импорты или порядок импорта в `__init__.py`. Ацикличность должна
следовать из AST объявленных импортов.

## Глубокая неизменяемость

Все Pydantic-модели `session/` — Pydantic v2 с `ConfigDict(frozen=True)`.

Разрешена одна узкая правка вне пакета: заморозь `BirthInput`,
`ResolutionWarning`, `ResolvedBirthData` в `birth/types.py`. Их поля —
immutable scalars и `tuple`; после этого весь граф `SessionState` immutable.
Другие файлы `birth/` не меняй. `ChartSpec` уже frozen.

## `session/errors.py`

```text
ExpiredSessionTransitionError(ValueError)

SessionPersistenceError(Exception) {
    error_code: str
}
StateReadError(SessionPersistenceError)
StateWriteError(SessionPersistenceError)
```

Во всех session exceptions и outcomes используется одно имя `error_code`:

```text
StateReadFailed(error_code=exc.error_code)
StateCommitFailed(error_code=exc.error_code)
```

Это сознательно отличается от `ArtifactError.code`, но совпадает с уже
существующими outcome-полями `ResolutionUnavailable.error_code` и
`CalculationFailed.error_code`. Внутри session-блока второго соглашения нет.

- `ExpiredSessionTransitionError` — детерминированное нарушение предусловия;
- absence, id conflict и version conflict — штатные результаты;
- read/write errors — инфраструктурные отказы;
- сырые runtime errors не пересекают порт;
- `StateWriteError` после commit означает неподтверждённый результат: запись
  могла выполниться, а ответ — потеряться.

## `session/state.py`

Все поля ниже валидируются моделью.

```text
ChartRef {
    state_version: int          # >= 1
    spec: ChartSpec
}

SessionState {
    session_id: str             # непустой, opaque для домена
    birth_input: BirthInput | None
    birth_resolved: ResolvedBirthData | None
    state_version: int          # >= 0
    base_chart: ChartRef | None
    created_at: datetime        # aware UTC
    expires_at: datetime        # aware UTC
    hard_expires_at: datetime   # aware UTC
}

StateDelta {
    birth_input: BirthInput | None
    birth_resolved: ResolvedBirthData | None
    base_chart_spec: ChartSpec | None
}
```

Все три поля `StateDelta` обязательны и не имеют default. Допустимы только
две формы: все заданы либо все явно `None`. `StateDelta()` и смешанная форма
невалидны. `expected_state_version` в delta не входит.

Инварианты `SessionState`:

1. `birth_input`, `birth_resolved`, `base_chart` либо все `None`, либо все
   заданы. Пустой reset-state может иметь версию больше нуля.
2. `base_chart.state_version == state_version`.
3. `ChartRef.state_version >= 1`; version 0 не содержит карту.
4. Все timestamps aware и имеют UTC offset 0.
5. `created_at <= expires_at <= hard_expires_at`.
6. Модель не требует равенства `hard_expires_at == created_at + HARD_TTL`:
   persisted deadline конкретной сессии не перевалидируется по новой policy.

Объяви:

```text
SLIDING_TTL = timedelta(days=7)
HARD_TTL = timedelta(days=30)

RESET_DELTA = StateDelta(
    birth_input=None,
    birth_resolved=None,
    base_chart_spec=None,
)
```

`RESET_DELTA` — единственный канонический reset-value; не конструируй его
заново в CAS, aggregate reset, P3 и тестах.

Чистые функции:

```text
new_session(session_id, *, now) -> SessionState
apply_delta(state, delta, *, now) -> SessionState
touched(state, *, now) -> SessionState
is_expired(state, *, now) -> bool
matches_intent(actual, delta) -> bool
```

`now` — aware UTC. Истечение:

```text
now >= expires_at OR now >= hard_expires_at
```

Общий deadline:

```text
min(hard_expires_at, max(expires_at, now + SLIDING_TTL))
```

`new_session` создаёт пустой version 0, `created_at=now`, sliding +7 дней,
hard +30 дней. Равенство с `HARD_TTL` — постусловие `new_session`, не
инвариант произвольной persisted-модели.

`apply_delta` и `touched` сначала отвергают expired state через
`ExpiredSessionTransitionError`. `apply_delta` сохраняет id/created/hard,
увеличивает version ровно на один, полностью заменяет триаду, создаёт
`ChartRef` с новой version и продлевает TTL. `touched` меняет только
`expires_at`.

Store применяет `apply_delta` после атомарной проверки actual version;
production caller не передаёт готовый candidate.

По ADR-0014 intent сравнивает только:

```text
actual.birth_resolved == delta.birth_resolved
AND actual.base_chart.spec|None == delta.base_chart_spec
```

`birth_input` сознательно исключён: разные представления, разрешившиеся в
одинаковые `ResolvedBirthData + ChartSpec`, дают `AlreadyApplied`.

## `session/outcomes.py`

Модуль не импортирует `dialog.py` и не содержит `SessionSnapshot`.

Все модели frozen:

```text
SessionCreated { state: SessionState }
SessionIdConflict { session_id: str }
Committed { state_version: int }
AlreadyApplied { state_version: int }
Superseded { actual: SessionState }
VersionConflict { actual: SessionState }
SessionAbsent { reason: Literal["expired", "not_found"] }
StateReadFailed { error_code: str }
StateCommitFailed { error_code: str }
```

`SessionCreated` валидатором требует `state.state_version == 0`. Это часть
контракта, а не комментарий или одно тестовое соглашение. Из остальных
инвариантов следует, что created-state пуст.

`SessionAbsent.reason` описывает наблюдение текущей операции. После удаления
reaper'ом прежний expired неотличим от not-found без tombstone; tombstone не
вводится.

## `session/dialog.py`

```text
Selection {
    topic: str                  # непустой
    focus: str                  # непустой
}

DialogTurn {
    turn_id: str                # непустой
    created_at: datetime        # aware UTC
    selection: Selection
    state_version_at_answer: int # >= 1
    status: Literal["complete", "partial"]
    truncated: bool = False
    text: str
}
```

`status` — завершилась ли генерация; `truncated` — был ли уже полученный
текст урезан storage-политикой. Допустимы все четыре сочетания complete/
partial и truncated true/false. Усечение не меняет status.

```text
MAX_DIALOG_TURNS = 50
MAX_DIALOG_TURN_CHARS = 8_000
MAX_DIALOG_CHARS = 120_000
```

Символ — Unicode code point (`len(str)`), не UTF-8 bytes. Чистая функция
добавления сначала режет новый turn до 8 000 и ставит `truncated=True`, затем
добавляет его и удаляет самые ранние successful append, пока соблюдены count
и total chars. Порядок — порядок append, не `created_at`. Цикл завершается:
один нормализованный turn заведомо помещается в общий предел.

```text
@runtime_checkable
class DialogStore(Protocol):
    async def append(session_id, turn, *, now) -> None | SessionAbsent
    async def read(session_id, *, now) -> tuple[DialogTurn, ...] | SessionAbsent
    async def clear(session_id, *, now) -> None | SessionAbsent
```

`None` — типизированный успех; пустые `TurnAppended`/`DialogCleared` не
вводятся. `read` read-only и выпускает только `StateReadError`; live session
без dialog даёт `()`. `append`/`clear` проверяют live parent, выпускают только
`StateWriteError`, не меняют state version и в общей атомарной секции
продлевают state и существующий dialog одним deadline. `clear` без dialog у
live session — idempotent success.

## `session/store.py`

```text
@runtime_checkable
class SessionStore(Protocol):
    async def create(session_id, *, now) -> SessionCreated | SessionIdConflict
    async def get(session_id, *, now) -> SessionState | SessionAbsent
    async def compare_and_set(
        session_id,
        expected_state_version,
        delta,
        *,
        now,
    ) -> int | VersionConflict | SessionAbsent
```

Все три порта — `SessionStore`, `DialogStore`, `SessionPersistence` — явно
помечены `@runtime_checkable`, как существующий `CalculationCache`.

### `get`

`get` — TTL-aware, но read-only: не удаляет строку, не продлевает TTL и не
обновляет dialog. Existing expired даёт `SessionAbsent("expired")`, missing —
`SessionAbsent("not_found")`, infrastructure failure — `StateReadError`.
Физическое удаление expired выполняет reaper P4.

### `create`

`create` — atomic insert-only для нового server-generated id. Cookie
client-controlled и используется только для lookup. При missing/invalid/
expired cookie transport не передаёт её значение в create, а генерирует новый
криптографически случайный id, создаёт запись и заменяет cookie. Это защищает
от ABA и session fixation.

При отсутствии строки store вызывает `new_session` и возвращает
`SessionCreated`. Любая live или ещё не убранная expired строка с тем же id не
перезаписывается: `SessionIdConflict`, после которого transport генерирует
другой id. `create` не возвращает `SessionAbsent`. CAS по отсутствующей записи
не создаёт session.

Запрещён delete + recreate под старым id/version 0: старый in-flight CAS с
expected 0 смог бы записать данные в новую lifecycle session.

### `compare_and_set`

В одной атомарной секции store читает actual, проверяет expiry и expected.
Absent/expired даёт `SessionAbsent` без записи. Mismatch даёт
`VersionConflict(actual)` из snapshot той же операции, без дополнительного
`get`. Match вызывает `apply_delta(actual, delta, now)`, сохраняет N+1,
устанавливает существующему dialog тот же expiry и возвращает N+1.

Failed CAS не меняет state, dialog или TTL. Store владеет фактическим version
increment; caller не может подменить immutable-поля готовым candidate.

Если `delta == RESET_DELTA`, тот же atomic CAS дополнительно очищает dialog.
Это единственный алгоритм полного reset на уровне persistence.

## `session/persistence.py`

Только здесь объяви frozen-модель:

```text
SessionSnapshot {
    state: SessionState
    dialog: tuple[DialogTurn, ...]
}
```

И aggregate:

```text
@runtime_checkable
class SessionPersistence(Protocol):
    sessions: SessionStore
    dialogs: DialogStore

    async def touch(session_id, *, now) -> SessionSnapshot | SessionAbsent
    async def reset(
        session_id,
        expected_state_version,
        *,
        now,
    ) -> int | VersionConflict | SessionAbsent
    async def delete(session_id) -> None
```

Все операции портов асинхронные: P2 и P4 сохраняют одинаковый публичный
контракт, даже если in-memory реализация не выполняет реальный I/O.

ContextService P3 получает один aggregate, а не независимо созданную пару
facets. Facets разделяют один backend, один lock в InMemory и одну transaction
boundary в SQLite.

### Aggregate `touch`

Это load-and-touch, которому делегирует `ContextService.load`. В одной
секции он читает live parent и dialog (`()` если записи нет), вычисляет один
deadline, обновляет state и existing dialog, не создаёт пустой dialog, не
меняет version и возвращает snapshot с обновлённым state. Absent/expired не
пишет. ContextService не строит цепочку `get → read → touch`.

### Aggregate `reset`

`reset` не является второй реализацией CAS. Он тождественен и в P2/P4 должен
делегировать единственному пути:

```text
sessions.compare_and_set(
    session_id,
    expected_state_version,
    RESET_DELTA,
    now=now,
)
```

Facet CAS, увидев `RESET_DELTA`, в той же секции увеличивает version, очищает
state и dialog и продлевает state. Поэтому delegating reset автоматически
атомарен. Не копируй его transaction/lock algorithm второй раз.

`ResetSessionCommand(scope="dialog")` использует `dialogs.clear` и version не
меняет. `scope="all"` использует только aggregate `reset`; два отдельных
вызова clear + CAS запрещены.

### Aggregate `delete`

Идемпотентно и атомарно удаляет dialog + state, не оставляет orphan и не
создаёт tombstone. Infrastructure failure — `StateWriteError`.

## Retry неподтверждённого commit

Будущий P3-контракт:

```text
ContextService.save(
    session_id,
    expected_state_version,
    delta,
    *,
    now,
) -> Committed | AlreadyApplied | Superseded |
     SessionAbsent | StateCommitFailed
```

Один logical retry повторяет исходную пару `(expected_state_version, delta)`
без rebasing. Unknown — commit outcome, а не исходная expected version.

- commit не состоялся → прежний CAS даёт `Committed`;
- commit прошёл, ответ потерян → conflict с тем же intent → `AlreadyApplied`;
- победил другой intent → conflict → `Superseded`.

После conflict ContextService использует `actual` и `matches_intent`, не
делает `get`. `StateWriteError` становится `StateCommitFailed` с тем же
`error_code`; absence остаётся lifecycle outcome. Если после применения того
же intent уже победила новая операция, retry старого intent даёт Superseded:
исторического журнала нет.

Application-задача обязана переносить исходную expected version при повторной
доставке. Новая операция после explicit restore использует свежую version и
не является retry. `idempotency_key`/`BuildAttempt` не вводятся.

## Требования к P3 (`ContextService`)

P1 не реализует сервис, но фиксирует acceptance P3. Сервис получает один
`SessionPersistence` и injected clock. Для каждой публичной логической
операции clock вызывается ровно один раз; одно `now` передаётся вниз.

`load` вызывает только aggregate `touch`; возвращает snapshot/absence;
`StateReadError` и failure обязательного touch преобразует в
`StateReadFailed`, никогда не отдаёт старый snapshot и не маскирует ошибку как
absence.

`save` передаёт original expected + delta; int → Committed; conflict same
intent → AlreadyApplied; other intent → Superseded; не перечитывает;
StateWriteError → StateCommitFailed, не VersionConflict.

`reset_all` вызывает только aggregate reset; success → Committed; conflict с
уже пустым state → AlreadyApplied; другой actual → Superseded; write failure
не оставляет partial reset. `append_turn`/`clear_dialog` используют facets
того же aggregate, единый now и не меняют state version.

P3-тесты обязаны доказать:

1. StateReadError не превращается в SessionAbsent.
2. Failed required touch не возвращает прочитанный snapshot.
3. StateWriteError не превращается в VersionConflict.
4. Conflict не вызывает дополнительный get.
5. Same/different intent дают AlreadyApplied/Superseded.
6. Один now используется на всю logical operation.
7. Sequential retry с original expected увеличивает version ровно один раз.
8. Scope-all reset вызывает одну aggregate operation.
9. Reset conflict не очищает dialog.
10. Reset failure не оставляет одну запись изменённой.

Используй fake clock/events, не sleep.

## Тесты P1

Создай:

```text
tests/session/__init__.py
tests/session/test_state.py
tests/session/test_dialog.py
tests/session/test_contracts.py
```

P1 тестирует только модели, pure rules и форму контрактов; fake/InMemory/
SQLite stores не создаются.

### `test_state.py`

Покрой: empty id/negative version; ChartRef >=1 и equality с state; all-or-none
state/delta; `StateDelta()` invalid, `RESET_DELTA` valid; aware UTC и
`created <= expires <= hard`; historical hard deadline принимается;
new-session postconditions; apply version +1/chart new version/reset no zero;
preservation created/hard; capped monotonic TTL; no resurrection;
именованную expired error; `matches_intent` по resolved+spec, включая
исключённый birth_input и reset.

Проверь frozen не только верхнего state, но и ChartRef, BirthInput,
ResolvedBirthData и вложенный ResolutionWarning.

### `test_dialog.py`

Покрой независимость status/truncated, все нужные сочетания, trim ровно до
8 000 Unicode chars без изменения status, пределы 50/120 000, eviction по
append order, termination для одного max turn и отсутствие мутации входов.

### `test_contracts.py`

Этот файл проверяет форму контрактов:

1. `SessionPersistenceError` сохраняет `error_code`.
2. Read/Write errors имеют правильное наследование; transition error не
   является persistence error.
3. Failed outcomes используют то же имя `error_code`.
4. Outcomes и `SessionSnapshot` frozen.
5. `SessionAbsent.reason` замкнут на expired/not_found.
6. `SessionCreated` валидатором отвергает state version != 0.
7. `SessionIdConflict.session_id` непустой.
8. Все три Protocol помечены `@runtime_checkable`.
9. `SessionSnapshot` объявлен/реэкспортирован из persistence, а не outcomes.
10. `exact_orb.session.__all__` содержит ожидаемые публичные модели,
    outcomes, errors, ports, constants и pure functions.

Не проверяй `sys.modules` и ацикличность в общем pytest-процессе: порядок
сбора тестов сделал бы такие assertions ложными или пустыми. Эти проверки
принадлежат module-boundary tests ниже.

## Module-boundary tests

Обнови `tests/test_module_boundaries.py`.

В `CONTRACT_MODULES` и explicit discovery whitelist добавь шесть модулей
session. Не добавляй весь prefix: позднее рядом появятся implementations.
Добавь `sqlite3` в forbidden contracts.

Для объявленных `exact_orb.*` imports файлов `session/` используй allowlist:

```text
exact_orb.session.*
exact_orb.birth.types
exact_orb.calculation.spec
```

Другой project import ломает тест. Stdlib/typing/Pydantic этим правилом не
запрещаются.

Добавь AST-тест ацикличности шести session contract modules. Он строит граф
по всем объявленным imports, включая imports внутри `if TYPE_CHECKING`, и
падает с читаемым cycle path. Отдельно запрети использование `TYPE_CHECKING`
как обхода архитектурного цикла в этих модулях.

Статически проверь отдельное существенное ребро: `outcomes.py` не объявляет
import `dialog.py`. Не проверяй это через `sys.modules`: импорт submodule
сначала исполняет package `__init__`, а eager-реэкспорт публичного
`DialogTurn` закономерно загрузит `dialog.py`, даже когда граф исходников
ацикличен. Не вводи lazy `__getattr__` только ради такой проверки.

Clean subprocess импортирует `exact_orb.session`, проверяет
доступность `SessionState`, `SessionSnapshot`, трёх ports и отсутствие:

```text
swisseph, sqlite3, aiosqlite, redis,
exact_orb.swiss_backend, exact_orb.engine, exact_orb.config,
exact_orb.calculation.artifacts, .engine, .types, .codec,
exact_orb.application, .agent, .orchestration, .tools, .llm, .cli
```

Отрицательные проверки всегда дополняй позитивным контролем исполненного
пути.

## Документация P1

Синхронизируй только прямые источники изменяемого контракта.

1. `exact-orb_session_requirements.md`: module placement, all-or-none delta,
   RESET_DELTA, status/truncated, read-only get, aggregate touch/reset,
   server-generated id/cookie lookup only, CAS, transition/error_code,
   P3 acceptance, retry и observable absence.
2. ADR-0009 revision: logical read extends via aggregate touch; raw get is
   read-only; new id after expiry; cookie not trusted for create; persisted
   hard deadline vs creation policy.
3. ADR-0014 revision: separate expected; store applies delta; RESET_DELTA and
   atomic reset; intent fields; retry original expected; atomic actual.
4. ADR-0024 revision: BEGIN IMMEDIATE path, aggregate reset transaction,
   guarded update и honest scope of 0.016 ms benchmark.
5. `exact-orb_build_natal_components.md`: только package/contracts sections —
   state/delta/snapshot, aggregate facets, CAS/reset и future ContextService.
6. `service_ready_architecture.md`: session contract modules in I1/I5/I6,
   allowlist and aggregate boundary.
7. `exact_orb_negative_corner_scenarios.md`: точечно синхронизируй **N7 и
   N8**. У них общий mechanism; expected вне delta; N8 повторяет original
   pair, не rebases.
8. Session diagrams 001–005: raw get vs aggregate touch; fresh generated id;
   scope-all aggregate reset; separate expected/delta; retry without rebase;
   resolved+spec intent.

Не создавай новый ADR: это явные revisions существующих решений. Если
потребуется изменить само решение, остановись и сообщи конфликт.

Не переписывай полностью roadmap/overview/application diagrams и
периферийные chart-artifacts/birth-resolution docs. Полные application-flow
и end-to-end tests N7/N8/N6/N10/N11 остаются application-задаче, но
контрактный текст N7/N8 синхронизируется сейчас. Назови отложенные stale
references в отчёте; не заявляй alignment всего репозитория.

## Зафиксировать для P2/P4, но не реализовывать в P1

Общий conformance P2 принимает aggregate factory. Он проверяет concurrent
create; no overwrite live/expired; observable absence; read-only get/read;
no resurrection; один winner CAS; atomic conflict actual; no implicit
create; identical TTL; no TTL regression; touch no version; empty dialog;
concurrent append; dialogue limits/truncated; clear version stability;
append/delete orphan safety; stable typed errors.

Reset tests проверяют именно единый путь: aggregate reset делегирует facet
CAS с `RESET_DELTA`; прямой CAS reset и aggregate reset имеют тождественную
семантику; version +1; state+dialog clear together; conflict/failure не дают
partial effect. Не пиши две реализации и два независимых conformance blocks.

Concurrency tests используют events/barrier, не sleep/random retry loops.

SQLite P4 для SELECT→Python apply→UPDATE использует:

```text
BEGIN IMMEDIATE
SELECT actual
check absence / expiry / expected
apply_delta(actual, delta, now)
UPDATE ... WHERE session_id=? AND state_version=?
assert rowcount == 1
clear dialog for RESET_DELTA, otherwise update its TTL
COMMIT
```

Version mismatch возвращается из locked snapshot. Guarded UPDATE обязателен;
после matched precheck rowcount !=1 — StateWriteError/invariant failure, не
normal conflict. SQLITE_BUSY/OperationalError не становятся VersionConflict;
любой failure rolls back. State/dialog changes commit together. Тест использует
два independently opened adapters к одному file, чтобы object lock не
маскировал DB semantics. Не проверяй SQL строковым assertion.

0.016 ms из ADR-0024 — bare conditional UPDATE, не full adapter latency. P4
перемеряет BEGIN/SELECT/decode/apply/encode/state+dialog/commit path.

## Ограничения

- Никаких InMemory/SQLite implementations, schema, migration, reaper или I/O.
- Не создавай ContextService/Research Corpus/application commands/handlers/
  orchestrator/bootstrap/transport.
- Не реализуй cookie handling — только зафиксируй boundary.
- Не добавляй dependencies, Redis, queue, background jobs, idempotency_key или
  BuildAttempt.
- Не переименовывай orchestration, не подключай ChartArtifactResolver, не
  вводи derived_chart/active_view/SetActiveView.
- Не меняй calculation behavior и не делай попутный refactor.
- Не ослабляй module boundaries и не редактируй historical prompts.
- Единственная правка birth/ — frozen трёх contract models + regression tests.
- Не создавай commit/push.

## Проверки

```text
pytest tests/session/
pytest tests/test_module_boundaries.py
pytest
```

Не запускай network/paid smoke tests и не заявляй непройденные проверки.

## Итоговый отчёт

Начни с результата. Перечисли files/contracts, acyclic placement Snapshot,
deep immutability, get/touch boundary, canonical RESET_DELTA и single reset
path, ABA/session-fixation defense, store-owned version, intent fields,
partial/truncated, named transition error, unified error_code,
runtime-checkable ports, P3 requirements, synchronized/deferred docs,
точные команды и реальные results, остаток P2–P5/application. Коммит не
создавай; ветку не сливай и не удаляй.
