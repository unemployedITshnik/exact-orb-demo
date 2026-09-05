# Блок сессий, P2: InMemory-адаптеры и общий conformance-набор

Работай в ветке `feat/session-context`.

P1 уже ввёл immutable-модели состояния и диалога, чистые переходы, outcomes,
типизированные persistence errors и три runtime-checkable порта:

- `SessionStore`;
- `DialogStore`;
- `SessionPersistence`.

В этой задаче реализуй только процессный InMemory-адаптер и общий black-box
conformance-набор, который в P4 будет повторно использован для SQLite.

`ContextService`, application-слой, SQLite и Research Corpus сюда не входят.

## 0. Preflight

До любых изменений:

1. Прочитай корневой `AGENTS.md`.
2. Выполни:

   ```text
   git rev-parse --show-toplevel
   git branch --show-current
   git status --short
   git log -5 --oneline
   ```

3. Если каталог не является Git-репозиторием, `.git` отсутствует, активна
   другая ветка либо в истории нет результата P1, остановись и сообщи об этом.
   Не выполняй `git init`, не реконструируй `.git` и не переноси изменения
   самостоятельно.
4. Сохрани все пользовательские и несвязанные изменения рабочего дерева.
5. До реализации запусти существующие session- и boundary-тесты. Если
   baseline падает, зафиксируй точные падения и не исправляй посторонние
   дефекты в рамках P2.

Изучи перед изменением:

```text
src/exact_orb/session/__init__.py
src/exact_orb/session/errors.py
src/exact_orb/session/state.py
src/exact_orb/session/outcomes.py
src/exact_orb/session/dialog.py
src/exact_orb/session/store.py
src/exact_orb/session/persistence.py

tests/session/test_state.py
tests/session/test_dialog.py
tests/session/test_contracts.py
tests/test_module_boundaries.py

docs/requirements/component_responsibilities/exact-orb_session_requirements.md
docs/requirements/component_responsibilities/exact-orb_build_natal_components.md
docs/requirements/decisions/0009-context-yes-profiles-later.md
docs/requirements/decisions/0014-explicit-state-mutations.md
docs/requirements/decisions/0024-sqlite-storage-implementation.md
docs/requirements/decisions/README.md
docs/sequence_diagrams/session/README.md
docs/sequence_diagrams/session/004-session-reset-and-delete.puml
docs/sequence_diagrams/session/005-compare-and-set.puml
```

`prompts/**` — исторический журнал. Старые промты не редактируй и не
используй вместо действующих контрактов.

Этот промт явно уточняет TTL-семантику `append` и `clear`; в этой части он
является текущим источником задачи.

## 1. Зафиксированная TTL-семантика

Принят вариант `write-and-renew`.

Действуют следующие правила:

1. `SessionStore.get` и `DialogStore.read` строго read-only.
2. `SessionPersistence.touch` — единственная операция read-and-renew.
3. Успешные `compare_and_set`, `DialogStore.append` и `DialogStore.clear`
   являются write-and-renew.
4. `append` атомарно:

   - продлевает state;
   - сохраняет dialog с тем же `expires_at`;
   - не меняет `state_version` и предметные поля state.

5. `clear` атомарно:

   - продлевает state;
   - удаляет содержимое/запись dialog;
   - не меняет `state_version`, данные рождения и карту.

6. `clear` для live session без dialog остаётся успешным и также считается
   пользовательской активностью: state TTL может быть продлён.
7. Слово «состояние не меняется» применительно к `clear` означает только
   неизменность предметного содержимого и `state_version`, но не
   `expires_at`.
8. Любое продление ограничено `hard_expires_at`.

Это уточнение границ принятого lifecycle-решения, а не новый persistence-
механизм. Отрази его точечно в документации, перечисленной в §13.

## 2. Цель и допустимые файлы

Создай:

```text
src/exact_orb/session/adapters/__init__.py
src/exact_orb/session/adapters/_time.py
src/exact_orb/session/adapters/in_memory.py
tests/session/conformance.py
tests/session/test_in_memory.py
```

Измени:

```text
tests/test_module_boundaries.py

docs/requirements/component_responsibilities/exact-orb_session_requirements.md
docs/requirements/component_responsibilities/exact-orb_build_natal_components.md
docs/requirements/decisions/0009-context-yes-profiles-later.md
docs/requirements/decisions/0024-sqlite-storage-implementation.md
docs/requirements/decisions/README.md
docs/sequence_diagrams/session/004-session-reset-and-delete.puml
docs/sequence_diagrams/session/README.md
```

В `src/exact_orb/session/dialog.py` разрешено изменить только docstrings
`DialogStore.append` и `DialogStore.clear`, чтобы явно отразить
write-and-renew.

В `dialog.py` не меняй:

- сигнатуры;
- модели;
- поля моделей;
- константы;
- валидаторы;
- `append_dialog_turn`;
- `__all__`.

Остальные P1 contract-модули не меняй.

Публичные concrete-типы:

```text
InMemorySessionStore
InMemoryDialogStore
InMemorySessionPersistence
```

Поддерживаемая точка создания:

```text
persistence = InMemorySessionPersistence()
persistence.sessions
persistence.dialogs
```

Экспортируй concrete-типы из `exact_orb.session.adapters`, но не добавляй их
в корневой `exact_orb.session.__init__`. Корневой пакет остаётся
contract-only и не должен загружать реализации.

`adapters._time` — private shared infrastructure обоих persistence-
адаптеров. Его не экспортируй из `adapters.__init__`.

Конструктор `InMemorySessionPersistence` не принимает:

- clock;
- `ttl_seconds`;
- settings;
- генератор идентификаторов;
- файловый путь;
- executor.

Время передаётся каждому методу как `now`, а TTL задаётся контрактными
`SLIDING_TTL` и `HARD_TTL`.

## 3. Конструкция facets и private backend

Один `InMemorySessionPersistence` владеет одним private backend.

Минимальная модель backend:

```text
one asyncio.Lock
dict[session_id, SessionState]
dict[session_id, _DialogRecord]
```

`_DialogRecord` хранит:

```text
turns: tuple[DialogTurn, ...]
expires_at: datetime
```

Запись диалога и state относятся к одному lifecycle. Источником
live/missing/expired для публичных операций является только parent
`SessionState`.

`_DialogRecord.expires_at` в P2 не участвует в принятии решений:

- `read`, `append`, `clear`, CAS и `touch` не проверяют его отдельно;
- он не может превратить live parent в expired dialog;
- он хранится для изоморфизма с будущей SQLite-схемой;
- в P4 его будет использовать reaper физической очистки.

Не удаляй это поле как «мёртвое» и не начинай использовать его как второй
источник lifecycle-истины.

Конструкторы concrete facets имеют обязательный positional-only private
backend:

```text
InMemorySessionStore(_backend, /)
InMemoryDialogStore(_backend, /)
```

У них нет:

