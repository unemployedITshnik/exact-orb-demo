# Sequence diagrams — блок РЕЗОЛВ

Увеличение блока `BirthDataResolver → PlaceCatalog → resolve_historical_tz`
из диаграмм `../build_natal/`. Контракты и полный список сценариев —
`docs/requirements/component_responsibilities/exact-orb_birth_data_resolution.md`.

| Файл | Сценарий | Исход |
|---|---|---|
| `001-resolve_date_time_place.puml` | R-1 (3.1): дата, время, место | `ResolvedBirthData`, `time_unknown = false` |
| `002-resolve_date_place_time_unknown.puml` | R-2 (3.2): дата и место, время пусто | `ResolvedBirthData`, `time_unknown = true` |
| `003-resolve_place_not_found.puml` | R-6 (3.3): места нет в каталоге | `InputRequired { birth.place, INVALID }` |

Остальные семнадцать сценариев тест-пака описаны таблицами в §5 документа
и отдельных диаграмм не требуют: они отличаются исходом, а не структурой
взаимодействия.

## Рендер

```
java -jar plantuml.jar -tpng -o out *.puml
```
