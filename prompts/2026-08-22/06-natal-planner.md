# Промт: ResolvedContract и NatalPlanner

**Контекст.**

Сегодня `src/exact_orb/intent/types.py` содержит:

```python
class UserRequest(BaseModel):
    """A single-subject user request."""

    text: str
    subject: dict[str, Any] | None = None


class InterpretationPlan(BaseModel):
    """Planner output consumed by orchestration."""

    intent: str
    focus: str | None = None
    required_tools: list[ToolRequest]
    data_selectors: list[str]
    prompt_recipe: str
    missing_slots: list[str] = Field(default_factory=list)
    output_format: str = "prose"
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
```

а `src/exact_orb/intent/planner.py`:

```python
class Planner(ABC):
    """Build an interpretation plan from a user request."""

    @abstractmethod
    def plan(self, request: UserRequest) -> InterpretationPlan:
        """Return the interpretation plan for ``request``."""

        raise NotImplementedError
```

`UserRequest.subject` — временная заглушка (`dict[str, Any] | None`),
которую комментарий в коде прямо называет черновой: "It will be refined
when the concrete natal tool is designed." Реального `IntentService`,
который бы превращал `text: str` в структурированные данные, в проекте
нет — `Planner.plan()` сегодня никем не вызывается за пределами тестов
(`DummyPlanner` в `test_agent_skeleton.py`).

ADR-0005 описывает целевую границу между будущим `IntentService` и
`Planner`: `IntentService.understand(text, context)` возвращает типизированный
`ResolvedContract`, и это единственный вход, который видит `Planner.plan()`
— сырой текст пользователя (`И-1`) до `Planner` не доходит.
ADR-0005 явно называет и последствие для сигнатуры: `Planner.plan(request:
UserRequest)` меняется на `plan(contract: ResolvedContract)`.

ADR-0008 описывает сценарий космограммы: если время рождения неизвестно, а
место — известно, натальный расчёт делается тем же `get_natal()`, но с
`include`, из которого исключены `"houses"`/`"rulers"`. Место — всегда
жёсткий слот: если оно не определено, сценарий блокируется целиком,
независимо от того, известно ли время.

Уже реализовано и проверено (см. `02-natal-tool-adapter.md` в этой же
папке и сам `natal_tool.py`): `get_natal(..., include=...)` действительно
обнуляет `chart.cusps`/`.angles`/`.house_rulers`/`.interceptions`, когда в
`include` нет `"houses"`/`"rulers"` соответственно. Это значит, что
космограмма сегодня реализуема через `NatalTool` без единой правки в
`engine/` — только через то, какие аргументы `NatalPlanner` положит в
`ToolRequest`.

**Решения, которые уже приняты и не обсуждаются в рамках этого промта:**

