# Промт: адаптер NatalTool (LocalTool для get_natal)

**Контекст.**

Проект `exact-orb`. Есть работающий расчётный слой:
`src/exact_orb/engine/charts/natal.py` экспортирует

```python
def get_natal(
    birth_datetime: datetime,
    latitude: float,
    longitude: float,
    *,
    house_system: str | bytes = b"P",
    body_ids: Mapping[str, int] | None = None,
    ephemeris_flags: int = swe.FLG_SWIEPH,
    rulership: RulershipScheme | str = RulershipScheme.COMBINED,
    near_interception_threshold: float = 1.0,
    ephemeris_path: str | None = None,
    selena_method: str | None = None,
    include: AbstractSet[str] | None = None,
    aspect_config: AspectConfig | None = None,
    configuration_config: ConfigurationConfig | None = None,
    strength_config: StrengthConfig | None = None,
) -> NatalChart
```

`birth_datetime` обязан быть timezone-aware — `to_utc()` внутри падает,
если это не так. `house_system` принимает и `str`, и `bytes`
(`normalize_house_system` сама кодирует строку в ASCII-байт), так что на
границе тула незачем требовать `bytes`. `rulership` — строковый `Enum`
(`RulershipScheme`), значения `"combined" | "modern" | "traditional"`, и
конструктор enum принимает как сам enum, так и обычную строку.

Есть также готовый порт tool'ов:

```python
# tools/base.py
class Tool(ABC):
    name: str
    @abstractmethod
    def run(self, request: ToolRequest) -> ToolResult: ...

# tools/types.py
class ToolRequest(BaseModel):
    tool_name: str
    args: dict[str, Any] = Field(default_factory=dict)

class ToolResult(BaseModel):
    tool_name: str
    data: dict[str, Any]
    warnings: list[str] = Field(default_factory=list)
    meta: dict[str, Any] = Field(default_factory=dict)
```

`NatalChart.warnings` — это `tuple[CalculationWarning, ...]`, а
`CalculationWarning` (в `engine/ephemeris/types.py`) — это
`{source: str, message: str, retflags: int | None}`.

`include` реально влияет на форму результата — это не декоративный
параметр. В конце `get_natal()`:

```python
chart = NatalChart(
    ...
    bodies=bodies if "positions" in include_blocks else None,
    cusps=cusps if "houses" in include_blocks else None,
    angles=angles if "houses" in include_blocks else None,
    house_rulers=house_rulers if "rulers" in include_blocks else None,
    interceptions=interceptions if "rulers" in include_blocks else None,
    aspects=calculated_aspects if "aspects" in include_blocks else None,
    ...
)
```

Значит `include` без `"houses"` даёт `chart.cusps is None` и
`chart.angles is None` — это форма результата без домов, соответствующая
cosmogram-mode output. Это пока **временный proxy**, а не финальная
модель вида карты: явное поле `chart_kind` в движке остаётся отдельной
задачей (C1). `DEFAULT_INCLUDE = frozenset({"positions", "houses",
"rulers", "aspects", "configurations", "strength"})` — то есть
`include=None` (дефолт) уже включает дома, ничего специально включать
не нужно для обычного натала.

**Решения, которые уже приняты и не обсуждаются в рамках этого промта:**

- Аргументы тула валидируются отдельной типизированной pydantic-моделью
  (`NatalToolArgs`), а не принимаются как сырой `**request.args`. Это и
  контракт для будущего `Planner`, и место, где ловится часть ошибок до
  входа в движок.
- Строится только `LocalTool`-адаптер (вызов `get_natal()` в процессе).
  `RemoteTool` и маршрутизация через конфиг (`natal = local | http://...`
  из ADR-0002) в этом промте не реализуются вообще — ни кода, ни
  заглушек под них.
- `NatalToolArgs` покрывает **только** те входы, которые нужны сегодняшним
  сценариям: `birth_datetime`, `latitude`, `longitude`, `house_system`,
  `rulership`, `include`. Остальные параметры `get_natal()`
  (`aspect_config`, `configuration_config`, `strength_config`,
  `ephemeris_path`, `selena_method`, `body_ids`, `ephemeris_flags`,
  `near_interception_threshold`) остаются на дефолтах движка — не
  прокидывай их через `NatalToolArgs` «на всякий случай».