- zero-argument constructor;
- backend по умолчанию;
- публичного backend factory;
- самостоятельного создания внутренних словарей.

`_InMemoryBackend` не экспортируется из `adapters.__init__` и не входит в
`__all__`.

Только `InMemorySessionPersistence()`:

1. создаёт private backend;
2. создаёт один `InMemorySessionStore` над ним;
3. создаёт один `InMemoryDialogStore` над ним;
4. сохраняет обе facets как стабильные атрибуты.

Это не делает намеренный импорт private-типа физически невозможным в Python,
но поддерживаемый public construction path не позволяет случайно получить две
facets над разными backend.

Дополнительные требования:

1. `sessions` и `dialogs` aggregate разделяют тот же backend и тот же lock.
2. Повторный доступ к facets не создаёт новые wrapper-объекты.
3. Разные вызовы `InMemorySessionPersistence()` полностью изолированы.
4. Один lock защищает state и dialog вместе.
5. После получения lock внутри критической секции нет других `await`.
6. Перед публикацией вычисляй новые immutable-значения целиком. Не мутируй
   сохранённые Pydantic-модели и tuples.
7. Не делай defensive copy возвращаемого графа: контрактные модели глубоко
   immutable. Общий conformance при этом не проверяет object identity,
   поскольку SQLite будет десериализовывать новые объекты.
8. Не вводи per-session locks, lock registry, cleanup locks или фоновые
   задачи.
9. InMemory рассчитан на задачи одного event loop. Thread-safe и
   cross-event-loop использование не обещается.
10. Не заявляй отдельную cancellation-гарантию. Cancellation при реальном
    ожидании contended lock в P2 намеренно не проверяется.
11. Не лови произвольный `Exception` и не маскируй ошибки программирования
    как persistence failures.

Переиспользуй существующие функции:

```text
new_session
is_expired
apply_delta
touched
append_dialog_turn
```

Не копируй их transition-правила внутрь адаптера.

## 4. Общий валидатор `now`

Создай в `src/exact_orb/session/adapters/_time.py` одну private shared
функцию валидации `now`.

Она принимает только `datetime`, который:

- timezone-aware;
- имеет UTC offset, равный `0`.

Naive `datetime` и timezone-aware `datetime` с ненулевым offset отвергаются
через обычный `ValueError`.

Этот helper является тонкой adapter-boundary обёрткой над уже существующим
`exact_orb.session.state._require_utc(now, name="now")`. Такое обращение к
private функции внутри одного пакета в этой задаче разрешено явно, чтобы не
создавать второй UTC-предикат и не расширять публичный P1 API.

Этот helper:

- не читает текущее время;
- не нормализует и не конвертирует вход;
- возвращает исходное значение либо завершает вызов `ValueError`;
- не импортирует InMemory или SQLite implementation;
- не реализует UTC-предикат повторно, а делегирует `state._require_utc`;
- является единственным adapter-level местом проверки `now`.

Все now-bearing методы InMemory используют этот helper до lookup и до любой
мутации.

Будущий SQLite-адаптер P4 обязан импортировать тот же helper из
`exact_orb.session.adapters._time`, а не:

- импортировать функцию из `in_memory.py`;
- обращаться к private `state._require_utc` напрямую;
- копировать предикат в `sqlite.py`.

Invalid `now` даёт одинаковый результат для live, missing и expired ID и не
зависит от backend.

Ошибка времени:

- не превращается в `StateReadError`;
- не превращается в `StateWriteError`;
- не превращается в `ExpiredSessionTransitionError`;
- не меняет state или dialog.

`delete` не принимает `now` и к этому правилу не относится.

`reset` выполняет тот же shared validator перед единственным делегированием
CAS. Повторная проверка внутри CAS допустима и не считается вторым reset-
алгоритмом.

## 5. Общие правила absence и expiry

После успешной валидации `now`:

```text
now >= expires_at OR now >= hard_expires_at
```

означает expired.

Порядок штатной проверки:

1. найти parent state;
2. если его нет — `SessionAbsent(reason="not_found")`;
3. если он истёк — `SessionAbsent(reason="expired")`;
4. только затем выполнять содержательную операцию.

На точной границе `now == expires_at` состояние уже истекло.

`ExpiredSessionTransitionError` — внутренняя ошибка чистого transition-слоя.
Ни одна persistence-операция не выпускает её наружу.

Адаптер обязан распознать expiry до вызова `apply_delta` или `touched` и
вернуть `SessionAbsent(reason="expired")`.

InMemory P2 не выполняет physical purge:

- `get`, `read`, CAS, `append`, `clear` и `touch` не удаляют expired rows;
- reaper появляется только в P4;
- existing expired state продолжает занимать ID и мешает `create`;
- никакая операция не оживляет expired lifecycle.

После будущего физического удаления reaper тот же lookup естественно станет
`not_found`: tombstone контрактом не предусмотрен. Метрики различают только
фактически наблюдавшийся outcome, а не историческую причину отсутствия после
purge.

`delete` физически удаляет обе части независимо от того, live или expired
сессия.

После explicit `delete` технически допустим новый `create` с тем же ID:
store не хранит tombstone. Production transport всё равно обязан генерировать
новый случайный серверный ID и не переиспользовать cookie.

## 6. `InMemorySessionStore`

### 6.1. `create`

После общей валидации `now`, под общим lock:

1. Если ключ уже есть в state map, вернуть:

   ```text
   SessionIdConflict(session_id=session_id)
   ```

   Это относится и к live, и к expired записи.

2. Не раскрывать и не заменять существующее состояние.
3. Для свободного ID вызвать:

   ```text
   new_session(session_id, now=now)
   ```

4. Сохранить state и вернуть:

   ```text
   SessionCreated(state=state)
   ```

5. Версия новой сессии — `0`.
6. Не создавать пустую dialog row.
7. `create` — insert-only; никакого upsert и opportunistic purge.

### 6.2. `get`

После общей валидации `now`, под lock:

- live → вернуть сохранённый `SessionState`;
- missing → `SessionAbsent("not_found")`;
- expired → `SessionAbsent("expired")`.

`get` не:

- продлевает TTL;
- удаляет expired запись;
- создаёт dialog;
- изменяет version;
- обновляет порядок или какие-либо служебные поля.

### 6.3. `compare_and_set`

После общей валидации `now` вся последовательность выполняется под тем же
общим lock:

1. lookup;
2. missing/expiry check;
3. comparison `actual.state_version == expected_state_version`;
4. построение candidate через `apply_delta`;
5. публикация state и связанного изменения dialog.

#### Missing/expired

Вернуть `SessionAbsent`; ничего не создавать и не менять.

`ExpiredSessionTransitionError` наружу не выходит.

#### Version mismatch

Вернуть:

```text
VersionConflict(actual=actual)
```

`actual` берётся из той же критической секции.

Конфликт не меняет:

- state;
- dialog;
- `expires_at`;
- `state_version`.

