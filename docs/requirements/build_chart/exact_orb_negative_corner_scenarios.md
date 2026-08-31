# exact-orb — негативные сценарии и corner cases построения карты

**Статус:** рабочий документ  
**Версия:** 1.0  
**Дата:** 2026-08-26  
**Область:** MVP-flow построения базовой натальной карты или космограммы через форму.

Документ описывает поведение системы при обрыве клиентского соединения, отказах application-слоя, ошибках разрешения входных данных, ошибках расчёта, проблемах сохранения состояния, истечении сессии и конкурентных запросах.

---

## 1. Общие контракты и инварианты

### 1.1. Временное состояние попытки построения

Для восстановления после закрытия вкладки и защиты от конкурирующих запросов в сессии хранится временное состояние текущей попытки:

```text
BuildAttempt {
    run_id: UUID
    revision: int
    idempotency_key: str | None
    expected_profile_version: int
    birth_input: BirthInput
    status: PROCESSING | FAILED | INTERRUPTED
    error_code: str | None
    started_at: datetime
}
```

В сессионном контексте:

```text
SessionContext {
    profile: SessionProfile | None
    current_build: BuildAttempt | None
}
```

`BuildAttempt` не является долговременным профилем. Он хранится в `Session Store` с тем же TTL, что и анонимная сессия, и нужен только для управления незавершённой пользовательской операцией.

### 1.2. Состояние успешной карты

```text
SessionProfile {
    birth_input: BirthInput
    birth_resolved: ResolvedBirthData
    profile_version: int

    base_chart: {
        profile_version: int
        spec: ChartSpec
    }

    derived_chart: {
        profile_version: int
        spec: ChartSpec
    } | None

    active_view: base | derived
}
```

`SessionProfile` изменяется только после полностью успешного расчёта и подтверждённого сохранения состояния.

### 1.3. Правило latest accepted build wins

Каждый новый осознанный запрос на построение карты увеличивает `build_revision`:

```text
build_revision += 1
```

Результат попытки разрешено сохранить в `SessionProfile`, только если одновременно выполняются две проверки:

```text
attempt.revision == current_build.revision
```

и:

```text
attempt.expected_profile_version == actual_profile_version
```

Первая проверка отвечает за актуальность пользовательского намерения. Вторая защищает committed state от конкурентной мутации.

### 1.4. Calculation Cache не является пользовательским состоянием

Даже если расчёт оказался устаревшим для конкретной сессии, полностью корректный `ChartArtifact` можно сохранить в `Calculation Cache`.

Нельзя:

- применять stale-результат к `SessionProfile`;
- делать stale-карту активной;
- откатывать более новый успешный результат.

### 1.5. Только полный успех меняет профиль

Успешная пользовательская операция состоит из двух частей:

1. получен полностью валидный `ChartArtifact`;
2. `SessionProfileDelta` успешно сохранена в `Session Store`.

Если расчёт прошёл, но состояние не сохранилось, операция считается неуспешной.

### 1.6. Пользовательская неоднозначность и системный отказ — разные outcomes

Недостаточные, некорректные или неоднозначные данные возвращаются как:

```text
InputRequired
```

Техническая недоступность resolver, calculation engine или session storage возвращается как typed system failure и `5xx`.

Техническую ошибку нельзя маскировать под просьбу исправить пользовательский ввод.

---

# N1. Закрытие вкладки или обрыв соединения с последующим открытием страницы

Сценарий делится на три случая в зависимости от того, успел ли backend принять и завершить запрос.

## N1.1. Запрос не успел попасть на backend

### Предусловия

- пользователь заполнил форму;
- успешной карты в сессии может не быть либо может существовать старая карта;
- HTTP-запрос оборвался до приёма API.

### Шаги

