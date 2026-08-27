# exact-orb — ответственности оркестраторов

**Статус:** рабочий документ  
**Область:** application coordination и agent runtime  
**Цель:** зафиксировать границы двух уровней оркестрации и не допустить смешения application-flow с agent execution.

---

## 1. Два уровня оркестрации

В exact-orb существуют два разных уровня координации:

1. **Application Orchestrator** — координирует пользовательскую операцию целиком.
2. **Agent Orchestrator / Agent Runtime** — координирует выполнение agent-сценария внутри тех операций, которым требуется интерпретация.

Это разные компоненты с разными областями ответственности.

```text
                         ┌─ BuildNatalHandler
                         │
API → Application ───────┼─ InterpretSelectionHandler
      Orchestrator       │
                         └─ InterpretMessageHandler
                                      │
                                      ▼
                              Agent Orchestrator
                                / Agent Runtime
                                      │
                             Planner / ToolExecutor
                                      │
                                     Tools
                                      │
                                      ▼
                                 EngineService
```

Ключевой принцип:

> **Application Orchestrator управляет пользовательской операцией.  
> Agent Orchestrator управляет исполнением agent-сценария.**

Agent Runtime находится **за Application Orchestrator**, а не заменяет его.

---

# 2. Application Orchestrator

## 2.1. Назначение

`Application Orchestrator` — единая точка координации application-flow.

Он отвечает за проведение входящей типизированной пользовательской команды через необходимые компоненты системы до получения результата и, при необходимости, изменения состояния сессии.

Оркестратор не является агентом и не выполняет agent loop.

Основной вопрос, на который он отвечает:

> **Как провести эту пользовательскую операцию через систему?**

---

## 2.2. Вход

Application Orchestrator получает от API уже определённый тип операции, например:

```text
BuildNatalCommand
UpdateBirthDataCommand
SetActiveViewCommand
InterpretSelectionCommand
InterpretMessageCommand
```

Выбор между этими операциями не является задачей LLM.

---

## 2.3. Основные ответственности

### 1. Управление lifecycle запроса

Оркестратор управляет общим жизненным циклом операции:

```text
request
→ context load
→ operation registration
→ handler selection
→ handler execution
→ result / ContextDelta
→ актуальность результата
→ state commit
→ response
```

---

### 2. Работа с сессионным контекстом

Оркестратор инициирует:

```text
ContextService.load(session_id)
```

и после успешного выполнения операции:

```text
ContextService.save(...)
```

Он обеспечивает единый lifecycle работы с состоянием независимо от выбранного handler.

---

### 3. Выбор Handler

Application Orchestrator маршрутизирует типизированную команду к соответствующему handler.

Например:

```text
BuildNatalCommand
    → BuildNatalHandler

InterpretSelectionCommand
    → InterpretSelectionHandler

InterpretMessageCommand
    → InterpretMessageHandler
```

Handler выбирается по типу операции, а не через LLM.

---

### 4. Управление BuildAttempt

Для операций, изменяющих расчётное состояние пользователя, Orchestrator участвует в lifecycle `BuildAttempt`.

Он должен обеспечить:

```text
run_id
build_revision
idempotency_key
expected_profile_version
status
```

и проверки:

```text
attempt.revision == current_build.revision
```

и:

```text
attempt.expected_profile_version == actual_profile_version
```

до применения результата к пользовательскому состоянию.

---

### 5. Контроль актуальности результата

Оркестратор не позволяет устаревшему выполнению изменить состояние.

Например:

```text
A revision = 10
B revision = 11

A завершается после появления B

10 != 11
→ A = SUPERSEDED
→ SessionProfile не изменяется
```

Таким образом Application Orchestrator контролирует lifecycle пользовательского намерения, а не только техническое завершение функции.

---

### 6. Idempotency lifecycle

Повторная доставка одной и той же команды не должна становиться новым пользовательским намерением.

```text
idempotency_key = X

request A
request A retry
```

должны соответствовать одной логической операции.

Application Orchestrator обеспечивает границу, на которой различаются:

```text
duplicate delivery
```

и:

```text
new user intent
```

---

### 7. Координация сохранения состояния

Handler может сформировать требуемое изменение состояния, но успешным пользовательский flow становится только после подтверждённого commit.

Принцип:

> **Успешный расчёт ≠ успешная пользовательская операция.**

Например:

```text
Engine → success
Session Store → failure
```

означает неуспешную пользовательскую операцию.

