# Ось лунных узлов без самоспекта и двойного веса

Работай только в ветке `fix/aspect-model-semantics`. Перед изменениями
убедись, что это текущая ветка; если нет — остановись.

Не создавай новую ветку, коммит, push или PR. Сохрани все пользовательские и
несвязанные изменения рабочего дерева.

`prompts/**` — исторический журнал. Не изменяй ни этот, ни другие промты.

## Исходное состояние

Первый этап уже выполнен коммитом `229be82`: `bodies`, аспекты,
конфигурации, транзиты и Research используют единые машинные идентификаторы
`true_node`, `south_node`, `mean_apog`, `pars_fortune`. ADR-0029 закрепил
ссылочную целостность и намеренно оставил семантику оси узлов следующей
отдельной работой.

Текущая модель по-прежнему рассматривает `true_node` и вычисленный как
`true_node + 180°` `south_node` как независимые точки аспектной сетки. Это
порождает два класса зависимых фактов:

1. гарантированную оппозицию оси к самой себе:

   ```text
   true_node opposition south_node
   orb = 0
   category = exact
   ```

2. зеркальные отношения одной внешней точки к двум концам одной оси:

   ```text
   conjunction  ↔ opposition
   sextile      ↔ trine
   semisextile  ↔ quincunx
   square       ↔ square
   ```

Одинаковый орб у таких записей следует из геометрии, а не доказывает два
независимых предметных факта. В результате связь с осью получает двойной вес,
а конфигурации могут использовать Северный и Южный узлы как самостоятельных
участников.

Это дефект действующего расчётного контракта, а не локальный дубль: точная
оппозиция и зеркальные записи закреплены requirements, expected-данными,
golden-выводом и сериализованными baseline.

## Цель и принятое предметное решение

Представлять ось лунных узлов одним расчётным участником — `true_node`.

После изменения:

- `true_node` является единственным представителем оси в натальных аспектах,
  натальных целях транзитов, station-aspects и конфигурациях;
- `south_node` остаётся вычисленной `BodyPosition` в `NatalChart.bodies`,
  доступной для отображения и самостоятельного чтения координаты;
- ни один новый `NatalChart` не содержит аспект с концом `south_node`;
- ни одна конфигурация, включая рекурсивные `contains`, не использует
  `south_node` как участника или конец ребра;
- ни один `TransitAspect.to` и `StationAspect.to` не ссылается на
  `south_node`;
- гарантированная оппозиция `true_node–south_node` отсутствует;
- связь внешней точки с осью представлена не более чем одним аспектом,
  рассчитанным непосредственно к долготе `true_node` по обычным орбисам;
- `south_node` не преобразуется в `true_node` постфактум и не порождает
  зеркальный тип аспекта.

Явная модель оси здесь — это канонический расчётный представитель плюс
производная отображаемая позиция. Не вводи новый публичный `NodeAxis` DTO,
отдельное семейство аспектов или вторую сериализованную структуру: для
текущего продукта они не нужны.

## Перед изменениями

1. Выполни:

   ```text
   git branch --show-current
   git status --short
   ```

2. Зафиксируй исходные результаты целевых тестов:

   ```text
   pytest tests/test_aspects.py tests/test_configurations.py tests/test_transits.py tests/test_chart_artifact_codec.py -q
   pytest tests/research/test_projection.py tests/research/test_models.py tests/test_calculation_version.py -q
   pytest tests/test_ephemeris.py tests/test_natal_include_gating.py tests/test_cli_render.py -q
   ```

