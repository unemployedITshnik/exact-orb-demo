# Aspect module and natal integration

Реализуй аспектный модуль exact-orb и подключи его к натальной карте.

═══════════════════════════════════════════════════════════
ЧАСТЬ 1. МОДУЛЬ
═══════════════════════════════════════════════════════════

Это МОДУЛЬ, не tool. Отдельным инструментом не выносится:
пользователь запрашивает карту, а не аспекты в отрыве от неё.
Модуль общий для натала и транзитов — логика пишется один раз.

  src/exact_orb/aspects/
  ├── types.py        AspectType, Aspect, AspectCategory
  ├── finder.py       find_aspects()
  ├── categories.py   классификация по орбису
  └── orbs.py         разрешение дифференцированных орбисов

ЯДРО

  find_aspects(
      set_a: Sequence[PositionedPoint],
      set_b: Sequence[PositionedPoint] | None,
      config: AspectConfig,
  ) -> list[Aspect]

  set_b = None → аспекты внутри одного набора (натальная карта),
                 каждая пара учитывается один раз, точка сама
                 с собой не аспектирует.
  set_b задан  → аспекты между наборами (транзиты к наталу),
                 каждая точка несёт пометку принадлежности карте.

Модель Aspect:
  from_point, to_point   с полем chart: "natal" | "transit" | ...
  aspect_type            conjunction | sextile | square | trine |
                         opposition | quincunx | semisextile
  exact_angle            0 | 60 | 90 | 120 | 180 | 150 | 30
  orb                    float, градусы
  category               exact | working | background
  applying               bool | None   (None для статичных карт)

РАССТОЯНИЕ — ГЛАВНЫЙ ИСТОЧНИК ОШИБОК
Считается по кратчайшей дуге через нормализацию разности:
тело в 359° и тело в 1° образуют соединение с орбисом 2°, а не 358°.
Явный тест обязателен.

ОРБИСЫ
Дифференцированные, приоритет разрешения:
  1. переопределение для пары (аспект + тело)
  2. минимальный из орбиса аспекта и орбиса тела
Светилам шире, фиктивным точкам уже. Значения в конфиге.
Правило задокументируй в docstring функции разрешения.

КАТЕГОРИИ
  exact       орб < 1.0°
  working     1.0–3.0°
  background  3.0–максимум

Два независимых набора порогов в конфиге: natal_orbs (максимум 7°)
и transit_orbs (максимум 6°). Натальные аспекты статичны и терпят
более широкие орбисы, транзитные работают только вблизи точности.

СХОДИМОСТЬ
В этом шаге НЕ реализуется. applying = None всегда.
Модуль phase.py появится отдельным шагом перед транзитами —
в натальной карте нет движения во времени, считать нечего.

═══════════════════════════════════════════════════════════
ЧАСТЬ 2. ПОДКЛЮЧЕНИЕ К NATAL
═══════════════════════════════════════════════════════════

get_natal() возвращает карту ЦЕЛИКОМ, включая аспекты.
Отдельного вызова для аспектов не существует — иначе оркестрация
сборки результата ложится на вызывающего, а это источник ошибок.

Опциональность через параметр, не через отдельный tool:

  get_natal(..., include: set[str] = {"positions", "houses",
                                      "rulers", "aspects"})

Не нужны аспекты — исключаются из include. Структура ответа
при этом не ломается: отсутствующий блок = None, не пустой список.

ЧТО АСПЕКТИРУЕТСЯ В НАТАЛЕ
Планеты, Хирон, Лилит, узлы, Селена, Парс Фортуны, Вертекс,
ASC и MC. DSC и IC отдельно НЕ аспектируются — они противоположны
ASC и MC, аспекты к ним дублируют информацию.

Набор участвующих точек конфигурируется: часть школ фиктивные
точки в аспектах не учитывает.

═══════════════════════════════════════════════════════════
ЧАСТЬ 3. ЭТАЛОН
═══════════════════════════════════════════════════════════

Карта 1985-09-01 20:45 UTC, 55.7522N 37.6155E, Плацидус.
max_orb = 7°, формат: A аспект B орбис

ТОЧНЫЕ (< 1°)
  north_node opposition south_node    0.00
  lilith     quincunx    pars         0.42
  moon       square      asc          0.44
  sun        square      pars         0.44
  uranus     opposition  chiron       0.44
  mercury    square      saturn       0.52
  jupiter    quincunx    asc          0.58
  sun        quincunx    jupiter      0.66
  north_node conjunction lilith       0.71
  lilith     opposition  south_node   0.71
  sun        trine       lilith       0.86
  chiron     quincunx    selena       0.93

РАБОЧИЕ (1–3°)
  moon       sextile     jupiter      1.02
  jupiter    sextile     pars         1.10
  north_node quincunx    pars         1.13
  sun        sextile     asc          1.24
  jupiter    square      lilith       1.52
  sun        trine       north_node   1.57
  sun        sextile     south_node   1.57
  sun        quincunx    moon         1.68
  pars       quincunx    asc          1.68
  neptune    sextile     pluto        1.78
  venus      trine       moon         2.07
  lilith     sextile     asc          2.10
  mars       square      saturn       2.18
  jupiter    square      north_node   2.23
  jupiter    square      south_node   2.23
  mercury    square      vertex       2.36
  mercury    conjunction mars         2.70
  north_node sextile     asc          2.81
  south_node trine       asc          2.81
  mars       opposition  mc           2.84
  saturn     conjunction vertex       2.88
  venus      square      pluto        2.94

ФОНОВЫЕ (3–7°)
  venus      opposition  jupiter      3.08
  neptune    sextile     mc           3.25
  sun        square      uranus       4.66
  venus      quincunx    neptune      4.72
  saturn     square      mc           5.01
  pluto      quincunx    moon         5.01
  pluto      trine       mc           5.03
  sun        square      chiron       5.10
  jupiter    sextile     uranus       5.32
  pluto      trine       asc          5.45
  mercury    opposition  mc           5.53
  jupiter    trine       chiron       5.76
  asc        quincunx    uranus       5.90
  moon       trine       uranus       6.34
  sun        sextile     pluto        6.68
  moon       sextile     chiron       6.78
  moon       square      neptune      6.79

ПРИМЕЧАНИЕ ПО СЕЛЕНЕ
Аспект chiron quincunx selena 0.93 рассчитан для Селены geocult
(Скорпион 15°22'27"). Если ваша реализация даёт другую методику,
этот аспект НЕ совпадёт — это ожидаемо и не является ошибкой.
Исключите Селену из сверки с эталоном либо параметризуйте тест
по методике.

═══════════════════════════════════════════════════════════
ЧАСТЬ 4. ТЕСТЫ
═══════════════════════════════════════════════════════════

- Полнота: множество найденных аспектов равно эталонному.
  Сравнение множествами, не по порядку.
- Орбисы с точностью 0.01°.
- Категории распределены верно.
- Симметрия: aspect(a,b) и aspect(b,a) — одна запись, не две.
- Граница фильтра: moon sextile jupiter (орб 1.02) не попадает
  при max_orb=1.0 и попадает при 1.1.
- Переход через 0° Овна: точки в 359° и 1° дают соединение 2°.
- include без "aspects" — блок отсутствует, остальное не ломается.

Property-based (hypothesis):
- орбис неотрицателен и не превышает максимума
- сдвиг всех долгот на константу не меняет набор аспектов
- точка сама с собой не аспектирует
- количество аспектов при max_orb=0 равно нулю

Запусти полный набор и покажи фактический вывод.
Не подгоняй допуски под результат — при расхождении сначала
объясни причину.

Добавь Промт в папку