Application Orchestrator отвечает за то, чтобы success не был возвращён до завершения требуемого state transition.

---

### 8. Общая классификация outcome

Application Orchestrator является общей границей обработки исходов:

```text
Success
InputRequired
Superseded
SessionExpired
PolicyDenied
InfrastructureFailure
InternalFailure
```

При этом исход создаётся тем компонентом, который обнаружил соответствующее условие.

Оркестратор не должен превращать инфраструктурную ошибку в пользовательскую ошибку ввода.

---

### 9. Observability

`run_id` создаёт единый correlation scope для пользовательской операции.

Через него должны связываться:

```text
API request
handler
resolver
agent runtime
tool execution
engine
cache
state commit
response
```

`run_id` используется для трассировки и observability, а не как идентификатор предметного состояния.

---

## 2.4. Чего Application Orchestrator НЕ делает

Application Orchestrator:

- не рассчитывает натальную карту;
- не содержит астрологической предметной логики;
- не выбирает формулы расчёта;
- не знает деталей Swiss Ephemeris;
- не извлекает intent из natural language;
- не строит prompt;
- не вызывает LLM непосредственно;
- не определяет набор agent tools;
- не разрешает зависимости между tools;
- не реализует agent loop;
- не знает, что для транзита требуется сначала получить натальную карту;
- не выбирает `natal`, `transit`, `solar_return` tools самостоятельно.

Особенно важный инвариант:

> **Application Orchestrator знает handlers, но не должен знать topology agent tools.**

---

# 3. Agent Orchestrator / Agent Runtime

## 3.1. Назначение

`Agent Orchestrator` является координатором agent execution.

Он запускается только внутри use case, которому действительно требуется agent-сценарий.

Например:

```text
InterpretSelectionHandler
        ↓
Agent Runtime
```

или:

```text
InterpretMessageHandler
        ↓
Agent Runtime
```

Для обычного построения карты Agent Runtime не требуется.

Основной вопрос, на который он отвечает:

> **Какие deterministic capabilities нужны для этого сценария интерпретации и в каком порядке их выполнить?**

---

## 3.2. Граница Agent Runtime

Типичный flow:

```text
ResolvedContract
      ↓
Planner
      ↓
ScenarioRegistry
      ↓
InterpretationPlan
      ↓
Policy
      ↓
ToolExecutor
      ↓
Tools
      ↓
ToolResults
```

После этого результаты передаются в interpretation pipeline.

---

## 3.3. Основные ответственности

### 1. Получение сценария

Agent Runtime получает уже нормализованный детерминированный контракт.

Например:

```text
topic = transit
focus = career
active_view = derived
```

Raw user text не должен использоваться для выбора tools.

---

### 2. Planning

`Planner` преобразует контракт в `InterpretationPlan`.

Например:

```text
scenario = transit

tools = [
    natal,
    transit
]

topics = [
    transit
]
```

Agent Runtime отвечает за различие:

```text
что нужно вычислить
```

и:

```text
что нужно интерпретировать
```

---

### 3. Разрешение последовательности tools

Порядок выполнения определяется сценарием:

```text
NatalTool
    ↓
TransitTool
```

Результат одного tool может использоваться как аргумент следующего.

Эта логика принадлежит Agent Runtime, а не Application Orchestrator.

---

### 4. Выполнение ToolExecutor

`ToolExecutor` выполняет предусмотренный планом набор tools:

```text
for tool_request in plan:
    result = tool.run(...)
```

В MVP выполнение может быть последовательным.

---

### 5. Сбор ToolResults

Agent Runtime возвращает структурированный набор результатов:

```text
ToolResults
```

и не превращает их самостоятельно в пользовательский текст.

Дальнейшая интерпретация является ответственностью `InterpretationService`.

---

## 3.4. Чего Agent Runtime НЕ делает

Agent Runtime:

- не управляет HTTP request;
- не управляет session cookie;
- не владеет `Session Store`;
- не решает lifecycle `BuildAttempt`;
- не создаёт `build_revision`;
- не выполняет state commit пользовательского профиля;
- не решает, является ли входящий запрос `BuildNatal` или `InterpretMessage`;
- не занимается transport retry;
- не является владельцем `ContextService`;
- не реализует сам астрологические вычисления;
- не обращается напрямую к Swiss Ephemeris;
- не должен использовать пользовательский текст для изменения параметров расчёта.

---

# 4. Взаимодействие с Tools и Engine

Tools принадлежат границе Agent Runtime.

