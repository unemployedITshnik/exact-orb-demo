# Канонические машинные идентификаторы точек карты

Работай только в ветке `fix/aspect-model-semantics`. Перед изменениями
убедись, что это текущая ветка; если нет — остановись.

Не создавай новую ветку, коммит, push или PR. Сохрани все пользовательские и
несвязанные изменения рабочего дерева.

`prompts/**` — исторический журнал. Не изменяй ни этот, ни другие промты.

## Контекст и корневая причина

Полный DEBUG-payload `BuildNatalSuccess` показал нарушение ссылочной
целостности внутри одной карты:

```text
NatalChart.bodies:              true_node, mean_apog, pars_fortune
AspectPointRef / configurations: north_node, lilith, pars
```

Причина — `AspectConfig.point_aliases`: `_natal_aspect_points()` намеренно
заменяет raw-имя рассчитанной точки на псевдоним перед созданием аспектов.
Затем конфигурации наследуют псевдонимы из аспектного графа, транзитный слой
повторяет ту же замену, а `research.projection` вынужден выполнять обратное
отображение.

Это не три независимых представления, а один машинный граф объектов. Его
ссылки должны использовать тот же namespace, что ключи и `name` у
`NatalChart.bodies` и `NatalChart.angles`. Псевдонимы являются ответственностью
CLI или другого presentation-слоя и не должны входить в расчётный DTO,
артефакт или Research projection.

## Цель

Сделать единственными машинными идентификаторами соответствующих точек:

```text
true_node
south_node
mean_apog
pars_fortune
```

После изменения:

- `bodies`, натальные аспекты, конфигурации, транзитные ссылки и Research v1
  используют одно и то же написание;
- каждый `AspectPointRef` внутри `NatalChart` разрешается в конкретную запись
  `NatalChart.bodies` или `NatalChart.angles`;
- `north_node`, `lilith` и `pars` не являются допустимыми машинными именами
  расчётного результата;
- псевдонимы могут остаться только в человекочитаемых названиях и
  presentation-коде, например в таблице русских подписей CLI;
- состав аспектов, конфигураций и все числовые значения отличаются от
  исходных только переименованием идентификаторов.

Это первый этап исправления модели аспектов. Семантику оси лунных узлов,
целостность материализованных рёбер конфигураций и неизвестное время рождения
в этой задаче не менять.

## Перед изменениями

1. Выполни:

   ```text
   git branch --show-current
   git status --short
   ```

2. Зафиксируй исходные результаты целевых тестов:

   ```text
   pytest tests/test_aspects.py tests/test_configurations.py tests/test_transits.py tests/research/test_projection.py tests/research/test_models.py tests/test_calculation_version.py -q
   ```

3. Изучи действующие источники истины и конфликт между ними:

   - `AGENTS.md`;
   - `docs/development_approach/problems_detected_by_human/002_full-build-natal-log-exposed-aspect-model-defects.md`;
   - `docs/requirements/component_responsibilities/exact-orb_calculation_requirements.md`,
     особенно Т-ЭФ-11, Т-ЭФ-21, Т-ЭФ-22, Т-АСП-5, Т-АСП-8…Т-АСП-12 и
     Т-КНФ-1…Т-КНФ-3;
   - `docs/requirements/component_responsibilities/exact-orb_research_corpus.md`,
     особенно vocabulary точек и раздел «Проекция»;
   - ADR-0017, ADR-0023 и ADR-0027;
   - `docs/requirements/decisions/README.md`;
   - `docs/architecture/exact_orb_class_diagram.puml`;
   - `docs/architecture/exact_orb_research_component.puml`;
   - `src/exact_orb/engine/aspects/types.py`;
   - `src/exact_orb/engine/aspects/finder.py`;
   - `src/exact_orb/engine/aspects/orbs.py`;
   - `src/exact_orb/engine/charts/natal.py`;
   - `src/exact_orb/engine/charts/transit.py`;
   - `src/exact_orb/engine/configurations/types.py`;
   - `src/exact_orb/research/models.py`;
   - `src/exact_orb/research/projection.py`;
   - `src/exact_orb/calculation/types.py` и
     `src/exact_orb/calculation/codec.py`;
   - `src/exact_orb/calculation/version.py` и
     `src/exact_orb/engine/__init__.py`;
   - связанные тесты, golden-данные и общие fixtures.

4. Через `rg` найди все актуальные употребления `point_aliases`,
   `DEFAULT_POINT_ALIASES`, `north_node`, `lilith` и машинного имени `pars` в
   `src/`, `tests/`, `docs/requirements/` и `docs/architecture/`. Не выполняй
   слепую глобальную замену: человекочитаемый термин «Лилит», CLI-подпись и
   историческое описание уже найденного дефекта могут оставаться.