- `ResolvedContract` — это именно тот контракт, который вернёт будущий
  `IntentService.understand()` (ADR-0005). Его форма:

  ```python
  class ResolvedContract(BaseModel):
      topics: list[str]
      latitude: float | None = None
      longitude: float | None = None
      utc: datetime | None = None
      time_known: bool = True
      time_assumed: bool = False
      focus: list[str] = Field(default_factory=list)
  ```

  Смысл полей:
  - `topics` — какие сценарии просит пользователь (`"natal"`, в будущем
    `"transit"`, `"synastry"`, ...). `NatalPlanner` умеет обработать
    только `"natal"`.
  - `latitude`/`longitude` — оба `None` одновременно, если место не
    определено; никогда не бывает так, что задан один без другого —
    это инвариант, который обеспечивает (в будущем) `IntentService`, а
    не эта модель. Это отдельный, независимый вопрос от `utc` (когда) —
    см. следующий пункт.
  - `utc` — заполняется, когда момент рождения установлен как
    абсолютный instant, **независимо от того, известно ли место**. Это
    два разных случая, которые различает `time_assumed`:
    - `time_assumed=False`: пользователь сам назвал время со смещением
      или часовым поясом (например «20:45 по UTC+4», «20:45 по
      Москве») — `IntentService` переводит это в UTC арифметикой,
      **координаты для этого не нужны вообще**, даже если место в
      запросе не названо.
    - `time_assumed=True`: место известно, а время — нет;
      `IntentService` предполагает полдень **по месту рождения** — вот
      здесь координаты как раз обязательны, потому что только через
      них можно узнать местный часовой пояс и перевести предполагаемый
      полдень в UTC.
    Если момент времени не установлен ни тем, ни другим способом —
    `utc` остаётся `None`. Прежняя формулировка («`utc is None` ровно
    когда `latitude`/`longitude` — `None`») была неточной: она связывала
    вопрос «когда» с вопросом «где», хотя первый случай выше показывает,
    что `utc` может быть известен и без места. Это исправлено здесь.
  - `time_known` — `False`, если пользователь вообще не называл время
    рождения (ни как точный момент, ни со смещением). `True` — дефолт:
    время названо явно, будь то с координатами или без них.
  - `time_assumed` — `True`, если `utc` не назван пользователем
    напрямую, а вычислен через допущение "полдень по месту рождения"
    (ровно случай `latitude`/`longitude` известны и `time_known is
    False`).
  - Важно: то, что `utc` может быть известен без места, **не отменяет**
    требования места для расчёта. `NatalPlanner` ниже проверяет
    `latitude`/`longitude` отдельно и независимо от `utc` — если места
    нет, слот `"place"` уходит в `missing_slots` в любом случае, даже
    когда `utc` уже точно известен. Причина не в том, что `get_natal()`
    использует место, чтобы понять «когда» — это уже сделано на уровне
    `IntentService` — а в том, что место нужно для домов/асцендента
    (когда они запрашиваются) и является жёстким слотом по ADR-0008
    независимо от сценария.
  - `focus` — тематические фокусы запроса (`"career"`, `"love"`, ...),
    отдельно от `topics` (тип сценария). Список, а не одна строка,
    потому что пользователь может попросить сразу несколько тем.
  - Никаких `field_validator`/`model_validator`, проверяющих
    согласованность полей (`lat`/`lon` заданы парой, `utc is None ==
    place is None`, и т.д.), в `ResolvedContract` в рамках этого промта
    не добавляется. Эти инварианты — ответственность будущего
    `IntentService`; `ResolvedContract` в этом промте — плоский
    контейнер данных, `Planner` доверяет тому, что получил.

- `ResolvedContract` добавляется в тот же файл, что `UserRequest` и
  `InterpretationPlan` — `intent/types.py`. Отдельного модуля под один
  класс не создаётся.

- `UserRequest` **не удаляется и не меняется**. `Orchestrator.handle()`
  и тест `test_agent_skeleton.py` продолжают использовать `UserRequest`
  как есть — переключение `Orchestrator` на `ResolvedContract` (и тем
  более реализация самого `IntentService`) в этот промт не входит, это
  отдельная будущая задача.

- Сигнатура `Planner.plan()` меняется: `request: UserRequest` →
  `contract: ResolvedContract`. Это ломает *типовую аннотацию*
  абстрактного метода, но не ломает существующий `DummyPlanner` в
  `test_agent_skeleton.py` — Python не проверяет соответствие сигнатур
  у `ABC` во время выполнения, `DummyPlanner.plan(self, request:
  UserRequest)` по-прежнему валидно переопределяет абстрактный метод.
  `test_agent_skeleton.py` в этом промте не трогается и должен
  продолжать проходить без изменений.

- Конкретный `NatalPlanner(Planner)` — новый файл
  `src/exact_orb/intent/natal_planner.py`, по аналогии с тем, как
  `NatalTool` живёт в `tools/natal_tool.py`, а не в `tools/base.py`.

- Если `"natal" not in contract.topics` — `NatalPlanner.plan()` кидает
  `NotImplementedError`. Других сценариев (`transit`, `synastry`,
  ...) в проекте пока нет вообще, реализовывать для них плейсхолдеры
  здесь не нужно.