1. Пользователь нажимает кнопку построения карты.
2. Frontend начинает отправку `BuildNatalCommand`.
3. Вкладка закрывается или соединение обрывается до того, как API принял команду.
4. Backend не создаёт `BuildAttempt` и не увеличивает `revision`.
5. Пользователь снова открывает страницу.
6. Browser передаёт существующую HttpOnly session cookie, если её TTL ещё не истёк.
7. `ContextService` загружает последнее успешно сохранённое состояние.
8. Если `SessionProfile` отсутствует, Frontend показывает форму ввода.
9. Если существует старая успешная карта, Frontend показывает её.

### Результат

Backend не восстанавливает ввод, который никогда не получил. Состояние сессии остаётся прежним.

### Инварианты

- `BuildAttempt` не создан;
- `SessionProfile` не изменён;
- Engine не вызван.

---

## N1.2. Backend принял запрос, расчёт ещё выполняется

### Предусловия

- API принял команду;
- в `Session Store` создан `BuildAttempt(PROCESSING)`;
- расчёт ещё не завершён.

### Шаги

1. API принимает `BuildNatalCommand`.
2. Application-flow создаёт и сохраняет:

   ```text
   BuildAttempt {
       run_id = A
       revision = N
       status = PROCESSING
   }
   ```

3. `BirthDataResolver` разрешает данные рождения.
4. Запускается получение или расчёт `ChartArtifact`.
5. Пользователь закрывает вкладку либо теряет соединение.
6. HTTP/SSE-соединение прекращается.
7. Выполнение расчёта не должно семантически зависеть от жизни вкладки и по возможности продолжается на backend.
8. Пользователь снова открывает страницу.
9. Cookie восстанавливает ту же анонимную сессию.
10. `ContextService` загружает `current_build.status = PROCESSING`.
11. Frontend показывает состояние «Расчёт выполняется».
12. После успешного завершения backend выполняет обычную проверку актуальности и commit.
13. Frontend получает готовую карту при повторном чтении состояния или новом запросе.

### Альтернативная ветка: зависший PROCESSING

Если текущая реализация не гарантирует продолжение после client disconnect:

1. `PROCESSING`, превышающий установленный timeout, признаётся устаревшим.
2. Статус меняется на `INTERRUPTED` либо попытка удаляется.
3. Frontend предлагает повторить построение.
4. Повторный запрос может получить cache hit, если корректный артефакт всё же был рассчитан.

### Инварианты

- закрытие вкладки само по себе не означает отмену пользовательского intent;
- незавершённая попытка не изменяет `SessionProfile`;
- stale `PROCESSING` не хранится бессрочно.

---

## N1.3. Расчёт успешно завершился после закрытия вкладки

### Шаги

1. Пользователь закрывает страницу после принятия команды backend.
2. Engine успешно возвращает `ChartArtifact`.
3. Handler проверяет, что `BuildAttempt` всё ещё является актуальным.
4. Проверяется `expected_profile_version`.
5. `ProfileService` формирует `SessionProfileDelta`.
6. `ContextService` атомарно сохраняет профиль.
7. `current_build` очищается.
8. Пользователь позднее открывает страницу.
9. Cookie восстанавливает ту же session.
10. `ContextService` загружает обновлённый `SessionProfile`.
11. По сохранённому `ChartSpec` строится `calculation_key`.
12. `Calculation Cache` возвращает `ChartArtifact`; при eviction карта пересчитывается.
13. Frontend показывает готовую карту.

### Результат

Успешный расчёт не теряется только потому, что пользователь закрыл вкладку.

---

# N2. Application layer или Orchestrator недоступен

В MVP Orchestrator находится внутри application, а не является отдельным сетевым сервисом. Поэтому сценарий трактуется как отказ application processing.

### Шаги

1. Frontend отправляет команду построения.
2. API выполняет DTO validation, session resolution и rate-limit check.
3. API передаёт команду application-слою.
4. Orchestrator не может принять или обработать запрос из-за внутреннего отказа.
5. `BuildNatalHandler` не запускается либо use case прекращается до предметных операций.
6. `BirthDataResolver` и Engine не вызываются.
7. `SessionProfile` не изменяется.
8. Если `BuildAttempt` ещё не был зарегистрирован, `revision` не увеличивается.
9. API возвращает один из typed outcomes:

   ```text
   503 SERVICE_UNAVAILABLE
   ```

   или:

   ```text
   500 INTERNAL_ERROR
   ```