3. Изучи действующие источники истины:

   - `AGENTS.md`;
   - `docs/development_approach/problems_detected_by_human/002_full-build-natal-log-exposed-aspect-model-defects.md`;
   - ADR-0017, ADR-0023, ADR-0027 и ADR-0029;
   - `docs/requirements/decisions/README.md`;
   - `docs/requirements/component_responsibilities/exact-orb_calculation_requirements.md`,
     особенно Т-ЭФ-21, Т-АСП-5, Т-АСП-8…Т-АСП-13, Т-КНФ-1…Т-КНФ-9 и
     Т-ГРН-7…Т-ГРН-8;
   - `docs/requirements/component_responsibilities/exact-orb_research_corpus.md`,
     особенно разделы о `BodyPoint`, `RelationalPoint` и проекции;
   - `docs/architecture/exact_orb_class_diagram.puml`;
   - `src/exact_orb/engine/aspects/types.py`;
   - `src/exact_orb/engine/aspects/finder.py`;
   - `src/exact_orb/engine/aspects/orbs.py`;
   - `src/exact_orb/engine/charts/natal.py`;
   - `src/exact_orb/engine/charts/transit.py`;
   - `src/exact_orb/engine/configurations/types.py`;
   - `src/exact_orb/engine/configurations/finder.py`;
   - `src/exact_orb/research/models.py` и `projection.py`;
   - `src/exact_orb/calculation/codec.py` и `version.py`;
   - `src/exact_orb/engine/__init__.py`;
   - связанные тесты, golden-данные и fixtures.

4. Через `rg` найди все актуальные употребления `south_node`,
   `natal_points`, `ConfigurationConfig.points`, `RelationalPoint`,
   `ENGINE_VERSION` и сериализованных baseline в `src/`, `tests/`,
   `docs/requirements/` и `docs/architecture/`.

Не выполняй глобальную замену: `south_node` должен остаться в расчёте
производной позиции, `BodyPoint`, CLI-подписях, body fixtures и тестах
положений.

## Архитектурное решение

Создай ADR-0030 о каноническом представителе оси лунных узлов и добавь его в
`docs/requirements/decisions/README.md`.

Это новое предметное решение, а не редакционное уточнение ADR-0029. Новый ADR
должен явно зафиксировать:

1. `true_node` — единственный расчётный представитель оси в отношениях.
2. `south_node` — производная позиция, равная `true_node + 180°`; она
   сохраняется в `NatalChart.bodies`, но не является независимым участником
   аспектов или конфигураций.
3. Исключение выполняется до поиска аспектов, а не эвристической
   дедупликацией готового списка по орбу или паре зеркальных типов.
4. Обычный аспект к `true_node` не переименовывается в аспект к оси и не
   дублируется отношением к `south_node`.
5. Для асимметричных лимитов, например `semisextile=1°` и `quincunx` с
   лимитом узла 3°, применяется только геометрия и лимит аспекта к
   `true_node`. Не нужно объединять допустимость двух концов оси.
6. Конфигурации строятся только из рёбер, уже прошедших эту нормализацию, и
   не могут вернуть `south_node` даже при отключённом пользовательском
   allowlist.
7. Натальные цели транзитов и station-aspects подчиняются той же модели оси.
8. Сериализованный результат и стандартные профили меняются, поэтому
   увеличивается `ENGINE_VERSION` и меняется `CalculationVersion`.
9. Отдельная `artifact_schema_version` не вводится. Формат
   `calculation_key`, `CALCULATION_VERSION_SCHEMA` и `ChartSpec` не меняются.
10. `feature_schema_version` Research v1 не меняется: форма признаков и
    digest algorithm прежние, а `ResearchRecord.calculation_version`
    разделяет результаты разных методик. Значение `south_node` остаётся в
    закрытом vocabulary v1 для чтения исторических записей, но новая
    проекция не выпускает его как конец отношения.
11. Вне решения остаются целостность материализованных рёбер конфигураций и
    семантика аспектов при неизвестном времени рождения.

ADR-0029 не переписывай под вид, будто оба узла никогда не участвовали в
аспектной сетке. В ADR-0030 явно укажи, какое оставленное ADR-0029 предметное
решение теперь заменено.

## Изменение расчётного поведения

### 1. Позиция Южного узла сохраняется

Не меняй `_add_derived_points()` и Т-ЭФ-21 по существу:

- `south_node.longitude` остаётся нормализованным `true_node + 180°`;
- сохраняются источник `derived`, широта, расстояние, скорости,
  ретроградность, дом, знак и `retflags`;
- `south_node` остаётся в `NatalChart.bodies` для natal и cosmogram;
- CLI продолжает показывать обе позиции в разделе тел.

Не удаляй `BodyPoint.SOUTH_NODE`, body fixture или presentation-label
`Юж. узел`.

### 2. Натальная аспектная сетка

В `AspectConfig` и натальном расчёте:

- удали `south_node` из стандартного `natal_points`; новый набор содержит 18
  точек в прежнем относительном порядке;
- удали неиспользуемый стандартный body-orb для `south_node`, сохранив орб
  `true_node=3°` и все остальные значения;
- запрети явно сконфигурированный `south_node` в `natal_points`, чтобы
  вызывающий не мог молча вернуть старую методику под новой
  `CalculationVersion`;
- выполняй исключение на этапе отбора `PositionedPoint`, до
  `find_aspects()`;
- не добавляй post-processing готовых аспектов и не сравнивай орбы для
  поиска зеркал;
- не меняй углы аспектов, правила орбисов, категории, сортировку и
  `applying=None`.

`find_aspects()` остаётся универсальным геометрическим алгоритмом. Не добавляй
в него условие по строкам `true_node` / `south_node`: если низкоуровневый
вызывающий явно передал две произвольные точки на расстоянии 180°, finder
должен по-прежнему уметь найти оппозицию. Предметная нормализация принадлежит
слою формирования сетки карты.

Не заменяй удалённый аспект к `south_node` зеркальным аспектом к
`true_node`. Единственный результат определяется обычным расчётом от
фактической долготы внешней точки до долготы `true_node`.

### 3. Конфигурации

В стандартном `ConfigurationConfig.points` оставь 13 независимых участников:
десять планет, Хирон, `true_node`, `mean_apog`. Удали только `south_node`.

Одного изменения default allowlist недостаточно: сейчас `points=None`
отключает фильтр. Предметный запрет оси должен выполняться независимо от
настройки дополнительного allowlist:

- явный `ConfigurationConfig(points=...)` с `south_node` должен отклоняться
  как противоречащий методике;
- `points=None` не должен возвращать `south_node` в граф;
- `AspectGraph` не должен использовать ребро, если хотя бы один его конец —
  зависимый `south_node`;
- остальные имена и универсальные синтетические `p0`, `p1`, ... остаются
  допустимыми для тестирования паттернов;
- не преобразуй `south_node` в `true_node` внутри графа: это может создать
  ложное ребро другого типа или дублировать уже существующее.

Конфигурации по-прежнему собираются только из переданных аспектов и не
пересчитывают геометрию. Не выполняй здесь следующий этап про равенство
вложенных `Configuration.aspects` элементам `NatalChart.aspects`, роли,
`max_orb` и `normalize_include`.

### 4. Транзиты и станции

Сейчас `_natal_points()` в transit-слое берёт все `natal.bodies` и тем самым
обходит `AspectConfig.natal_points`. Исправь этот путь: натальные цели
транзитов должны формироваться из того же разрешённого набора отношений, что
и натальные аспекты.

- используй один источник истины для разрешённых имён, не заводи отдельный
  несинхронизируемый tuple только для транзитов;
- исключи `south_node` до расчёта текущего аспекта, exact dates и closest
  approach;
- тот же отфильтрованный набор передавай в поиск station-aspects;
- не меняй список транзитных тел, окна поиска, root finding,
  applying-семантику, станции, сортировку и орбисы;
- `true_node` остаётся допустимой натальной целью.

### 5. Инварианты моделей результата и кэш

Расширь существующий cross-object validator `NatalChart` узким семантическим
инвариантом оси:

- `south_node` допустим как ключ и `BodyPosition.name` в `bodies`;
- `south_node` недопустим в концах `NatalChart.aspects`;
- он недопустим в `Configuration.points`, `Configuration.aspects` и
  рекурсивных `Configuration.contains`;
