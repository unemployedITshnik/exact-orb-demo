# Промт: блок разрешения данных рождения (`birth/`)

**Контекст.**

Проект `exact-orb`. Реализуется путь

```text
BuildNatalHandler → BirthDataResolver → PlaceCatalog
                  → resolve_historical_tz → BirthDataResolver → BuildNatalHandler
```

Проектные документы, которым код обязан соответствовать:

* `docs/requirements/component_responsibilities/exact-orb_birth_data_resolution.md`
  — контракты, алгоритм, двадцать сценариев;
* `docs/requirements/component_responsibilities/exact-orb_build_natal_components.md`
  §3, §5 — место блока в общем пути;
* ADR-0005 (резолв места и времени), ADR-0007 (`InputRequired`),
  ADR-0008 (космограмма и полдень), ADR-0009 (сессия).

Сейчас в проекте пакета `birth/` нет вообще. Есть `src/exact_orb/errors.py`
с иерархией ошибок эфемерид, `config.py`, `engine/` — их трогать не нужно.

Каталог мест собирается скриптом `scripts/build_place_catalog.py`
из дампа GeoNames и имеет формат JSONL, по объекту на строку:

```json
{"place_id": "524901", "name": "Москва", "name_ascii": "Moscow",
 "country": "RU", "admin1": "Moscow", "latitude": 55.75222,
 "longitude": 37.61556, "tz_id": "Europe/Moscow", "population": 10381222}
```

**Решения, которые уже приняты и не обсуждаются в рамках этого промта:**

- **Интерфейсы асинхронные.** `PlaceCatalog.lookup` и
  `BirthDataResolver.resolve` — `async def`. `resolve_historical_tz`
  синхронная: она не делает I/O.
- **Порт — это `typing.Protocol`, а не ABC.** Никаких фабрик, реестров
  и конфигурационной маршрутизации: второй реализации сегодня нет.
- **`resolve_historical_tz` принимает `local_datetime` и `tz_id`,
  а не координаты.** «Координаты → `tz_id`» — географическая задача,
  требующая данных границ зон; каталог отдаёт `tz_id` готовым.
  Библиотеки вроде `timezonefinder` не подключать.
- **`pytz` не использовать.** Только `zoneinfo` из stdlib; `tzdata`
  добавляется зависимостью в `pyproject.toml`, потому что на Windows
  `zoneinfo` без неё пуст.
- **Конвенция полудня живёт в резолвере**, не во временной функции:
  вторая половина того же решения — `time_unknown = True` — принимается
  там же (ADR-0008).
- **`place_substituted` и `place_input_text` отменены** ревизией ADR-0005
  от 2026-08-27. Не добавлять их ни в один контракт.
- **Порядок проверок в `resolve_historical_tz`: несуществующее время
  первым.** По PEP 495 для несуществующего времени `fold=0` и `fold=1`
  тоже дают разные `utcoffset()`, поэтому проверка на удвоение, выполненная
  первой, классифицирует несуществующее время как удвоенное.
- **При `time_unknown = True` аномалии зоны не поднимаются
  в `InputRequired`.** Система не спрашивает пользователя о значении,
  которое подставила сама.
- **Резолвер в состояние не пишет и не читает.** Ни `SessionStore`,
  ни кэша, ни файлов помимо каталога, загруженного на старте.

---

**Задача.**

## 1. `src/exact_orb/outcomes.py`

Общие типизированные исходы. Файл на верхнем уровне пакета, а не внутри
`birth/`, потому что `InputRequired` по ADR-0007 возвращают также
`ContractValidator` и `Planner` — один тип и одно место его определения.

```python
IssueCode = Literal["MISSING", "AMBIGUOUS", "INVALID", "UNSUPPORTED"]

class Issue(BaseModel):
    field: str                                  # путь в контракт: "birth.place"
    code: IssueCode
    candidates: tuple[Any, ...] | None = None
    constraints: dict[str, Any] | None = None

class InputRequired(BaseModel):
    issues: tuple[Issue, ...]

class ResolutionUnavailable(BaseModel):
    error_code: str
    retryable: bool = True
```