- Если место не определено (`latitude is None or longitude is None`) —
  слот `"place"` всегда попадает в `missing_slots`; `"time"`
  добавляется туда же дополнительно, если `not contract.time_known`.
  Комбинация "место и время оба неизвестны" даёт `missing_slots ==
  ["place", "time"]` — обе метки сразу, порядок фиксирован
  (`place` раньше `time`).

- Строки `intent`/`prompt_recipe`/`data_selectors` фиксированы:
  `intent="natal_interpretation"` для обоих ветвящихся сценариев
  (натал и космограмма — это один и тот же интент с разным набором
  данных, не два разных интента); `prompt_recipe`/`data_selectors` —
  `"natal.general"` для натала, `"cosmogram.general"` для космограммы
  (без версионного суффикса вроде `.v1` — в отличие от значений,
  использованных в `DummyPlanner` из `test_agent_skeleton.py`, который
  остаётся как есть и не задаёт стандарт для реальных планеров).

**Задача.**

1. В `src/exact_orb/intent/types.py` добавить класс `ResolvedContract`
   ровно в форме, описанной выше (нужен импорт `datetime` из модуля
   `datetime`, уже используемого проектом в аналогичных местах).
   `UserRequest`, `InterpretationPlan` не менять.

2. В `src/exact_orb/intent/planner.py` изменить абстрактный метод:

   ```python
   from .types import InterpretationPlan, ResolvedContract

   class Planner(ABC):
       """Build an interpretation plan from a resolved contract."""

       @abstractmethod
       def plan(self, contract: ResolvedContract) -> InterpretationPlan:
           """Return the interpretation plan for ``contract``."""

           raise NotImplementedError
   ```

   (Импорт `UserRequest` в этом файле убрать, если после правки он
   становится неиспользуемым.)

3. Создать `src/exact_orb/intent/natal_planner.py`:

   ```python
   """Planner for natal-chart interpretation requests."""

   from __future__ import annotations

   from .planner import Planner
   from .types import InterpretationPlan, ResolvedContract
   from exact_orb.tools.types import ToolRequest

   _COSMOGRAM_INCLUDE = ("positions", "aspects", "configurations", "strength")


   class NatalPlanner(Planner):
       """Builds an ``InterpretationPlan`` for the ``"natal"`` topic.

       ADR-0008: when the birth place is known but the birth time is not,
       this falls back to a cosmogram recipe — the same ``natal`` tool,
       called with an ``include`` set that drops ``"houses"``/``"rulers"``
       so ``NatalTool`` reports ``chart_kind == "cosmogram"`` (see
       ``natal_tool.py``).
       """

       def plan(self, contract: ResolvedContract) -> InterpretationPlan:
           if "natal" not in contract.topics:
               raise NotImplementedError(
                   f"NatalPlanner cannot plan topics={contract.topics!r}"
               )

           focus = contract.focus[0] if contract.focus else None

           if contract.latitude is None or contract.longitude is None:
               missing_slots = ["place"]
               if not contract.time_known:
                   missing_slots.append("time")
               return InterpretationPlan(
                   intent="natal_interpretation",
                   focus=focus,
                   required_tools=[],
                   data_selectors=[],
                   prompt_recipe="natal.missing_slots",
                   missing_slots=missing_slots,
                   confidence=0.0,
               )

           if contract.time_known:
               return InterpretationPlan(
                   intent="natal_interpretation",
                   focus=focus,
                   required_tools=[
                       ToolRequest(
                           tool_name="natal",
                           args={
                               "birth_datetime": contract.utc,
                               "latitude": contract.latitude,
                               "longitude": contract.longitude,
                           },
                       )
                   ],
                   data_selectors=["natal.general"],
                   prompt_recipe="natal.general",
               )

           return InterpretationPlan(
               intent="natal_interpretation",
               focus=focus,
               required_tools=[
                   ToolRequest(
                       tool_name="natal",
                       args={
                           "birth_datetime": contract.utc,
                           "latitude": contract.latitude,
                           "longitude": contract.longitude,
                           "include": _COSMOGRAM_INCLUDE,
                       },
                   )
               ],
               data_selectors=["cosmogram.general"],
               prompt_recipe="cosmogram.general",
           )
   ```

