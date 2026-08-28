# Промт: добавить `RunContext` в `BirthDataResolver.resolve` для correlation logging

## Цель

Добавить необязательный telemetry-контекст `run` в
`BirthDataResolver.resolve(...)`, не меняя birth-модели и результат резолва.

`run_id` не должен попадать в `BirthInput`, `ResolvedBirthData`, cache key
или предметную логику.

## Контракт

Новый модуль **`src/exact_orb/run_context.py`** — верхний уровень, рядом
с `outcomes.py`:

```python
class RunContext(BaseModel):
    """Correlation scope of one user operation. Telemetry, not domain data."""

    run_id: UUID
    started_at: datetime

    @classmethod
    def new(cls) -> "RunContext": ...
```

`started_at` — timezone-aware UTC.

**Не класть в `birth/` и не класть в `application/`.** В `birth/` это
не предметная модель; в `application/` — `birth/` начнёт зависеть
от прикладного слоя, и упадёт существующий тест направления импортов.
`outcomes.py` лежит на верхнем уровне по той же причине.

Изменить резолвер:

```python
async def resolve(
    self,
    birth_input: BirthInput,
    *,
    run: RunContext | None = None,
) -> ResolvedBirthData | InputRequired | ResolutionUnavailable:
    ...
```

Имя первого параметра — `birth_input`, как сейчас: переименование ломает
совместимость по ключевому имени без выгоды. Тип возврата — явный union,
как сейчас; алиаса `BirthResolutionOutcome` в проекте нет, и заводить его
внутри этого промта не нужно.

Без `run` поведение остаётся прежним.

## Логирование

`LOGGER = logging.getLogger(__name__)` на уровне модуля.

**Стиль — inline `key=value` через `%`-аргументы. `extra=` не использовать.**
Это не вкусовщина: файловый форматтер проекта —

```python
"%(asctime)s %(levelname)s session=%(session)s logger=%(name)s %(message)s"
```

— кастомные атрибуты записи не читает, поэтому `logger.info(msg, extra={...})`
напечатает сообщение без единого переданного значения. В проекте `extra=`
не встречается ни разу.

Записи писать через `LOGGER.debug(...)`. Уровнями управляет существующая
конфигурация логирования; модуль не должен делать `LOGGER.setLevel(...)`.

Четыре записи:

```text
event=start                     в начале
outcome=resolved                + tz_id
outcome=input_required          + перечисление field:code через запятую
outcome=resolution_unavailable  + error_code
```

Начальная запись помечается `event=`, терминальные — `outcome=`: старт
не является исходом, и при разборе логов терминальные записи должны
отличаться от начальной.

Поле `run_id` присутствует в каждой записи:

* `run` передан → `str(run.run_id)`;
* `run is None` → `"-"`.

Столбец не должен пропадать, иначе строки перестанут разбираться
единообразно.

**Не логировать:** `latitude`, `longitude`, `birth_date`, `birth_time`,
`utc_datetime`, `canonical_place`, `place_id`.

Запрет на `place_id` — осознанный выбор в пользу И-14, а не упущение.
Цена: при устаревшем идентификаторе после обновления каталога в логе
не будет видно, какой именно `place_id` не разрешился. Не возвращать
его «для удобства».

`tz_id` в логах разрешён.

## Тесты

Новый файл `tests/test_run_context.py`. **Существующие
`tests/test_birth_tz.py`, `tests/test_birth_places.py`,
`tests/test_birth_resolver.py` не изменять ни на строку** — параметр
необязательный именно ради этого, и их неизменность есть доказательство
обратной совместимости.

Перехват записей — `caplog` с:

```python
caplog.set_level(logging.DEBUG, logger="exact_orb.birth.resolver")
```

Фильтровать записи по `record.name == "exact_orb.birth.resolver"`, чтобы
тесты не ловили шум от других логгеров.

1. `resolve(...)` без `run` отрабатывает как раньше: результат равен
   результату с `run` для того же ввода.
2. `resolve(..., run=RunContext.new())` на успешном вводе пишет две записи,
   `event=start` и `outcome=resolved`; обе содержат тот же `run_id`.
3. Для `InputRequired` — `event=start` и `outcome=input_required`;
   терминальная запись содержит тот же `run_id` и перечисление `field:code`.
4. Для `ResolutionUnavailable` — `event=start`
   и `outcome=resolution_unavailable`; терминальная содержит тот же `run_id`
   и `error_code`.
5. Без `run` все записи содержат `run_id=-`.
6. Логи не содержат чувствительных значений фикстуры. Проверять
   по реальным подстрокам:

```text
"55.75222"    latitude Москвы
"37.61556"    longitude
"1990-09-02"  birth_date
"14:30"       birth_time
"Москва"      canonical_place
```

**Ловушка:** `tz_id=Europe/Moscow` в лог разрешён, поэтому подстрока
`Moscow` там присутствует законно. Проверять `canonical_place` нужно
по кириллическому `"Москва"`; проверка на латинское `"Moscow"` упадёт
на разрешённом поле.

7. `RunContext.new()` даёт разные `run_id` при двух вызовах.
8. Два прогона одного и того же ввода с разными `run_id` дают равные
   `ResolvedBirthData` при сравнении объектов целиком, без `exclude`.

Тест 6 стережёт И-14. Тест 8 — то, что телеметрия не просочилась
в предметную модель.

## Проверка

```bash
pytest tests/test_birth_resolver.py tests/test_run_context.py
pytest
```

## Критерий готовности

* `RunContext` добавлен как telemetry contract в
  `src/exact_orb/run_context.py`.
* `BirthInput` и `ResolvedBirthData` не содержат `run_id`;
  `rg -n "run_id" src/exact_orb/birth/types.py` ничего не находит.
* `BirthDataResolver.resolve(..., run=...)` логирует correlation id
  на всех четырёх записях.
* Без `run` резолвер совместим с текущим поведением; три существующих
  файла тестов не изменены.
* `rg -n "extra=" src/exact_orb` ничего не находит.
* Логи не раскрывают birth/place данные.
* Изменения ровно в двух файлах кода: новый `run_context.py`
  и `birth/resolver.py`.
* Весь пак проходит: было 344, ожидается 344 плюс новые тесты.