Не выполнять дополнительный `get`.

Поддерживаются оба направления конфликта:

```text
expected_state_version < actual.state_version
expected_state_version > actual.state_version
```

В частности, свежая сессия версии `0` и `expected_state_version=1` возвращает:

```text
VersionConflict(actual=<state version 0>)
```

без `ValidationError` и без мутации.

#### Version match, обычная delta

Основной производственный happy path должен быть закреплён точно:

```text
fresh state version 0
+ populated StateDelta
+ expected_state_version=0
→ result == 1
→ stored state.state_version == 1
→ stored state.base_chart.state_version == 1
```

Алгоритм:

1. Вызвать:

   ```text
   next_state = apply_delta(actual, delta, now=now)
   ```

2. Сохранить `next_state`.
3. Если dialog row существует, сохранить прежний tuple ходов и обновить её
   private deadline до `next_state.expires_at`.
4. Если dialog row отсутствует, не создавать пустую.
5. Вернуть `next_state.state_version`.

Равенство private dialog deadline и state deadline не проверяется общим
conformance, потому что ни один порт его не раскрывает. Оно проверяется
InMemory-specific тестом в §10.5 и позднее SQLite-specific тестом P4.

#### Version match, reset delta

Reset распознаётся по значению:

```text
delta == RESET_DELTA
```

а не только по object identity.

Эквивалентно заново созданный `StateDelta(None, None, None)` должен иметь ту
же reset-семантику.

В той же критической секции:

1. применить `apply_delta`;
2. получить version `N + 1`;
3. сохранить пустое предметное state;
4. удалить dialog;
5. вернуть новую версию.

Не добавляй отдельный CAS, отдельную reset-транзакцию или второй lock-path.

## 7. `InMemoryDialogStore`

Все операции после общей валидации `now` проверяют parent state под общим
backend lock.

Live state без dialog row означает пустой диалог, а не ошибку.

### 7.1. `read`

- live + dialog → вернуть tuple в порядке успешных append;
- live без dialog → `()`;
- missing/expired → соответствующий `SessionAbsent`.

`read` строго read-only:

- не продлевает state или dialog;
- не создаёт пустую dialog row;
- не сортирует ходы;
- не удаляет expired данные.

### 7.2. `append`

Под lock:

1. Проверить live parent.
2. Взять существующий tuple либо `()`.
3. Получить новый tuple только через:

   ```text
   append_dialog_turn(previous, turn)
   ```

4. Получить обновлённый state через:

   ```text
   next_state = touched(actual, now=now)
   ```

5. Сохранить state.
6. Сохранить dialog row:

   ```text
   turns = bounded_turns
   expires_at = next_state.expires_at
   ```

7. Вернуть `None`.

Инварианты:

- `state_version` не меняется;
- данные рождения и карта не меняются;
- private state/dialog deadlines совпадают;
- TTL не укорачивается и не проходит hard ceiling;
- append order определяется порядком захвата lock, а не `created_at`;
- два concurrent append сохраняют оба хода;
- порядок между двумя одновременно начавшимися append не фиксирован;
- входной `DialogTurn` не мутируется;
- применяются все три предела: 50 ходов, 8 000 code points на ход,
  120 000 code points суммарно;
- усечение ставит `truncated=True`, не меняя `status`.

Общий conformance проверяет только публично наблюдаемое:

- `SessionState.expires_at` продлён;
- ход сохранён;
- после прежнего deadline, но до нового, state и ход ещё доступны.

Равенство private `_DialogRecord.expires_at` и state deadline проверяется
только InMemory-specific тестом.

`append` не выполняет CAS и не сверяет `turn.state_version_at_answer` с
текущей версией.

Ход с marker, не равным актуальной версии, сохраняется успешно и без
изменения:

- marker меньше текущей версии;
- marker больше текущей версии;
- marker `1` при live state версии `0`.

Это намеренно: генерация могла начаться до обычного изменения state, а
`state_version_at_answer` является исторической меткой, не CAS-предикатом.

`turn.created_at` не используется для сортировки и не сверяется с `now`.

### 7.3. Повторный `turn_id`

`append` не идемпотентен.

Каждый успешный вызов добавляет одну запись, даже если в диалоге уже есть ход
с тем же `turn_id`. `turn_id` в P2 — correlation/UI metadata, а не
idempotency key и не уникальный ключ хранения.

Не вводи:

- дедупликацию;
- unique constraint;
- `TurnConflict`;
- `AlreadyAppended`;
- сравнение payload двух ходов с одинаковым ID.

Иная политика потребует отдельного изменения P1-контракта.

### 7.4. `clear`

Для live session:

1. Построить `next_state = touched(actual, now=now)`.
2. Сохранить обновлённый state.
3. Удалить dialog row, если она существует.
4. Вернуть `None`.

Live session без dialog — такой же успешный вызов; state TTL всё равно может
быть продлён.

Не меняются:

- `state_version`;
- `birth_input`;
- `birth_resolved`;
- `base_chart`;
- `created_at`;
- `hard_expires_at`.

Общий conformance проверяет наблюдаемое пустое значение и продление
`SessionState.expires_at`, но не обращается к private представлению dialog.

InMemory-specific тест закрепляет, что `clear` удаляет private dialog row.

## 8. `InMemorySessionPersistence`

### 8.1. `touch`

После общей валидации `now`, в одной критической секции:

1. найти и проверить live state;
2. missing/expired вернуть без записи;
3. вызвать `touched(actual, now=now)` для построения нового state;
4. сохранить новый state;
5. если dialog row существует, сохранить тот же tuple с private
   `expires_at == next_state.expires_at`;
6. если dialog row отсутствует, не создавать её;
7. вернуть:

   ```text
   SessionSnapshot(
       state=next_state,
       dialog=existing_turns_or_empty_tuple,
   )
   ```

`touch`:

- не меняет version или предметное содержимое;
- не укорачивает TTL;
- не проходит `hard_expires_at`;
- возвращает state и dialog из одной атомарной секции;
- не строится как `get → read → отдельные записи`.

Общий conformance проверяет:

- продлённый `snapshot.state.expires_at`;
- неизменное содержимое `snapshot.dialog`;
- доступность state и dialog после старого deadline, но до нового.

Равенство private dialog deadline и state deadline проверяется только
InMemory-specific тестом.

### 8.2. `reset`

`reset` не получает lock самостоятельно и не содержит своей копии алгоритма.

После общей проверки `now` он выполняет ровно одно persistence-делегирование:

```text
return await self.sessions.compare_and_set(
    session_id,
    expected_state_version,
    RESET_DELTA,
    now=now,
)
```

Это один вызов facet CAS:

- с исходным `session_id`;
- с исходным expected;
- с тем же объектом `RESET_DELTA`;
- с тем же `now`.

Повторная port-boundary проверка того же `now` внутри CAS допустима и не
считается вторым алгоритмом.

Так исключаются второй reset-path и deadlock на нерекурсивном
`asyncio.Lock`.

