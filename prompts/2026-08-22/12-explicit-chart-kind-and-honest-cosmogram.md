# Промт: честная космограмма и явный chart_kind

**Заменяет:** `prompts/2026-08-22/10-include-gates-computation.md` и
`prompts/2026-08-22/11-include-gates-computation-tests.md`.

**Уточняет после исполнения:** `02-natal-tool-adapter.md` и
`05-natal-tool-tests.md`. Эти исполненные промты не редактировать.

**Контекст.**

ADR-0008 говорит: если время рождения неизвестно, считается космограмма.
Полдень используется только как техническая точка для долгот планет; дома,
ASC/MC, управители домов и производные от углов данные не рассчитываются
вовсе.

Сейчас `get_natal(..., include=...)` фильтрует только финальный
`NatalChart`, но расчёт выполняет полностью:

- `calculate_houses()` вызывается всегда;
- `calculate_bodies(..., cusps)` всегда проставляет `body.house`;
- `_add_derived_points()` добавляет `pars_fortune` от ASC;
- аспекты строятся к `asc`/`mc`/`vertex`;
- strength получает акцидентальные данные от домов;
- на широтах, где Плацидус вырождается, космограмма падает, хотя дома ей
  не нужны.

Второй блокер: `NatalTool` сейчас выводит `chart_kind` по форме результата:

```python
chart_kind = "natal" if chart.cusps is not None else "cosmogram"
```

Это нарушает инвариант ADR-0008: вид карты должен быть явным полем, а не
догадкой по отсутствию домов.

**Архитектурное решение.**

Сценарное решение `natal` vs `cosmogram` принадлежит intent/planner-слою:
именно там известно, что время рождения неизвестно и выбран сценарий
космограммы.

`get_natal()` не должен читать пользовательский intent и не должен
угадывать сценарий по результату. Он получает явный режим расчёта,
использует его при проверке `include` и возвращает `chart_kind` в
`NatalChart`.

Пока полноценный `IntentService` не реализован, граница выглядит так:

- planner/tool request для космограммы передаёт:
  `chart_kind="cosmogram"` и `include=("positions", "aspects", "configurations")`;
- planner/tool request для обычного натала передаёт:
  `chart_kind="natal"` и полный include либо `include=None`;
- `NatalTool` валидирует и прокидывает `chart_kind` в `get_natal()`;
- `get_natal()` возвращает `NatalChart.chart_kind`;
- `NatalTool` кладёт в `ToolResult.meta` именно `chart.chart_kind`.

Если `intent/natal_planner.py` уже существует в рабочем дереве, обновить
его `ToolRequest`: обычный натал должен передавать `"chart_kind": "natal"`,
космограмма — `"chart_kind": "cosmogram"`. Если файла ещё нет, не
создавать planner в этом промте.

**Задача.**

1. В `src/exact_orb/engine/charts/natal.py` добавить тип:

```python
ChartKind = Literal["natal", "cosmogram"]
```

2. В модель `NatalChart` добавить публичное поле:

```python
chart_kind: ChartKind
```

Поля существующих моделей не переименовывать и не удалять.

3. В сигнатуру `get_natal()` добавить обязательный keyword-only параметр:

```python
chart_kind: ChartKind
```

Без значения по умолчанию.

`get_natal()` не должен сам определять вид карты по `include`, `cusps`,
`angles` или другим данным результата. Вид карты приходит сверху как
явное решение сценарного слоя.

Обновить все прямые вызовы `get_natal()` в `src` и `tests`:

- обычный натал: `chart_kind="natal"`;
- космограмма: `chart_kind="cosmogram"`.

Если после правки где-то остаётся вызов `get_natal()` без `chart_kind`,
это ошибка рефакторинга.

4. В `_normalize_include()` сохранить текущую проверку неизвестных блоков
и добавить когерентность:

```python
if "rulers" in include_blocks and "houses" not in include_blocks:
    raise ValueError('include block "rulers" requires "houses"')
if "strength" in include_blocks and "houses" not in include_blocks:
    raise ValueError('include block "strength" requires "houses"')
```

5. Добавить проверку согласованности `chart_kind` и `include` после
нормализации `include`:

- `chart_kind == "natal"` требует `"houses"` в `include_blocks`;
- `chart_kind == "cosmogram"` требует отсутствия `"houses"`, `"rulers"` и
  `"strength"`.