10. Frontend показывает retryable error без внутренних деталей.

### Пользовательское сообщение

```text
Не удалось выполнить запрос. Попробуйте ещё раз.
```

### Инварианты

- расчёт не начинается;
- профиль не мутируется;
- raw exception не отдаётся пользователю.

### Будущее remote-размещение

Если Orchestrator будет вынесен в отдельный сервис, сценарий дополнится:

```text
API → Orchestrator Client → timeout / connection refused → Circuit Breaker → 503
```

---

# N3. Ошибка на этапе разрешения данных рождения

## N3A. Некорректные, неполные или неоднозначные данные пользователя

### Примеры

- дата `31 февраля`;
- обязательное место отсутствует;
- `place_id` не найден;
- найдено несколько кандидатов места;
- локальное время неоднозначно из-за перевода часов.

### Шаги

1. API принимает `BuildNatalCommand`.
2. Создаётся `BuildAttempt(PROCESSING)`.
3. `BuildNatalHandler` вызывает `BirthDataResolver`.
4. Resolver обнаруживает, что вход нельзя однозначно разрешить.
5. Resolver возвращает единый контракт:

   ```text
   InputRequired {
       issues: [{
           field: "birth.place",
           code: AMBIGUOUS,
           candidates: [...]
       }]
   }
   ```

6. Handler поднимает `InputRequired` в Orchestrator без изменения контракта.
7. Engine и `Calculation Cache` не вызываются.
8. `ProfileService` не вызывается.
9. Ранее успешный `SessionProfile`, если он есть, остаётся неизменным.
10. Текущий `BuildAttempt` завершается или удаляется: ожидания фонового результата нет.
11. API возвращает typed response с полями и кандидатами.
12. Frontend подсвечивает соответствующее поле либо показывает dropdown кандидатов.
13. После исправления данных пользователь отправляет новую команду с новой `revision`.

### Результат

Это штатный outcome пользовательского ввода, а не системная ошибка.

---

## N3B. Техническая недоступность resolver-а

### Примеры

- timeout внешнего геокодера;
- локальный place catalog недоступен;
- отсутствует или повреждена `tzdata`;
- unexpected resolver exception.

### Шаги

1. Build-flow начинается штатно.
2. `BirthDataResolver` вызывает локальную или внешнюю зависимость.
3. Зависимость отвечает ошибкой либо превышает timeout.
4. Resolver возвращает typed infrastructure failure:

   ```text
   ResolutionUnavailable
   ```

5. Ошибка не преобразуется в `InputRequired`.
6. Engine не вызывается.
7. `SessionProfile` не изменяется.
8. `BuildAttempt.status` меняется на `FAILED`.
9. В `BuildAttempt.error_code` сохраняется безопасный технический код.
10. API возвращает retryable `5xx`.
11. Frontend сообщает о временной недоступности разрешения места или времени.

### Пользовательское сообщение

```text
Не удалось определить данные места или времени. Попробуйте повторить позже.
```

### Инвариант

Пользователь не должен получать рекомендацию «исправьте город», если отказала инфраструктура системы.

---

# N4. Ошибка Calculation Engine

### Возможные причины

- ошибка Swiss Ephemeris;
- отсутствуют или повреждены файлы эфемерид;
- внутренняя ошибка `calculate_natal()`;
- timeout удалённого Calculation Service;
- нарушение контракта результата.

### Шаги