- ошибка должна быть детерминированной и содержать путь нарушившей ссылки.

Это гарантирует, что старый или вручную собранный artifact с зависимыми
отношениями не станет допустимым новым `NatalChart` только потому, что ссылка
успешно разрешается в `bodies`. `ArtifactNatalChart` наследует этот контракт,
а кодек переводит validation failure в существующий `cache_corrupt`-путь.
Не добавляй отдельную таблицу допустимых payload-версий в кодек.

Добавь аналогичный инвариант на границе `TransitChart`:

- ни один `TransitAspect.to` не ссылается на `south_node`;
- ни один `StationAspect.to` внутри `TransitStation.natal_aspects` не
  ссылается на `south_node`.

Не запрещай строку `south_node` глобально в низкоуровневых
`AspectPointRef` / `NatalPointRef`: эти типы сами по себе не владеют
контекстом полной карты. Инвариант принадлежит агрегату результата.

### 6. Research v1

Новая Research projection должна сохранять `south_node` как `BodyFeature`,
потому что его позиция остаётся частью `NatalChart.bodies`, но не должна
получать его как конец нового `AspectFeature` или участника
`ConfigurationFeature`.

Сохрани `BodyPoint.SOUTH_NODE` и `RelationalPoint.SOUTH_NODE`. Последний нужен
для чтения уже сохранённых `feature_schema_version=1` записей старой
`CalculationVersion`; удаление enum-значения сделало бы существующий
persisted payload невалидным.

Измени drift-инвариант: активный набор `AspectConfig.natal_points` теперь
равен Research relational vocabulary без исторически допустимого
`south_node`, а не всему `RELATIONAL_POINTS`. Явно докажи обе стороны:

- `south_node` входит в body vocabulary;
- `south_node` остаётся допустимым legacy relational value v1;
- `south_node` отсутствует в активном aspect/configuration profile;
- новая проекция реального artifact не выпускает его в отношениях.

Не меняй `FEATURE_SCHEMA_VERSION`, `RESEARCH_DIGEST_FORMAT_VERSION`, форму
`ChartFeatures`, golden digest, не связанный с реальным изменившимся
artifact, и не добавляй миграцию Research records.

### 7. CalculationVersion

Увеличь `ENGINE_VERSION` с `"2"` до `"3"`: меняются методика формирования
аспектов, транзитных целей и конфигураций, а не только представление.

`profiles_digest` также должен измениться естественно после обновления
`AspectConfig.natal()`, `AspectConfig.transit()` и `ConfigurationConfig()`.
Тест должен доказать отличие нового профиля от старого существенным полем, а
не просто сравнить digest с захардкоженной случайной строкой.

Не меняй `CALCULATION_VERSION_SCHEMA`, формат `calculation_key`, `ChartSpec`,
`feature_schema_version` и не вводи `artifact_schema_version`.

## Требования к regression-тестам

Сначала сопоставь сценарии с существующим покрытием и расширяй ближайшие
тесты. Не создавай второй параллельный набор fixtures.

Обязательное доказательство:

1. `south_node` по-прежнему присутствует в `bodies`, имеет источник
   `derived` и долготу ровно на 180° от `true_node` с учётом нормализации.
2. В референсной натальной карте ни один конец аспекта не равен
   `south_node`; список непуст и содержит аспекты к `true_node` как
   позитивный контроль.
3. В частности, отсутствует `true_node opposition south_node`.
4. Для референсной карты удалены именно пять прежних отношений к Южному
   узлу:

   ```text
   true_node opposition south_node
   mean_apog opposition south_node
   sun sextile south_node
   jupiter square south_node
   south_node trine asc
   ```

   Остальные типы, орбисы, категории и `applying=None` не меняются.
5. Зеркальные позитивные контроли остаются только у представителя оси:

   ```text
   true_node conjunction mean_apog
   sun trine true_node
   jupiter square true_node
   true_node sextile asc
   ```