## Архитектурное решение

Текущий ADR-0023 фиксирует обратную канонизацию псевдонимов в Research
projection. Перенос канонического namespace на границу самого расчётного
результата меняет принятое архитектурное решение, поэтому не оформляй его
молчаливой ревизией ADR-0023.

Создай новый ADR-0029 о канонических идентификаторах точек карты и добавь его
в `docs/requirements/decisions/README.md`. ADR должен явно зафиксировать:

1. Канонический идентификатор точки один и совпадает между ключом словаря,
   `BodyPosition.name` / `AnglePosition.name` и всеми ссылками на эту точку.
2. Для рассматриваемых точек канонические имена — `true_node`, `south_node`,
   `mean_apog`, `pars_fortune`.
3. `AspectPointRef`, точки и вложенные рёбра конфигураций, `NatalPointRef` в
   транзитах и Research v1 используют эти имена без alias-преобразований.
4. `north_node`, `lilith`, `pars` относятся только к presentation-слою. Они
   не являются compatibility aliases публичного машинного контракта.
5. Решение заменяет только фрагмент ADR-0023 о направлении канонизации имён;
   схема, privacy/consent, retention и остальные решения ADR-0023 остаются в
   силе.
6. Сериализованный расчётный результат меняется, поэтому
   `CalculationVersion` обязан измениться. Отдельная
   `artifact_schema_version` не вводится, а `feature_schema_version` Research
   v1 не меняется: Research и до этой правки сохранял канонические значения.
7. Вне области решения остаются:
   - исключение гарантированной оппозиции двух узлов;
   - устранение зеркальных аспектов к двум концам оси;
   - доказательство соответствия вложенных аспектов конфигурации основному
     списку;
   - семантика аспектов при неизвестном времени рождения.

После принятия ADR синхронизируй актуальные responsibilities и архитектурные
диаграммы. Старый ADR-0023 не переписывай под вид, будто новое решение
действовало изначально.

## Изменение расчётного namespace

### 1. AspectConfig и орбисы

В `src/exact_orb/engine/aspects/types.py`:

- удали `AspectConfig.point_aliases` и `DEFAULT_POINT_ALIASES` из расчётной
  модели;
- сделай удалённое поле явно недопустимым, чтобы
  `AspectConfig(point_aliases=...)` не принимался и не игнорировался молча;
- оставь `natal_points` в прежнем порядке и с прежним составом, но используй
  только канонические идентификаторы;
- замени ключи стандартных `body_orbs`:

  ```text
  north_node -> true_node
  lilith     -> mean_apog
  pars       -> pars_fortune
  ```

- так же канонизируй ключи `aspect_body_overrides` для Парса;
- не меняй значения орбисов, порядок аспектов, категории и правила выбора
  минимального ограничения.

Не добавляй новую общую таблицу alias-ов в `engine`, `calculation` или
`research`. Человекочитаемые отображения принадлежат presentation-слою.

### 2. Натальные аспекты и конфигурации

В `src/exact_orb/engine/charts/natal.py`:

- `_natal_aspect_points()` должен выпускать имя, под которым точка реально
  хранится в `bodies` или `angles`, без преобразования;
- `_configuration_config_with_signs()` должен индексировать знаки теми же
  каноническими именами;
- не меняй список точек, участвующих в аспектной сетке;
- не меняй геометрию, сортировку, орбисы, категории и `applying=None`.

В `ConfigurationConfig.points` замени только псевдонимы на канонические имена.
Оба узла и лунный апогей пока остаются независимыми допустимыми участниками
конфигураций — их предметная нормализация будет отдельной задачей.

### 3. Транзиты

В `src/exact_orb/engine/charts/transit.py` убери повторную alias-канонизацию.
Натальные цели `TransitAspect.to` и `StationAspect.to` должны получать ровно
каноническое имя из исходной натальной карты.

Состав транзитов, поиск exact dates, applying-семантику, станции, числовые
значения и сортировку не меняй.

### 4. Сквозной инвариант ссылочной целостности

Добавь на ответственном слое результата карты cross-object validation для
`NatalChart`. Не пытайся валидировать существование точки внутри отдельного
`AspectPointRef`: у него самого нет доступа к карте.

Инвариант должен проверять единый namespace:

- ключ каждой использованной записи `bodies` / `angles`, её поле `name` и
  пара `(chart, body)` у ссылки согласованы;
- каждый конец каждого элемента `NatalChart.aspects` разрешается в
  `NatalChart.bodies` или `NatalChart.angles`;
