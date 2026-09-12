# Целостность материализованных конфигураций

Работай только в ветке `fix/aspect-model-semantics`. Перед изменениями
убедись, что это текущая ветка; если нет — остановись.

Не создавай новую ветку, коммит, push или PR. Сохрани все пользовательские и
несвязанные изменения рабочего дерева.

`prompts/**` — исторический журнал. Не изменяй ни этот, ни другие промты.

## Исходное состояние

Первые два этапа уже выполнены:

- коммит `229be82` и ADR-0029 ввели единые машинные идентификаторы точек и
  проверку разрешимости каждого `AspectPointRef` в `NatalChart.bodies` или
  `NatalChart.angles`;
- коммит `3cca8eb` и ADR-0030 сделали `true_node` единственным расчётным
  представителем оси лунных узлов, сохранив `south_node` только как
  производную `BodyPosition`.

Текущий configuration finder действительно строит фигуры только из уже
посчитанных аспектов. `build_configuration()` переносит найденные рёбра в
`Configuration.aspects`, вычисляет `max_orb` и назначает роли участникам.

Однако после сериализации, декодирования или ручной сборки модели основной и
вложенные объекты становятся независимо задаваемыми данными:

```text
NatalChart.aspects
NatalChart.configurations[].aspects
NatalChart.configurations[].contains[].aspects
```

Действующий validator `NatalChart` проверяет только, что ссылки в этих
структурах разрешаются и не используют зависимый `south_node`. Он не
доказывает, что вложенное ребро является точной копией канонического аспекта,
что роли и рёбра образуют заявленный тип фигуры и что `max_orb` действительно
получен из её рёбер.

Есть и связанный пробел include-контракта. `domain.normalize_include()`
разрешает запросить `configurations` без `aspects`. В этом случае
`calculate_natal()` скрыто считает аспекты для finder, возвращает
`configurations`, но записывает `NatalChart.aspects=None`. Материализованное
представление остаётся без публичного источника истины.

Существующий property-тест finder проверяет только совпадение нестрогого ключа
ребра с входом. Он не покрывает полный `Aspect`, агрегат `NatalChart`,
рекурсивный `contains`, JSON-кодек и fail-open поведение Calculation Cache.

## Цель и принятое решение

Сохранить полные `Aspect` внутри `Configuration` как обоснованное
материализованное представление, но перестать считать их независимым
источником истины.

После изменения:

- `NatalChart.aspects` — единственный канонический список аспектов карты;
- публичный блок `configurations` допустим только вместе с блоком `aspects`;
- каждый аспект каждой конфигурации, включая весь `contains`, структурно
  равен одному аспекту из `NatalChart.aspects`;
- `Configuration.points`, роли, рёбра, `max_orb` и `chart` внутренне
  согласованы;
- вложенные конфигурации проверяются рекурсивно и соответствуют действующей
  семантике большого креста и Т-квадрата;
- рассинхронизированный сериализованный artifact отклоняется как
  `ChartArtifactDecodeError(reason="validation")`, регистрируется как
  `cache_corrupt` и пересчитывается по существующему fail-open пути;
- валидный расчётный результат, его сортировка, числа, CLI и Research
  projection не меняются.

Не заменяй вложенные аспекты идентификаторами, индексами или новым DTO. Для
текущего продукта полные рёбра нужны как самодостаточное объяснение фигуры;
задача этого этапа — установить доказуемую производность копии.

## Перед изменениями

1. Выполни:

   ```text
   git branch --show-current
   git status --short
   git log -3 --oneline
   ```

2. Убедись, что в истории присутствуют `229be82` и `3cca8eb`, а текущая
   ветка — `fix/aspect-model-semantics`.

3. Зафиксируй исходные результаты целевых тестов:

   ```text
   pytest tests/test_configurations.py tests/test_aspects.py tests/test_natal_include_gating.py -q
   pytest tests/test_chart_artifact_codec.py tests/test_chart_artifact_resolver.py -q
   pytest tests/test_calculation_engine.py tests/test_calculation_keys.py -q
   pytest tests/research/test_projection.py -q
   ```