6. `AspectConfig.natal_points` и оба стандартных orb-профиля не содержат
   `south_node`; `true_node` сохраняет прежний орб 3°.
7. Явная попытка вернуть `south_node` в `AspectConfig.natal_points`
   отклоняется. Низкоуровневый `find_aspects()` при этом остаётся
   name-agnostic и геометрически корректным.
8. Референсные конфигурации больше не содержат два T-square с
   `south_node`. Остальные шесть конфигураций, их роли, рёбра, `max_orb` и
   категории остаются прежними.
9. Синтетический configuration-тест доказывает, что `points=None` не обходит
   запрет `south_node`; позитивный независимый паттерн в том же тесте или
   соседнем сценарии подтверждает, что finder действительно выполнялся.
10. Ни верхнеуровневые, ни вложенные конфигурации не содержат `south_node`.
11. Транзитный результат не содержит `south_node` в `TransitAspect.to`, но
    содержит аспект к `true_node` как позитивный контроль.
12. Station-aspects используют тот же фильтр. Негативное утверждение должно
    сопровождаться управляемым тестом, в котором station-aspects реально
    найдены для другой натальной точки.
13. `NatalChart` отклоняет payload с `south_node` в основном аспекте,
    configuration point, configuration edge и рекурсивном `contains`, хотя
    само тело `south_node` присутствует и разрешимо.
14. `TransitChart` отклоняет payload с `south_node` в обычном transit aspect
    и station-aspect.
15. Artifact encode/decode и normalized JSON baseline отражают новый состав
    отношений. Сначала проверь структурные assertions, затем обновляй
    intentional digest; не подгоняй его вместо проверки смысла.
16. Research projection сохраняет `BodyFeature(point=south_node)`, но в её
    аспектах и конфигурациях `south_node` отсутствует. Исторический
    `ChartFeatures(feature_schema_version=1, ...)` с
    `RelationalPoint.SOUTH_NODE` остаётся валидным.
17. `CalculationVersion` меняется и по `ENGINE_VERSION`, и по обновлённому
    `profiles_digest`.
18. CLI golden сохраняет строку позиции Южного узла, но удаляет аспекты и
    конфигурации с ним. Человекочитаемые подписи не переименовываются.

Не вычисляй новые ожидаемые результаты вручную там, где их можно получить из
доказанного изменения исходного набора. Если меняется что-либо кроме
отношений с `south_node`, зависимых конфигураций, детерминированной сортировки
и соответствующих сериализованных digest, остановись и найди причину.

## Актуальная документация

Синхронизируй только документы, описывающие изменившийся контракт:

- `exact-orb_calculation_requirements.md`:
  - сохранить Т-ЭФ-21 о вычислении позиции;
  - изменить таблицу орбисов и Т-АСП-8 на сетку из 18 точек;
  - добавить явный инвариант представителя оси после Т-АСП-13;
  - изменить Т-КНФ-2 на 13 независимых участников;
  - описать влияние ADR-0030 на `CalculationVersion`;
- `exact-orb_research_corpus.md`: развести body vocabulary, исторически
  допустимый relational vocabulary v1 и активный набор новых отношений;
- `exact_orb_class_diagram.puml`: показать, что `south_node` остаётся
  производной `BodyPosition`, но не входит в relation graph;
- новый ADR-0030 и индекс ADR.

ADR-0023 и ADR-0029 оставь исторически правдивыми. Файл
`docs/development_approach/problems_detected_by_human/002_full-build-natal-log-exposed-aspect-model-defects.md`
не помечай «устранено»: в нём остаются незакрытые этапы про конфигурационную
целостность и неизвестное время. Не переписывай первоначальное наблюдение.

## Жёсткие ограничения

- Не удаляй `south_node` из `NatalChart.bodies`, fixtures положений, CLI или
  `BodyPoint` Research.
