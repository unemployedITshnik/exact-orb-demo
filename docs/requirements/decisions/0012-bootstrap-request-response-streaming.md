# ADR-0012. Bootstrap и chart/chat page; request/response для расчётов, streaming для интерпретации

Дата: 2026-08-21.  
Ревизия: 2026-08-26 — Build Chart возвращён под `Application Orchestrator`; уточнено, что Agent Runtime на calculation-flow не вызывается; для MVP детерминированные расчёты остаются обычным request/response, background execution и polling отложены.  
Статус: принято.

## Контекст

Интерфейс начинается до чата: пользователь сначала вводит данные рождения и получает карту, после чего взаимодействует с ней через controls, preset actions и, при наличии capability, свободный чат.

Детерминированные расчёты и LLM generation имеют разные transport requirements.

При этом обе категории операций проходят через единый `Application Orchestrator` по ADR-0006.

Нет необходимости делать transport protocol одинаковым только потому, что application coordination layer общий.

## Решение

### Start page

Пользователь вводит:

- `birth_date` — required;
- `birth_time` — optional;
- `birth_city` — required.

Место выбирается из подсказок, после выбора frontend получает `place_id`.

Build API принимает типизированные данные формы.

Путь первого построения:

```text
Frontend
→ Build Chart API
→ Application Orchestrator
→ BuildNatalHandler
→ BirthDataResolver
→ ChartArtifactResolver
→ EngineService
→ state commit
→ Chart page
```

При неизвестном времени строится космограмма согласно ADR-0008.

На данном пути:

- `IntentService` не вызывается;
- `Planner` не вызывается;
- `ScenarioRegistry` не используется;
- `ToolExecutor` не используется;
- Agent Runtime не запускается;
- LLM не вызывается.

### Chart page

Chart page содержит:

- визуализацию активной карты;
- исходные данные рождения;
- явные controls их изменения;
- переключение base / derived view;
- preset interpretation actions;
- свободный чат при наличии соответствующей capability.

Изменения состояния являются отдельными типизированными application commands.

### Application coordination

Все бизнес-операции после API проходят через `Application Orchestrator`.

Например:

```text
Build Chart API
        ↓
Application Orchestrator
        ↓
BuildNatalHandler
```

и:

```text
Selection API
        ↓
Application Orchestrator
        ↓
InterpretSelectionHandler
        ↓
Agent Runtime
```

Общий Orchestrator не означает общий execution pipeline.

### Transport для calculation operations

Операции без LLM в MVP используют обычный HTTP request/response.

Например:

```text
POST /charts/natal
    ↓
calculate / cache
    ↓
state commit
    ↓
200 + ChartDTO
```

К этому классу относятся:

- первое построение base chart;
- изменение данных рождения;
- перестроение base chart;
- построение derived chart;
- изменение параметров derived chart;
- переключение вида, если оно требует расчёта.

Success возвращается только после завершения необходимых state mutations.

### Request/response не определяет внутреннюю модель исполнения

Клиентский request/response контракт не означает, что CPU-bound расчёт обязан выполняться непосредственно в event loop web process.

Внутри deployment допускаются:

- отдельные worker processes;
- thread/process executors;
- локальная serialization вокруг thread-unsafe библиотеки;
- в дальнейшем отдельный Calculation Service.

Эта реализация не должна менять внешний transport contract без необходимости.

`async def` сам по себе не делает CPU-bound расчёт параллельным.

### Background execution

Схема:

```text
POST
→ 202 Accepted
→ durable job
→ polling / reconnect
```

для calculation-flow в MVP **не вводится**.

Также не гарантируется продолжение конкретного HTTP build-run после уничтожения worker process.

Возобновление незавершённого calculation run требует отдельного durable execution protocol и отдельного архитектурного решения.

Такое усложнение вводится только после измерения реальной latency и concurrency.

### Метрики до изменения transport model

До перехода к background jobs должны быть измерены как минимум:

```text
calculate_natal:
    p50
    p95
    p99

calculate_cosmogram:
    p50
    p95
    p99

calculate_transit:
    p50
    p95
    p99
```

и поведение при конкурентности:

```text
1
2
5
10
```

одновременных расчётов либо при ином профиле, соответствующем ожидаемой нагрузке.

Особое внимание требуется `pyswisseph`, поскольку библиотека использует глобальное состояние и параллельное выполнение требует проверки либо изоляции.

### Transport для interpretation operations

Операции, включающие LLM generation, используют SSE.

События:

```text
status
input_required
token
done
error
```

После начала stream HTTP status изменить нельзя.

Техническая ошибка после начала генерации доставляется событием:

```text
error
```

внутри потока.

### Client disconnect

Для обычного request/response calculation-flow disconnect клиента не превращает `run_id` в resumable handle.

После повторного открытия страницы frontend восстанавливает **последнее успешно committed состояние сессии**.

Гарантированное восстановление незавершённого build, отображение `PROCESSING` после reopen и присоединение к выполняющейся операции требуют отдельного durable `BuildAttempt/job` protocol и в настоящий ADR не входят.

## Альтернативы

### SSE для всех операций

Отвергнуто.

Для детерминированного build streaming не даёт достаточной пользы относительно сложности протокола.

### Background task + polling с первого MVP

Отложено.

Без измеренной latency и нагрузки это преждевременное инфраструктурное усложнение.

### Build API вызывает calculation service напрямую, минуя Application Orchestrator

Отменено редакцией 2026-08-26.

Единый `Application Orchestrator` нужен для общего lifecycle пользовательских операций; при этом Agent Runtime по-прежнему не участвует в обычном build.

## Последствия

- Пользователь получает карту до первого обращения к LLM.
- Calculation и interpretation имеют разные transport semantics.
- Build Chart контролируется Application Orchestrator.
- Agent Runtime на bootstrap path отсутствует.
- Для MVP не требуется queue/polling infrastructure.
- Масштабирование расчётного слоя может выполняться независимо от изменения HTTP-контракта.
- `run_id` остаётся correlation identifier, а не механизмом resume.
- Durable recovery незавершённого build остаётся отдельным будущим решением.
