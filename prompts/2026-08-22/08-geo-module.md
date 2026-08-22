# Промт: пакет geo/ — разрешение места и часового пояса

**Контекст.**

Сегодня в проекте нет ни геокодирования, ни работы с часовыми поясами.
Единственное место, где вообще появляется смещение — `cli.py`, и там оно
переложено на пользователя: `INPUT_PATTERN` требует ввод формата
`dd.mm.yyyy hh.mm gmt+x`, а `ParsedDateTime.utc_datetime` делает
тривиальное `local_datetime.astimezone(timezone.utc)` над уже готовым
фиксированным офсетом. Место (`place: str`) используется только как
подпись в выводе `format_human()` — в расчёт оно не идёт никак.

Расчётный слой при этом требует и того, и другого:
`get_natal(birth_datetime, latitude, longitude, ...)` принимает
timezone-aware `datetime` и две обязательные координаты, а
`calculate_houses(julian_day_ut, latitude, longitude, house_system)`
реально использует широту — вплоть до того, что Плацидус на высоких
широтах вырождается и `calc.py` кидает по этому поводу отдельную ошибку.

Будущий `IntentService` (ADR-0005) должен превращать текст пользователя
в `ResolvedContract` с готовыми `latitude`/`longitude`/`utc`. Именно там
нужны обе операции, которых нет: «текст города → координаты» и
«локальное время + часовой пояс → UTC».

**Почему отдельный пакет, а не внутри `engine/` и не внутри
`IntentService`:**

- В `engine/` этому не место. `get_natal()` сегодня — чистая функция без
  I/O и внешних баз, и именно поэтому её можно проверять golden-эталоном
  (`tests/fixtures/natal_1985.py`, сверено с geocult.ru). Геокодирование
  и tz-резолвинг сделали бы результат движка зависимым от версии
  tz-базы и от файла с городами. Плюс у движка нет канала для диалога, а
  обе операции упираются в неоднозначности, которые снимает только
  пользователь. Граница уже проведена правильно: `validate_geography()`
  **проверяет** координаты, но не **ищет** их.
- Внутри `IntentService` этому тоже не место: это детерминированная
  табличная логика, которую нужно юнит-тестировать без участия LLM. И
  она нужна не только ему — `cli.py` сможет наконец принимать город
  вместо ручного `gmt+x`.

**Решения, которые уже приняты и не обсуждаются в рамках этого промта:**

- Пакет называется `geo` и лежит в `src/exact_orb/geo/`, разложенный на
  `types.py` (модели), `places.py` (поиск места), `timezones.py`
  (разрешение времени), `__init__.py` (реэкспорт) — по образцу
  `engine/ephemeris/`.
- Источник данных о городах — **локальный дамп GeoNames**
  (`cities15000.txt`), не сетевой геокодер. Причина: детерминированность
  и отсутствие сети в тестах, как и у эфемерид.
- Часовой пояс города берётся **из самого дампа GeoNames** (колонка 17,
  IANA-имя вроде `Europe/Moscow`), а не вычисляется из координат.
  Поэтому библиотека вида `timezonefinder` не нужна и **не
  добавляется**. Следствие: обратная задача «произвольные координаты →
  часовой пояс» (без города) в этот промт не входит вообще; типовой путь
  «пользователь назвал город» её не требует.
- Разрешение времени работает через stdlib `zoneinfo`, а не через
  сторонние tz-библиотеки. В зависимости добавляется пакет `tzdata` —
  на Windows у `zoneinfo` нет системной базы, без него `ZoneInfo(...)`
  падает, а проект разрабатывается на Windows.
- Путь к дампу настраивается по уже принятому в проекте приоритету
  (`config.py`, `_resolve_ephemeris_path`): явный аргумент → переменная
  окружения → `[tool.exact_orb]` в `pyproject.toml` → дефолт. Для
  чтения из pyproject используй уже существующий публичный хелпер
  `read_exact_orb_pyproject_value("geonames_path")`.
- «Город не найден» — это **не исключение**, а нормальный исход
  (пустой кортеж кандидатов): `IntentService` на него переспросит
  пользователя. Исключение кидается только когда сам файл данных
  недоступен — это ошибка развёртывания, а не ввода.

**Проверенные факты, на которые нужно опираться (не перепроверяй, но и
не противоречь им).**

Историческая tz-база обязательна, текущего офсета города недостаточно.
Показательный пример — это **ровно golden-фикстур самого проекта**:

```python
>>> datetime(1985, 9, 2, 0, 45, tzinfo=ZoneInfo("Europe/Moscow")).utcoffset()
datetime.timedelta(seconds=14400)          # +04:00 — летнее время 1985 года
>>> ...astimezone(timezone.utc)
datetime(1985, 9, 1, 20, 45, tzinfo=utc)   # == REFERENCE["datetime_utc"]

>>> datetime(2026, 9, 2, 0, 45, tzinfo=ZoneInfo("Europe/Moscow")).utcoffset()
datetime.timedelta(seconds=10800)          # +03:00 — сегодня
```

То есть `REFERENCE["datetime_utc"]` из `tests/fixtures/natal_1985.py` —
это 00:45 2 сентября 1985 по Москве при историческом офсете +4. Взяв
сегодняшний офсет Москвы (+3), получишь время, ошибочное на час.

Две ловушки перевода часов, обе обязаны обрабатываться:

1. **Неоднозначное время** (часы переводят назад — локальное время
   случается дважды). `datetime(2020, 10, 25, 2, 30)` в `Europe/Berlin`
   даёт `00:30 UTC` при `fold=0` и `01:30 UTC` при `fold=1`.
2. **Несуществующее время** (часы переводят вперёд — локального времени
   не было вовсе). `datetime(2020, 3, 29, 2, 30)` в `Europe/Berlin`
   `zoneinfo` **принимает молча, без исключения**, возвращая при разных
   `fold` два разных и одинаково бессмысленных UTC.

Алгоритм различения этих трёх случаев проверен и работает:

```python
a = naive.replace(tzinfo=tz, fold=0)
b = naive.replace(tzinfo=tz, fold=1)
if a.utcoffset() == b.utcoffset():
    status = "ok"
else:
    # офсеты разошлись — либо дыра, либо повтор; различаем round-trip'ом
    roundtrip = a.astimezone(timezone.utc).astimezone(tz).replace(tzinfo=None)
    status = "nonexistent" if roundtrip != naive else "ambiguous"
```

**Задача.**

1. `src/exact_orb/geo/types.py`:

```python
class PlaceCandidate(BaseModel):
    """One GeoNames record that matched a place query."""

    geoname_id: int
    name: str
    country_code: str
    admin1_code: str | None
    latitude: float
    longitude: float
    timezone: str          # IANA name straight from the dump
    population: int


class TimeResolution(BaseModel):
    """Result of turning a naive local datetime into UTC."""

    status: Literal["ok", "ambiguous", "nonexistent"]
    utc: datetime
    offset: timedelta
    timezone: str
    alternative_utc: datetime | None = None
```

   Плюс исключение `GeoDataUnavailableError(RuntimeError)` — файл дампа
   не найден или нечитаем.

   `alternative_utc` заполняется только при `status != "ok"` и несёт
   второй вариант (`fold=1`), чтобы `IntentService` мог показать
   пользователю обе трактовки в `clarification`-событии (ADR-0012).

2. `src/exact_orb/geo/timezones.py`:

```python
def local_to_utc(local_naive: datetime, timezone_name: str) -> TimeResolution
```

   - `local_naive` обязан быть **naive**; если у него есть `tzinfo` —
     `ValueError("local_naive must be naive; the timezone comes from
     timezone_name")`. Это зеркало проверки в `NatalTool`, только
     наоборот: там требуется aware, здесь — naive.
   - Неизвестное имя пояса пробрасывает `zoneinfo.ZoneInfoNotFoundError`
     как есть, не заворачивая.
   - Классификация — ровно алгоритмом выше. `utc` всегда заполнен
     (вариант `fold=0`) даже при `nonexistent`, чтобы у вызывающего был
     хоть какой-то якорь; ориентироваться он обязан на `status`.

3. `src/exact_orb/geo/places.py`:

```python
def resolve_place(
    query: str,
    *,
    limit: int = 10,
    data_path: str | os.PathLike[str] | None = None,
) -> tuple[PlaceCandidate, ...]
```

   - Формат `cities15000.txt` — TSV без заголовка, 19 колонок. Нужные
     индексы: `0` geonameid, `1` name, `2` asciiname,
     `3` alternatenames (через запятую), `4` latitude, `5` longitude,
     `8` country code, `10` admin1 code, `14` population, `17` timezone.
   - Поиск: регистронезависимое **точное** совпадение нормализованного
     `query` с `name`, `asciiname` или любым из `alternatenames`.
     Нечёткий/префиксный поиск в этот промт не входит — благодаря
     `alternatenames` запрос «Москва» и так находит `Moscow`.
     Нормализация — `str.strip().casefold()`, ничего сложнее.
   - Сортировка результата — по `population` убыванию, чтобы «Moscow»
     давал сначала российскую Москву, а Moscow (Idaho) — ниже.
     Обрезка по `limit`.
   - Ничего не найдено → пустой кортеж, без исключения.
   - Дамп читается один раз и кэшируется на уровне модуля (как
     `_STATUS` в `config.py`); повторные вызовы не перечитывают файл.
     Кэш ключуется разрешённым путём, чтобы смена `data_path` в тестах
     не отдавала устаревшие данные.
   - Файла нет / не читается → `GeoDataUnavailableError` с внятным
     текстом, включающим разрешённый путь и подсказку, откуда взять
     дамп.

