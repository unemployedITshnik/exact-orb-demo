# Промт: команда `resolve` в существующем CLI

**Контекст.**

Блок `birth/` реализован промтом `01-birth-data-resolution.md`. Нужен
способ прогнать его руками, не поднимая ни API, ни оркестратор: overview
§4.2 называет CLI «параллельной ветвью прямого расчёта, минующей
агентский стек, инструментом разработчика и основой golden-тестов».

**Текущее устройство `src/exact_orb/cli.py`.** Парсер **плоский**,
подкоманд нет:

```python
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="exact-orb", ...)
    parser.add_argument("input", nargs="*", metavar="INPUT",
                        help="date/time in format: dd.mm.yyyy hh.mm gmt+x")
    parser.add_argument("--lat", type=float, default=55.7522, ...)
    parser.add_argument("--lon", type=float, default=37.6155, ...)
    parser.add_argument("--place", default="Москва", ...)
    ...

def main(argv: Sequence[str] | None = None) -> int:
    _configure_output_encoding()
    parser = build_parser()
    args = parser.parse_args(argv)
    ephemeris_status = configure_ephemeris(args.ephe_path)
    init_logging(ephemeris_status=..., house_system_default=...)
    ...
    print(output_text)
    return 0
```

Вызов сегодня выглядит так:

```
exact-orb "02.09.1990 14.30 gmt+3" --lat 55.75 --lon 37.62
```

Есть `tests/test_cli_render.py`, опирающийся на это поведение.

**Решения, которые уже приняты и не обсуждаются в рамках этого промта:**

- **Существующий вызов обязан продолжать работать без изменений.**
  Перевод парсера на `add_subparsers()` сделал бы обязательным префикс
  вроде `exact-orb natal "…"` и сломал бы и привычку, и
  `test_cli_render.py`. Это неприемлемая цена за диагностическую команду.
- **Диспетчеризация по первому токену.** Если первый элемент `argv`
  равен `"resolve"`, управление уходит в отдельный парсер; иначе
  выполняется существующий путь, буква в букву как сейчас. Приём
  контейнерный: он живёт в `main()` и не задевает `build_parser()`.
- **`resolve`-путь не трогает эфемериды.** `configure_ephemeris` для него
  не вызывается: разрешение места и времени к Swiss Ephemeris отношения
  не имеет, а лишний вызов означал бы падение команды при отсутствии
  файлов `ephe/*.se1`, к которым она безразлична.
- **`BirthDataResolver.resolve` асинхронный**, CLI синхронный — мост
  через `asyncio.run`.

---

**Задача.**

## 1. `src/exact_orb/cli_resolve.py`

Отдельный модуль, чтобы `cli.py` (уже 31 КБ) не рос дальше.

```python
def build_resolve_parser() -> argparse.ArgumentParser: ...
def resolve_main(argv: Sequence[str]) -> int: ...
```

Аргументы:

```text
--date        обязательный, ISO: 1990-09-02
--time        необязательный, HH:MM; отсутствует == time_unknown
--place-id    обязательный
--catalog     путь к JSONL-каталогу; по умолчанию data/places.jsonl
--min-date    по умолчанию 1800-01-01
--max-date    по умолчанию 2399-12-31
--format      human | json, по умолчанию human
```

`--date` и `--time` принимают ISO-формат, а не `dd.mm.yyyy hh.mm gmt+x`
основной команды: пояс здесь не вводится руками, он берётся из каталога
по `--place-id`, и имитировать формат, где пояс присутствует, значило бы
предлагать ввести то, что команда обязана вычислить сама.

Поведение:

1. Разобрать аргументы, собрать `BirthInput`. Ошибка разбора даты
   или времени — `parser.error(...)`, код возврата 2.
2. Если файла каталога нет — внятное сообщение с путём и подсказкой
   про `scripts/build_place_catalog.py`, код возврата 2. Не traceback.
3. `LocalPlaceCatalog.from_file(...)`, собрать `BirthDataResolver`.
4. `asyncio.run(resolver.resolve(birth_input))`.
5. Напечатать результат, вернуть код.

Коды возврата — по типу исхода, чтобы команда годилась для скриптов:

```text
0   ResolvedBirthData
1   InputRequired          пользовательский ввод
3   ResolutionUnavailable  технический отказ
2   ошибка аргументов или отсутствие каталога
```

Разделение `1` и `3` — та же граница, что и в самом резолвере:
пользовательская ошибка и системный отказ не смешиваются нигде,
включая код возврата.

## 2. Форматы вывода

**human** — успешный исход:

```text
РАЗРЕШЕНИЕ ДАННЫХ РОЖДЕНИЯ

Место       Москва  (place_id 524901)
Координаты  55.752220 N, 37.615560 E
Пояс        Europe/Moscow
Локально    1990-09-02 14:30
Смещение    UTC+04:00
UTC         1990-09-02 10:30:00Z
Время       указано

ПРЕДУПРЕЖДЕНИЯ
  time  pre_1970_offset_unverified
        IANA guarantees clock agreement only after 1970-01-01
```

Строка `Время` — `указано` либо `не указано, опорная точка — местный
полдень`. Она обязана присутствовать всегда: по ADR-0008 подставленный
полдень не должен создавать видимость известного времени, и в выводе
диагностической команды это различие тем более должно быть явным.

**Строка `Смещение` печатает секунды, когда они есть.** Поле контракта —
`utc_offset_seconds`, и до введения стандартного времени смещения не
кратны минуте: `Europe/Moscow` на 1800 год даёт `+02:30:17`. Формат —
`UTC±HH:MM` при нулевых секундах и `UTC±HH:MM:SS` иначе. Округлять
до минут нельзя: команда диагностическая, и её задача — показать
применённое значение, а не удобное.