`InputRequired` и `ResolutionUnavailable` — **разные типы**, а не один
с флагом. Их нельзя перепутать по построению: техническая недоступность
никогда не должна превращаться в просьбу исправить ввод.

## 2. `src/exact_orb/birth/types.py`

```python
class BirthInput(BaseModel):
    birth_date: date
    birth_time: time | None = None      # None == time_unknown
    place_id: str

class ResolutionWarning(BaseModel):
    source: Literal["place", "time"]
    code: str                            # проверяемый код, не свободный текст
    message: str

class ResolvedBirthData(BaseModel):
    # влияет на числа расчёта
    utc_datetime: datetime               # tz-aware, UTC
    latitude: float
    longitude: float
    # восстановительные и отображаемые
    tz_id: str
    utc_offset_seconds: int              # секунды, не минуты — см. ниже
    canonical_place: str
    # пояснительные
    time_unknown: bool
    warnings: tuple[ResolutionWarning, ...] = ()
```

Два валидатора, и оба — инварианты, а не перестраховка.

`@field_validator("utc_datetime")` требует tz-aware значения в UTC
(`value.utcoffset() == timedelta(0)`), сообщение
`"utc_datetime must be timezone-aware UTC"`. Ниже резолвера локального
времени не существует, и naive datetime здесь означает потерянную
информацию.

`@field_validator("birth_time")` на `BirthInput` требует **naive**
значения: `value.tzinfo is None`, сообщение `"birth_time must be naive"`.
Причина механическая: `datetime.combine(date, time(tzinfo=...))`
наследует `tzinfo` времени и возвращает aware-значение, которое
`resolve_historical_tz` отвергнет как `ValueError` — то есть
пользовательский ввод с поясом упал бы неожиданным исключением в глубине
вместо типизированного исхода. Пояс на этом пути приходит из каталога
по `place_id`, а не от клиента: клиентский пояс здесь — это ввод
величины, которую система обязана вычислить сама. На уровне API
`ValidationError` переводится в `InputRequired { birth.time, INVALID }`
по общему правилу §3.1 документа резолва.

**Смещение хранится в секундах, и единица вынесена в имя поля.**
До введения стандартного времени зоны жили по местному солнечному,
и смещения не кратны минуте: `Europe/Moscow` на 1800 год даёт `+02:30:17`,
`Europe/Paris` — `+00:09:21`, `Asia/Tokyo` на 1880 — `+09:18:59`.
Это не экзотика: **399 зон из 498** имеют нецелые минуты на 1800-01-01,
а нижняя граница поддерживаемого диапазона как раз 1800 год.

Хранение в минутах сделало бы поле «применённое смещение» неправдой для
большей части старого диапазона — `utc_datetime` считался бы верно,
а поле рядом с ним врало бы на секунды. Единица в имени (`_seconds`)
убирает целый класс ошибок при чтении кода.

Ровно три поля во входе. `place_text` не добавлять: форма выбирающая,
набранное — поисковый префикс, а не данные.

## 3. `src/exact_orb/birth/places.py`

```python
class ResolvedPlace(BaseModel):
    place_id: str
    canonical_name: str
    latitude: float
    longitude: float
    tz_id: str

class PlaceNotFound(BaseModel):
    place_id: str

PlaceResolution = ResolvedPlace | PlaceNotFound

class PlaceCatalogUnavailableError(RuntimeError):
    """Каталог недоступен: техническая ошибка, не ошибка ввода."""

class PlaceCatalog(Protocol):
    async def lookup(self, place_id: str) -> PlaceResolution: ...

class LocalPlaceCatalog:
    def __init__(self, places: Mapping[str, ResolvedPlace]) -> None: ...

    @classmethod
    def from_file(cls, path: str | os.PathLike[str]) -> "LocalPlaceCatalog": ...

    async def lookup(self, place_id: str) -> PlaceResolution: ...
```