4. Изучи действующие источники истины:

   - `AGENTS.md`;
   - `docs/development_approach/problems_detected_by_human/002_full-build-natal-log-exposed-aspect-model-defects.md`;
   - ADR-0008, ADR-0017, ADR-0027, ADR-0029 и ADR-0030;
   - `docs/requirements/decisions/README.md`;
   - `docs/requirements/component_responsibilities/exact-orb_calculation_requirements.md`,
     особенно Т-НАТ-2…Т-НАТ-6, Т-АСП-12…Т-АСП-14, Т-КНФ-1…Т-КНФ-9,
     Т-ГРН-7…Т-ГРН-8 и Т-ДЕТ-3…Т-ДЕТ-4;
   - `docs/requirements/component_responsibilities/exact-orb_build_natal_components.md`,
     особенно нормализацию `include`, контракт `ChartArtifact` и
     `cache_corrupt`;
   - `docs/architecture/exact_orb_class_diagram.puml`;
   - `src/exact_orb/domain.py`;
   - `src/exact_orb/engine/aspects/types.py`;
   - `src/exact_orb/engine/charts/natal.py`;
   - `src/exact_orb/engine/configurations/types.py`, `finder.py` и все
     `patterns/*.py`;
   - `src/exact_orb/calculation/spec.py`, `types.py`, `chart_contract.py`,
     `codec.py` и `artifacts.py`;
   - связанные тесты, helpers, golden-данные и fixtures.

5. Через `rg` найди все актуальные употребления:

   ```text
   normalize_include
   Configuration(
   Configuration.aspects
   Configuration.points
   Configuration.contains
   max_orb
   include_nested
   cache_corrupt
   NATAL_ARTIFACT_JSON_BASELINE_SHA256
   ENGINE_VERSION
   ```

Не выполняй глобальную замену и не создавай второй параллельный набор
валидаторов с другим определением конфигураций.

## Архитектурное решение

Создай ADR-0031 о целостности материализованных конфигураций и добавь его в
`docs/requirements/decisions/README.md`.

ADR должен явно зафиксировать:

1. `NatalChart.aspects` является каноническим источником рёбер карты.
2. `Configuration.aspects` сохраняет полные `Aspect` как производное
   материализованное представление для объяснимости и сериализации.
3. Ссылки или отдельный публичный `AspectId` сейчас не вводятся.
4. `configurations` требует публичный блок `aspects`; запрос без него
   отклоняется, а не молча расширяется и не создаёт фигуры при
   `NatalChart.aspects=None`.
5. Производность означает точное структурное равенство полного `Aspect`, а не
   совпадение только пары точек, типа или округлённого орбиса.
6. Роли, набор участников и топология рёбер проверяются по типу конфигурации.
7. `max_orb` и `chart` вычислимы из сериализованных участников и рёбер и
   поэтому входят в обязательный aggregate-инвариант.
8. Те же правила рекурсивно действуют для `Configuration.contains`.
9. Несогласованный cache payload является corrupt, а не stale: он не
   представляет внутренне валидный artifact другой версии или другого
   запроса.
10. Кодек не владеет предметной проверкой: он продолжает преобразовывать
    validation failure агрегата в существующий reason `validation`.
11. Валидный расчётный алгоритм и стандартные профили не меняются, поэтому
    `ENGINE_VERSION`, `profiles_digest`, `CALCULATION_VERSION_SCHEMA` и
    `calculation_key` не меняются.
12. Отдельная `artifact_schema_version` не вводится: строгая текущая модель
    отклоняет несовместимый JSON, а Calculation Cache уже работает fail-open.
13. Research v1 не меняется: projection получает только валидный artifact и
    не переносит вложенные рёбра конфигурации в `ConfigurationFeature`.
14. ADR-0031 закрывает отложенную часть ADR-0029 и ADR-0030 про целостность
    materialized-конфигураций, не переписывая исторический смысл этих ADR.
