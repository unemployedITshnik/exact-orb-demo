# ADR-0020. Agent Runtime находится за application handlers; сценарии — константы

Дата: 2026-08-25.  
Ревизия: 2026-08-26 — Agent Runtime явно отделён от `Application Orchestrator`; runtime вызывается interpretation handlers, но не является общим application coordinator; `Tool` определён как agent-facing adapter к общей deterministic capability.  
Статус: принято.

## Контекст

Для demo Agent Runtime логически не обязателен: preset-интерпретация имеет почти фиксированный plan — `topic + focus` определяет известные tools и recipe.

Прямой путь:

```text
InterpretSelectionHandler
→ ChartArtifactResolver
→ InterpretationService
```

был бы короче.

Однако free-form interaction ожидается в дальнейшем, а сочетания техник уже требуют явного разделения:

- scenario selection;
- `tools[]`;
- `topics[]`;
- execution order;
- tool dependencies.

Поэтому каркас Agent Runtime выгодно заложить до появления сложного planning.

Одновременно нельзя смешивать Agent Runtime с верхнеуровневой application orchestration.

Application lifecycle включает API command, session context, handler routing и state commit.

Agent lifecycle начинается только после того, как application handler решил выполнить интерпретационный use case.

## Решение

В архитектуре существуют два последовательных coordination levels:

```text
API
 ↓
Application Orchestrator
 ↓
Handler
 ↓
Agent Runtime        // только для agent use cases
```

`Application Orchestrator` описан ADR-0006.

Настоящий ADR определяет внутренний `Agent Runtime`.

## Граница Agent Runtime

Agent Runtime вызывается из interpretation handlers:

```text
InterpretSelectionHandler
    → Agent Runtime
```

и позже:

```text
InterpretMessageHandler
    → Agent Runtime
```

Обычный `BuildNatalHandler` Agent Runtime не вызывает.

```text
BuildNatalHandler
    → ChartArtifactResolver
    → EngineService
```

Таким образом Agent Runtime не является обязательным middleware всех пользовательских операций.

## Ответственность Agent Runtime

Agent Runtime отвечает на вопрос:

> Какие capabilities нужны для данного интерпретационного сценария, в каком порядке их выполнить и как передать результаты между шагами?

Типовой pipeline:

```text
ResolvedContract
    ↓
Planner
    ↓
ScenarioRegistry
    ↓
InterpretationPlan
    ↓
PolicyService
    ↓
ToolExecutor
    ↓
Tools
    ↓
ToolResults
    ↓
InterpretationService
```

`InterpretationService` остаётся отдельным компонентом и самостоятельно владеет своим LLM/cache/budget pipeline.

Agent Runtime координирует его вызов, но не реализует его внутренности.

## ScenarioRegistry

`ScenarioRegistry` — литеральный immutable словарь, загружаемый при старте.

Сценарий определяет как минимум:

```text
id
required_fields[]
tools[]
topics[]
```

Минимум два сценария должны проверять различие `tools[]` и `topics[]`.

Например:

```text
transit:
    tools  = [natal, transit]
    topics = [transit]
```

и:

```text
natal_and_transit:
    tools  = [natal, transit]
    topics = [natal, transit]
```

Натал в первом сценарии — служебная зависимость, а не тема ответа.

Разрешение общего dependency graph не строится.

Набор техник конечен, порядок выполнения задаётся декларативно в `ScenarioDefinition`.

## Planner

Контракт:

```text
Planner.plan(contract)
    → InterpretationPlan | InputRequired
```

Planner:

- выбирает `ScenarioDefinition`;
- проверяет required fields;
- строит `tool_requests[]`;
- формирует `topics[]`.

Planner не:

- определяет верхнеуровневый API use case;
- работает с Session Store;
- изменяет пользовательское состояние;
- вызывает Engine напрямую;
- использует raw user text для выбора расчётных параметров.

`ResolvedContract` не содержит сырого пользовательского текста.

## InterpretationPlan

`InterpretationPlan` содержит как минимум:

```text
scenario_id
tool_requests[]
topics[]
```

`topics[]` сразу проектируется как список:

```text
{
    tool
    recipe
    mode
}
```

даже если demo использует только один topic.

Это сохраняет инвариант ADR-0004.

## Tool

`Tool` является agent-facing портом согласно ADR-0002.

Agent Runtime использует:

```text
ToolExecutor
→ ToolRegistry
→ Tool
```

Application Orchestrator конкретных tools не знает.

Tool предоставляет доступ к deterministic capability:

```text
NatalTool
    ↓
ChartArtifactResolver
    ↓
EngineService
```

Сам расчёт существует независимо от Tool.

Поэтому один deterministic path используется обоими вариантами:

```text
BuildNatalHandler
    → ChartArtifactResolver
    → EngineService
```

и:

```text
Agent Runtime
    → NatalTool
    → ChartArtifactResolver
    → EngineService
```

## ToolExecutor

В MVP `ToolExecutor` — последовательный deterministic executor.

Он получает готовые `tool_requests[]` и выполняет их в порядке сценария.

```text
for request in tool_requests:
    result = tool.run(request)
```

Результат предыдущего шага может передаваться в аргументы следующего по mapping, заданному `ScenarioDefinition`.

Например:

```text
NatalTool
    ↓ NatalChart
TransitTool
```

Эта зависимость является знанием Agent Runtime.

Application Orchestrator о ней не знает.

На первом этапе ToolExecutor не требует:

- динамического graph solving;
- самостоятельного replanning;
- бесконечного agent loop;
- параллельного scheduling;
- сложного retry policy.

Такие функции добавляются только при появлении сценария, который невозможно выразить конечным declarative plan.

## InterpretationService

После получения `ToolResults` Agent Runtime вызывает `InterpretationService`.

Внутренний pipeline:

```text
DataSelector
→ Evidence
→ PromptBuilder
→ Interpretation Cache
→ AdmissionControl.reserve
→ LLM Gateway
→ OutputGuard
→ Cache put
→ commit | cancel
```

принадлежит самому `InterpretationService`.

Agent Runtime не размазывает эти шаги по собственному коду.

Это ограничивает риск второго god-object.

## Capability и Policy

`CapabilityService` и `PolicyService` существуют как отдельные точки вызова с первого дня.

`CapabilityService` в demo может возвращать константный набор без `freeform_interpretation`.

`PolicyService` в минимальной реализации может пропускать заранее разрешённый preset flow.

Но вызовы этих компонентов должны существовать в правильном месте, чтобы позже включение subscription режима не потребовало переписывания Agent Runtime.

Application Orchestrator policy-решения самостоятельно не принимает.

## IntentService

`IntentService` относится только к natural-language пути.

В MVP demo preset flow он не вызывается.

При появлении subscription free-form путь выглядит концептуально:

```text
InterpretMessageHandler
→ InputGuard
→ IntentService
→ ContractValidator
→ Agent Runtime
```

Agent Runtime получает уже типизированный `ResolvedContract`.

Raw user text не используется для выбора tools.

`InterpretationQuery` передаётся в interpretation pipeline отдельно согласно ADR-0018.

## Типы, проектируемые вперёд

Три шва проектируются сразу не по минимальному demo-сценарию.

### `ResolvedContract`

Проектируется так, чтобы позднее принять результат free-form understanding.

Preset заполняет только необходимое подмножество.

### `InterpretationPlan.topics[]`

Сразу список `{tool, recipe, mode}`, а не одно поле.

### `InterpretationQuery`

Слот присутствует в recipe contract с первого дня, но в demo preset пуст.

## Рецепты

Рецепт хранится как файл на диске.

Заголовок содержит:

```text
id
topic
focus
mode
chart_kind
evidence
forbids
max_tokens
```

Тело содержит prompt recipe.

Версия recipe определяется хэшем содержимого файла.

Это позволяет автоматически инвалидировать Interpretation Cache после изменения текста prompt.

Рецепты композиционные:

```text
base.<topic>
+ focus.<focus>
+ mode.<mode>
+ kind.<chart_kind>
```

а не отдельный независимый prompt для каждой комбинации.

`forbids` являются одновременно:

- instruction модели;
- проверяемым ограничением eval/test;
- входом для OutputGuard.

## Реестры

При старте приложения наполняются:

```text
ScenarioRegistry
PromptRegistry
ToolRegistry
```

После startup они immutable.

Обязательна проверка согласованности:

- каждый tool из `ScenarioRegistry.tools[]` существует в `ToolRegistry`;
- каждый recipe из `topics[]` существует в `PromptRegistry`;
- каждый используемый mode существует;
- orphan recipe даёт warning;
- missing dependency блокирует startup.

Регистрация tools или recipes во время пользовательского запроса запрещена.

## Чего Agent Runtime не делает

Agent Runtime:

- не является API entry point;
- не выбирает application handler;
- не управляет session cookie;
- не владеет Session Store;
- не выполняет state commit;
- не создаёт `profile_version`;
- не реализует расчётные алгоритмы;
- не обращается напрямую к Swiss Ephemeris;
- не использует пользовательский текст как инструкцию к ToolExecutor;
- не реализует внутренний pipeline InterpretationService.

## Альтернативы

### Не строить Agent Runtime до subscription

Отвергнуто.

Добавить реализацию существующей точки вызова дешевле, чем позднее протаскивать новые abstraction boundaries через handlers, tests и contracts.

### Проводить Build Chart через Agent Runtime

Отвергнуто.

Явный детерминированный build имеет известный execution path и не нуждается в Planner, ScenarioRegistry и ToolExecutor.

### Application Orchestrator выбирает agent tools самостоятельно

Отвергнуто.

Это нарушает разделение двух уровней координации и превращает Application Orchestrator во второй Agent Runtime.

### Полноценный динамический planner с dependency graph

Отвергнуто для MVP.

Набор техник конечен, сценарии известны заранее и могут быть представлены декларативно.

### Рецепты в Python-коде

Отвергнуто.

Recipe version входит в Interpretation Cache key; файловая форма позволяет связать version непосредственно с содержанием.

## Последствия

- Application Orchestrator и Agent Runtime могут развиваться независимо.
- Добавление нового Tool не требует изменения Application Orchestrator.
- Build Chart не зависит от Agent Runtime.
- Agent-driven и UI-driven flows используют одно deterministic calculation ядро.
- `tools[]` и `topics[]` остаются независимыми понятиями.
- Agent Runtime остаётся небольшим deterministic execution layer, пока продукт не потребует настоящего dynamic agent loop.
- Главный объём demo остаётся не в runtime infrastructure, а в качестве `DataSelector` и focus-specific recipes.
- Риск окостенения runtime вокруг demo контролируется тестами сценариев с различными последовательностями tools.
- Согласованность реестров проверяется на startup, а не во время пользовательского запроса.