`from_file` читает JSONL описанного формата, отображает `name`
в `canonical_name`, и загружает всё в память **один раз**. Строки без
`tz_id` пропускаются (без них место бесполезно). Битая строка JSON — это
`ValueError` с номером строки на старте, а не молчаливый пропуск.

`PlaceResolution` **не содержит варианта с кандидатами.** Build API
принимает только `place_id`, форма выбирающая, и `AMBIGUOUS` для места
на этом пути не возникает (ADR-0005). Поиск по строке с кандидатами —
контракт эндпоинта подсказок, и он в этом промте не реализуется.

## 4. `src/exact_orb/birth/tz.py`

```python
class TzOk(BaseModel):
    utc_datetime: datetime                      # tz-aware UTC
    utc_offset_seconds: int
    warnings: tuple[ResolutionWarning, ...] = ()

class TzNonexistent(BaseModel):
    local_datetime: datetime                    # naive
    tz_id: str
    normalized: datetime                        # naive; куда сдвигает зона

class TzAmbiguous(BaseModel):
    local_datetime: datetime                    # naive
    tz_id: str
    offsets: tuple[int, int]                    # секунды, (fold=0, fold=1)

TzResolution = TzOk | TzNonexistent | TzAmbiguous

class UnknownTimezoneError(RuntimeError):
    """tz_id неизвестен zoneinfo: каталог повреждён или tzdata устарела."""

PRE_1970 = datetime(1970, 1, 1)

def resolve_historical_tz(local_datetime: datetime, tz_id: str) -> TzResolution: ...

def resolve_anomaly(anomaly: TzNonexistent | TzAmbiguous) -> TzOk: ...
```

**Все три `local_datetime` и `normalized` — naive**, и это закреплено
валидаторами (`value.tzinfo is None`). Aware-значение в `normalized`
означало бы, что зона к нему уже применена, и следующий шаг применил бы
её второй раз — ошибка, которую видно только по итоговому сдвигу карты.

Реализация:

1. `local_datetime` обязан быть **naive**; aware-значение — `ValueError`
   с текстом `"local_datetime must be naive"`. Локальное время
   с приклеенным поясом здесь означает, что кто-то уже применил
   преобразование, и применять его второй раз нельзя.
2. `ZoneInfo(tz_id)`; `KeyError` / `ZoneInfoNotFoundError` →
   `UnknownTimezoneError`.
3. **Несуществующее время — первая проверка**, через round-trip:

```python
dt = local_datetime.replace(tzinfo=tz)
back = dt.astimezone(timezone.utc).astimezone(tz).replace(tzinfo=None)
if back != local_datetime:
    return TzNonexistent(local_datetime=local_datetime, tz_id=tz_id, normalized=back)
```

4. **Удвоенное — вторая:**

```python
o0 = dt.replace(fold=0).utcoffset()
o1 = dt.replace(fold=1).utcoffset()
if o0 != o1:
    return TzAmbiguous(..., offsets=(int(o0.total_seconds()), int(o1.total_seconds())))
```

5. Иначе `TzOk` с `utc_datetime = dt.astimezone(timezone.utc)`
   и `utc_offset_seconds = int(o0.total_seconds())`.
6. Если `local_datetime < PRE_1970`, `TzOk.warnings` содержит

```python
ResolutionWarning(
    source="time",
    code="pre_1970_offset_unverified",
    message="IANA guarantees clock agreement only after 1970-01-01",
)
```

Обоснование пункта 6: база tz формирует зоны по согласию часов **после**
1970-01-01 и о более ранних смещениях прямо пишет, что «many, perhaps
most … are either wrong or misleading». Для натальной карты ошибка в час
— это сдвиг Ascendant примерно на пятнадцать градусов.

### `resolve_anomaly`

```python
def resolve_anomaly(anomaly: TzNonexistent | TzAmbiguous) -> TzOk:
    """Детерминированно разрешить аномалию зоны.

    TzNonexistent → момент, посчитанный от normalized.
    TzAmbiguous   → fold = 0, то есть более раннее смещение.

    Warning о самой аномалии здесь НЕ ставится: он принадлежит вызывающему,
    потому что зависит от того, почему момент подставлен.
    """
```