1. `BirthDataResolver` успешно возвращает `ResolvedBirthData`.
2. `CalculationKeyFactory` строит `calculation_key`.
3. `Calculation Cache` возвращает miss.
4. `ChartArtifactResolver` вызывает `CalculationEnginePort`.
5. Engine начинает расчёт.
6. Возникает typed calculation failure.
7. Частичный либо невалидный результат не записывается в `Calculation Cache`.
8. `ProfileService` не вызывается.
9. Текущий успешный `SessionProfile` остаётся прежним.
10. `BuildAttempt.status` меняется с `PROCESSING` на `FAILED`.
11. В сессии сохраняется безопасный `error_code`.
12. Orchestrator формирует:

   ```text
   CalculationFailed {
       run_id
       retryable: true | false
       error_code
   }
   ```

13. API возвращает typed error.
14. Frontend показывает сообщение и действие «Повторить».
15. Повторная команда создаёт новый build attempt.

### Инварианты

- failure не кэшируется как `ChartArtifact`;
- partial artifact не кэшируется;
- только полностью валидная карта может изменить профиль.

---

# N5. Ошибка расчёта → закрытие вкладки → повторное открытие

## N5.1. В сессии уже есть старая успешная карта

### Предусловие

```text
SessionProfile {
    base_chart = OLD
}
```

### Шаги

1. Пользователь вводит новые данные рождения.
2. Создаётся `BuildAttempt B` с новым `revision`.
3. Resolver или Engine завершается технической ошибкой.
4. Сохраняется:

   ```text
   current_build {
       run_id = B
       status = FAILED
       birth_input = NEW
       error_code = ...
   }
   ```

5. Старый `SessionProfile` не изменяется.
6. Пользователь закрывает вкладку.
7. Позднее открывает страницу снова.
8. Cookie восстанавливает ту же session.
9. `ContextService.load()` возвращает:

   ```text
   SessionProfile = OLD successful state
   current_build = FAILED
   ```

10. Frontend показывает старую рабочую карту.
11. Frontend дополнительно показывает уведомление об ошибке нового построения.
12. Форма может быть предварительно заполнена из `current_build.birth_input`.
13. Пользователь выбирает одно из действий:
    - повторить расчёт;
    - изменить данные;
    - оставить старую карту без изменений.

### Пользовательское сообщение

```text
Не удалось построить карту для новых данных. Сохранена предыдущая карта.
```

---

## N5.2. Ранее успешной карты не было

### Состояние после ошибки

```text
SessionProfile = None
current_build = FAILED
```

### Поведение после reopening

1. Frontend показывает форму.
2. Форма заполняется последним `birth_input`, если он сохранён.
3. Отображается безопасное сообщение об ошибке.
4. Пользователь может повторить либо изменить данные.

---

# N6. Запрос A → закрытие вкладки → повторное открытие → новые данные B

Это основной concurrency case. Применяется правило `latest accepted build wins`.

## Этап 1. Запуск A

1. Пользователь отправляет данные A.
2. Текущее состояние:

   ```text
   profile_version = 5
   ```

3. Создаётся:

   ```text
   BuildAttempt A {
       revision = 10
       expected_profile_version = 5
       status = PROCESSING
   }
   ```

4. Начинается расчёт A.
5. Пользователь закрывает вкладку.

## Этап 2. Запуск более нового B

6. Пользователь снова открывает страницу.
7. Cookie восстанавливает session.
8. Frontend видит незавершённый A.
9. Пользователь вводит новые данные B и подтверждает построение.
10. Создаётся:

    ```text
    BuildAttempt B {
        revision = 11
        expected_profile_version = 5
        status = PROCESSING
    }
    ```

11. `current_build` теперь указывает на B.

## Ветка 1. A заканчивается первым

12. A возвращает полностью валидный `ChartArtifact`.
13. Artifact A можно сохранить в `Calculation Cache`.
14. Перед мутацией профиля проверяется:

    ```text
    A.revision == current_build.revision
    10 == 11 → false
    ```

15. A получает внутренний outcome `STALE_BUILD`.
16. `ProfileService` для A не вызывается.
17. `SessionProfile` не изменяется.

## Ветка 2. B заканчивается и становится актуальным

