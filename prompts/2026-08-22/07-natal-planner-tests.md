# Промт: тесты для ResolvedContract и NatalPlanner

**Контекст.**

Промт `06` в этой же папке добавляет `ResolvedContract` в
`intent/types.py`, меняет сигнатуру `Planner.plan()` на
`plan(contract: ResolvedContract)` и добавляет
`intent/natal_planner.py` (`NatalPlanner`) с четырьмя ветками:

1. `"natal" not in contract.topics` → `NotImplementedError`.
2. Место не определено (`latitude`/`longitude is None`) →
   `missing_slots` (`["place"]` или `["place", "time"]`), пустые
   `required_tools`/`data_selectors`, `confidence == 0.0`.
3. Место и время известны → рецепт `"natal.general"`,
   `ToolRequest(tool_name="natal", args={...})` без `"include"`.
4. Место известно, время — нет (космограмма, ADR-0008) → рецепт
   `"cosmogram.general"`, `ToolRequest(...)` с
   `args["include"] == ("positions", "aspects", "configurations",
   "strength")`.

`NatalPlanner.plan()` **не вызывает** `get_natal()` и вообще не
обращается к движку — он только собирает `ToolRequest`/
`InterpretationPlan` из уже готового `ResolvedContract`. Поэтому, в
отличие от `test_tools_natal.py`, эти тесты не считают реальную
эфемериду и не нуждаются в golden-данных с точностью до градуса — им
достаточно проверять структуру возвращаемого `InterpretationPlan`.
Для реалистичных чисел (широта/долгота/дата рождения) переиспользуй
`tests/fixtures/natal_1985.py::REFERENCE`, как это уже делает
`test_tools_natal.py`, вместо того чтобы придумывать свои координаты.

**Решения, которые уже приняты и не обсуждаются в рамках этого промта:**

- Тесты идут в новом файле `tests/test_intent_natal_planner.py`, а не
  дописываются в `tests/test_agent_skeleton.py` (тот файл — про
  абстрактный скелет с `DummyPlanner`, этот — про конкретный
  `NatalPlanner`), по той же логике, что развела `test_tools_natal.py`
  и `test_agent_skeleton.py`.
- `test_agent_skeleton.py` в рамках этого промта не меняется и не
  перезапускается с ожиданием иного результата — он и так должен
  остаться зелёным после промта `06` (это его критерий приёмки, не
  этого).
- Значения `latitude`/`longitude`/`datetime_utc` берутся из
  `REFERENCE` (`tests/fixtures/natal_1985.py`) импортом, не
  дублируются как литералы.

**Задача.**

Создать `tests/test_intent_natal_planner.py`:

1. `test_resolved_contract_defaults` — `ResolvedContract(topics=["natal"])`
   даёт `latitude is None`, `longitude is None`, `utc is None`,
   `time_known is True`, `time_assumed is False`, `focus == []`.

2. `test_natal_planner_rejects_unknown_topics` — параметризованный (или
   два отдельных теста) на `topics=[]` и `topics=["transit"]`: оба
   кидают `NotImplementedError`.

3. `test_natal_planner_reports_missing_place_only` —
   `ResolvedContract(topics=["natal"])` (место не задано, `time_known`
   по умолчанию `True`) → `plan.missing_slots == ["place"]`,
   `plan.required_tools == []`, `plan.data_selectors == []`,
   `plan.confidence == 0.0`.

4. `test_natal_planner_reports_missing_place_and_time` — то же самое, но
   с `time_known=False` → `plan.missing_slots == ["place", "time"]`.

5. `test_natal_planner_builds_natal_plan` — `ResolvedContract(
   topics=["natal"], latitude=REFERENCE["latitude"],
   longitude=REFERENCE["longitude"], utc=REFERENCE["datetime_utc"])`
   (т.е. `time_known=True` по умолчанию) →
   `plan.intent == "natal_interpretation"`,
   `plan.prompt_recipe == "natal.general"`,
   `plan.data_selectors == ["natal.general"]`,
   `plan.missing_slots == []`, `len(plan.required_tools) == 1`,
   `plan.required_tools[0].tool_name == "natal"`,
   `plan.required_tools[0].args == {"birth_datetime":
   REFERENCE["datetime_utc"], "latitude": REFERENCE["latitude"],
   "longitude": REFERENCE["longitude"]}` (ключа `"include"` в `args`
   нет).

6. `test_natal_planner_builds_cosmogram_plan` — тот же контракт, но
   `time_known=False, time_assumed=True` →
   `plan.prompt_recipe == "cosmogram.general"`,
   `plan.data_selectors == ["cosmogram.general"]`,
   `plan.missing_slots == []`,
   `plan.required_tools[0].args["include"] == ("positions", "aspects",
   "configurations", "strength")`, а `latitude`/`longitude`/
   `birth_datetime` в `args` — те же, что в тесте про натал.

7. `test_natal_planner_maps_focus_to_first_topic` —
   `ResolvedContract(topics=["natal"], focus=["career", "love"], ...)`
   (остальные поля — как в п.5) → `plan.focus == "career"`; отдельно
   `focus=[]` (или контракт без `focus`) → `plan.focus is None`.

**Ограничения.**

- Не мокать `ToolRequest`/`InterpretationPlan` — использовать реальные
  классы из `exact_orb.tools`/`exact_orb.intent`.
- Не вызывать `get_natal()` и не импортировать ничего из `engine/` —
  эти тесты про `Planner`, не про расчётный слой.
- Не дублировать `REFERENCE` — импортировать из
  `tests.fixtures.natal_1985`.
- Не проверять в этом файле, что `Planner`/`NatalPlanner` абстрактны —
  это (для `Planner`) уже покрыто `test_agent_skeleton.py::test_base_interfaces_are_abstract`.

**Проверка.**

Запусти и покажи фактический вывод (не описывай ожидаемое):

```bash
python -m pytest tests/test_intent_natal_planner.py tests/test_agent_skeleton.py tests/test_tools_natal.py -v
```

Все три файла должны быть полностью зелёными; `test_agent_skeleton.py`
не должен измениться от смены сигнатуры `Planner.plan()` в промте `06`.
