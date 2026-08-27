# Sequence diagrams — прежний набор (модель с `BuildAttempt`)

**Статус:** частично отложено. Дата пометки: 2026-08-26.

Диаграммы 001–011 нарисованы под модель, в которой построение карты
управлялось временным состоянием попытки: `BuildAttempt` со статусами
`PROCESSING | FAILED | INTERRUPTED`, счётчиком `build_revision`, правилом
*latest accepted build wins* и клиентским `idempotency_key`.

ADR-0012 в ревизии 2026-08-26 отложил durable execution protocol:
детерминированные расчёты остаются обычным request/response, восстановление
незавершённого build после reopen требует отдельного решения. Актуальность
результата обеспечивается compare-and-set по `profile_version` (ADR-0014).

**Действующий набор — `../build_natal/`.**

| Файл | Состояние |
|---|---|
| 001 позитивный расчёт | заменён `build_natal/001` |
| 002 позитивная космограмма | заменён `build_natal/003` |
| 003 disconnect и reopen | **отложен** — требует `BuildAttempt` |
| 004 application unavailable | актуален, транспортный уровень |
| 005 resolver outcomes | заменён `build_natal/004` и `005`; ветка `AMBIGUOUS` на build-пути не возникает (ADR-0005) |
| 006 calculation failure | заменён `build_natal/005` |
| 007 failure и reopen | **отложен** — требует `BuildAttempt` |
| 008 concurrent latest wins | заменён `build_natal/006`: одного CAS достаточно |
| 009 idempotency duplicate | **отложен** — клиентский ключ идемпотентности не вводится |
| 010 state commit failure | заменён `build_natal/007` |
| 011 session expired | заменён `build_natal/007` |

Отложенные диаграммы **не удаляются**: они понадобятся при возврате
к `BuildAttempt`. Условие возврата зафиксировано в
`component_responsibilities/build_natal_components.md` §12 — расчёт,
устойчиво превышающий несколько сотен миллисекунд, либо асинхронный build.

То же касается `docs/requirements/build_chart/exact_orb_negative_corner_scenarios.md`
§1.1 и §1.3: они описывают `BuildAttempt` и `build_revision` как действующий
контракт, и их следует читать как отложенные.