15. Неизвестное время рождения остаётся отдельным следующим решением.

### Явная граница категории, стихии и модальности

`Configuration.category`, `element` и `modality` не должны получить ложную
aggregate-валидацию в этом этапе.

- `category` зависит не только от `max_orb`, но и от настраиваемого
  `ConfigurationConfig.configuration_categories`;
- `element` и `modality` могут зависеть от настраиваемого
  `ConfigurationConfig.point_signs`;
- использованный `ConfigurationConfig` сейчас не хранится в `NatalChart` или
  `ChartSpec`.

Поэтому при декодировании нельзя доказать эти значения только из artifact для
всех поддерживаемых низкоуровневых вызовов. Сохрани действующую enum/schema-
валидацию и producer-тесты, но не сравнивай эти поля с default thresholds или
знаками карты как будто пользовательской настройки не существует.

Не добавляй configuration provenance в `ChartSpec`, `NatalChart` или
`ChartArtifact` и не удаляй настройку. Если документация раньше обещала
aggregate-проверку категории, уточни эту границу в ADR-0031, не расширяя
текущий этап.

## Изменение include-контракта

### 1. Единая нормализация

В `exact_orb.domain.normalize_include()` добавь правило:

```text
configurations requires aspects
```

Если `configurations` присутствует, а `aspects` отсутствует, выбрасывай
детерминированный `ValueError`, в тексте которого есть оба имени блоков.

Не добавляй `aspects` автоматически. `include` — явный публичный запрос, а
тихое расширение скрывает фактический объём результата и меняет каноническое
значение `ChartSpec` без явного согласия вызывающего.

Правило должно одинаково применяться:

- в `NatalChartSpec`;
- в prevalidation `EngineService`;
- в прямом `calculate_natal()`;
- для `natal` и `cosmogram`.

Не заводи отдельную несовпадающую проверку только в `calculation/spec.py` или
только в `engine/charts/natal.py`. `domain.normalize_include()` остаётся
единственным источником правил совместимости include.

После нормализации `calculate_natal()` должен считать аспекты по условию
`"aspects" in include_blocks`. Условие через объединение
`{"aspects", "configurations"}` больше не выражает действующий контракт.

Сохрани различие `None` и пустой коллекции:

- блок не запрошен — поле `None`;
- блок запрошен и вычислен без результатов — пустой tuple;
- `configurations` не может быть не-`None`, когда `aspects is None`.

### 2. Защита агрегата

Одной входной нормализации недостаточно: `NatalChart` и artifact можно собрать
напрямую или декодировать из JSON.

Cross-object validator `NatalChart` должен отклонять любое состояние, где
`configurations is not None`, но `aspects is None`, включая
`configurations=()`.

Ошибка должна содержать путь/имена обоих полей. Не восстанавливай отсутствующие
аспекты из конфигураций и не заменяй `None` пустым tuple.

## Каноническое равенство аспектов

Для проверки материализации используй полный `Aspect` как value object.

Вложенный аспект считается принадлежащим `NatalChart.aspects`, только если
структурно совпадают все его публичные поля:

- `from_point.chart` и `from_point.body`;
- `to_point.chart` и `to_point.body`;
- `aspect_type`;
- `exact_angle`;
- `orb`;
- `category`;
- `applying`.

Не нормализуй направление ребра во время этой проверки. Если в основном
списке записано `sun → moon`, копия `moon → sun` не является точной
материализацией этого объекта, даже при симметричной геометрии.

Не используй:

- сравнение только по unordered pair и `aspect_type`;
- округление орбиса;
- `pytest.approx` или epsilon в production validator;
- повторный расчёт углов по долготам;
- поиск «похожего» аспекта;
- исправление вложенного DTO значением из основного списка.

Finder создаёт копию из уже готового объекта, поэтому точное value equality
должно выполняться и после JSON round-trip. Несовпадение любого поля означает
corrupt materialization.

