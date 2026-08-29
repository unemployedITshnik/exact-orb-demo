# ADR-0006. Application Orchestrator — единый координатор application-flow; stateless между запросами

Дата: 2026-08-21.  
Ревизия: 2026-08-26 — отменено ограничение редакции 2026-08-25 «Orchestrator только для потоков с интерпретацией»; разделены `Application Orchestrator` и `Agent Runtime`; построение и перестроение карты снова проходят через Application Orchestrator, но не через Agent Runtime.  
Статус: принято.

## Контекст

Исходная архитектура рассматривала Orchestrator как единую точку координации пользовательского запроса.

В редакции 2026-08-25 область Orchestrator была сужена только до потоков, включающих интерпретацию: детерминированное построение карты предлагалось выполнять отдельным application service напрямую из API.

После проработки lifecycle построения карты такое разделение признано искусственным.

Даже операция без LLM состоит не только из вызова расчётного ядра. Application-flow может включать:

- загрузку сессионного контекста;
- маршрутизацию типизированной команды;
- correlation через `run_id`;
- вызов соответствующего handler;
- проверку актуальности результата;
- применение `ContextDelta`;
- atomic state transition;
- классификацию результата и отказа;
- observability.

Одновременно внутри interpretation-flow существует другой уровень координации: planning, выбор scenario, последовательность tools и исполнение agent-сценария.

Эти ответственности необходимо разделить.

## Решение

В системе существуют **два уровня оркестрации**:

1. `Application Orchestrator`;
2. `Agent Runtime` / `Agent Orchestrator`.

Они отвечают за разные lifecycle.

```text
                         ┌─ BuildNatalHandler
                         │
API → Application ───────┼─ InterpretSelectionHandler
      Orchestrator       │
                         └─ InterpretMessageHandler
                                      │
                                      ▼
                                 Agent Runtime
```

## Application Orchestrator

`Application Orchestrator` — единая точка координации application-flow после транспортного слоя.

Он отвечает на вопрос:

> Как провести конкретную пользовательскую операцию через систему?

На вход он получает уже типизированную application-команду.

Например:

```text
BuildNatalCommand
UpdateBirthDataCommand
SetActiveViewCommand
InterpretSelectionCommand
InterpretMessageCommand
```

Выбор handler выполняется детерминированно по типу команды и не требует LLM.

Пример:

```text
BuildNatalCommand
    → BuildNatalHandler

InterpretSelectionCommand
    → InterpretSelectionHandler

InterpretMessageCommand
    → InterpretMessageHandler
```

### Ответственности Application Orchestrator

Application Orchestrator:

1. создаёт или принимает correlation `run_id`;
2. загружает необходимый application context;
3. выбирает handler;
4. запускает handler;
5. принимает результат handler'а и `ContextDelta`;
6. координирует сохранение состояния;
7. контролирует application-level completion;
8. приводит outcomes компонентов к контракту application/API;
9. обеспечивает единый observability scope;
10. для streaming-операций координирует lifecycle канала.

Предметное решение остаётся внутри handler'а и специализированных services.

### Build path

Построение базовой карты проходит через Application Orchestrator:

```text
API
→ Application Orchestrator
→ BuildNatalHandler
→ BirthDataResolver
→ ChartArtifactResolver
→ EngineService
→ ProfileService
→ ContextDelta
→ Application Orchestrator
→ ContextService
```

При этом данный flow **не является agent-flow**.

Он не проходит через:

```text
Planner
ScenarioRegistry
ToolExecutor
ToolRegistry
InterpretationService
LLM
```

### Interpretation path

Интерпретационный flow использует второй уровень координации:

```text
API
→ Application Orchestrator
→ InterpretSelectionHandler / InterpretMessageHandler
→ Agent Runtime
→ Planner
→ tools
→ InterpretationService
```

Таким образом `Agent Runtime` находится **за Application Orchestrator**, а не заменяет его.

## Agent Runtime

Agent Runtime отвечает на другой вопрос:

> Какие capabilities нужны для данного agent-сценария, в каком порядке их выполнить и как передать результаты между шагами?

В его область входят:

```text
Planner
ScenarioRegistry
ToolExecutor
ToolRegistry
Tools
```

Application Orchestrator не знает topology agent tools.

Особенно важный инвариант:

> **Application Orchestrator знает handlers, но не знает конкретных tools и их зависимостей.**

Например он не знает, что для transit-сценария сначала требуется natal tool.

Это является знанием ScenarioRegistry / Agent Runtime.

## Владение interpretation pipeline

Внутренняя связка:

```text
interpretation cache get
→ budget reserve
→ LLM
→ OutputGuard
→ cache put
→ commit | cancel
```

по-прежнему принадлежит `InterpretationService`.

Agent Runtime может вызвать `InterpretationService`, но не забирает внутрь себя ответственность за реализацию этого pipeline.

Application Orchestrator тем более не управляет отдельными шагами этого конвейера.

## Владение состоянием

`ContextService` остаётся владельцем persistence.

`ProfileService` выполняет предметные мутации `SessionProfile` и формирует изменение состояния.

Handler вызывает `ProfileService` и возвращает `ContextDelta`.

Application Orchestrator координирует применение дельты через `ContextService`.

Сам Application Orchestrator предметных решений о содержимом профиля не принимает.

## Значение stateless

Application Orchestrator не хранит пользовательское состояние **между запросами**.

Следующий запрос может обслужить другая реплика.

Внутри одного run Orchestrator является stateful coordinating frame.

Он может временно удерживать:

- загруженный context;
- выбранный handler;
- handler result;
- `ContextDelta`;
- correlation metadata;
- application outcome;
- открытый SSE-stream для interpretation-flow.

Это не противоречит stateless deployment.

Durable state находится вне процесса.

## `run_id`

`run_id` является correlation identifier.

Он используется для связывания:

```text
API
→ handler
→ Agent Runtime
→ Tool
→ Engine
→ cache
→ state commit
→ response
```

`run_id` сам по себе не является handle возобновления незавершённой операции.

Если позже появится durable asynchronous build или resumable execution, для этого потребуется отдельный контракт состояния.

## Чего Application Orchestrator не делает

Application Orchestrator:

- не рассчитывает карту;
- не содержит астрологической предметной логики;
- не знает Swiss Ephemeris;
- не строит prompts;
- не обращается непосредственно к LLM;
- не выбирает agent tools;
- не разрешает зависимости tools;
- не реализует agent loop;
- не выполняет DataSelector;
- не принимает policy-решения вместо PolicyService;
- не определяет стоимость вместо AdmissionControl;
- не мутирует `SessionProfile` самостоятельно.

## Чего Agent Runtime не делает

Agent Runtime:

- не является API entry point;
- не управляет session cookie;
- не определяет верхнеуровневый тип application command;
- не владеет Session Store;
- не применяет `ContextDelta`;
- не создаёт предметное состояние пользователя;
- не реализует астрологические расчёты.

## Альтернативы

### Build Chart в обход Application Orchestrator

Отвергнуто.

Хотя execution path детерминирован, application lifecycle остаётся общим с другими пользовательскими операциями. Обход создаёт второй coordination path с собственными правилами context, errors, state commit и observability.

### Application Orchestrator одновременно является Agent Runtime

Отвергнуто.

Компонент начал бы одновременно знать sessions, handlers, scenarios, tools, их зависимости, prompts и state lifecycle и быстро стал бы god-object.

### Все запросы проводить через Agent Runtime

Отвергнуто.

Явный детерминированный Build Chart не требует Planner, ScenarioRegistry или ToolExecutor.

## Последствия

- Все пользовательские операции имеют единую application coordination boundary.
- Build Chart остаётся полностью под контролем общего application lifecycle.
- Детерминированный build не превращается в agent-flow.
- Agent Runtime может эволюционировать независимо от API и session lifecycle.
- Новые tools не требуют изменений Application Orchestrator.
- Новый application use case требует явного handler либо явной маршрутизации.
- Число зависимостей Application Orchestrator необходимо контролировать как метрику риска god-object.
- Разделение Application Orchestrator и Agent Runtime становится архитектурным инвариантом.
