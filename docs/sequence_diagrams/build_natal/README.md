# Sequence diagrams — построение натальной карты

Диаграммы совмещают реализованный путь `BuildNatalHandler` с целевым внешним
application-flow, зафиксированным в
`docs/requirements/component_responsibilities/exact-orb_build_natal_components.md`
и ADR-0006, 0012, 0014, 0017, 0020. `ApplicationOrchestrator`, commit-flow и
внешний `ApplicationResult` на сверенном commit ещё не реализованы; на
диаграммах это проектируемый внешний контур, а не доступный API.

Ключевое отличие от [отложенной модели](../deferred/build_attempt/README.md):
`BuildAttempt`, `build_revision` и статусы попытки не используются.
Актуальность результата обеспечивается compare-and-set по `state_version`
внутри `SessionStore` (ADR-0014), durable recovery незавершённого build
отложена (ADR-0012).

| № | Файл | Сценарий | Исход |
|---|---|---|---|
| 000 | `000-build_natal_end_to_end.puml` | Сквозной путь одной операции | `BuildNatalOutcome`; после целевого commit — `ApplicationResult` |
| 001 | `001-build_natal_positive_cache_miss.puml` | Первое построение, промах кэша | `Success` |
| 002 | `002-build_natal_cache_hit.puml` | Повтор с теми же данными | `Success`, движок не вызван |
| 003 | `003-build_cosmogram_time_unknown.puml` | Пустое поле времени | `Success`, `chart_kind = cosmogram`, устойчивые аспекты + `time_uncertainty` |
| 004 | `004-build_natal_input_required.puml` | Неизвестный `place_id`; несуществующее или удвоенное локальное время | `InputRequired` |
| 005 | `005-build_natal_technical_failures.puml` | Отказ зависимости резолва; отказ движка | `ResolutionUnavailable`, `CalculationFailed` |
| 006 | `006-build_natal_superseded_cas.puml` | Два конкурентных построения в одной сессии | `Superseded` |
| 007 | `007-build_natal_commit_failure_and_session_expired.puml` | Store недоступен при commit; истёк TTL сессии | `StateCommitFailed`, `SessionAbsent(reason = expired)` |
| 008 | `008-build_natal_application_unavailable.puml` | Application-контур отказал до запуска handler | Транспортный `500` или `503`; `BuildNatalOutcome` не получен |

Диаграммы `000`–`007` показывают запроектированные ветви будущего
`ApplicationResult`; этот union ещё не реализован. `000` показывает сквозной
целевой путь, а `001`–`007` разбирают отдельные прикладные сценарии.
Транспортная диаграмма `008` заканчивается отказом до запуска handler и поэтому
не получает `BuildNatalOutcome`. Реализованный контракт handler заканчивается
на `BuildNatalOutcome`.

## Общие инварианты действующего BuildNatal-flow

- **Agent Runtime не запускается.** `Planner`, `ScenarioRegistry`,
  `ToolExecutor` и LLM на build-пути отсутствуют (ADR-0012, ADR-0020).
- **Техническая ошибка не становится `InputRequired`** — инвариант B-1.
- **`Success` только после подтверждённого commit** — успешный расчёт
  не равен успешной пользовательской операции (ADR-0006).
- **`Calculation Cache` не является пользовательским состоянием:**
  корректный, но устаревший для сессии артефакт остаётся в кэше (ADR-0017).
- **Движок возвращает `CalculationResult`, а кэш хранит `bytes`:**
  `ChartArtifact` собирает только `ChartArtifactResolver`.
- **Boundary-журнал показывает полный сквозной объектный поток:** по
  ADR-0025/0028 все пять границ пишут полные входы и фактические выходы только
  на DEBUG. Конкретный запуск ищется по `run_id`, артефакт и cache hit — по
  полному `calculation_key`; ниже DEBUG payload не сериализуется.
- **Результат нормализован:** `CalculationResult` содержит только chart, а
  `ChartArtifact` — key, spec, calculation version и chart. Сквозной validator
  связывает resolved birth data, spec, chart, key и итоговый delta (ADR-0027).

## Рендер

```
java -jar plantuml.jar -tpng -o out *.puml
```

Проверено на PlantUML 1.2024.7.