Проверяй все верхнеуровневые конфигурации и весь рекурсивный `contains` против
одного и того же канонического `NatalChart.aspects`.

Не вводи требование ссылочной идентичности Python-объектов через `is`: после
декодирования равные DTO закономерно являются разными экземплярами.

## Внутренняя целостность Configuration

Проверку выполняй на ответственном уровне агрегата/предметного helper, а не в
JSON-кодеке. Она должна быть доступна `NatalChart` после полной валидации
вложенных Pydantic-моделей и выдавать детерминированный путь ошибки, например:

```text
configurations[0].aspects[1]
configurations[0].points['apex']
configurations[0].max_orb
configurations[0].contains[0].aspects[2]
```

Не размножай определения фигур. Вынеси минимальные общие константы или helper
так, чтобы finder и validator не расходились в матрице ролей и рёбер. При этом
не выполняй широкий рефакторинг pattern modules.

### 1. Роли и участники

Для каждого типа требуется точный набор ролей:

| Тип | Роли |
|---|---|
| `t_square` | `apex`, `base_1`, `base_2` |
| `yod` | `apex`, `base_1`, `base_2` |
| `bisextile` | `center`, `wing_1`, `wing_2` |
| `grand_trine` | `point_1`, `point_2`, `point_3` |
| `grand_cross` | `axis_1_a`, `axis_1_b`, `axis_2_a`, `axis_2_b` |
| `trapeze` | `opposition_1`, `opposition_2`, `base_1`, `base_2` |

Обязательные инварианты:

- нет отсутствующих или лишних ролей;
- значения всех ролей различны по `(chart, body)`;
- множество `Configuration.points.values()` в точности равно множеству
  концов `Configuration.aspects`;
- ни один участник не присутствует только в `points` или только в рёбрах.

### 2. Топология по ролям

Проверяй не только количество типов рёбер, но и их связь с ролями:

- `t_square`: `base_1–base_2` — opposition; `apex–base_1` и
  `apex–base_2` — square;
- `yod`: `base_1–base_2` — sextile; `apex–base_1` и `apex–base_2` —
  quincunx;
- `bisextile`: `center–wing_1` и `center–wing_2` — sextile;
  `wing_1–wing_2` — trine;
- `grand_trine`: все три пары участников — trine;
- `grand_cross`: каждая пара `axis_*_a–axis_*_b` — opposition, четыре пары
  между осями — square;
- `trapeze`: `opposition_1–opposition_2` — единственная opposition;
  на остальных пяти парах находятся ровно две trine и три sextile.

Для проверки пары можно считать аспект геометрически неориентированным, но
полное совпадение вложенного DTO с каноническим аспектом всё равно остаётся
ориентированным, как описано выше.

Отклоняй:

- пустой список рёбер;
- повтор одного ребра внутри фигуры;
- лишнее ребро;
- правильное количество типов с неправильным распределением по ролям;
- фигуру, собранную из канонических аспектов, но не соответствующую своему
  `Configuration.type`.

Не пересчитывай аспектную геометрию по долготам и не проверяй допустимость
орбиса заново: канонический `NatalChart.aspects` уже владеет этими фактами.

### 3. Производные поля

`Configuration.max_orb` обязан точно равняться максимальному `orb` среди её
`aspects`. Не округляй и не используй tolerance: это максимум уже
сериализованных значений.

`Configuration.chart` обязан равняться:

- единственному `chart`, если все `Configuration.points` принадлежат одной
  карте;
- строке `mixed`, если представлены несколько карт.

Не меняй `chart` автоматически при валидации.

Как оговорено в ADR-0031, на этом этапе не добавляй aggregate-проверку
`category`, `element` или `modality`: для произвольного поддерживаемого
`ConfigurationConfig` не хватает сериализованного источника параметров.

## Рекурсивный contains

Сохрани текущую семантику Т-КНФ-7:

- только `grand_cross` может содержать вложенные конфигурации;
- каждый элемент `contains` имеет тип `t_square`;
- участники вложенного Т-квадрата являются строгим подмножеством участников
  родительского креста;