### 8.3. `delete`

Под общим lock идемпотентно удалить:

1. dialog;
2. state.

После возврата не должно оставаться orphan dialog.

Операция:

- не принимает `now`;
- не создаёт tombstone;
- успешна для missing и expired lifecycle;
- не делегируется двум публичным вызовам facets.

Порядок внутренних `pop` не является публичным контрактом; наблюдатель не
может войти между ними из-за общего lock.

## 9. Ошибки и граница P2/P4

Штатные absence и conflict представлены outcomes, а не exceptions.

Категории инфраструктурных ошибок остаются такими:

- `get`, `read` и обязательная запись внутри `touch` — `StateReadError`;
- `create`, CAS, `append`, `clear`, `reset`, `delete` —
  `StateWriteError`.

Но у dict + `asyncio.Lock` нет естественного инфраструктурного отказа.
Поэтому P2:

- не добавляет production/test fault flags;
- не добавляет callback для инъекции ошибки;
- не ловит blanket `Exception`;
- не придумывает фиктивные стабильные backend error codes;
- не преобразует validation/programming errors в `StateReadError` или
  `StateWriteError`.

Существующий `tests/session/test_contracts.py` продолжает проверять форму
иерархии и сохранение `error_code`.

Общий P2 conformance проверяет атомарность штатных success, absence и
conflict. Он не заявляет, что проверил infrastructure rollback.

P4 отдельно обязан проверить:

- использование общего `adapters._time` всеми now-bearing SQLite-методами;
- mapping реальных SQLite/OSError failures;
- rollback до commit;
- неизвестный outcome при ошибке подтверждения после commit;
- отсутствие частичного state/dialog commit;
- равенство persisted state/dialog deadlines;
- использование dialog deadline reaper;
- два независимо открытых adapter handle к одному SQLite-файлу.

P3 отдельно преобразует persistence errors в `StateReadFailed` и
`StateCommitFailed`. InMemory-адаптер не возвращает service outcomes
`Committed`, `AlreadyApplied`, `Superseded`, `StateReadFailed` или
`StateCommitFailed`.

## 10. Общий conformance-набор

### 10.1. Устройство набора и factory extension point

`tests/session/conformance.py` не должен импортировать InMemory или SQLite.

Определи в нём test harness:

```text
PersistenceHandles:
    primary: SessionPersistence
    peer: SessionPersistence

PersistenceFactory:
    () -> AsyncContextManager[PersistenceHandles]
```

Каждый вход в factory создаёт изолированный backend и гарантирует cleanup при
выходе.

Base suite объявляет единственную точку переопределения:

```text
class SessionPersistenceConformance:
    def make_factory(self, tmp_path: Path) -> PersistenceFactory:
        raise NotImplementedError
```

Каждый concrete suite обязан переопределить именно `make_factory()`.
Base suite предоставляет наследуемую pytest-fixture, а не module-level seam:

```text
@pytest.fixture
def persistence_factory(self, tmp_path: Path) -> PersistenceFactory:
    return self.make_factory(tmp_path)
```

Все inherited tests получают factory только через эту fixture:

```text
async with persistence_factory() as handles:
    ...
```

Не используй неоговорённую module-level fixture как extension point.

Concrete suite P2 называется точно:

```text
class TestInMemorySessionPersistence(SessionPersistenceConformance):
    def make_factory(self, tmp_path: Path) -> PersistenceFactory:
        ...
```

P4 должен суметь добавить:

```text
class TestSqliteSessionPersistence(SessionPersistenceConformance):
    def make_factory(self, tmp_path: Path) -> PersistenceFactory:
        ...
```

без изменения `conformance.py` и без копирования inherited tests.

InMemory override может не использовать значение `tmp_path`; SQLite override
использует его для двух независимо открытых handles к одному временному
файлу.

Назначение `peer` — запуск той же конкурентной матрицы в P4 через два
независимо открытых SQLite adapter handle к одному файлу.

Для InMemory в P2 допустимо:

```text
primary is peer
```

Для каждого конкурентного теста обязательны два режима:

```text
same-handle:  (primary, primary)
cross-handle: (primary, peer)
```

Каждый параметризованный режим получает fresh `PersistenceHandles` и fresh
lifecycle.

Для InMemory оба режима вырожденно эквивалентны. Для P4:

```text
primary is not peer
```

и оба handle работают с одним SQLite backend.

Оформи выбор пары единообразно, например через:

```text
pair_kind = "same-handle" | "cross-handle"
```

Левая и правая операции явно выполняются через выбранные `left` и `right`.
Подготовка состояния и итоговое наблюдение выполняются через `primary`.

Общий conformance не утверждает, что `primary is not peer`.

У concrete-класса нет собственного `__init__`, мешающего pytest collection,
и `__test__` не равен `False`.

Именно в `tests/session/test_in_memory.py` установи module-level marker:

```text
pytestmark = pytest.mark.no_ephemeris_autoinit
```

Маркер только в несобираемом `conformance.py` недостаточен.

Добавь обычный собираемый meta-test, который проверяет:

- имя concrete-класса начинается с `Test`;
- `__test__` не равен `False`;
- базовый conformance содержит ненулевое число методов `test_*`;
- эти унаследованные методы видны concrete-классу;
- concrete-класс действительно переопределяет `make_factory`.

Не проверяй `request.session.items`: это ломает селективный запуск одного
node id.

### 10.2. Единый источник test data P2

Все новые P2 sample objects и builders объявляются один раз в:

```text
tests/session/conformance.py
```

Включая:

- `NOW`;
- sample birth input;
- resolved birth data;
- chart specs;
- populated и reset deltas;
- selection;
- dialog turns;
- lifecycle setup helpers;
- hard-cap advancement helper.

`tests/session/test_in_memory.py` импортирует нужные builders оттуда и не
переобъявляет `BIRTH_INPUT`, `RESOLVED`, `SPEC`, turn builders или
эквивалентные литералы.

Существующие `test_state.py` и `test_dialog.py` ради этого не рефактори:
задача — не создавать третий набор данных в новых P2-файлах.

### 10.3. Правила общего набора

Общий suite:

- использует только публичные порты и outcomes;
- не читает private dict, lock или `_DialogRecord.expires_at`;
- не проверяет object identity возвращаемых моделей;
- не проверяет конкретное физическое представление пустого dialog;
- не импортирует test-модули друг из друга ради fixtures;
- получает fresh lifecycle на каждый тест;
- проверяет Protocol только через `isinstance(instance, ProtocolType)`.

Не используй `issubclass` для `SessionPersistence`: это data protocol с
атрибутами `sessions` и `dialogs`, и такой вызов закономерно может дать
`TypeError`.

Нумерованные пункты §10.4 — проверяемые утверждения, а не требование создать
отдельную test-функцию на каждый номер.

Ожидается разумная параметризация общих сценариев. В частности:

- invalid `now` проверяется одним параметризованным семейством по методам и
  двум невалидным значениям;
- отсутствие утечки `ExpiredSessionTransitionError` проверяется одним
  параметризованным семейством по операциям;
- same/cross handle — параметр каждого race;
- TTL non-regression и hard ceiling — параметризованные сценарии по
  renewer-операциям.

Не пиши отдельную механическую test-функцию на каждый нумерованный пункт и не
теряй утверждения при объединении: каждая строка матрицы должна иметь явный
assertion хотя бы в одном тесте.

### 10.4. Обязательная общая матрица

#### Конструкция и Protocol

1. Aggregate проверяется через:

   ```text
   isinstance(persistence, SessionPersistence)
   ```

2. Обе facets проверяются через:

   ```text
   isinstance(persistence.sessions, SessionStore)
   isinstance(persistence.dialogs, DialogStore)
   ```

3. `sessions` и `dialogs` — стабильные объекты при повторном доступе.
4. Два fresh factory context не разделяют состояние.

Import-boundary корневого `exact_orb.session` здесь не проверяется. Ему место
только в subprocess-тестах §12.

#### Валидация времени

5. Для каждого now-bearing метода параметризованно проверить:

   - naive `datetime`;
   - aware `datetime` с offset `UTC+03:00`.

6. Оба значения дают обычный `ValueError`.
7. Они не дают `StateReadError`, `StateWriteError` или
   `ExpiredSessionTransitionError`.
8. Валидация происходит до lookup/mutation.
9. После отказа state и dialog не изменены.
10. Для `create` invalid `now` не создаёт новую запись.
11. `delete` в эту параметризацию не входит.

#### Create/get

12. `create` возвращает точный version-0 state:

    - пустые три предметных поля;
    - `created_at == now`;
    - `expires_at == now + SLIDING_TTL`;
    - `hard_expires_at == now + HARD_TTL`.

13. Повторный create live ID даёт `SessionIdConflict` и не перезаписывает
    данные.
14. Повторный create expired ID также даёт `SessionIdConflict`.
15. Concurrent create одного ID выполняется:

    ```text
    left.sessions.create(...)
    right.sessions.create(...)
    ```

    и даёт ровно один `SessionCreated` и один `SessionIdConflict`.

16. Пункт 15 прогоняется в режимах `same-handle` и `cross-handle`.
17. `get` различает live, missing и expired.
18. `get` не меняет состояние или TTL.
19. Точная граница `now == expires_at` считается expired.
20. Чтение expired записи не удаляет её: следующий create того же ID всё ещё
    конфликтует.

#### CAS

21. Populated delta по fresh version `0` с `expected=0`:

    - возвращает `1`;
    - сохраняет state версии `1`;
    - создаёт `ChartRef(state_version=1)`.

22. CAS missing не создаёт session.
23. CAS expired не оживляет session.
24. CAS expired возвращает `SessionAbsent("expired")`, а не выпускает
    `ExpiredSessionTransitionError`.
25. Version mismatch с `expected < actual` возвращает
    `VersionConflict(actual)` без изменений.
26. Version mismatch с `expected > actual` возвращает
    `VersionConflict(actual)` без изменений.
27. Отдельно закрепи:

    ```text
    actual.state_version == 0
    expected_state_version == 1
    → VersionConflict(actual version 0)
    ```

28. Ни один конфликт не меняет state, dialog или TTL.
29. Concurrent CAS с одним expected и разными delta выполняется через:

    ```text
    left.sessions.compare_and_set(...)
    right.sessions.compare_and_set(...)
    ```

30. Каждый режим даёт:

    - один `int`;
    - один `VersionConflict`;
    - итоговую версию `N + 1`;
    - `conflict.actual`, соответствующий победившему committed state.

31. Пункт 29 прогоняется для `same-handle` и `cross-handle`.
32. Обычный успешный CAS сохраняет публично наблюдаемый tuple dialog turns.
33. CAS продлевает наблюдаемый `SessionState.expires_at`.
34. После прежнего deadline, но до нового deadline, `get` и `read`
    возвращают live state и прежние turns.
35. Общий suite не проверяет private dialog deadline.

#### Dialog

36. Live session без dialog возвращает `()`.
37. `read` missing/expired возвращает правильный `SessionAbsent`.
38. `read` не продлевает TTL и не создаёт dialog.
39. Первый append создаёт публично наблюдаемый dialog и возвращает `None`.
40. Append сохраняет предметное state и `state_version`.
41. Append продлевает наблюдаемый `SessionState.expires_at`.
42. После прежнего deadline, но до нового deadline, `get` и `read`
    возвращают live state и добавленный ход.
43. Общий suite не проверяет private `_DialogRecord.expires_at`.
44. Append missing/expired не создаёт orphan.
45. Append expired возвращает `SessionAbsent("expired")`, а не выпускает
    `ExpiredSessionTransitionError`.
46. Concurrent append выполняется через:

    ```text
    left.dialogs.append(...)
    right.dialogs.append(...)
    ```

47. Оба хода сохраняются, а порядок между concurrent append не фиксируется.
48. Пункт 46 прогоняется для `same-handle` и `cross-handle`.
49. Порядок последовательных append не зависит от `created_at`.
50. Два коротких append с одинаковым `turn_id` сохраняются как два хода.
51. Append с marker меньше actual version успешен и сохраняет marker без
    изменения.
52. Append с marker больше actual version успешен и сохраняет marker без
    изменения.
53. Append с marker `1` в live state версии `0` успешен и сохраняет ход.
54. Ход длиннее 8 000 code points обрезается и получает `truncated=True`.
55. Усечение отдельно проверено для `status="complete"` и
    `status="partial"`; status не меняется.
56. Превышение 50 ходов вытесняет самые ранние успешные append.
57. Превышение 120 000 суммарных code points также вытесняет самые ранние
    ходы.
58. `clear` live dialog возвращает `None`, очищает ленту и сохраняет карту,
    birth data и version.
59. `clear` live session без dialog успешен.
60. Оба варианта `clear` продлевают наблюдаемый state TTL.
61. `clear` missing/expired не создаёт и не оживляет данные.
62. `clear` expired возвращает `SessionAbsent("expired")`, а не выпускает
    `ExpiredSessionTransitionError`.

Не повторяй все unit-тесты чистой `append_dialog_turn`: conformance проверяет,
что адаптер действительно применяет уже протестированную policy.

#### Aggregate touch

63. `touch` missing/expired возвращает `SessionAbsent` без записи.
64. `touch` expired не выпускает `ExpiredSessionTransitionError`.
65. `touch` live session без dialog возвращает frozen snapshot с
    `dialog=()`.
66. `touch` возвращает согласованные state и dialog из одной операции.
67. `snapshot.state.expires_at` продлён.
68. `snapshot.dialog` сохраняет прежнее содержимое.
69. После прежнего deadline, но до нового, `get` и `read` видят live state и
    те же turns.