Сообщения ошибок должны содержать и `chart_kind`, и конфликтующий блок.

6. В `get_natal()` сделать `include` гейтом вычислений, а не только
сборки ответа:

```python
houses_included = "houses" in include_blocks

if houses_included:
    cusps, angles = calculate_houses(julian_day_ut, latitude, longitude, hsys)
else:
    cusps, angles = None, {}
```

7. В `src/exact_orb/engine/ephemeris/calc.py` изменить `calculate_bodies()`:

```python
def calculate_bodies(
    julian_day_ut: float,
    body_ids: Mapping[str, int],
    flags: int,
    cusps: tuple[HouseCusp, ...] | None,
) -> tuple[dict[str, BodyPosition], list[CalculationWarning]]:
```

Если `cusps is None`, не вызывать `house_for_longitude()` и ставить
`house=None` каждому телу. Остальную логику не менять.

8. В `_add_derived_points()` и `_derived_point()` разрешить
`cusps: tuple[HouseCusp, ...] | None`.

При `cusps is None`:

- `south_node` добавляется с `house=None`;
- `selena` добавляется с `house=None`;
- `pars_fortune` не добавляется, потому что `angles` пуст и условия
  `"asc" in angles` не выполнено.

Не вызывать `part_of_fortune()` без домов/ASC.

9. Аспекты и конфигурации в космограмме считать только по телам.

`_calculate_natal_aspects(bodies, angles, ...)` можно оставить без
изменений: при `angles={}` она не добавит угловые точки.

В тестах проверять отсутствие аспектов к:

```python
{"asc", "mc", "dsc", "ic", "vertex", "pars", "pars_fortune"}
```

10. `strength` без домов не считать и не отдавать частично.

Комбинация `"strength"` без `"houses"` должна быть `ValueError`.
Не реализовывать режим “только эссенциальная сила” в этом промте.

11. Если `houses_included is False`, добавить предупреждение:

```python
CalculationWarning(
    source="include",
    message=(
        "houses were not requested: house placements, angles, and "
        "angle-derived points are absent from this chart"
    ),
    retflags=None,
)
```

12. В финальном `NatalChart(...)` заполнить:

```python
chart_kind=chart_kind
cusps=cusps if houses_included else None
angles=angles if houses_included else None
```

`house_rulers` и `interceptions` возвращать только если `"rulers"` в
`include_blocks`, как раньше.

13. В `src/exact_orb/tools/natal_tool.py`:

- импортировать `Literal`;
- добавить в `NatalToolArgs` обязательное поле:

```python
chart_kind: Literal["natal", "cosmogram"]
```

Без дефолта.

- прокинуть `chart_kind=args.chart_kind` в `get_natal()`;
- удалить временный вывод `chart_kind` из `chart.cusps`;
- в `ToolResult.meta` использовать только:

```python
meta={"chart_kind": chart.chart_kind}
```

- обновить докстроку: больше не писать, что `chart_kind` временно
  выводится из формы результата.

14. Если в рабочем дереве уже есть `src/exact_orb/intent/natal_planner.py`,
обновить `ToolRequest`:

Обычный натал:

```python
args={
    "birth_datetime": contract.utc,
    "latitude": contract.latitude,
    "longitude": contract.longitude,
    "chart_kind": "natal",
}
```

Космограмма:

```python
args={
    "birth_datetime": contract.utc,
    "latitude": contract.latitude,
    "longitude": contract.longitude,
    "chart_kind": "cosmogram",
    "include": ("positions", "aspects", "configurations"),
}
```

Если файла нет, не создавать его в этом промте.

**Тесты.**

Создать `tests/test_natal_include_gating.py`.

Покрыть:

1. `get_natal(..., chart_kind="natal")` без `include` остаётся обычным
   наталом: `chart.chart_kind == "natal"`, `cusps`/`angles` есть,
   `strength is not None`, `warnings == ()`.

2. Долгота Солнца в обычном натале совпадает с
   `EXPECTED_BODY_LONGITUDES["sun"]` из `tests.fixtures.natal_1985`
   с `abs=1e-3`.

3. `get_natal(..., chart_kind="cosmogram", include={"positions"})`:
   `chart.chart_kind == "cosmogram"`,
   `cusps is None`, `angles is None`,
   `house_rulers is None`, `interceptions is None`.

4. В космограмме все `body.house is None`.