18. B возвращает `ChartArtifact`.
19. Проверяется:

    ```text
    B.revision == current_build.revision
    11 == 11 → true
    ```

20. Затем проверяется optimistic lock:

    ```text
    expected_profile_version == actual_profile_version
    5 == 5 → true
    ```

21. `ProfileService` формирует новую версию:

    ```text
    profile_version = 6
    birth_input = B
    birth_resolved = B
    base_chart = B
    derived_chart = None
    active_view = base
    ```

22. `ContextService` атомарно сохраняет дельту.
23. `current_build` очищается.
24. Пользователь получает карту B.

## Ветка 3. B заканчивается первым, A — позже

1. B успешно сохраняется как новая карта.
2. A позже завершает вычисление.
3. Artifact A можно сохранить в Calculation Cache.
4. Проверка `revision` для A не проходит.
5. A завершается как `STALE_BUILD` без rollback и без пользовательской ошибки.

### Инварианты

- более старый запрос никогда не перезаписывает более новый;
- stale-результат не вызывает rollback;
- stale-результат не считается ошибкой новой пользовательской сессии;
- `profile_version` не заменяет `build_revision`: нужны обе проверки.

---

# N7. Double click, retry или повторная доставка одного запроса

Этот сценарий отличается от N6: данные и пользовательское намерение не изменились.

### Шаги

1. Frontend создаёт один логический запрос построения.
2. Команде присваивается:

   ```text
   idempotency_key = X
   ```

3. Из-за double-click, refresh либо сетевого retry backend получает:

   ```text
   request A,  idempotency_key = X
   request A', idempotency_key = X
   ```

4. Backend обнаруживает уже зарегистрированный `idempotency_key`.
5. Второй запрос не создаёт новую `build_revision`.
6. Второй запрос не запускает новый Engine calculation.
7. Возможное поведение API:
   - вернуть состояние существующего `BuildAttempt`;
   - присоединить новый transport-response к тому же run;
   - после завершения вернуть ранее сохранённый результат.
8. `SessionProfile` мутируется максимум один раз.
9. Клиент получает один логический результат.

### Разделение механизмов

```text
idempotency_key
```

защищает от повторной доставки одного логического запроса.

```text
build_revision
```

защищает от конкуренции разных пользовательских намерений.

---

# N8. Расчёт успешен, но Session Store недоступен при commit

### Шаги

1. `BirthDataResolver` успешно разрешает данные.
2. Engine возвращает полностью валидный `ChartArtifact`.
3. Artifact записывается в `Calculation Cache`.
4. `ProfileService` формирует `SessionProfileDelta`.
5. `ContextService` выполняет compare-and-set в `Session Store`.
6. Session Store отвечает timeout, unavailable либо storage error.
7. Commit пользовательского состояния не подтверждён.
8. Операция не считается успешной, даже если карта рассчитана.
9. `BuildNatalResponse` с success возвращать нельзя.
10. Orchestrator формирует:

    ```text
    STATE_PERSISTENCE_FAILED
    ```

11. API возвращает `500` или `503` в зависимости от класса отказа.
12. Frontend предлагает повторить операцию.
13. При retry `Calculation Cache` вероятнее всего возвращает hit.
14. Engine повторно не вызывается.
15. Система повторяет попытку атомарного commit.

### Инвариант

Успех расчёта не равен успеху пользовательской операции. Пользователь должен получить success только после подтверждённого сохранения `SessionProfile`.

---

# N9. Session TTL истёк во время расчёта

### Шаги

1. Пользователь начинает построение карты.
2. Создаётся `BuildAttempt` в существующей session.
3. Resolver успешно разрешает вход.
4. Engine начинает расчёт.
5. До завершения расчёта TTL сессии истекает.
6. Session Store удаляет `SessionContext`.
7. Engine успешно возвращает `ChartArtifact`.
8. Artifact можно сохранить в `Calculation Cache`.
9. Handler пытается применить результат к прежней session.
10. `ContextService` обнаруживает `session not found` или `session expired`.
11. Для старого результата автоматически не создаётся новая session.
12. `SessionProfileDelta` не сохраняется.
13. Операция завершается typed outcome:

    ```text
    SESSION_EXPIRED
    ```