- Не меняй формулу, поля или точность вычисления `south_node`.
- Не добавляй эвристику одинакового орба и post-hoc дедупликацию аспектов.
- Не добавляй специальное знание об узлах в универсальный `find_aspects()`.
- Не превращай аспект к Южному узлу в зеркальный аспект к Северному.
- Не меняй аспекты и конфигурации, не зависящие от `south_node`.
- Не реализуй ссылочную целостность materialized-конфигураций из следующего
  промта: равенство вложенных рёбер, ролей, `max_orb` и основного списка.
- Не меняй `normalize_include` и зависимость `configurations` от `aspects`.
- Не меняй поведение при `time_unknown=true` и аспекты Луны.
- Не меняй `applying=None`.
- Не удаляй `house_system="P"` у космограммы.
- Не удаляй `artifact.spec` или `delta.base_chart_spec` и не ослабляй их
  сквозное равенство.
- Не вводи `artifact_schema_version`, новый Research schema version,
  compatibility aliases, migration layer, зависимости, сервисы или сетевые
  вызовы.
- Не меняй полное DEBUG-логирование component boundaries.
- Не редактируй исторические `prompts/**`.
- Не выполняй попутный рефакторинг.
- Не запускай платные или сетевые smoke-тесты.
- Не создавай коммит, push или PR.

## Проверки

Запускай поэтапно и сообщай только фактические результаты.

### 1. Целевые

```text
pytest tests/test_aspects.py tests/test_configurations.py -q
pytest tests/test_transits.py -q
pytest tests/test_chart_artifact_codec.py tests/test_calculation_version.py -q
pytest tests/research/test_projection.py tests/research/test_models.py -q
pytest tests/test_cli_render.py tests/test_ephemeris.py tests/test_natal_include_gating.py -q
```

### 2. Связанные

```text
pytest tests/test_calculation_engine.py tests/test_calculation_block_integration.py -q
pytest tests/application/test_build_natal_integration.py -q
pytest tests/test_module_boundaries.py -q
```

### 3. Полный набор

```text
pytest -q
git diff --check
```

### 4. Статические проверки

Через `rg` классифицируй все оставшиеся употребления `south_node`.

Допустимы:

- расчёт и хранение производной `BodyPosition`;
- body fixtures и проверки положения;
- CLI/presentation labels;
- `BodyPoint` и legacy-compatible `RelationalPoint` Research v1;
- ADR и исторический файл проблемы;
- негативные тесты запрета в отношениях.

Недопустимы:

- стандартный `AspectConfig.natal_points` и body-orb profiles;
- стандартный `ConfigurationConfig.points`;
- expected натальные аспекты, конфигурации и транзитные цели;
- новые `AspectFeature` / `ConfigurationFeature`, построенные проекцией;
- обход запрета через `points=None`.

Если доступен локальный PlantUML, проверь изменённые `.puml`. Новые
зависимости для этого не скачивай.

В конце просмотри полный `git diff` и `git status --short`, чтобы не включить
несвязанные пользовательские файлы.

## Итоговый отчёт

Начни с результата, затем кратко укажи:

- корневую причину двойного веса и принятое представление оси;
- созданный ADR-0030 и его отношение к ADR-0029;
- где `south_node` сохранён как позиция и где запрещён как участник
  отношения;
- каким образом натальный, транзитный, station и configuration paths
  используют один разрешённый набор;
- какие пять аспектов и две конфигурации исчезли из референсного результата;
- подтверждение неизменности остальных чисел и `applying=None`;
- инварианты `NatalChart` и `TransitChart` и поведение corrupt cache payload;
- почему Research v1 сохраняет enum-значение `south_node`, но не выпускает
  его в новых отношениях;
- новое значение `ENGINE_VERSION`, изменение `profiles_digest` и причины не
  вводить другие schema versions;
- фактически изменённые production-файлы, тесты и актуальные документы;
- точные команды проверок и реальные результаты;
- результат `git diff --check`, статического поиска и PlantUML-проверки;
- что не проверялось и два следующих незакрытых этапа: целостность
  конфигураций и неизвестное время рождения.