```text
Agent Runtime
      ↓
ToolExecutor
      ↓
NatalTool / TransitTool / ...
      ↓
ChartArtifactResolver
      ↓
EngineService
      ↓
engine/charts
```

При этом сам deterministic calculation capability существует независимо от Agent Runtime.

Поэтому построение карты через форму может идти:

```text
Application Orchestrator
        ↓
BuildNatalHandler
        ↓
ChartArtifactResolver
        ↓
EngineService
        ↓
calculate_natal()
```

а agent-сценарий:

```text
Application Orchestrator
        ↓
InterpretSelectionHandler
        ↓
Agent Runtime
        ↓
NatalTool
        ↓
ChartArtifactResolver
        ↓
EngineService
        ↓
calculate_natal()
```

Таким образом существует **один расчётный путь**, но несколько application-level способов его вызвать.

---

# 5. Tool как agent-facing adapter

`Tool` не является самим расчётом.

```text
Tool ≠ Calculation Engine
```

`Tool` — адаптер, через который Agent Runtime получает доступ к deterministic capability.

Например:

```text
NatalTool
    ↓
ChartArtifactResolver
    ↓
EngineService.calculate_natal()
```

То же вычисление может быть вызвано напрямую из другого application use case:

```text
BuildNatalHandler
    ↓
ChartArtifactResolver
    ↓
EngineService.calculate_natal()
```

Это позволяет переиспользовать расчётное ядро без зависимости от Agent Runtime.

---

# 6. Граница ответственности

| Компонент | Основной вопрос |
|---|---|
| **Application Orchestrator** | Как провести пользовательскую операцию через систему? |
| **Handler** | Что должен сделать конкретный use case? |
| **Agent Orchestrator / Runtime** | Какие tools нужны для данного agent-сценария и в каком порядке их выполнить? |
| **Tool** | Как Agent Runtime вызывает конкретную capability? |
| **ChartArtifactResolver** | Есть ли уже воспроизводимый расчётный артефакт или его необходимо вычислить? |
| **EngineService** | Какую deterministic calculation operation необходимо выполнить? |
| **Engine** | Как математически рассчитывается конкретная техника? |

---

# 7. Ключевые инварианты

### O-1. Application Orchestrator находится выше Agent Runtime

```text
API
 ↓
Application Orchestrator
 ↓
Handler
 ↓
Agent Runtime
```

Agent Runtime не является transport/application entry point.

### O-2. Agent Runtime вызывается не для каждого запроса

Детерминированный `BuildNatal` не обязан проходить через Planner, ScenarioRegistry или ToolExecutor.

### O-3. Application Orchestrator не выбирает tools

Выбор и последовательность tools принадлежат Agent Runtime.

### O-4. Tools не являются обязательным входом в Engine

Engine capabilities могут использоваться как через agent-facing Tool, так и напрямую application handler'ом.

### O-5. Один deterministic calculation path

Не должно существовать отдельной реализации расчёта для формы и отдельной для агента.

Оба пути сходятся на:

```text
ChartArtifactResolver
→ EngineService
→ engine/charts
```

### O-6. Мутация пользовательского состояния остаётся выше Agent Runtime

Agent Runtime может получить расчётные результаты, но не должен самостоятельно менять `SessionProfile`.

### O-7. Orchestrator не содержит предметной логики

Ни Application Orchestrator, ни Agent Orchestrator не знают алгоритмов расчёта карты.

---

# 8. Итоговая модель

```text
                         APPLICATION LAYER

                              API
                               │
                               ▼
                    Application Orchestrator
                               │
               ┌───────────────┼────────────────┐
               │               │                │
               ▼               ▼                ▼
        BuildNatal       InterpretSelection  InterpretMessage
          Handler             Handler           Handler
               │               │                │
               │               └───────┬────────┘
               │                       ▼
               │                 Agent Runtime
               │              Planner / Registry
               │                 ToolExecutor
               │                       │
               │                     Tools
               │                       │
               └─────────────┐   ┌─────┘
                             ▼   ▼
                     ChartArtifactResolver
                              │
                              ▼
                        EngineService
                              │
                              ▼
                     Deterministic Engine
```

Главное архитектурное разделение:

> **Application Orchestrator контролирует lifecycle пользовательской операции.**

> **Agent Orchestrator контролирует lifecycle agent execution.**

> **Engine остаётся независимым deterministic ядром и не зависит ни от одного из оркестраторов.**