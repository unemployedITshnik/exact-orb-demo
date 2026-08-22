# Промт A1

**Контекст.**

Проект `exact-orb` — детерминированные астрологические расчёты.
Файл `src/exact_orb/engine/ephemeris/natal.py` (~1030 строк) смешивает два слоя: низкоуровневые примитивы эфемерид и оркестрацию именно натальной карты. Из-за этого `engine/charts/transit.py` импортирует из него семь приватных имён через границу пакета: `_calculate_houses`, `_house_for_longitude`, `_normalize_degrees`, `_normalize_house_system`, `_to_utc`, `_validate_geography`, `_zodiac_position`. То же делают `ephemeris/points.py` и `ephemeris/selena.py`.

Докстрока `engine/charts/__init__.py` гласит «Chart-level calculations built on deterministic ephemeris data» — техники должны лежать там, а `ephemeris` остаётся слоем примитивов.

Задача реализует ADR-0001. В документации этот слой назван `ephemeris/core`; **`types.py` и `calc.py` вместе и есть этот core-слой** — разбиение на два файла выбрано ради соответствия принятому в проекте стилю (`aspects/types.py`, `strength/types.py`, `tools/types.py`).

**Задача.**

Механический рефакторинг без изменения логики.

Создать `src/exact_orb/engine/ephemeris/types.py` — модели и таблицы:
`ZodiacPosition`, `BodyPosition`, `HouseCusp`, `AnglePosition`, `CalculationWarning`, `RulershipScheme`, `ZODIAC_SIGNS`, `MODERN_RULERS`, `TRADITIONAL_RULERS`, `COMBINED_RULERS`, `DEFAULT_BODY_IDS`, `ANGLE_INDICES`, `SECONDS_PER_SIGN`, `FULL_CIRCLE`, `EPSILON`.

Создать `src/exact_orb/engine/ephemeris/calc.py` — функции-примитивы, переименованные из приватных в публичные:
`calculate_bodies`, `calculate_houses`, `house_for_longitude`, `zodiac_position`, `normalize_degrees`, `to_utc`, `julian_day_ut`, `validate_geography`, `normalize_house_system`, `rulers_for_sign`.

Создать `src/exact_orb/engine/charts/natal.py` — всё натально-специфичное:
`NatalChart`, `Interception`, `HouseRulers`, `get_natal`, `DEFAULT_INCLUDE`, `INCLUDE_BLOCKS`, `_normalize_include`, `_calculate_natal_aspects`, `_calculate_natal_configurations`, `_configuration_config_with_signs`, `_natal_aspect_points`, `_calculate_natal_strength`, `_strength_category`, `_weak_strength_note`, `_interception_summary`, `_calculate_interceptions`, `_calculate_house_rulers`, `_additional_signs_in_house`, `_add_derived_points`, `_derived_point`, `_elapsed_ms`.

Удалить `src/exact_orb/engine/ephemeris/natal.py`. Совместимостный шим не создавать.

**Обновить все импорты, найденные поиском** по `src` и `tests`:

```bash
rg "ephemeris\.natal|from \.natal" src tests
```

Список найденного на момент постановки задачи — справочный, не исчерпывающий; ориентироваться следует на результат поиска:
`engine/charts/transit.py`, `engine/ephemeris/points.py`, `engine/ephemeris/selena.py`, `cli.py`, `tests/test_houses.py`, `tests/test_invariants.py`, `tests/test_selena.py`, `tests/test_transits.py`, `tests/test_cli_render.py`, `tests/test_ephemeris.py`, `tests/test_aspects.py`, `tests/test_configurations.py`, `tests/test_edge_cases.py`, `tests/test_strength.py`.

**Ограничения.**

Никаких изменений в логике, формулах, сигнатурах публичных функций и полях моделей. Только перемещение и переименование приватных примитивов в публичные.

`julian_day_ut` — **точное переименование** текущего `_julian_day_ut`, без изменения поведения относительно часовых поясов. То же касается `to_utc`. Семантику этих функций не «улучшать»: исторический резолв часовых поясов — отдельная задача другого слоя (B1), и любое изменение здесь сломает golden-тест незаметным образом.

Импорт-граф после рефакторинга зафиксирован так:

- `ephemeris/types.py` — не импортирует ни один проектный chart-модуль; внешние зависимости допустимы;
- `ephemeris/calc.py` — импортирует только `ephemeris/types.py` и внешние зависимости (`swisseph`, стандартная библиотека);
- `ephemeris/points.py` и `ephemeris/selena.py` — импортируют из `ephemeris/types.py` и `ephemeris/calc.py`;
- `charts/natal.py` — импортирует примитивы из `ephemeris/types.py` и `ephemeris/calc.py`;
- `charts/transit.py` — импортирует `NatalChart` из `charts/natal.py`, а примитивы из `ephemeris/types.py` и `ephemeris/calc.py`;
- `ephemeris` не импортирует из `charts` ни в каком виде.

Приватные имена (с ведущим подчёркиванием) не должны импортироваться через границу модуля ни в одном файле после рефакторинга.

`_calculate_interceptions` и `_calculate_house_rulers` остаются в `charts/natal.py`, хотя концептуально относятся к любой карте с домами. Перенос — отдельная задача, когда появится соляр.

**Критерии приёмки.**

1. `pytest` проходит полностью. Правки в тестах — только в путях импорта и в именах примитивов, смысл проверок не меняется.
2. Golden-файл `tests/golden/natal_1985_human.txt` совпадает байт в байт.
3. `rg "ephemeris\.natal|from \.natal" src tests` не находит ничего.
4. AST-проверка `ImportFrom` не находит ни одного `from ... import _private_name` в `src` и `tests`:

```bash
python -c "import ast,pathlib,sys; bad=[]; paths=list(pathlib.Path('src').rglob('*.py'))+list(pathlib.Path('tests').rglob('*.py')); [bad.append(f'{p}:{n.lineno}: from {n.module} import {a.name}') for p in paths for n in ast.walk(ast.parse(p.read_text(encoding='utf-8'))) if isinstance(n, ast.ImportFrom) for a in n.names if a.name.startswith('_')]; print('\n'.join(bad)); sys.exit(bool(bad))"
```

5. CLI работает как раньше: `exact-orb "01.09.1985 20.45 gmt+3"` даёт прежний вывод. Если entrypoint не установлен в текущем окружении, использовать эквивалент `python -m exact_orb "01.09.1985 20.45 gmt+3"`.

**Чего не делать.**

- Не добавлять `chart_kind` — это отдельный промт (C1). Здесь задача строго механическая.
- Не трогать `aspects`, `configurations`, `strength`.
- Не менять `include`, не добавлять поддержку космограммы.
- Не менять поведение работы с часовыми поясами.
- Не оптимизировать, не рефакторить сверх перечисленного, не переписывать докстроки по существу.