- рёбра вложенного Т-квадрата являются подмножеством полных рёбер
  родительского креста;
- вложенный Т-квадрат сам не содержит другие конфигурации;
- его аспекты по-прежнему обязаны точно присутствовать в
  `NatalChart.aspects`.

Проверяй путь рекурсивно, даже если текущий finder создаёт только один уровень.
Не вводи произвольную глубину новых видов вложенности и не меняй поведение
`include_nested`.

Не требуй одновременно наличия вложенного Т-квадрата на верхнем уровне:
`include_nested` не сериализован в `NatalChart`, а действующий finder либо
прикрепляет и подавляет nested-фигуры, либо возвращает их верхним уровнем.

## Кэш и артефакты

`ArtifactNatalChart` наследует validator `NatalChart`; не дублируй
configuration-инварианты в `ChartArtifact` или `codec.py`.

Проверка должна обеспечивать следующий путь:

```text
несогласованный JSON
→ ArtifactNatalChart / NatalChart validation failure
→ ChartArtifactDecodeError(reason="validation")
→ cache_corrupt
→ miss
→ расчёт свежего artifact
→ попытка сохранить валидную замену
```

Не классифицируй такой payload как `cache_stale`: его key/spec/version могут
совпадать с запросом, но сам payload внутренне противоречив.

Не добавляй специальные версии payload, миграцию, repair-path или обработку
конкретных полей в resolver. Не логируй Pydantic details и содержимое
чувствительного artifact; сохраняется действующая техническая диагностика с
reason `validation`.

## CalculationVersion и сериализация

Не увеличивай `ENGINE_VERSION`: остаётся значение `"3"`.

Причина: для всех ранее валидных запросов finder, числовой расчёт, стандартные
профили и сериализованный результат не меняются. Усиливается принимающая
валидация и сужается недопустимая комбинация include, а не методика
вычисления карты.

Не меняй:

- `AspectConfig` и `ConfigurationConfig` defaults;
- `profiles_digest` payload;
- `CALCULATION_VERSION_SCHEMA`;
- формат `calculation_key`;
- `NATAL_ARTIFACT_JSON_BASELINE_SHA256` для неизменившегося референсного
  результата;
- gzip-параметры и normalized JSON;
- `FEATURE_SCHEMA_VERSION` и Research digest.

Если valid reference artifact меняет байты, остановись и найди причину: этот
этап не должен переписывать корректный результат.

## Требования к regression-тестам

Сначала сопоставь сценарии с существующим покрытием и расширяй ближайшие
тесты. Переиспользуй текущие reference chart, artifact builders и fake cache;
не создавай параллельные fixtures.

Обязательное доказательство:

1. `normalize_include()` отклоняет `configurations` без `aspects` для
   `natal` и `cosmogram`; сообщение содержит оба имени блока.
2. `NatalChartSpec` применяет то же правило, а default include обоих видов
   карты остаётся прежним и валидным.
3. Prevalidation `EngineService` отображает вручную сконструированный
   некорректный spec в действующий `SPEC_INVALID`, не запуская executor.
4. Прямой `calculate_natal()` отклоняет invalid include до обращения к
   Swiss Ephemeris.
5. Позитивные контроли подтверждают оба допустимых режима:
   `aspects` без `configurations` и оба блока вместе.
6. `NatalChart` отклоняет `configurations=()` и непустые configurations при
   `aspects=None`, но сохраняет `None` для обоих невключённых блоков.
7. В референсной карте каждый аспект каждой конфигурации рекурсивно имеет
   точное value-equal соответствие в `NatalChart.aspects`.
8. Параметризованный poisoned-payload тест меняет по одному полю вложенного
   аспекта: endpoint, направление, `aspect_type`, `exact_angle`, `orb`,
   `category`, `applying`. Каждый вариант отклоняется с точным путём.
9. Удаление соответствующего аспекта только из `NatalChart.aspects` делает
   оставшуюся конфигурацию невалидной.
