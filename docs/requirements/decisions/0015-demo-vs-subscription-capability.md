# ADR-0015. Demo и subscription различаются capability, а не только квотой

Дата: 2026-08-25. Ревизия: 2026-08-25 — capability и policy выделены
в компоненты.
Статус: принято.

## Контекст

Demo должен показать архитектуру, расчёты и LLM-интерпретацию, не превращаясь
в неограниченный proxy к оплачиваемой модели. При этом ценность диалогового
продукта требует собственного вопроса пользователя. Значит различие не сводится
к квоте.

Свободный текст — самая дорогая и самая уязвимая операция в системе, и режим
доступа является основным способом ею управлять.

## Решение

**Классы операций** — `calculation`, `preset_interpretation`,
`freeform_interpretation` (ADR-0013).

**Demo:** `calculation` и `preset_interpretation`. Preset строится
из контролируемых параметров `topic ∈ {natal, transit}`,
`focus ∈ {general, career, money, love}`. Raw prompt в промпт не добавляется —
пользовательского текста в этом режиме нет вовсе.

**Subscription:** дополнительно `freeform_interpretation` (ADR-0018).

**Решение принимают компоненты, а не подписи на стрелках:**

```
CapabilityService   session / account / tariff → CapabilitySet
PolicyService       CapabilitySet + GuardVerdict + operation / tools / args
                    → AuthorizedPlan | AuthorizedInterpretationQuery
                    | PolicyDenied
```

`PolicyService` заменяет прежний `ToolPolicy`, покрывая и допустимость
инструментов с аргументами, и допустимость пользовательского вопроса.
Оркестратор capability-решений не принимает.

**Граница.** Raw user text не передаётся в `EngineService`, `Tool`,
`Calculation Cache`, `ScenarioRegistry`. `Planner` принимает только
типизированный контракт.

## Альтернативы

Free-form всем анонимным с ограничением по токенам — отвергнуто: барьером
осталась бы только экономика, а измеренных расходов ещё нет. Исключить free-form
из продукта — отвергнуто. Два приложения — отвергнуто: одна система с разными
policy capabilities.

## Последствия

- Authorization определяет разрешённый тип операции, а не только квоту.
- Preset и free-form имеют разные input contracts.
- В demo `CapabilityService` возвращает набор без `freeform_interpretation`,
  но точка вызова существует с первого дня (ADR-0020).
- Demo пригоден для публичного размещения с ограниченным token exposure.
