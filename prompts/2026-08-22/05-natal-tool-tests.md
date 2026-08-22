# Промт: тесты для NatalTool и ToolRegistry.default()

**Контекст.**

Промты `02` и `03` в этой же папке добавляют `tools/natal_tool.py`
(`NatalTool`, `NatalToolArgs`) и `ToolRegistry.default()`. Нужны
тесты по образцу уже существующего `tests/test_agent_skeleton.py`
(тот же стиль: простые функции `test_*`, без классов, `pytest.raises`
для проверки исключений).

Эталонные данные рождения уже есть и переиспользуются другими тестами
расчётного слоя — `tests/fixtures/natal_1985.py`:

```python
REFERENCE = {
    "datetime_utc": datetime(1985, 9, 1, 20, 45, 0, tzinfo=timezone.utc),
    "latitude": 55.7522,
    "longitude": 37.6155,
    "house_system": "P",
    "source": "geocult.ru",
}

EXPECTED_BODY_LONGITUDES = {
    "sun": 159.340833,
    ...
}
```

Долгота Солнца там сверена с независимым источником (geocult.ru) — это
готовый golden-эталон, не нужно придумывать свои значения или считать
их «на глаз».

**Решения, которые уже приняты и не обсуждаются в рамках этого промта:**

- Тесты идут в **отдельном** файле `tests/test_tools_natal.py`, а не
  дописываются в `tests/test_agent_skeleton.py` (тот файл — про
  абстрактный скелет с `DummyTool`, этот — про конкретный `NatalTool`).
- Birth-data для тестов берётся из `tests/fixtures/natal_1985.py`
  (импортом), а не дублируется как отдельный литерал внутри теста.
- Гигиена импортов (что именно тянет `import exact_orb.tools`) в этом
  файле не проверяется — это предмет промта `08` и отдельного файла
  `tests/test_tools_imports.py`.

**Задача.**

Создать `tests/test_tools_natal.py`:

1. `test_natal_tool_args_requires_timezone_aware_datetime` —
   `NatalToolArgs(birth_datetime=<naive>, latitude=.., longitude=..)`
   кидает `pydantic.ValidationError`, сообщение содержит
   `"timezone-aware"` (используй `pytest.raises(ValidationError,
   match="timezone-aware")`).

2. `test_natal_tool_args_defaults` — при минимально заполненных
   обязательных полях `house_system == "P"`, `rulership == "combined"`,
   `include is None`.

3. `test_natal_tool_run_rejects_incomplete_args` —
   `NatalTool().run(ToolRequest(tool_name="natal", args={"latitude": 55.75}))`
   (без `longitude` и `birth_datetime`) кидает `ValidationError`.

4. `test_natal_tool_run_returns_tool_result` — с полным набором
   валидных аргументов (взятых из `REFERENCE`) `NatalTool().run(...)`
   возвращает `ToolResult` с `tool_name == "natal"`,
   `meta == {"chart_kind": "natal"}`, `warnings` — список,
   `"bodies"` и `"cusps"` присутствуют в `result.data`.

5. `test_natal_tool_run_matches_known_sun_longitude` — тот же вызов,
   `result.data["bodies"]["sun"]["longitude"]` совпадает с
   `EXPECTED_BODY_LONGITUDES["sun"]` с допуском `pytest.approx(...,
   abs=1e-3)`. Смысл теста: прогон аргументов через
   `ToolRequest`/`NatalToolArgs`/`ToolResult` (сериализация через
   `model_dump(mode="json")`) не должен искажать сам расчёт.

6. `test_tool_registry_default_registers_natal` —
   `ToolRegistry.default().list_tools() == ["natal"]` и
   `isinstance(registry.get("natal"), NatalTool)`.

**Ограничения.**

- Не мокать `get_natal()` и `swisseph` — тесты должны реально считать
  через движок (как остальные тесты расчётного слоя); эфемеридные файлы
  уже настроены через `pyproject.toml` (`[tool.exact_orb]
  ephemeris_path = "ephe"`), дополнительная настройка в тесте не нужна.
- Не дублировать `REFERENCE`/`EXPECTED_BODY_LONGITUDES` — импортировать
  из `tests.fixtures.natal_1985`.
- Не проверять в этом файле форму `ToolRegistry()` без `default()`
  — это уже покрыто `test_agent_skeleton.py`.

**Проверка.**

Запусти и покажи фактический вывод (не описывай ожидаемое):

```bash
python -m pytest tests/test_tools_natal.py tests/test_agent_skeleton.py -v
```

Оба файла должны быть полностью зелёными; `test_agent_skeleton.py` не
должен измениться от того, что `tools/__init__.py` и `tools/registry.py`
были расширены (см. промты `03`, `04`).