- то же верно для `Configuration.points` и обоих концов каждого элемента
  `Configuration.aspects`;
- проверка рекурсивно охватывает `Configuration.contains`;
- если соответствующий блок `None`, это сохраняет значение «не вычислялся»;
  ссылки при отсутствии адресуемого `bodies`/`angles` должны отклоняться, а
  не пропускаться;
- ошибка должна быть детерминированной и указывать тип/путь неразрешимой
  ссылки, не подменяя проблему `KeyError` или сырым runtime-исключением.

`ArtifactNatalChart` наследует контракт `NatalChart`, поэтому сериализация и
decode кэшированного артефакта не должны допускать старый рассинхронизированный
payload. Не дублируй независимую таблицу допустимых имён в кодеке.

На этом этапе проверяется только разрешимость ссылок. Не добавляй ещё
инварианты из следующей задачи:

- полное равенство вложенного аспекта элементу `NatalChart.aspects`;
- соответствие `Configuration.points` концам рёбер;
- пересчёт `max_orb`;
- обязательное наличие `aspects` при запросе `configurations`.

### 5. Research projection

`research.models` уже использует канонические enum-значения. Упрости
`project_chart_features()` так, чтобы он переносил идентификаторы аспектов и
конфигураций напрямую и больше не исправлял чужой namespace.

- удали `_ALIAS_TO_RAW_POINT`, `_raw_point` и зависимые ветви;
- не принимай aliased artifact как второй корректный вариант;
- сохрани whitelist-проекцию, безопасное отображение `ValidationError`,
  frozen-модели, порядок данных и все privacy-инварианты;
- не меняй `feature_schema_version`, digest format или существующие
  канонические значения Research v1;
- не добавляй миграцию Research records: их persisted vocabulary уже
  канонический.

### 6. CalculationVersion

Изменение затрагивает контракт результата и стандартные профили. Увеличь
ручной `ENGINE_VERSION` согласно его собственному контракту. Учти, что
`profiles_digest` также естественно изменится после обновления
`AspectConfig.natal()`, `AspectConfig.transit()` и `ConfigurationConfig()`.

Обнови тесты `CalculationVersion`, которые ошибочно предполагают конкретное
текущее значение `ENGINE_VERSION`; тест изменения компоненты должен сравнивать
два действительно разных значения и не превращаться в тавтологию.

Не меняй `CALCULATION_VERSION_SCHEMA`, формат `calculation_key` и модель
`ChartSpec`. Не вводи `artifact_schema_version`.

## Требования к тестам

Сначала сопоставь новый контракт с существующим покрытием. Не добавляй
дублирующие сценарии. Обновление ожидаемых машинных имён является изменением
контракта, но не основанием менять эталонные числа.

Обязательное доказательство:

1. Референсный натал выдаёт тот же набор аспектов, те же орбисы, категории и
   порядок после единственного преобразования имён:

   ```text
   north_node -> true_node
   lilith     -> mean_apog
   pars       -> pars_fortune
   ```

2. Гарантированная оппозиция `true_node–south_node` и зеркальные аспекты к
   узлам пока остаются. Не удаляй и не переоценивай их в expected data.
3. Референсные конфигурации сохраняют количество, типы, роли, рёбра,
   `max_orb` и категории; меняются только три идентификатора.
4. Транзитные и station-ссылки на натальные точки канонические, а их орбисы и
   даты не изменились.
5. Стандартные натальный и транзитный профили используют прежние числовые
   орбисы под каноническими ключами.
6. `AspectConfig(point_aliases=...)` отклоняется, а не создаёт второй вариант
   результата и не игнорируется молча.
7. Позитивный тест доказывает, что ссылки на тело и угол разрешаются.
8. Негативные regression-тесты отклоняют неразрешимый `AspectPointRef`:
   - в `NatalChart.aspects`;
   - в `Configuration.points`;
   - во вложенном `Configuration.aspects`;
   - в рекурсивном `Configuration.contains`.
9. Artifact encode/decode сохраняет канонические ссылки; payload с
   неразрешимой ссылкой не принимается как валидный `ChartArtifact`.
10. Research rich-artifact и drift-тесты больше не строят допустимый aliased
    вариант. Они прямо доказывают совпадение engine vocabulary с Research
    vocabulary без таблицы обратного отображения.
11. В сериализованном расчётном результате `north_node`, `lilith` и машинное
    поле со значением `pars` отсутствуют. Проверка должна быть структурной, а
    не хрупким поиском подстроки в произвольном человекочитаемом тексте.