10. Для каждого `ConfigurationType` позитивный синтетический пример проходит
    проверку с точным набором ролей и рёбер.
11. Отсутствующая роль, лишняя роль, повтор участника и участник вне концов
    рёбер отклоняются.
12. Для T-square, Yod, Bisextile и Grand Cross тест меняет распределение
    правильных типов рёбер между ролями; простого совпадения count недостаточно
    для прохождения.
13. Grand Trine отклоняет любое нетригональное ребро; Trapeze отклоняет
    неверные endpoints opposition и неверное распределение двух trine/трёх
    sextile.
14. Дублированное или лишнее ребро конфигурации отклоняется.
15. Изменённый `max_orb` отклоняется; позитивный контроль использует точный
    максимум рёбер, а не отдельно захардкоженное случайное число.
16. Неверный `Configuration.chart` отклоняется и для single-chart, и для
    synthetic mixed-chart конфигурации.
17. Валидный `grand_cross.contains[t_square]` проходит; неверный тип parent,
    неверный nested type, участник вне parent, ребро вне parent и второй
    уровень `contains` отклоняются с рекурсивным путём.
18. `find_configurations()` по-прежнему возвращает аспекты из входного списка,
    правильные роли, `max_orb`, категории, порядок и прежние шесть
    конфигураций референсной карты.
19. Artifact encode/decode round-trip остаётся байт-в-байт детерминированным,
    а существующий reference SHA не меняется.
20. Raw gzip JSON с рассинхронизированным configuration edge, points или
    `max_orb` даёт `ChartArtifactDecodeError(reason="validation")`.
21. Хотя бы один resolver integration test кладёт такой raw payload в fake
    cache и доказывает `cache_corrupt`, один вызов engine, один miss и запись
    свежей валидной замены. Негативное утверждение сопровождается проверкой,
    что payload действительно дошёл до decode-path.
22. Ошибка кодека и cache log не содержат Pydantic details, полного payload,
    времени, координат или текста предупреждений.
23. Research projection реального валидного artifact остаётся прежней; shape
    и digest v1 не меняются.
24. `ENGINE_VERSION == "3"`; diff не меняет calculation-version profiles.

Не подгоняй golden или SHA вместо проверки смысла. В intentional corruption
tests сначала создай валидный artifact, затем измени raw JSON после
`model_dump`, потому что новый validator не позволит сконструировать
противоречивый `ChartArtifact` обычным публичным конструктором.

## Актуальная документация

Синхронизируй только документы изменившегося контракта:

- `exact-orb_calculation_requirements.md`:
  - добавить `configurations requires aspects` в Т-НАТ-3;
  - заменить пробел Т-НАТ-6 принятым запретом и честной семантикой `None`;
  - уточнить Т-АСП-13: разрешимость ссылки остаётся отдельным базовым
    инвариантом;
  - добавить Т-КНФ-10 о точном соответствии materialized edges основному
    списку;
  - добавить Т-КНФ-11 о ролях, топологии, `max_orb`, `chart` и `contains`;
  - дополнить Т-ГРН-8 corrupt-cache поведением;
  - обновить coverage mapping;
- `exact-orb_build_natal_components.md`:
  - описать include-зависимость;
  - указать, что рассинхронизированная конфигурация является
    `cache_corrupt` и пересчитывается;
- `exact_orb_class_diagram.puml`:
  - показать `NatalChart.aspects` как канонический источник для
    `Configuration.aspects`;
  - кратко отметить aggregate validation, не изображая новые DTO, которых
    нет;
- новый ADR-0031 и индекс ADR.

Не изменяй Research requirements и `exact_orb_research_component.puml`:
валидная Research projection и её схема в этом этапе не меняются.

ADR-0029 и ADR-0030 оставь исторически правдивыми: новый ADR закрывает явно
отложенную ими работу. Файл
`docs/development_approach/problems_detected_by_human/002_full-build-natal-log-exposed-aspect-model-defects.md`
не помечай «устранено»: следующий этап про неизвестное время рождения ещё не
выполнен. Первоначальное наблюдение не переписывай.