4. Путь к данным — по приоритету проекта: аргумент `data_path` →
   `EXACT_ORB_GEONAMES_PATH` → `[tool.exact_orb].geonames_path` →
   дефолт `data/geonames/cities15000.txt`. Оформи это отдельной функцией
   в `places.py` (не тащи в `config.py`, чтобы `config.py` не начал
   зависеть от `geo/`).

5. `src/exact_orb/geo/__init__.py` — реэкспорт
   `GeoDataUnavailableError`, `PlaceCandidate`, `TimeResolution`,
   `local_to_utc`, `resolve_place`, с `__all__` по алфавиту.

6. `pyproject.toml` — добавить `"tzdata"` в `dependencies` и
   `geonames_path = "data/geonames/cities15000.txt"` в
   `[tool.exact_orb]`. Сам дамп в репозиторий **не коммитить** —
   добавь его путь в `.gitignore` рядом с эфемеридами.

**Ограничения.**

- Не трогать `engine/` вообще. В частности — не звать `geo/` из
  `get_natal()` и не добавлять в него параметры места/пояса.
- Не менять `intent/`, `tools/`, `orchestration/`. `IntentService`,
  который всё это свяжет, — отдельная будущая задача; здесь пишется
  только сам пакет `geo/` как самостоятельная библиотека.
- Не трогать `cli.py` в этом промте, хотя он и станет первым
  потенциальным потребителем.
- Не добавлять `timezonefinder`, `geopy`, `pytz`, `dateutil` и вообще
  любые зависимости кроме `tzdata`.
- Не ходить в сеть ни на импорте, ни в рантайме.
- Не реализовывать нечёткий поиск, опечатки, префиксы, поиск по
  «город, страна» одной строкой — только точное совпадение по именам и
  алиасам.
- Не заводить `field_validator` на `PlaceCandidate` — данные приходят из
  доверенного дампа, а не от пользователя.

**Критерии приёмки.**

1. `from exact_orb.geo import PlaceCandidate, TimeResolution, local_to_utc, resolve_place`
   импортируется без ошибок.
2. `local_to_utc(datetime(1985, 9, 2, 0, 45), "Europe/Moscow")` даёт
   `status == "ok"`, `offset == timedelta(hours=4)` и `utc`, точно
   равный `REFERENCE["datetime_utc"]` из
   `tests/fixtures/natal_1985.py` (`1985-09-01 20:45 UTC`).
3. `local_to_utc(datetime(2020, 10, 25, 2, 30), "Europe/Berlin")` даёт
   `status == "ambiguous"`, `utc == 2020-10-25 00:30 UTC`,
   `alternative_utc == 2020-10-25 01:30 UTC`.
4. `local_to_utc(datetime(2020, 3, 29, 2, 30), "Europe/Berlin")` даёт
   `status == "nonexistent"` и непустой `alternative_utc`.
5. `local_to_utc(<aware datetime>, "Europe/Moscow")` кидает `ValueError`
   с текстом про `naive`.
6. `resolve_place("Москва")` и `resolve_place("Moscow")` оба находят
   один и тот же город с `country_code == "RU"` первым в результате, и
   у него `timezone == "Europe/Moscow"`.
7. `resolve_place("Moscow")` возвращает больше одного кандидата
   (российская Москва и американские одноимённые города), отсортированных
   по убыванию населения.
8. `resolve_place("нетакогогорода")` возвращает `()`, не кидая
   исключение.
9. `resolve_place("Moscow", data_path="<несуществующий путь>")` кидает
   `GeoDataUnavailableError`, а не `FileNotFoundError`.
10. Композиция работает: взяв `resolve_place("Москва")[0]`, передав его
    `.timezone` в `local_to_utc(datetime(1985, 9, 2, 0, 45), ...)` и
    отдав результат вместе с `.latitude`/`.longitude` в `get_natal()`,
    получаем чарт, чья долгота Солнца совпадает с
    `EXPECTED_BODY_LONGITUDES["sun"]`. Это и есть весь смысл пакета —
    он должен стыковаться с движком без ручных преобразований.

Тесты для этого пакета — отдельный промт (см. `09-geo-module-tests.md`
в этой же папке); здесь код должен просто им удовлетворять.
