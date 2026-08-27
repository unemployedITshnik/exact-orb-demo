# Sequence diagrams — построение натальной карты

Диаграммы описывают **действующую модель реализации**, зафиксированную
в `docs/requirements/component_responsibilities/build_natal_components.md`
и ADR-0006, 0012, 0014, 0017, 0020.

Ключевое отличие от прежнего набора в `build_charts/`: `BuildAttempt`,
`build_revision` и статусы попытки не используются. Актуальность результата
обеспечивается compare-and-set по `profile_version` внутри `SessionStore`
(ADR-0014), durable recovery незавершённого build отложена (ADR-0012).

| № | Файл | Сценарий | Исход |
|---|---|---|---|
| 001 | `001-build_natal_positive_cache_miss.puml` | Первое построение, промах кэша | `Success` |
| 002 | `002-build_natal_cache_hit.puml` | Повтор с теми же данными | `Success`, движок не вызван |
| 003 | `003-build_cosmogram_time_unknown.puml` | Пустое поле времени | `Success`, `chart_kind = cosmogram` |
| 004 | `004-build_natal_input_required.puml` | Неизвестный `place_id`; несуществующее или удвоенное локальное время | `InputRequired` |
| 005 | `005-build_natal_technical_failures.puml` | Отказ зависимости резолва; отказ движка | `ResolutionUnavailable`, `CalculationFailed` |
| 006 | `006-build_natal_superseded_cas.puml` | Два конкурентных построения в одной сессии | `Superseded` |
| 007 | `007-build_natal_commit_failure_and_session_expired.puml` | Store недоступен при commit; истёк TTL сессии | `StateCommitFailed`, `SessionExpired` |

Семь диаграмм покрывают все члены `ApplicationResult`.

## Что видно на всех диаграммах

- **Agent Runtime не запускается.** `Planner`, `ScenarioRegistry`,
  `ToolExecutor` и LLM на build-пути отсутствуют (ADR-0012, ADR-0020).
- **Техническая ошибка не становится `InputRequired`** — инвариант B-1.
- **`Success` только после подтверждённого commit** — успешный расчёт
  не равен успешной пользовательской операции (ADR-0006).
- **`Calculation Cache` не является пользовательским состоянием:**
  корректный, но устаревший для сессии артефакт остаётся в кэше (ADR-0017).

## Рендер

```
java -jar plantuml.jar -tpng -o out *.puml
```

Проверено на PlantUML 1.2024.7.