70. Общий suite не проверяет private dialog deadline.
71. Version и предметное содержимое не меняются.

#### TTL non-regression

72. Более ранний `now` не укорачивает TTL для каждого renewer:

    - CAS;
    - `touch`;
    - `append`;
    - `clear`.

Проверь это одной параметризованной группой через публичный lifecycle, без
подстановки state в private backend.

#### Hard ceiling

73. Hard ceiling проверяется только через публичный lifecycle и одной
    параметризованной группой для:

    - CAS;
    - `touch`;
    - `append`;
    - `clear`.

Для каждого renewer создай fresh session в `t0`, затем выполни:

```text
create at t0                       → expires t0 + 7d
touch at t0 + 6d                  → expires t0 + 13d
touch at t0 + 12d                 → expires t0 + 19d
touch at t0 + 18d                 → expires t0 + 25d
tested renewer at t0 + 24d        → expires t0 + 30d
```

Последняя операция проверяет именно момент обрезки:

```text
now + SLIDING_TTL == t0 + 31d
hard_expires_at == t0 + 30d
result.expires_at == t0 + 30d
```

На `t0 + 30d` lifecycle уже expired.

Не добавляй промежуточный `touch` в `t0 + 24d` перед проверяемой операцией и
не подкладывай искусственный `SessionState` в private dict.

#### Reset

74. Direct CAS с `RESET_DELTA`:

    - повышает version на один;
    - очищает предметное state;
    - очищает dialog;
    - продлевает state.

75. Отдельно созданная value-equivalent reset delta распознаётся так же.
76. Aggregate reset имеет ту же наблюдаемую семантику, что direct CAS reset
    на отдельно подготовленном эквивалентном lifecycle.
77. Reset conflict не меняет state, dialog или TTL.
78. Reset missing/expired не создаёт и не очищает посторонние данные.
79. Reset expired возвращает `SessionAbsent("expired")`, а не выпускает
    `ExpiredSessionTransitionError`.
80. Повтор старого expected после успешного reset возвращает conflict с уже
    пустым actual state.
81. Race `touch`/reset выполняется через:

    ```text
    left.touch(...)
    right.reset(...)
    ```

82. Результат соответствует только одному из двух полных serial orders:

    - touch наблюдает цельный pre-reset snapshot, затем reset;
    - reset выполняется первым, затем touch наблюдает цельный post-reset
      snapshot.

83. Смешение pre-reset state и post-reset dialog недопустимо.
84. Пункт 81 прогоняется для `same-handle` и `cross-handle`.

#### Delete

85. Delete missing и повторный delete успешны.
86. Delete live/expired lifecycle удаляет state и dialog.
87. Race append/delete выполняется через:

    ```text
    left.dialogs.append(...)
    right.delete(...)
    ```

88. Допустимы только два serial outcome:

    - append победил, затем delete удалил обе части;
    - delete победил, append вернул `SessionAbsent("not_found")`.

89. После race не остаётся orphan dialog.
90. Пункт 87 прогоняется для `same-handle` и `cross-handle`.
91. После delete разрешён диагностический create того же ID; новый lifecycle
    не должен увидеть старый dialog.

### 10.5. InMemory-specific тесты

Только `tests/session/test_in_memory.py` может проверять private lifecycle-
инварианты, которые не выражаются через порт.

Обязательно проверь:

1. `InMemorySessionStore()` без backend даёт `TypeError`.
2. `InMemoryDialogStore()` без backend даёт `TypeError`.
3. Обе facets aggregate ссылаются на один backend.
4. Обе facets aggregate используют один lock.
5. Facets не пересоздаются при повторном доступе.
6. Два aggregate не разделяют backend.
7. `touch` live session без dialog не создаёт private dialog record.
8. `clear` удаляет private dialog record.
9. После `append`:

   ```text
   _DialogRecord.expires_at == observable SessionState.expires_at
   ```

10. После обычного CAS при существующем dialog:

    - tuple ходов сохранён;
    - `_DialogRecord.expires_at == observable SessionState.expires_at`.

11. После `touch` при существующем dialog:

    - tuple ходов сохранён;
    - `_DialogRecord.expires_at == snapshot.state.expires_at`.

12. Отдельный spy-тест доказывает, что aggregate `reset` вызывает facet CAS
    ровно один раз с:

    - исходным session ID;
    - исходным expected;
    - самим `RESET_DELTA`;
    - тем же `now`.

Для delegation-теста допустим `AsyncMock` на leaf seam
`InMemorySessionStore.compare_and_set`.

Не проверяй исходный текст метода и не делай string assertion по реализации.

Не добавляй:

- public debug methods;
- `dump()`;
- `clear_all()`;
- fault switches;
- публичный backend factory;
- доступ к backend только ради тестов.

### 10.6. Конкурентные тесты

Используй:

- `asyncio.Barrier`;
- `asyncio.Event`;
- `asyncio.gather`;
- timeout только как защиту от зависания.

Не используй:

- `sleep`;
- случайные задержки;
- retry loops;
- предположение о порядке планирования tasks;
- private lock для принудительного порядка в общем conformance.

Проверяй допустимые линейризованные outcomes, а не конкретного победителя.

У InMemory после входа в метод нет suspension point между чтением и
публикацией. При свободном `asyncio.Lock` его получение не создаёт
содержательной межзадачной конкуренции.

Поэтому корректная no-await InMemory-реализация выполняет race-сценарии
вырожденно и последовательно в рамках одного event loop.

Эти тесты:

- могут поймать случайно добавленный suspension point или неверный итоговый
  outcome;
- проверяют готовность общего harness;
- не доказывают межсоединительную атомарность;
- сами по себе не доказывают необходимость или наличие lock.

Один shared lock закрепляется InMemory-specific structural test.

Настоящий смысл `cross-handle` режим получает в P4, где `primary` и `peer` —
два независимо открытых adapter handle.

## 11. Два handoff-ограничения для P3/application

### 11.1. Reset и выполняющаяся интерпретация

Не добавляй в P2 несуществующее version fencing для `DialogStore.append`.

Текущий контракт намеренно позволяет сохранить ход с
`state_version_at_answer`, который меньше актуального version: обычное
изменение карты во время генерации не должно уничтожать уже полученный ответ.

Из этого следует незакрытая гонка полного reset с уже выполняющимся append:

- append перед reset → reset очистит ход;
- reset перед append → старый ход может быть добавлен после очистки.

P2 гарантирует только линейризуемость этих операций и не вводит скрытое
отбрасывание хода.

Не добавляй тест, всегда требующий пустой dialog после concurrent
reset/append.

Решение требует отдельного application/P3-контракта: cancellation,
lifecycle epoch или иной fencing token.

### 11.2. `VersionConflict(actual version 0)`

P2 обязан вернуть `VersionConflict(actual)` и для fresh state версии `0`,
если expected завышен.

Это выявляет незакрытую границу P3:

