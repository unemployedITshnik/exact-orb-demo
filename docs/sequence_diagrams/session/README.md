# Sequence diagrams — состояние сессии

Диаграммы фиксируют модель MVP. `SessionState` — состояние анонимной
TTL-сессии, а не пользовательский профиль. Отдельная сохраняемая сущность
`SessionContext` не вводится: `ContextService` загружает и сохраняет
`SessionState` через фасет `SessionStore`. Согласованный read-and-renew
возвращает frozen `SessionSnapshot { state, dialog }` через агрегат
`SessionPersistence`; фасетные `get`/`read` read-only.

В `SessionState` нет полной карты и `calculation_key`. Он хранит
пользовательский ввод, разрешённые данные рождения, ссылку на текущую
натальную карту и версию состояния. Полный `ChartArtifact` принадлежит
артефактному слою.

**Ходы диалога хранятся отдельной записью** через `DialogStore`, а не внутри
`SessionState`. Иначе каждая запись карты через CAS переписывала бы весь
диалог, а добавление хода конкурировало бы с изменением карты за одну версию.
Обоснование — `component_responsibilities/exact-orb_session_requirements.md`
§3.1, контракт — §3.2.

`session_id` всегда генерирует доверенный server transport. HttpOnly cookie
используется только как lookup key; команды и query не принимают ID из
body/query/path. Отсутствующий или истёкший ключ не переиспользуется для
create — transport гасит cookie и генерирует свежий ID.

Межзаписные touch/reset/delete принадлежат одному `SessionPersistence`.
Полный reset агрегат только делегирует фасетному CAS с `RESET_DELTA`;
RESET_DELTA-aware CAS сам очищает диалог в общей backend-секции.

Sequence намеренно сворачивают внутренности расчётного и интерпретационного
конвейеров. Их подробности остаются в `../build_natal/` и
`../chart_artifacts/`.

| № | Файл | Сценарий | Исход |
|---|---|---|---|
| 001 | `001-natal-session-lifecycle.puml` | Первая карта → интерпретация → явное изменение данных через форму | Диалог доступен только для успешно сохранённой текущей карты |
| 002 | `002-session-restore-on-return.puml` | Aggregate touch возвращает карту и переписку; сессии нет | Согласованный snapshot либо `SessionAbsent` |
| 003 | `003-session-store-read-failure.puml` | Отказ required touch; exact retry после неподтверждённого commit | `StateReadFailed`; N8 без rebase |
| 004 | `004-session-reset-and-delete.puml` | Очистка диалога, атомарный reset/delete, гашение cookie | `RESET_DELTA` и один aggregate lifecycle |
| 005 | `005-compare-and-set.puml` | Механизм CAS и три исхода записи | `Committed`, `Superseded`, `AlreadyApplied` |

Диаграмма 005 объясняет механизм, а не пользовательский сценарий:
прикладные ветки конкурентного построения нарисованы в
`../build_natal/006-build_natal_superseded_cas.puml` и
`../build_charts/008-exact_orb_build_chart_concurrent_latest_wins.puml`.

`derived_chart`, `active_view` и команда `SetActiveView` в MVP отсутствуют:
транзиты не входят в первый срез, поэтому активный вид всегда `base`.
Сужение временное и снимается вместе с транзитами (ревизия ADR-0016).

Изменение данных рождения из диалога и будущий agent tool `build_natal`
находятся за границей MVP. В текущем потоке используется только tool
`ensure_natal`, который материализует уже выбранную в сессии карту и не
меняет `SessionState`.

## Рендер

```
java -jar plantuml.jar -tpng -o out *.puml
```