4. В `src/exact_orb/intent/__init__.py` добавить экспорт `NatalPlanner`
   и `ResolvedContract`, сохранив алфавитный порядок `__all__`:

   ```python
   """Intent planning contracts."""

   from .natal_planner import NatalPlanner
   from .planner import Planner
   from .types import InterpretationPlan, ResolvedContract, UserRequest

   __all__ = [
       "InterpretationPlan",
       "NatalPlanner",
       "Planner",
       "ResolvedContract",
       "UserRequest",
   ]
   ```

**Ограничения.**

- Не трогать `orchestration/orchestrator.py`, `orchestration/types.py`.
  `Orchestrator.handle()` остаётся стабом, принимающим `UserRequest`, и
  `test_agent_skeleton.py` должен пройти без единой правки в нём самом.
- Не удалять и не переименовывать `UserRequest`.
- Не создавать `IntentService` и не реализовывать разбор сырого текста
  в `ResolvedContract` — это отдельная, ещё не начатая задача.
- Не добавлять `field_validator`/`model_validator` на `ResolvedContract`.
- Не реализовывать ветки для тем, отличных от `"natal"` — только
  `raise NotImplementedError` для них.
- Не менять `tools/natal_tool.py`, `tools/registry.py`,
  `tools/__init__.py` — эта задача только про `intent/`.
- Строки `"natal.missing_slots"`, `"natal.general"`,
  `"cosmogram.general"` не обязаны существовать как зарегистрированные
  рецепты в `PromptRegistry`/`DataSelector` — конкретные реализации
  этих сущностей не входят в этот промт.

**Критерии приёмки.**

1. `from exact_orb.intent import NatalPlanner, ResolvedContract` —
   импортируется без ошибок.
2. `ResolvedContract(topics=["natal"])` создаётся без ошибок и даёт
   `latitude is None`, `longitude is None`, `utc is None`,
   `time_known is True`, `time_assumed is False`, `focus == []`.
3. `ResolvedContract(topics=["natal"], utc=<aware datetime>,
   time_known=True)` без `latitude`/`longitude` создаётся без ошибок —
   `utc` не обязан быть `None`, даже когда место не задано (случай
   «время названо со смещением, место не названо»). `NatalPlanner`
   на таком контракте всё равно даёт `missing_slots == ["place"]`
   (не `["place", "time"]`, раз `time_known is True`) — см. п.4/5.
4. `NatalPlanner().plan(ResolvedContract(topics=[]))` (и
   `topics=["transit"]`) кидает `NotImplementedError`.
5. `NatalPlanner().plan(ResolvedContract(topics=["natal"]))` (место не
   задано, `time_known=True` по умолчанию) даёт `missing_slots ==
   ["place"]`, `required_tools == []`, `confidence == 0.0`.
6. То же самое с `time_known=False` даёт `missing_slots == ["place",
   "time"]`.
7. `NatalPlanner().plan(...)` с заданными `latitude`/`longitude`/`utc` и
   `time_known=True` даёт `prompt_recipe == "natal.general"`,
   `data_selectors == ["natal.general"]`, `missing_slots == []`,
   единственный элемент `required_tools` — `ToolRequest(tool_name="natal",
   args={"birth_datetime": <utc>, "latitude": <lat>, "longitude": <lon>})`
   без ключа `"include"`.
8. То же самое с `time_known=False` (место есть, `utc` — допущенный
   полдень) даёт `prompt_recipe == "cosmogram.general"`,
   `data_selectors == ["cosmogram.general"]`, `missing_slots == []`, а
   `required_tools[0].args["include"] == ("positions", "aspects",
   "configurations", "strength")`.
9. `ResolvedContract(topics=["natal"], focus=["career", "love"])` даёт
   `InterpretationPlan.focus == "career"`; `focus=[]` (дефолт) даёт
   `InterpretationPlan.focus is None`.
10. `python -m pytest tests/test_agent_skeleton.py -v` остаётся полностью
    зелёным без единой правки в этом файле.

Тесты для этого файла — отдельный промт (см.
`07-natal-planner-tests.md` в этой же папке); здесь код должен просто
им удовлетворять.