- `VersionConflict.actual.state_version` может быть `0`;
- `AlreadyApplied.state_version` требует `ge=1`;
- для `RESET_DELTA` и empty actual функция `matches_intent` может вернуть
  `True`;
- слепое создание `AlreadyApplied(state_version=0)` даст
  `ValidationError`.

P2 не меняет outcomes и не классифицирует конфликт.

Перед P3 нужно отдельно решить одно из следующего:

- guard классификации по соотношению actual/expected;
- семантику reset уже пустой version-0 session;
- допустимость `AlreadyApplied(0)`;
- другой явно типизированный исход.

Назови это P3 debt в итоговом отчёте. Не исправляй его внутри store.

## 12. Границы модулей

Расширь `tests/test_module_boundaries.py`.

### 12.1. Статический allowlist

Добавь позитивное обнаружение новых adapter-модулей, включая:

```text
exact_orb.session.adapters
exact_orb.session.adapters._time
exact_orb.session.adapters.in_memory
```

Adapter-модули могут импортировать из проекта только:

```text
exact_orb.session.*
```

Разрешены нужные модули стандартной библиотеки, например:

```text
asyncio
dataclasses
datetime
typing
collections.abc
contextlib
```

Запрети adapter-слою прямые project imports из:

```text
exact_orb.birth
exact_orb.calculation
exact_orb.config
exact_orb.application
exact_orb.agent
exact_orb.orchestration
exact_orb.tools
exact_orb.llm
exact_orb.cli
exact_orb.engine
exact_orb.swiss_backend
```

Контрактные `birth.types` и `calculation.spec` могут загружаться только
транзитивно через `exact_orb.session.state`, а не импортироваться адаптером
напрямую.

### 12.2. Запрет скрытых источников времени и ID

Статически закрепи общий инвариант: адаптер не получает текущее время ни из
wall clock, ни из monotonic/event-loop clock. Единственный источник текущего
времени — аргумент `now`.

Запрети:

```text
datetime.now()
datetime.utcnow()
time.time()
time.monotonic()
asyncio.get_event_loop().time()
asyncio.get_running_loop().time()
loop.time()
```

Также adapter не должен:

- импортировать `time`, `uuid`, `random`, `os` или config;
- генерировать `session_id`;
- читать environment variables.

Импорт `asyncio` разрешён только для concurrency primitives, прежде всего
`asyncio.Lock`.

`adapters._time` проверяет переданный `now`, но сам текущее время не читает.

### 12.3. Clean subprocess imports

В чистом subprocess проверь два направления.

#### Contract root

```text
import exact_orb.session
```

должен:

- успешно экспортировать прежний contract API;
- не загружать `exact_orb.session.adapters`;
- не загружать native/runtime/edge/SQLite-модули.

Состав `exact_orb.session.__all__` из P1 не меняется.

#### Explicit adapter import

```text
import exact_orb.session.adapters
import exact_orb.session.adapters._time
import exact_orb.session.adapters.in_memory
```

должен:

- успешно находить три concrete-типа;
- не загружать `swisseph`;
- не загружать `sqlite3`, `aiosqlite`, `redis`;
- не загружать config, engine, application, agent, orchestration, tools,
  llm или cli;
- не инициализировать эфемериды.

У негативной проверки должен оставаться позитивный контроль: требуемые
adapter-классы и shared time validator действительно импортированы.

## 13. Точечная синхронизация документации

Не выполняй общий rewrite документации.

Исправь только фактические противоречия и новые явно зафиксированные
port-инварианты P2.

### 13.1. ADR-0009

Явной ревизией уточни:

- `touch` — единственный read-and-renew;
- `get` и `read` read-only;
- успешные CAS, append и clear — write-and-renew;
- append продлевает обе записи одним deadline;
- clear не меняет предметное state/version, но продлевает lifecycle state и
  очищает dialog.

Не создавай новый ADR: механизм lifecycle не меняется, уточняется граница
ранее принятого решения.

Синхронизируй соответствующую строку в `decisions/README.md`.

### 13.2. ADR-0024

Точечно ревизуй SQLite-решение, чтобы P4 не получил устаревший контракт:

- `DialogStore.append` и `DialogStore.clear` выполняются в одной
  `BEGIN IMMEDIATE … COMMIT` вместе с продлением parent state;
- append сохраняет dialog с тем же deadline;
- clear продлевает state и удаляет dialog;
- предметные поля state и `state_version` не меняются;
- частичное изменение state/dialog недопустимо;
- parent `SessionState` остаётся единственным источником
  live/missing/expired;
- persisted dialog deadline нужен согласованному lifecycle и reaper;
- P4 измеряет full adapter path для append/clear, а не приписывает им
  показатели bare SQL.

Это уточнение SQLite-реализации уже принятого write-and-renew, а не новое
хранилищное решение. Синхронизируй строку ADR-0024 в `decisions/README.md`.

### 13.3. Session requirements

Точечно синхронизируй:

- §3.2 — TTL-семантику append/clear;
- §4.1 — полный перечень read-only и write-and-renew операций;
- §5.2 — заменить «SessionState не трогается вообще» на точное утверждение
  про неизменные content/version и возможное продление `expires_at`;
- §12 — InMemory aggregate как поддерживаемую composition root;
- §14 — наблюдаемые conformance-свойства append/clear TTL.

Также зафиксируй:

- каждый port-параметр `now` требует aware UTC offset `0`;
- оба адаптера используют общий `adapters._time` validator;
- invalid `now` даёт `ValueError` до storage lookup/mutation;
- duplicate `turn_id` не дедуплицируется;
- `state_version_at_answer` не сверяется с current version;
- `_DialogRecord.expires_at` в P2 не является источником liveness;
- `ExpiredSessionTransitionError` не пересекает persistence boundary;
- `VersionConflict.actual` может содержать state версии `0`;
- P3 обязан тотально классифицировать этот случай, не создавая
  `AlreadyApplied(0)` автоматически.

Уточни различие logical expiry и physical removal:

- на границе операций expired state недоступен;
- P2 не удаляет строку;
- P4 reaper удалит её физически;
- после purge outcome будет `not_found`, поскольку tombstone отсутствует.

### 13.4. Component responsibilities

В `exact-orb_build_natal_components.md` синхронизируй только §6.2:

- composition через `InMemorySessionPersistence`;
- write-and-renew append/clear;
- общий backend для facets;
- общий `adapters._time` validator для InMemory и SQLite.

В пакетном дереве §2 добавь `_time.py` как единственную внутреннюю точку
adapter-boundary валидации `now` для InMemory и SQLite.

Не исправляй stale bootstrap-пример в §9.1: `bootstrap.py`,
`ContextService` и application wiring относятся к отдельной application-
задаче. Назови этот stale fragment в отчёте.

### 13.5. Диаграмма 004

В ветке очистки dialog отрази:

- CAS не выполняется;
- version, birth data и chart не меняются;
- `expires_at` state продлевается успешным `clear`;
- dialog становится пустым.