## Жёсткие ограничения

- Не заменяй `Configuration.aspects` ссылочными DTO, индексами или ID.
- Не удаляй вложенные аспекты из сериализованного результата.
- Не восстанавливай и не исправляй corrupt payload автоматически.
- Не сравнивай только пару точек, тип или округлённый орбис.
- Не пересчитывай геометрию аспектов внутри configuration validator.
- Не добавляй tolerance для точных materialized copies.
- Не меняй определения фигур, finder, дедупликацию, сортировку или
  `include_nested`, кроме минимального общего источника role/topology metadata,
  если он действительно нужен validator.
- Не меняй числовой состав референсных аспектов и шести конфигураций.
- Не меняй орбисы, aspect/configuration categories и `applying=None`.
- Не добавляй ложную проверку `category`, `element` или `modality` без
  сериализованного `ConfigurationConfig`.
- Не добавляй `ConfigurationConfig` в `ChartSpec`, `NatalChart` или artifact.
- Не меняй `AspectConfig`, `ConfigurationConfig`, `ENGINE_VERSION` или
  calculation-version profiles.
- Не меняй модель оси узлов и не возвращай `south_node` в отношения.
- Не реализуй семантику `time_unknown=true` и неопределённость Луны.
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
pytest tests/test_configurations.py tests/test_aspects.py -q
pytest tests/test_natal_include_gating.py tests/test_calculation_engine.py tests/test_calculation_keys.py -q
pytest tests/test_chart_artifact_codec.py tests/test_chart_artifact_resolver.py -q
pytest tests/research/test_projection.py -q
```

### 2. Связанные

```text
pytest tests/test_calculation_block_integration.py tests/test_calculation_engine_integration.py -q
pytest tests/application/test_build_natal_integration.py -q
pytest tests/test_module_boundaries.py -q
```

### 3. Полный набор

```text
pytest -q
git diff --check
```

### 4. Статические проверки

Через `rg` и просмотр diff подтверди:

- нет пути `configurations` без `aspects` в нормализованном `ChartSpec`;
- `calculate_natal()` больше не считает скрытый приватный список аспектов
  только ради публичных конфигураций;
- каждый aggregate validation failure содержит детерминированный путь;
- кодек и resolver не получили предметного знания о полях конфигурации;
- `ENGINE_VERSION`, `CALCULATION_VERSION_SCHEMA`, profile payload, artifact
  baseline SHA и Research schema/digest не изменились;
- не затронуты node-axis, unknown-time и DEBUG logging paths.

Если доступен локальный PlantUML, проверь изменённый `.puml`. Новые
зависимости для этого не скачивай.

В конце просмотри полный `git diff` и `git status --short`, чтобы не включить
несвязанные пользовательские файлы.

## Итоговый отчёт

Начни с результата, затем кратко укажи:

- почему полные вложенные аспекты сохранены и какой список стал каноническим;
- созданный ADR-0031 и какие отложенные части ADR-0029/0030 он закрывает;
- выбранную семантику `configurations requires aspects` и почему invalid
  include отклоняется, а не расширяется;
- точное определение structural equality вложенного `Aspect`;
- где реализованы проверки ролей, топологии, `max_orb`, `chart` и
  рекурсивного `contains`;
- почему `category`, `element` и `modality` не получили ложную
  aggregate-валидацию без configuration provenance;
- поведение codec и fail-open Calculation Cache для corrupt payload;
- подтверждение неизменности валидного reference result, artifact SHA,
  Research v1 и `ENGINE_VERSION="3"`;
- фактически изменённые production-файлы, тесты и актуальные документы;
- точные команды проверок и реальные результаты;
- результат `git diff --check`, статического поиска и PlantUML-проверки;
- что не проверялось и следующий незакрытый этап: честная семантика аспектов
  при неизвестном времени рождения.