Алгоритм функции:

```text
TzNonexistent →
    повторно вызвать resolve_historical_tz(anomaly.normalized, anomaly.tz_id)
    и ожидать TzOk; если снова пришла аномалия, это UnknownTimezoneError
    уровня повреждённых данных зоны

TzAmbiguous →
    dt = anomaly.local_datetime.replace(
        tzinfo=ZoneInfo(anomaly.tz_id),
        fold=0,
    )
    вернуть TzOk с utc_datetime = dt.astimezone(timezone.utc)
    и utc_offset_seconds = int(dt.utcoffset().total_seconds())
```

**Зачем отдельная функция, а не поле в `TzAmbiguous`.** Резолверу в ветке
`time_unknown = True` нужен готовый момент, и без такой функции он либо
сам импортирует `ZoneInfo`, либо считает смещение арифметикой — то есть
timezone-логика вытекает за пределы `tz.py`. Поле `utc_datetimes`
в `TzAmbiguous` решило бы половину задачи: у `TzNonexistent` момента
всё равно нет, и резолверу пришлось бы звать `resolve_historical_tz`
второй раз, надеясь, что нормализованное значение не окажется
аномальным снова. Одна функция закрывает обе ветки и держит всю
арифметику зон в одном модуле.

Функция чистая, тестируется отдельно и не знает ни про полдень,
ни про `time_unknown`.

## 5. `src/exact_orb/birth/resolver.py`

```python
NOON = time(12, 0)

class BirthDataResolver:
    def __init__(
        self,
        *,
        places: PlaceCatalog,
        min_birth_date: date,
        max_birth_date: date,
        today_provider: Callable[[], date] = date.today,
    ) -> None: ...

    async def resolve(
        self,
        birth_input: BirthInput,
    ) -> ResolvedBirthData | InputRequired | ResolutionUnavailable: ...
```

Если `min_birth_date > max_birth_date`, конструктор кидает `ValueError`
с текстом `"min_birth_date must be <= max_birth_date"`. Это ошибка
конфигурации приложения, и её нужно ловить на старте, а не превращать
в странный `InputRequired` для пользователя.

`today_provider` — параметр, а не прямой вызов `date.today()`: иначе
верхнюю границу невозможно протестировать детерминированно — тест начал
бы вести себя по-разному в зависимости от дня прогона.

Алгоритм:

```text
1.  issues = []
    effective_max = min(max_birth_date, today_provider())
    дата вне [min_birth_date, effective_max] →
        Issue("birth.date", UNSUPPORTED,
              constraints={"min": min_birth_date.isoformat(),
                           "max": effective_max.isoformat()})
    (проверка не прерывает выполнение)

2.  place = await places.lookup(place_id)
        PlaceNotFound                  → issues += Issue("birth.place", INVALID)
        PlaceCatalogUnavailableError   → return ResolutionUnavailable(
                                             error_code="PLACE_CATALOG_UNAVAILABLE",
                                             retryable=True)

3.  if issues: return InputRequired(issues=tuple(issues))

4.  time_unknown   = birth_input.birth_time is None
    local_datetime = combine(birth_date, birth_time or NOON)

5.  tz = resolve_historical_tz(local_datetime, place.tz_id)
        UnknownTimezoneError → return ResolutionUnavailable(
                                   error_code="UNKNOWN_TIMEZONE", retryable=False)

    TzOk          → продолжаем с его warnings

    TzNonexistent → time_unknown == False:
                        InputRequired([Issue("birth.time", INVALID)])
                    time_unknown == True:
                        tz = resolve_anomaly(tz)
                        + warning noon_anchor_adjusted

    TzAmbiguous   → time_unknown == False:
                        InputRequired([Issue("birth.time", AMBIGUOUS,
                                             candidates=tz.offsets)])
                    time_unknown == True:
                        tz = resolve_anomaly(tz)
                        + warning noon_anchor_adjusted

6.  ResolvedBirthData(...)
```

