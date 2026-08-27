# ADR-0002. `Tool` — agent-facing порт с адаптерами `Local` / `Remote`

Дата: 2026-08-21.  
Ревизия: 2026-08-26 — уточнена граница `Tool`: это порт Agent Runtime, а не обязательный путь любого расчёта; `Application Orchestrator` конкретных tools не знает; детерминированная capability может вызываться как через Tool, так и напрямую application handler'ом.  
Статус: принято, не реализовано.

## Контекст

Требуется гибкая расширяемая конфигурация Agent Runtime и возможность менять физическое размещение используемых агентом capabilities без переписывания agent execution layer.

Ранее `Tool` рассматривался почти как универсальная граница между application stack и расчётным ядром. После разделения двух уровней оркестрации это определение стало слишком широким.

В системе существуют два разных способа вызвать один и тот же детерминированный расчёт.

Явная application-операция может обратиться к capability напрямую:

```text
BuildNatalHandler
    → ChartArtifactResolver
    → EngineService
    → calculate_natal()
```

Agent-сценарий обращается к той же capability через agent-facing Tool:

```text
Agent Runtime
    → ToolExecutor
    → NatalTool
    → ChartArtifactResolver
    → EngineService
    → calculate_natal()
```

Следовательно, Tool не является самим расчётом и не должен становиться обязательным middleware для любой операции только ради унификации вызова.

## Решение

`Tool.run(ToolRequest) -> ToolResult` — **agent-facing порт**, через который `Agent Runtime` получает доступ к детерминированным capabilities системы.

Интерфейс Tool асинхронный сразу.

Конкретные tools известны:

- `ToolRegistry`;
- `ToolExecutor`;
- сценарию через идентификаторы в `tools[]`.

`Planner` формирует `tool_requests[]`, но непосредственно tools не вызывает.

`Application Orchestrator` конкретных tools **не знает и не вызывает**.

### Место Tool в архитектуре

```text
Application Orchestrator
        ↓
InterpretSelectionHandler
        ↓
Agent Runtime
        ↓
Planner
        ↓
ToolExecutor
        ↓
ToolRegistry
        ↓
Tool
        ↓
deterministic capability
```

Для явного построения карты Agent Runtime и Tool не нужны:

```text
Application Orchestrator
        ↓
BuildNatalHandler
        ↓
ChartArtifactResolver
        ↓
EngineService
```

Оба пути сходятся на одной application capability и одном расчётном ядре.

### Tool не является business capability

Инвариант:

> `Tool` — способ доступа Agent Runtime к capability, а не сама capability.

Например, `NatalTool` не содержит алгоритм расчёта натальной карты.

Он адаптирует:

```text
ToolRequest
    ↓
ChartArtifactResolver / EngineService
    ↓
ToolResult
```

Предметная реализация остаётся в `engine/charts`.

### `LocalTool`

`LocalTool` вызывает capability внутри текущего application deployment.

Типовой путь:

```text
LocalTool
    → ChartArtifactResolver
    → EngineService
```

Сам Tool не знает деталей Swiss Ephemeris и не реализует расчёт.

### `RemoteTool`

`RemoteTool` предоставляет Agent Runtime тот же контракт через сетевой вызов.

```text
Agent Runtime
    → RemoteTool
    → HTTP
    → remote capability
```

`ToolRequest` и `ToolResult` остаются транспортно-нейтральными.

Переход между `LocalTool` и `RemoteTool` не должен менять Planner, ScenarioRegistry или ToolExecutor.

### Граница Local / Remote

Настоящий ADR фиксирует Local / Remote abstraction **на границе Agent Runtime → capability**.

Он не утверждает, что Tool является единственной сетевой границей всей системы.

В частности, физическое размещение `EngineService`, `ChartArtifactResolver` и Calculation Cache может быть пересмотрено отдельно.

Application handler не должен искусственно обращаться к собственному локальному Engine через Agent Tool только потому, что Agent Runtime использует этот порт.

### Обобщённые адаптеры

Для типового случая используются обобщённые адаптеры `LocalTool` / `RemoteTool`.

Отдельный Tool-класс требуется только там, где есть существенный mapping:

- аргументов;
- результатов;
- зависимостей;
- transport semantics.

Наличие отдельного имени capability в `ToolRegistry` не требует отдельного класса-обёртки, если достаточно конфигурационного mapping.

## Альтернативы

### Все обращения к Engine проводить через Tool

Отвергнуто.

Это сделало бы agent-facing abstraction обязательным application layer и связало бы обычные детерминированные use cases с Agent Runtime без необходимости.

### Application Orchestrator непосредственно вызывает Tools

Отвергнуто.

В таком случае Application Orchestrator начинает знать topology agent execution: названия tools, их порядок и зависимости, постепенно превращаясь во второй Agent Runtime.

### Класс-обёртка на каждую технику

Отвергнуто как избыточный boilerplate для типового случая `request → capability → result`.

## Последствия

- Tool является частью Agent Runtime, а не универсальным входом в Engine.
- Application Orchestrator не зависит от состава ToolRegistry.
- Добавление нового Tool не требует изменения Application Orchestrator.
- Один и тот же deterministic calculation path переиспользуется UI-driven и agent-driven flows.
- Agent Runtime может перейти с локальной capability на удалённую без изменения Planner и ScenarioRegistry.
- Физическая граница возможного выноса всего расчётного блока в отдельный сервис остаётся отдельным архитектурным решением.
