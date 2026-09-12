# Единая система достоинств и диспозиторов

Работай только в ветке `fix/aspect-model-semantics`. Перед изменениями
убедись, что это текущая ветка; если нет — остановись.

Не создавай новую ветку, коммит, push или PR. Сохрани все пользовательские и
несвязанные изменения рабочего дерева.

`prompts/**` — исторический журнал. Не изменяй этот или другие сохранённые
промты.

## Исходное состояние

В `StrengthConfig` по умолчанию выбрана система достоинств `modern`.
`evaluate_dignity()` честно использует её: например, Плутон в Скорпионе
получает `domicile` и score 5.

Цепочки диспозиторов при этом всегда строятся по локальной традиционной
таблице `TRADITIONAL_SIGN_RULERS`, потому что `_calculate_strength()` вызывает
`calculate_dispositor_chains()` без явной системы. Из-за этого один блок
`NatalStrength` описывает достоинства и диспозицию несовместимыми методами.

Для контрольной карты из полного DEBUG-payload это меняет интерпретационную
структуру:

- при традиционной диспозиции Плутон в Скорпионе идёт через Марс, хотя в
  соседнем dignity-блоке объявлен находящимся в собственной обители;
- Луна в Водолее идёт к Сатурну вместо Урана;
- при современной диспозиции Плутон становится финальным диспозитором, а
  цепочка Луны проходит через Уран;
- меняются циклы, `steps_to_cycle`, взаимные рецепции и множество финальных
  диспозиторов.

Пробел уже честно записан в Т-НАТ-11: диспозиторы используют традиционную
таблицу независимо от выбранной системы. Это изменение закрывает именно этот
пробел.

`ChartSpec.rulership="combined"` относится к управителям домов и
интерцептированных знаков. Он возвращает несколько управителей знака и не
может без новой графовой модели использоваться линейной `DispositorChain`.

## Цель и принятое решение

Сделать `StrengthConfig.dignity_system` единственным настраиваемым источником
системы для двух связанных частей `NatalStrength`:

1. эссенциальных достоинств;
2. цепочек диспозиторов и взаимных рецепций.

После изменения:

- `dignity_system="traditional"` использует традиционные достоинства и
  традиционных единственных управителей знаков;
- `dignity_system="modern"` использует современные достоинства и современных
  единственных управителей: Скорпион → Плутон, Водолей → Уран, Рыбы → Нептун;
- production-путь никогда не выбирает таблицу диспозиторов неявным default;
- `NatalStrength` явно сообщает `dispositor_system`, и это значение обязано
  совпадать с `dignity_system`;
- `ChartSpec.rulership` продолжает управлять только `house_rulers` и
  `Interception.rulers`;
- `combined` не становится допустимой системой линейных диспозиторов;
- числовые достоинства, их приоритет, баллы силы, положения, аспекты,
  конфигурации и правила домов вне сменившихся цепочек не меняются.

Это одно предметное изменение. Выполни его одним атомарным diff вместе с ADR,
тестами и актуальной документацией. Не включай в работу `pars_fortune–asc`,
аспекты углов, интерцепционные проекции или `degree_flags`.

## Перед изменениями

1. Выполни:

   ```text
   git branch --show-current
   git status --short
   git log -6 --oneline
   ```

2. Убедись, что текущая ветка — `fix/aspect-model-semantics`, а исходный
   `ENGINE_VERSION` равен `"4"`.

3. Зафиксируй исходный результат ближайших тестов:

   ```text
   pytest tests/test_strength.py -q
   pytest tests/test_calculation_version.py tests/test_chart_artifact_codec.py -q
   pytest tests/research/test_projection.py tests/research/test_models.py -q
   pytest tests/test_cli_render.py tests/test_natal_include_gating.py -q
   ```

   Если baseline красный, зафиксируй точные падения и не исправляй несвязанные
   дефекты.

4. Изучи актуальные источники истины:

   - `AGENTS.md`;
   - Т-НАТ-9…Т-НАТ-11 и Т-СИЛ-1…Т-СИЛ-11 в
     `exact-orb_calculation_requirements.md`;
   - раздел CalculationVersion в тех же requirements;
   - `docs/requirements/decisions/README.md` и действующие ADR о
     `CalculationVersion`, артефактах и Research;
   - `src/exact_orb/domain.py`;
   - `src/exact_orb/engine/ephemeris/types.py` и `calc.py`;
   - `src/exact_orb/engine/strength/types.py`, `dignities.py` и
     `dispositors.py`;
   - `_calculate_strength()` в `src/exact_orb/engine/charts/natal.py`;
   - `src/exact_orb/calculation/version.py` и `src/exact_orb/engine/__init__.py`;
   - `src/exact_orb/research/projection.py` и модель Research;
   - ближайшие тесты, fixtures и golden-данные.