Не утверждай, что `SessionState` «не трогается вообще».

После изменения проверь реальный render PlantUML локальным renderer, если он
уже доступен. Не скачивай jar и не используй сетевой PlantUML server.

Если renderer отсутствует, сообщи об этом явно и не заявляй, что render
прошёл.

Старые файлы в `prompts/**` не редактируй.

## 14. Что не входит в P2

Не реализуй и не меняй:

### P3

- `ContextService`;
- injected clock сервиса;
- `Committed`, `AlreadyApplied`, `Superseded` mapping;
- решение для `AlreadyApplied(0)`;
- `StateReadFailed`/`StateCommitFailed` mapping;
- retry после неподтверждённого commit;
- дополнительный `get` или rebase;
- routing reset scopes.

На уровне P2 низкоуровневый N7 — это один `int` и один
`VersionConflict`, а не `Committed` и `AlreadyApplied`.

### P4

- SQLite;
- schema/migrations;
- codecs/serialization;
- WAL и `synchronous`;
- `BEGIN IMMEDIATE`;
- guarded SQL UPDATE и `rowcount`;
- executor/thread offload;
- reaper;
- restart persistence;
- benchmark;
- fault/rollback tests SQLite;
- SQLite-specific persisted TTL inspection.

Допустимо создать только общий `adapters._time`, который P4 позже обязан
переиспользовать. Сам SQLite-адаптер в P2 не создавай.

### Остальное

- Research Corpus;
- application orchestrator;
- handlers/commands;
- bootstrap;
- transport, cookies и ID generator;
- CLI;
- настройки TTL;
- новые зависимости;
- сеть или новые сервисы;
- Postgres/Redis;
- compatibility aliases;
- idempotency keys;
- dialog deduplication;
- lifecycle epochs;
- background cleanup;
- изменения расчётного поведения.

Не переименовывай `orchestration/` в этой задаче.

Не создавай commit, push или PR без отдельной команды пользователя.

## 15. Проверки

Запускай поэтапно, используя интерпретатор проекта.

### 15.1. Baseline до изменений

```text
python -m pytest -q tests/session tests/test_module_boundaries.py
```

### 15.2. Проверка collection

```text
python -m pytest --collect-only -q tests/session/test_in_memory.py
```

Команда должна показать ненулевое число собранных inherited conformance
cases. Приведи это число в отчёте.

### 15.3. Целевые после реализации

```text
python -m pytest -q tests/session/test_in_memory.py tests/session/test_contracts.py tests/session/test_state.py tests/session/test_dialog.py tests/test_module_boundaries.py
```

### 15.4. Связанные

```text
python -m pytest -q tests/session tests/test_module_boundaries.py
```

### 15.5. Полный набор

```text
python -m pytest -q
```

Не добавляй отсутствующие quality gates. Не запускай платные, сетевые или LLM
smoke tests.

Если изменена PlantUML-диаграмма и локальный renderer доступен, отрендери как
минимум `004-session-reset-and-delete.puml` и проверь отсутствие syntax
error. Не коммить сгенерированный PNG, если он не отслеживается проектом.

## 16. Acceptance P2

P2 завершён, когда одновременно выполнено следующее:

1. Реализованы три InMemory concrete-типа и общий adapter time validator.
2. Aggregate владеет одним backend и одним lock.
3. Zero-argument facet construction не может молча создать независимый
   backend.
4. Поддерживаемый construction path всегда создаёт coherent facets.
5. Все методы сохраняют async-контракт.
6. Каждый now-bearing метод использует общий `adapters._time` validator.
7. Invalid `now` даёт `ValueError` без lookup или мутации.
8. `create`, `get`, CAS и absence-семантика соответствуют P1.
9. Populated CAS закрепляет переход version `0 → 1` и `ChartRef.version=1`.
10. CAS conflict проверен в обе стороны, включая actual version `0`.
11. `ExpiredSessionTransitionError` не выходит через persistence ports.
12. Expired lifecycle не очищается и не оживляется скрыто.
13. `append` и `clear` реализуют согласованный write-and-renew.
14. Duplicate `turn_id` явно имеет append-семантику.
15. Version marker не валидируется относительно current state.
16. CAS reset и aggregate reset используют один путь.
17. `touch`, reset и delete атомарны относительно обеих записей.
18. Общий conformance проверяет только публично наблюдаемый TTL.
19. Private deadline equality проверено только InMemory-specific тестами.
20. Для каждого race определены `same-handle` и `cross-handle` режимы.
21. TTL non-regression проверен для CAS, touch, append и clear.
22. Момент hard-cap clipping достигнут только публичной цепочкой операций.
23. Base suite имеет явный `make_factory()` extension point.
24. P4 сможет подключить concrete factory без изменения `conformance.py`.
25. Нумерованная матрица реализована компактной параметризацией без потери
    assertions.
26. Concrete suite действительно собирается pytest и содержит ненулевое
    число inherited tests.
27. Корневой session package остаётся contract-only.
28. Module boundary tests имеют positive control и проходят в subprocess.
29. Документы точечно согласованы с реализованной семантикой.
30. Целевые, связанные и полные тесты фактически запущены и их результаты
    приведены без предположений.
31. Никакой код P3/P4/P5/application не добавлен.

## 17. Итоговый отчёт

Начни отчёт с результата.

Затем кратко укажи:

1. Какие concrete-типы, shared time validator и test harness добавлены.
2. Как устроены общий backend, lock, facets и reset delegation.
3. Как сформулирована write-and-renew TTL-семантика.
4. Как оба будущих adapter family используют единую точку валидации `now`.
5. Какие документы изменены.
6. Сколько conformance cases обнаружил `pytest --collect-only`.
7. Точные команды и реальные результаты всех проверок.
8. Проверялся ли реальный PlantUML render.
9. Какие пользовательские/несвязанные изменения сохранены.
10. Что общий conformance проверял только observable state TTL, а private
    dialog deadline проверялся InMemory-specific тестами.
11. Что SQLite-specific persisted deadline и reaper остаются P4.
12. Что конкурентность InMemory проверена вырожденно в рамках одного event
    loop.
13. Что независимые storage handles фактически будут проверены только
    SQLite-адаптером P4.
14. Что cancellation при реальном ожидании contended lock в P2 намеренно не
    проверялась.
15. Что infrastructure failure mapping и SQLite rollback не проверялись.

Отдельно назови оставшиеся ограничения:

- concurrent reset не fences уже выполняющийся dialog append;
- `VersionConflict(actual version 0)` требует отдельного тотального решения
  классификации P3 и не может слепо стать `AlreadyApplied(0)`;
- после physical reaper исторический `expired` становится `not_found`;
- `_DialogRecord.expires_at` в P2 хранится, но не читается;
- stale bootstrap-пример остаётся application-долгом.

Не называй P3/P4/P5 или application-слой реализованными.