Блок `ПРЕДУПРЕЖДЕНИЯ` опускается, если их нет.

**human** — `InputRequired`:

```text
ТРЕБУЕТСЯ УТОЧНЕНИЕ ВВОДА

  birth.date   UNSUPPORTED   min=1800-01-01 max=2399-12-31
  birth.place  INVALID
```

**human** — `ResolutionUnavailable`:

```text
ТЕХНИЧЕСКИЙ ОТКАЗ

  PLACE_CATALOG_UNAVAILABLE   retryable=true
```

Заголовки разные не для красоты: это единственное, что отличает
«исправь ввод» от «повтори позже» при беглом взгляде на вывод.

**json** — `{"outcome": "resolved" | "input_required" | "unavailable",
"data": {...}}`, где `data` — `model_dump(mode="json")` соответствующего
объекта. `json.dumps(..., ensure_ascii=False, indent=2)`, как в
существующем `format_json`.

## 3. Диспетчер в `src/exact_orb/cli.py`

Единственная правка в файле — в начале `main()`:

```python
def main(argv: Sequence[str] | None = None) -> int:
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    if raw_argv and raw_argv[0] == "resolve":
        from exact_orb.cli_resolve import resolve_main
        return resolve_main(raw_argv[1:])
    # дальше существующий код без изменений
```

Импорт внутри функции намеренный: `cli.py` не должен тянуть `birth/`
при обычном расчёте карты.

Ниже этой вставки в `main()` не меняется ни строки — включая
`_configure_output_encoding()`, `configure_ephemeris`, `init_logging`
и весь существующий поток.

`_configure_output_encoding()` вызвать в начале `resolve_main`:
без него кириллица в выводе ломается в консоли Windows.

## 4. Логирование

`init_logging` в `resolve`-пути **не вызывать**: он требует
`ephemeris_status`, которого здесь нет и быть не должно.

Если нужен диагностический вывод — обычный `logging.getLogger(__name__)`
на уровне `DEBUG`. **В журнал не должны попадать** координаты, дата
и время рождения: overview прямо отмечает, что существующий `cli.py`
нарушает И-14, и повторять это нарушение в новом коде нельзя. Логировать
можно `place_id`, коды исходов и длительность.

## 5. `README.md`

Добавить раздел с примером:

```
exact-orb resolve --date 1990-09-02 --time 14:30 --place-id 524901
exact-orb resolve --date 1990-09-02 --place-id 524901          # космограмма
exact-orb resolve --date 1967-06-03 --place-id 2553604 --format json
```

Третий пример неслучаен: Касабланка 3 июня 1967 года — дата, на которой
подставленный полдень не существует, и команда показывает
`noon_anchor_adjusted` вместо требования уточнить ввод.

---

**Ограничения.**

- Не переводить существующий парсер на `add_subparsers()`.
- Не менять `build_parser()`, `parse_datetime_input`, `format_human`,
  `format_json` и вообще ничего в `cli.py`, кроме вставки диспетчера.
- Не ломать `tests/test_cli_render.py` — он обязан проходить без правок.
- Не вызывать `configure_ephemeris` и `init_logging` в `resolve`-пути.
- Не добавлять в `resolve` расчёт карты, обращение к `EngineService`,
  `ChartArtifactResolver` или кэшам: команда заканчивается на
  `ResolvedBirthData`.
- Не писать координаты, дату и время рождения в журнал.
- Не добавлять второй `console_scripts` entry point в `pyproject.toml`.

---

**Критерии приёмки.**

1. `exact-orb "02.09.1990 14.30 gmt+3"` работает ровно как до правки,
   `tests/test_cli_render.py` проходит без изменений.
2. `exact-orb resolve --date 1990-09-02 --time 14:30 --place-id 524901
   --catalog tests/fixtures/places.jsonl` печатает `UTC` со значением
   `1990-09-02 10:30:00Z` и `Смещение UTC+04:00`, код возврата 0.
3. Тот же вызов без `--time` печатает
   `Время  не указано, опорная точка — местный полдень`, код возврата 0.
4. `--place-id 999999999` печатает блок `ТРЕБУЕТСЯ УТОЧНЕНИЕ ВВОДА`
   со строкой `birth.place  INVALID`, код возврата **1**.
5. `--date 1500-01-01` печатает `birth.date  UNSUPPORTED` с
   `min=` и `max=`, код возврата **1**.
6. `--place-id 9000001` (запись с `tz_id = "Nowhere/Fake"`) печатает
   `ТЕХНИЧЕСКИЙ ОТКАЗ` и `UNKNOWN_TIMEZONE`, код возврата **3**.
7. `--catalog <несуществующий путь>` печатает внятное сообщение
   с упоминанием `build_place_catalog.py`, код возврата 2, без traceback.
8. `--format json` даёт валидный JSON с ключами `outcome` и `data`;
   `outcome` принимает одно из трёх значений.
9. `exact-orb resolve --date 1967-06-03 --place-id 2553604` завершается
   кодом 0 и содержит `noon_anchor_adjusted` в блоке предупреждений.
10. Запуск `resolve` при отсутствующем каталоге `ephe/` не падает:
    эфемериды на этом пути не нужны.
11. Журнал не содержит ни `latitude`, ни `longitude`, ни даты рождения.
12. `--date 1800-01-01 --place-id 524901` печатает
    `Смещение UTC+02:30:17` — с секундами, не округлённое до `+02:30`.