**Почему проверки даты схлопнуты в одну.** Раздельные проверки «вне
диапазона эфемерид» и «в будущем» дают два issue на одно поле для даты
вроде `2500-01-01` при `max_birth_date = 2399-12-31` и сегодняшнем
2026 году. Для UI это шум: поле одно, действие одно — назвать дату
раньше. Дата рождения по определению не может быть позже сегодняшнего
дня, поэтому верхняя граница всегда `min(max_birth_date, today)`,
и проверка одна. Для транзитов, где дата не ограничена сегодняшним днём,
такого схлопывания не будет — но транзиты и не проходят через этот
резолвер.

**Почему issues накапливаются, а не возвращается первая.**
`InputRequired.issues` — список: неверные дата и место должны
подсветиться за один заход, а не исправляться по очереди двумя запросами.
Границу накопления задаёт зависимость данных — время резолвится только
после места, потому что нужен `tz_id`. Отсюда два этапа: шаги 1–2 вместе,
затем 4–5.

**Почему при `time_unknown` аномалия не становится `InputRequired`.**
Подставленный полдень попадает в несуществующее время в реальных зонах:
`Africa/Casablanca 1967-06-03` (часы прыгнули 12:00 → 13:00),
`Pacific/Apia 2011-12-30` (день не существовал целиком). Вернув здесь
`InputRequired` на поле времени, система попросила бы человека исправить
значение, которое он намеренно оставил пустым и исправить не может.

Код предупреждения — `noon_anchor_adjusted`, `source="time"`.
Выбор `fold = 0` при удвоении зафиксирован, чтобы результат не зависел
от версии реализации.

## 6. `src/exact_orb/birth/__init__.py`

Экспортировать `BirthInput`, `ResolvedBirthData`, `ResolutionWarning`,
`BirthDataResolver`, `PlaceCatalog`, `LocalPlaceCatalog`, `ResolvedPlace`,
`PlaceNotFound`, `PlaceCatalogUnavailableError`, `resolve_historical_tz`,
`resolve_anomaly`, `TzOk`, `TzNonexistent`, `TzAmbiguous`,
`UnknownTimezoneError`.

## 7. `pyproject.toml`

Добавить в `dependencies`: `tzdata`.

Добавить в `[project.optional-dependencies].dev`: `pytest-asyncio`.

Добавить в `[tool.pytest.ini_options]`: `asyncio_mode = "auto"` — иначе
каждый асинхронный тест придётся помечать `@pytest.mark.asyncio`,
и забытая пометка даёт молча пропущенный тест, а не падение.

---

**Ограничения.**

- Не трогать `engine/`, `tools/`, `intent/`, `interpretation/`,
  `orchestration/`, `llm/`.
- Не трогать `cli.py` — интеграция с CLI это отдельный промт
  (`03-birth-resolution-cli.md`).
- Не писать тесты — отдельный промт (`02-birth-data-resolution-tests.md`);
  код должен просто им удовлетворять.
- Не подключать `timezonefinder`, `pytz`, `geopy`, `python-dateutil`
  и любые геокодеры.
- Не создавать `RemoteGeocoder`, вариант `Candidates` в `PlaceResolution`,
  кэш каталога, эндпоинт подсказок.
- Не добавлять `place_substituted`, `place_input_text`, `place_text`.
- Резолвер не обращается к `SessionStore`, `ChartArtifactResolver`,
  `EngineService` и не выбирает `chart_kind`.
- Не подставлять и не «чинить» несуществующее время при явно указанном
  `birth_time`.

---

**Критерии приёмки.**

1. `from exact_orb.birth import BirthDataResolver, resolve_historical_tz`
   импортируется без ошибок.
2. `BirthInput(birth_date=date(1990,9,2), place_id="524901")` даёт
   `birth_time is None`.
3. `ResolvedBirthData(..., utc_datetime=<naive>)` кидает
   `ValidationError` с текстом, содержащим `timezone-aware UTC`.
4. `resolve_historical_tz(datetime(1990,9,2,14,30), "Europe/Moscow")`
   возвращает `TzOk` с `utc_offset_seconds == 14400` и
   `utc_datetime == datetime(1990,9,2,10,30, tzinfo=timezone.utc)`.