5. Через `rg` найди все актуальные употребления:

   ```text
   dignity_system
   dispositors
   mutual_receptions
   TRADITIONAL_SIGN_RULERS
   TRADITIONAL_RULERS
   MODERN_RULERS
   COMBINED_RULERS
   calculate_dispositor_chains
   ENGINE_VERSION
   profiles_digest
   ```

Не выполняй глобальную замену: historical ADR/prompts и независимый
`ChartSpec.rulership` должны остаться исторически и предметно корректными.

## 1. ADR-0033

Создай
`docs/requirements/decisions/0033-unified-dignity-and-dispositor-system.md` и
добавь его в `docs/requirements/decisions/README.md`.

ADR должен зафиксировать:

1. Достоинства и диспозиторы внутри `NatalStrength` являются одной
   интерпретационной методикой и используют одно значение
   `StrengthConfig.dignity_system`.
2. Поддерживаются только `traditional` и `modern`.
3. В modern-системе первичные управители Скорпиона, Водолея и Рыб — Плутон,
   Уран и Нептун соответственно; в traditional сохраняются Марс, Сатурн и
   Юпитер.
4. `combined` описывает множественных управителей домов и интерцепций, но не
   линейную диспозицию. Построение ветвящегося dispositor graph не входит в
   текущий продукт.
5. `ChartSpec.rulership` и `StrengthConfig.dignity_system` остаются разными
   параметрами разных bounded context. Это допустимо только потому, что их
   область действия явно названа и они больше не смешиваются внутри
   `NatalStrength`.
6. `NatalStrength.dispositor_system` — обязательная явная метка применённой
   системы, равная `dignity_system`; отдельного пользовательского выбора для
   неё нет.
7. Текущая форма `DispositorChain` сохраняется. Не меняются правила
   представления пути, повторения точки замыкания, `steps_to_cycle`, `cycle`
   и `MutualReception`.
8. Меняется расчётная методика и сериализованный `NatalStrength`, поэтому
   существующий `ENGINE_VERSION` увеличивается с `"4"` до `"5"`.
   `StrengthConfig` не меняется, поэтому `profiles_digest` обязан остаться
   прежним.
9. `CALCULATION_VERSION_SCHEMA`, calculation-key schema, `ChartSpec`,
   Research schema и отдельная `artifact_schema_version` не меняются.
10. Старые cache entries становятся недостижимы через новый
    `CalculationVersion`; несовместимый найденный payload по-прежнему
    отклоняется существующей строгой artifact-валидацией.

Разбери и отклони минимум три альтернативы:

- оставить достоинства modern, а диспозиторы традиционными без явной метки;
- использовать `ChartSpec.rulership="combined"` как линейную таблицу, выбирая
  одного из двух управителей скрытым приоритетом;
- добавить независимо настраиваемый `dispositor_system`, позволяющий снова
  создать противоречащие системы в одном `NatalStrength`.

## 2. Один источник таблиц управителей

Удали локальную дублирующую таблицу `TRADITIONAL_SIGN_RULERS` из
`strength/dispositors.py`.

Переиспользуй действующие чистые таблицы `TRADITIONAL_RULERS` и
`MODERN_RULERS`. Не копируй их в strength-модуль и не заводи третью таблицу с
теми же двенадцатью значениями.

Для линейной диспозиции каждая запись выбранной standard-таблицы обязана
содержать ровно одного управителя. Нарушение этого условия должно давать
детерминированный `ValueError`; не выбирай первый элемент многозначной записи
молча. Поэтому `COMBINED_RULERS` не проходит эту границу.

Сохрани низкоуровневую возможность передать произвольный `ruler_map` для
синтетических тестов циклов. При этом убери неявный production-default:

- стандартный вызов обязан явно выбрать `system="traditional"` или
  `system="modern"`;
- синтетический вызов обязан явно передать `ruler_map`;
- вызов без обоих источников отклоняется;
- одновременная передача standard system и custom map отклоняется как
  неоднозначная;
- неизвестная система и `combined` отклоняются до построения цепочек.

Точное оформление сигнатуры выбери по стилю проекта, но приведённые
инварианты обязательны. Не связывай универсальный алгоритм цепочек с
`NatalChart`, `ChartSpec` или CLI.

## 3. Production-путь силы

В `_calculate_strength()` передавай
`system=config.dignity_system` в расчёт диспозиторов явно.

Добавь в `NatalStrength` обязательное поле без default:

```text
dispositor_system: Literal["traditional", "modern"]
```

Агрегат обязан валидировать:

```text
dispositor_system == dignity_system
```

Ошибка должна явно называть оба поля. Не добавляй compatibility alias,
необязательный `None` или отдельную настройку, позволяющую системам
расходиться.

При сборке `NatalStrength` записывай в оба поля одно значение
`config.dignity_system`.

Не меняй:

- таблицы и приоритет достоинств: обитель → экзальтация → изгнание → падение
  → перегрин;
- правило одного статуса и отсутствие сложения обители с экзальтацией;
- dignity scores и расчёт общей силы;
- balance, accidental strength, lunar phase, degree flags и interceptions;
- `ChartSpec.rulership`, `rulers_for_sign()`, `house_rulers` и
  `Interception.rulers`;
- форму, сортировку и cycle semantics `DispositorChain`.

## 4. Artifact, cache, Research и presentation

`NatalStrength.dispositor_system` входит в сериализованный chart/artifact.
Обнови строгие fixtures и normalized artifact baseline только после
структурных проверок.

Увеличь:

```text
ENGINE_VERSION  "4" → "5"
```

Не меняй `StrengthConfig`, поэтому канонический strength profile и
`profiles_digest` должны остаться прежними. Regression-тест должен сравнить
до- и послеизменённый `CalculationVersionRecord` по существенным полям:

- изменился только `engine_version`;
- `profiles_digest` тот же;
- итоговый `calculation_version` другой.

Не меняй key schema/prefix, `CALCULATION_VERSION_SCHEMA`, `ChartSpec`, state
payload version, Research versions или digest format. Не вводи
`artifact_schema_version`.

Research v1 сейчас не проецирует диспозиторные цепочки. Сохрани это поведение:

- добавь `dispositor_system` в строгие source fixtures `NatalStrength`;
- проекция достоинств продолжает использовать `dignity_system`;
- не добавляй dispositor features и не увеличивай `FEATURE_SCHEMA_VERSION`;
- за различение результатов новой методики отвечает
  `ResearchRecord.calculation_version`.

CLI и другие presentation-слои не должны самостоятельно пересчитывать или
переименовывать цепочки. Обновляй human golden только если реально выводимые
цепочки или взаимные рецепции изменились; не печатай новое поле отдельной
строкой без существующей пользовательской потребности.

## 5. Обязательные regression-тесты

Расширяй ближайшие существующие тесты, прежде всего `tests/test_strength.py`.
Не создавай параллельный набор одинаковых fixtures.

Обязательное доказательство:

1. В standard traditional-режиме диспозиторы используют Марс для Скорпиона,
   Сатурн для Водолея и Юпитер для Рыб.
2. В standard modern-режиме используются Плутон, Уран и Нептун.
3. Production `_calculate_strength()` явно следует
   `StrengthConfig.dignity_system`; тест должен падать при возврате к
   неявному traditional-default.
4. Плутон в Скорпионе при `modern` одновременно имеет `domicile` и является
   собственным финальным диспозитором. Проверяй действующую форму
   `chain/cycle/steps_to_cycle`, не переписывая DTO ради более короткого
   отображения.
5. Для контрольной карты 1990-09-02 14:30 Europe/Moscow современная цепочка
   Луны проходит через Уран, а традиционная — непосредственно через Сатурн.
   В том же сценарии проверь изменение множества финальных диспозиторов,
   включая Плутон как modern-позитивный контроль.
6. `dignity_system="traditional"` сохраняет прежние традиционные цепочки и
   взаимные рецепции как обратный контроль.
7. `NatalStrength` отклоняет несовпадающие `dignity_system` и
   `dispositor_system`.
8. `calculate_dispositor_chains()` отклоняет отсутствие явного источника,
   одновременные `system+ruler_map`, неизвестную систему и `combined`.
9. Custom `ruler_map` по-прежнему доказывает циклы длины 1, 2 и 3 и
   завершение цепочки при отсутствующей точке.
10. При `ChartSpec.rulership="combined"` и modern strength управители домов
    остаются combined, а диспозиторы — modern. Этот тест доказывает две явные
    области ответственности, а не случайное совпадение результата.
11. Помимо цепочек, циклов, взаимных рецепций и нового metadata-поля, у
    референсной карты не меняются bodies, houses, аспекты, конфигурации,
    dignity statuses/scores, accidental scores, total/category и balance.
12. Artifact codec round-trip сохраняет `dispositor_system`; payload без
    обязательного поля или с несовпадающими системами отклоняется validation
    path.
13. Новый `CalculationVersion` отличается старым `engine_version`, при
    неизменном `profiles_digest`.
14. Research projection после обновления source fixture остаётся той же
    формы и не выпускает dispositor features.

Не вычисляй ожидаемые цепочки копированием текущего противоречивого output.
Сначала зафиксируй выбранную таблицу знаков, затем вручную проверь несколько
коротких цепочек и используй property-тесты для конечности и циклов.

## 6. Актуальная документация

Синхронизируй только документы, описывающие изменившийся контракт:

- новый ADR-0033 и индекс ADR;
- `exact-orb_calculation_requirements.md`:
  - заменить пробел Т-НАТ-11 действующим единым правилом;
  - уточнить Т-СИЛ-3 и Т-СИЛ-7;
  - явно развести strength system и `ChartSpec.rulership` домов;
  - отразить version impact;
- `exact-orb_research_corpus.md` — только чтобы явно сказать, что Research v1
  проецирует достоинства, но не диспозиторные цепочки, и различает методику по
  `calculation_version`;
- `docs/architecture/exact_orb_class_diagram.puml` — добавить фактическое
  поле `NatalStrength.dispositor_system` и показать единый источник
  `StrengthConfig.dignity_system` для достоинств и диспозиторов.

Не обновляй sequence diagrams: порядок component-вызовов не меняется. Не
редактируй finding `002` — он относится к аспектной модели, а не к системе
диспозиторов.

Если доступен локальный PlantUML, проверь изменённую class diagram. Новые
зависимости и Java не скачивай; `.png` вручную не редактируй.

## Жёсткие ограничения

- Не используй `ChartSpec.rulership="combined"` для линейной цепочки.
- Не выбирай первого combined-управителя скрытой эвристикой.
- Не оставляй traditional default в production-пути.
- Не добавляй независимо выбираемый `dispositor_system` в `StrengthConfig`
  или `ChartSpec`.
- Не дублируй таблицы управителей между ephemeris и strength.
- Не меняй dignity tables, scores, приоритет одного статуса или суммирование
  достоинств.
- Не меняй форму и семантику `DispositorChain` и `MutualReception`.
- Не меняй house rulers, интерцепции, аспекты, конфигурации, космограммную
  устойчивость, `applying=None` или ось узлов.
- Не исправляй `pars_fortune–asc`, angle-angle аспекты или `degree_flags`.
- Не меняй `ChartSpec`, calculation-key schema, state payload, Research
  schema и `profiles_digest`.
- Не вводи новый механизм версионирования: используй существующий
  `ENGINE_VERSION` внутри `CalculationVersion`.
- Не добавляй `artifact_schema_version`, compatibility aliases, migrations,
  зависимости, сервисы или сетевые вызовы.
- Не меняй полное DEBUG-логирование component boundaries.
- Не редактируй исторические `prompts/**` и старые ADR.
- Не запускай платные или сетевые smoke-тесты.
- Не выполняй попутный рефакторинг.
- Не создавай коммит, push или PR.

## Проверки

Запускай поэтапно и сообщай только фактические результаты.

### Целевые

```text
pytest tests/test_strength.py -q
pytest tests/test_chart_artifact_codec.py tests/test_calculation_version.py -q
pytest tests/research/test_projection.py tests/research/test_models.py -q
pytest tests/test_cli_render.py tests/test_natal_include_gating.py -q
```

### Связанные

```text
pytest tests/test_calculation_engine.py tests/test_calculation_engine_integration.py -q
pytest tests/test_calculation_block_integration.py -q
pytest tests/application/test_build_natal_integration.py -q
pytest tests/test_module_boundaries.py -q
```

### Полный набор

```text
pytest -q
git diff --check
```

### Статические проверки

Через `rg` проверь:

- локальная `TRADITIONAL_SIGN_RULERS` удалена;
- standard production-вызов диспозиторов всегда передаёт систему явно;
- `combined` не попадает в dispositor path;
- `dispositor_system` присутствует во всех актуальных `NatalStrength`
  fixtures и совпадает с `dignity_system`;
- `ENGINE_VERSION="5"`;
- `profiles_digest`, key schema, `CALCULATION_VERSION_SCHEMA`, state и
  Research versions не менялись;
- `ChartSpec.rulership` по-прежнему используется только управителями домов и
  интерцепций.

Просмотри полный `git diff` и `git status --short`. Не включай несвязанные
пользовательские файлы.

## Итоговый отчёт

Начни с результата, затем кратко укажи:

- корневую причину скрытого traditional-default;
- принятое единое правило `StrengthConfig.dignity_system`;
- различие modern/traditional для Скорпиона, Водолея и Рыб;
- почему `combined` сохранён только для домов и не применяется к цепочке;
- как исключено дублирование таблиц управителей;
- изменившиеся цепочки, циклы, взаимные рецепции и финальные диспозиторы в
  контрольных картах;
- подтверждение неизменности остальных strength/chart значений;
- новый обязательный `NatalStrength.dispositor_system` и его инвариант;
- изменение `ENGINE_VERSION` и неизменность `profiles_digest` и остальных
  schema versions;
- фактически изменённые production-файлы, тесты, fixtures, golden и
  документы;
- точные команды и реальные результаты целевых, связанных и полного pytest;
- результат `git diff --check`, статического поиска и PlantUML-проверки;
- что не проверялось;
- оставшиеся отдельные работы: `pars_fortune–asc`, политика angle-angle,
  интерцепционные проекции и `degree_flags`.