5. В космограмме `"pars_fortune" not in chart.bodies`, но
   `"south_node"` и `"selena"` присутствуют и имеют `house is None`.

6. Долготы общих тел при `include={"positions"}` совпадают с обычным
   наталом. Сравнивать только общие ключи.

7. `include={"positions", "aspects", "configurations"}` для космограммы
   не содержит аспектов к углам и `pars`.

8. Для контраста обычный натал содержит хотя бы один аспект к углам,
   чтобы тест не был зелёным случайно.

9. Два вызова космограммы в пределах одних суток с разным временем:
   у всех тел `house is None`, `pars_fortune` отсутствует. Долготы тел
   не сравнивать.

10. `include={"positions", "rulers"}` с `chart_kind="cosmogram"` кидает
    `ValueError`, сообщение содержит `rulers` и `houses`.

11. `include={"positions", "strength"}` с `chart_kind="cosmogram"` кидает
    `ValueError`, сообщение содержит `strength` и `houses`.

12. `chart_kind="natal"` вместе с `include={"positions"}` кидает
    `ValueError`, сообщение содержит `chart_kind` и `houses`.

13. `chart_kind="cosmogram"` вместе с `include=None` или include с
    `"houses"` кидает `ValueError`.

14. Космограмма на широте `78.0` с `include={"positions"}` считается
    успешно: `calculate_houses()` не должен вызываться.

15. Тот же вызов на широте `78.0` с обычным наталом по-прежнему кидает
    `ValueError` про Placidus/high latitude.

16. При `include` без `"houses"` в `chart.warnings` есть ровно одно
    предупреждение с `source == "include"`, а message содержит `houses`.

Обновить `tests/test_tools_natal.py`:

- все вызовы `NatalTool().run(...)` с валидными args должны передавать
  `"chart_kind": "natal"` или `"chart_kind": "cosmogram"`;
- happy path: дополнительно проверить, что
  `result.data["chart_kind"] == "natal"`;
- добавить/обновить тест: `NatalTool().run(...)` без `chart_kind` в
  `args` кидает `pydantic.ValidationError`;
- тест космограммы должен передавать:
  `chart_kind="cosmogram"` и
  `include=("positions", "aspects", "configurations")`;
- убрать `"strength"` из reduced include;
- дополнительно проверить:
  все `body["house"] is None`,
  `"pars_fortune" not in result.data["bodies"]`,
  `result.data["chart_kind"] == "cosmogram"`,
  `result.meta == {"chart_kind": "cosmogram"}`.

Обновить остальные тесты, которые напрямую вызывают `get_natal()`:
добавить `chart_kind="natal"` без изменения смысла проверок.

**Ограничения.**

- Не менять формулы эфемерид, аспектов, конфигураций и strength.
- Не реализовывать `IntentService`.
- Не создавать `NatalPlanner`, если его ещё нет.
- Не добавлять режим частичной strength для космограммы.
- Не менять CLI-поведение обычного натала; в CLI добавить
  `chart_kind="natal"` при вызове `get_natal()`.
- Не менять golden-файл.
- Не мокать `swisseph`, `get_natal()` или `calculate_houses()`.
- Исполненные промты не редактировать; если нужно зафиксировать эту правку
  в папке промтов, сохранить новым файлом со ссылкой на заменяемые.

**Критерии приёмки.**

1. `python -m pytest tests/test_natal_include_gating.py tests/test_tools_natal.py -v`
   зелёный.

2. `python -m pytest tests/ -q` зелёный.

3. Golden-тест `tests/golden/natal_1985_human.txt` совпадает байт в байт.

4. CLI работает как раньше:

```bash
exact-orb "01.09.1985 20.45 gmt+3"
```

5. В `src/exact_orb/tools/natal_tool.py` больше нет вывода
   `chart_kind` через `chart.cusps`.

6. В `NatalChart` есть явное поле `chart_kind`.

7. Космограмма без домов не падает на высокой широте, где обычный натал
   с Плацидусом падает.

8. В космограмме нет домов у тел, нет `pars_fortune`, нет аспектов к
   ASC/MC/DSC/IC/vertex и нет strength.

9. Поиск не находит прямых вызовов `get_natal()` без `chart_kind=`.
   Проверить вручную результатом:

```bash
rg "get_natal\\(" src tests
```

10. `NatalTool().run(...)` без `chart_kind` в `args` кидает
    `pydantic.ValidationError`.