5. `resolve_historical_tz(datetime(2011,3,27,2,30), "Europe/Moscow")`
   возвращает **`TzNonexistent`**, а не `TzAmbiguous`, с
   `normalized == datetime(2011,3,27,3,30)`.
6. `resolve_historical_tz(datetime(2014,10,26,1,30), "Europe/Moscow")`
   возвращает `TzAmbiguous` с `offsets == (14400, 10800)`.
7. `resolve_historical_tz(datetime(1955,3,10,12,0), "Europe/Moscow")`
   возвращает `TzOk`, среди `warnings` есть код
   `pre_1970_offset_unverified`; для даты 1990 года такого warning нет.
8. `resolve_historical_tz(<aware datetime>, "Europe/Moscow")` кидает
   `ValueError` с текстом `local_datetime must be naive`.
9. `resolve_historical_tz(datetime(1990,1,1,12,0), "Nowhere/Fake")`
   кидает `UnknownTimezoneError`.
10. `LocalPlaceCatalog.from_file(<jsonl>)` затем
    `await catalog.lookup("524901")` даёт `ResolvedPlace`
    с `tz_id == "Europe/Moscow"`; `lookup("999999999")` даёт
    `PlaceNotFound`.
11. Резолвер на валидном вводе с временем возвращает `ResolvedBirthData`
    с `time_unknown is False`.
12. Резолвер на вводе без времени возвращает `ResolvedBirthData`
    с `time_unknown is True` и `utc_datetime`, посчитанным от 12:00
    локального.
13. Резолвер на неизвестном `place_id` возвращает `InputRequired`
    с единственным issue `field == "birth.place"`, `code == "INVALID"`.
14. Резолвер на дате вне диапазона **и** неизвестном месте возвращает
    `InputRequired` с **двумя** issue.
15. Резолвер на `Africa/Casablanca`, дата `1967-06-03`, время не указано,
    возвращает `ResolvedBirthData` (не `InputRequired`) с warning
    `noon_anchor_adjusted`.
16. Тот же вход, но `birth_time = time(12, 0)` явно, возвращает
    `InputRequired` с `field == "birth.time"`, `code == "INVALID"`.
17. Каталог, чей `lookup` кидает `PlaceCatalogUnavailableError`, даёт
    `ResolutionUnavailable`, а **не** `InputRequired`.
18. Ни один модуль в `birth/` не импортирует ничего из `exact_orb.engine`,
    `exact_orb.tools`, `exact_orb.orchestration`.
19. `BirthInput(birth_date=..., birth_time=time(14,30,tzinfo=timezone.utc),
    place_id=...)` кидает `ValidationError` с текстом
    `birth_time must be naive`.
20. `resolve_historical_tz(datetime(1800,1,1,12,0), "Europe/Moscow")`
    возвращает `utc_offset_seconds == 9017` — то есть `+02:30:17`,
    не округлённые до минут 9000.
21. `resolve_anomaly(TzNonexistent(...))` для `Africa/Casablanca 1967-06-03
    12:00` даёт `TzOk` с моментом, посчитанным от 13:00 локального;
    `resolve_anomaly(TzAmbiguous(...))` для `Pacific/Kwajalein 1969-09-30
    12:00` даёт `utc_offset_seconds == 39600` (fold = 0).
22. Ни `resolve_anomaly`, ни `resolve_historical_tz` не ставят warning
    `noon_anchor_adjusted` — его ставит резолвер.
23. Резолвер на дате `2500-01-01` при `max_birth_date = 2399-12-31`
    возвращает **один** issue на `birth.date`, а не два, и его
    `constraints["max"]` равен дате из `today_provider()`.
24. `resolve_historical_tz` и `resolve_anomaly` не импортируют ничего
    из `exact_orb.birth.resolver`: зависимость направлена в одну сторону.
25. `BirthDataResolver(..., min_birth_date > max_birth_date)` кидает
    `ValueError` с текстом `min_birth_date must be <= max_birth_date`.