14. При следующем открытии страницы browser получает новую session.
15. Frontend показывает форму заново.

### Инварианты

- старый asynchronous run не создаёт профиль в новой сессии;
- Calculation Cache может сохранить артефакт, но пользовательское состояние не восстанавливается без действующей session;
- пользователь получает явное сообщение об истечении сессии.

---

# N10. Старый расчёт завершается после нового успешного результата

Этот случай не требует отдельной sequence diagram и включается как альтернативная ветка N6.

### Шаги

1. A запущен раньше B.
2. B завершается первым и успешно становится новым `SessionProfile`.
3. A заканчивается позже.
4. Artifact A можно сохранить в Calculation Cache.
5. Проверка:

   ```text
   A.revision != current_build.revision
   ```

   не проходит.

6. A получает внутренний outcome `STALE_BUILD`.
7. A не изменяет профиль.
8. A не переключает `active_view`.
9. A не откатывает B.
10. Пользователю не показывается ошибка новой карты.

---

# 2. Набор sequence diagrams

| Код | Диаграмма | Содержание |
|---|---|---|
| `S1` | Позитивный natal build | Валидное время, cache miss, успешный расчёт и commit |
| `S2` | Позитивная cosmogram | Время неизвестно, дома не считаются, успешный commit |
| `N1` | Disconnect / reopen | `PROCESSING` → reconnect → successful restore; альтернативно interrupted timeout |
| `N2` | Application unavailable | Отказ Orchestrator/application до предметной обработки |
| `N3` | Resolver outcomes | `InputRequired` против infrastructure failure |
| `N4` | Calculation failure | Engine error без мутации профиля и без partial cache |
| `N5` | Failed build → reopen | Восстановление старой карты и последнего неуспешного ввода |
| `N6` | Concurrent A → B | Latest accepted build wins; stale A отбрасывается |
| `N7` | Duplicate request | Idempotency key, один run и одна мутация |
| `N8` | State commit failure | Расчёт успешен, Session Store недоступен, операция неуспешна |
| `N9` | Session expires | Artifact рассчитан, но commit в истёкшую session запрещён |

`N10` входит в `N6` как альтернативная ветка завершения старого расчёта после успешного B.

---

# 3. Приоритет реализации и тестирования

## Критические для корректности состояния

1. `N6` — конкурирующие запросы и latest accepted build wins.
2. `N8` — расчёт успешен, но commit состояния не выполнен.
3. `N9` — истечение сессии во время asynchronous operation.
4. `N7` — duplicate delivery и идемпотентность.

## Критические для отказоустойчивости UX

1. `N1` — закрытие вкладки и восстановление.
2. `N5` — сохранение старой рабочей карты после неуспешной замены.
3. `N3` — корректное различение ошибки ввода и системного отказа.
4. `N4` — безопасная обработка ошибки расчётного ядра.

## Базовый инфраструктурный отказ

1. `N2` — недоступность application processing.

---

# 4. Ключевые проверяемые свойства

Golden/integration-тесты этих сценариев должны подтверждать:

- неуспешный расчёт никогда не изменяет `SessionProfile`;
- stale run никогда не перезаписывает более новый build;
- duplicate delivery не создаёт второй расчёт и вторую мутацию;
- success возвращается только после подтверждённого сохранения состояния;
- пользовательская неоднозначность возвращается как `InputRequired`, а infrastructure failure — как `5xx`;
- закрытие вкладки не удаляет анонимную сессию до истечения TTL;
- истёкшая session не восстанавливается старым asynchronous run;
- полностью корректный stale artifact может быть сохранён в Calculation Cache, но не в пользовательском профиле;
- partial и failed artifacts не попадают в Calculation Cache;
- старый успешный профиль сохраняется при неуспешной попытке его заменить.