Не подгоняй golden-числа и допуски под реализацию. Если после переименования
изменилось что-либо кроме идентификаторов и зависящей от них детерминированной
сортировки, остановись и найди причину.

## Актуальная документация

Синхронизируй только документы, которые описывают действующий контракт:

- `exact-orb_calculation_requirements.md`: канонические ключи орбисов,
  Т-АСП-8/9, ссылочный инвариант и влияние на `CalculationVersion`;
- `exact-orb_research_corpus.md`: единый vocabulary и прямая проекция без
  aliases;
- `exact_orb_class_diagram.puml`: отсутствие `point_aliases` в
  `AspectConfig` и, если отражается модель, новый инвариант;
- `exact_orb_research_component.puml`: удалить обратное преобразование
  alias → raw;
- новый ADR-0029 и индекс ADR.

ADR-0023 оставь исторически правдивым. В новом ADR и индексе явно укажи, какая
узкая часть его решения заменена.

Файл
`docs/development_approach/problems_detected_by_human/002_full-build-natal-log-exposed-aspect-model-defects.md`
не помечай «устранено»: он включает ещё не выполненные этапы про ось узлов,
конфигурации и неизвестное время. Не переписывай первоначальное наблюдение.

## Жёсткие ограничения

- Не удаляй `south_node` из `NatalChart.bodies` или аспектной сетки.
- Не удаляй аспект `true_node opposition south_node`.
- Не устраняй зеркальные аспекты к двум узлам и не меняй вес оси.
- Не меняй алгоритм или состав конфигураций, кроме переименования участников.
- Не меняй `normalize_include` и зависимость `configurations` от `aspects`.
- Не меняй поведение при `time_unknown=true` и аспекты Луны.
- Не меняй `applying=None`.
- Не удаляй `house_system="P"` у космограммы.
- Не удаляй `artifact.spec` или `delta.base_chart_spec` и не ослабляй их
  сквозное равенство.
- Не вводи `artifact_schema_version`, compatibility aliases, migration layer,
  новые зависимости, сервисы или сетевые вызовы.
- Не меняй полное DEBUG-логирование component boundaries.
- Не редактируй исторические `prompts/**`.
- Не выполняй попутный рефакторинг.
- Не запускай платные или сетевые smoke-тесты.
- Не создавай коммит, push или PR.

## Проверки

Запускай поэтапно и сообщай только фактические результаты.

### 1. Целевые

```text
pytest tests/test_aspects.py tests/test_configurations.py tests/test_transits.py -q
pytest tests/research/test_projection.py tests/research/test_models.py -q
pytest tests/test_chart_artifact_codec.py tests/test_calculation_version.py -q
```

### 2. Связанные

```text
pytest tests/test_ephemeris.py tests/test_natal_include_gating.py tests/test_calculation_engine.py tests/test_calculation_block_integration.py -q
pytest tests/application/test_build_natal_integration.py -q
pytest tests/test_module_boundaries.py -q
```

### 3. Полный набор

```text
pytest -q
git diff --check
```

### 4. Статические проверки

Через `rg` классифицируй все оставшиеся актуальные употребления:

```text
point_aliases
DEFAULT_POINT_ALIASES
north_node
lilith
pars
```

В production-коде расчёта, артефактах и Research projection не должно
остаться alias-механизма. Допустимы presentation labels, русский предметный
термин «Лилит», новый ADR как описание заменённого решения, исторический файл
проблемы и тесты, которые доказывают отклонение старых машинных имён.

Если доступен локальный PlantUML, проверь изменённые `.puml`. Новые зависимости
для этого не скачивай.

В конце просмотри полный `git diff` и `git status --short`, чтобы не включить
несвязанные пользовательские файлы.

## Итоговый отчёт

Начни с результата, затем кратко укажи:

- корневую причину и принятое решение о едином namespace;
- созданный ADR-0029 и точную часть ADR-0023, которую он заменяет;
- фактически изменённые production-файлы, тесты и актуальные документы;
- подтверждение, что `bodies`, аспекты, конфигурации, транзиты и Research
  используют `true_node`, `south_node`, `mean_apog`, `pars_fortune`;
- где именно расположен cross-object validator и какие уровни ссылок он
  проверяет;
- подтверждение, что состав и числа аспектов/конфигураций не менялись;
- новое значение `ENGINE_VERSION` и почему отдельные schema versions не
  вводились;
- точные команды тестов и реальные результаты;
- результат `git diff --check`, статического поиска и PlantUML-проверки;
- что не проверялось, оставшиеся ограничения и три следующих незакрытых
  этапа: ось узлов, целостность конфигураций, неизвестное время.