**Задача.**

Создать `src/exact_orb/tools/natal_tool.py`.

1. `NatalToolArgs(BaseModel)`:
   - `birth_datetime: datetime` — обязательное, без дефолта;
   - `latitude: float`, `longitude: float` — обязательные;
   - `house_system: str = "P"`;
   - `rulership: str = "combined"`;
   - `include: tuple[str, ...] | None = None`.
   - `@field_validator("birth_datetime")`, который требует
     `value.tzinfo is not None and value.utcoffset() is not None` и
     кидает `ValueError("birth_datetime must be timezone-aware")` иначе.
     Смысл: дать понятную ошибку на границе тула, а не смутный traceback
     из `to_utc()` внутри движка.

2. `NatalTool(Tool)`:
   - `name = "natal"`;
   - `run(self, request: ToolRequest) -> ToolResult`:
     1. если `request.tool_name != self.name`, кинуть
        `ValueError("expected tool_name 'natal', got '<actual>'")`;
     2. `args = NatalToolArgs.model_validate(request.args)`;
     3. вызвать `get_natal(birth_datetime=args.birth_datetime,
        latitude=args.latitude, longitude=args.longitude,
        house_system=args.house_system, rulership=args.rulership,
        include=set(args.include) if args.include is not None else None)`;
     4. вычислить `chart_kind = "natal" if chart.cusps is not None else
        "cosmogram"`;
     5. вернуть `ToolResult(tool_name=self.name,
        data=chart.model_dump(mode="json"),
        warnings=[f"{w.source}: {w.message}" for w in chart.warnings],
        meta={"chart_kind": chart_kind})`.

3. `chart_kind` определяется **по форме результата** — анализом
   `chart.cusps`, а не константой и не отдельным флагом в
   `NatalToolArgs`. Это временный proxy до C1, где движок начнёт
   возвращать явный `chart_kind`; здесь не добавлять такое поле в engine.

**Ограничения.**

- Не трогать `engine/charts/natal.py` и вообще ничего в `engine/`.
- Не создавать `RemoteTool`, не добавлять параметр маршрута/конфига ни в
  `NatalTool`, ни в `NatalToolArgs`.
- Не расширять `NatalToolArgs` сверх перечисленных полей.
- Не менять `tools/base.py`, `tools/types.py`.
- Не трогать сами значения `include_blocks` в движке — только
  использовать уже существующее поведение.

**Критерии приёмки.**

1. `from exact_orb.tools.natal_tool import NatalTool, NatalToolArgs`
   импортируется без ошибок.
2. `NatalToolArgs(birth_datetime=<naive datetime>, latitude=.., longitude=..)`
   кидает `pydantic.ValidationError` с текстом, содержащим
   `timezone-aware`.
3. `NatalToolArgs(birth_datetime=<aware>, latitude=.., longitude=..)` без
   остальных полей даёт `house_system == "P"`, `rulership == "combined"`,
   `include is None`.
4. `NatalTool().run(ToolRequest(tool_name="natal", args={...валидные, include не задан...}))`
   возвращает `ToolResult` с `tool_name == "natal"`,
   `meta == {"chart_kind": "natal"}`, `data` содержит ключи `"bodies"` и
   `"cusps"` (последний — не `None`).
5. `NatalTool().run(ToolRequest(tool_name="natal", args={"latitude": 55.75}))`
   (без `longitude`/`birth_datetime`) кидает `ValidationError`, а не
   падает где-то глубже с непонятной ошибкой.
6. `NatalTool().run(ToolRequest(tool_name="solar", args={...валидные...}))`
   кидает `ValueError` с текстом, содержащим `expected tool_name`.
7. `NatalTool().run(...)` с `include=("positions", "aspects",
   "configurations", "strength")` (без `"houses"`/`"rulers"`) возвращает
   `meta == {"chart_kind": "cosmogram"}` и `data["cusps"] is None`.

Тесты для этого файла — отдельный промт (см. `05-natal-tool-tests.md` в
этой же папке); здесь код должен просто им удовлетворять.
