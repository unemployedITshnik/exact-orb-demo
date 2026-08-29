#!/usr/bin/env python3
"""Собрать каталог мест из дампов GeoNames.

Источник: https://download.geonames.org/export/dump/
  cities1000.zip          — населённые пункты от 1000 жителей
  admin1CodesASCII.txt    — названия регионов первого уровня

Данные GeoNames распространяются по лицензии Creative Commons Attribution:
атрибуция обязательна, строка о ней должна быть видна пользователю продукта.
Действующую версию лицензии сверить на geonames.org перед релизом.

Формат выхода — JSONL, по объекту на строку:

    {"place_id": "524901", "name": "Москва", "name_ascii": "Moscow",
     "country": "RU", "admin1": "Moscow", "latitude": 55.75222,
     "longitude": 37.61556, "tz_id": "Europe/Moscow",
     "population": 10381222}

`place_id` — это geonameid. Он и есть идентификатор, который уходит
на клиент в подсказках и возвращается в BuildNatalCommand (ADR-0005).
Доверенным он не является: backend проверяет его по этому же каталогу.

Пример:

    python scripts/build_place_catalog.py \
        --cities data/cities1000.txt \
        --admin1 data/admin1CodesASCII.txt \
        --out data/places.jsonl \
        --full-countries RU,UA,BY,KZ,MD,AM,GE,AZ,KG,UZ,TJ,TM,LT,LV,EE \
        --other-min-population 100000
"""

from __future__ import annotations

import argparse
import json
import sys
import unicodedata
from pathlib import Path

# Порядок колонок дампа GeoNames, см. readme.txt в каталоге export/dump.
GEONAMEID = 0
NAME = 1
ASCIINAME = 2
ALTERNATENAMES = 3
LATITUDE = 4
LONGITUDE = 5
FEATURE_CLASS = 6
FEATURE_CODE = 7
COUNTRY_CODE = 8
ADMIN1_CODE = 10
POPULATION = 14
TIMEZONE = 17

# Только населённые пункты. Класс P исключает горы, реки и административные
# полигоны, которые местом рождения быть не могут.
POPULATED_PLACE_CLASS = "P"

# PPLX — район города, PPLH — исчезнувший населённый пункт, PPLW — затопленный.
# Первый порождает дубли в подсказках, два других не могут быть местом
# рождения живущего человека.
EXCLUDED_FEATURE_CODES = frozenset({"PPLX", "PPLH", "PPLW"})


def is_cyrillic(text: str) -> bool:
    """Строка содержит хотя бы одну кириллическую букву."""

    return any("CYRILLIC" in unicodedata.name(char, "") for char in text)


def pick_display_name(name: str, alternatenames: str) -> str:
    """Выбрать отображаемое имя, предпочитая кириллическое написание.

    Колонка `alternatenames` в дампах городов усечена и не размечена по языкам,
    поэтому кириллица здесь — эвристика, а не гарантия русского названия.
    Для полноценной локализации нужен отдельный дамп `alternateNamesV2`
    с кодами языков и флагом isPreferred; он весит на два порядка больше
    и для MVP не окупается.
    """

    if is_cyrillic(name):
        return name

    for candidate in alternatenames.split(","):
        candidate = candidate.strip()
        if candidate and is_cyrillic(candidate):
            return candidate

    return name


def load_admin1(path: Path) -> dict[str, str]:
    """Прочитать названия регионов первого уровня.

    Ключ — "RU.77", значение — "Moscow". Нужен, чтобы подсказка различала
    одноимённые города: сам дамп городов несёт только код.
    """

    admin1: dict[str, str] = {}
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 2:
                continue
            admin1[parts[0]] = parts[1]
    return admin1


def build(
    *,
    cities_path: Path,
    admin1_path: Path | None,
    out_path: Path,
    full_countries: frozenset[str],
    other_min_population: int,
) -> tuple[int, int]:
    """Отфильтровать дамп и записать каталог. Возвращает (прочитано, записано)."""

    admin1 = load_admin1(admin1_path) if admin1_path else {}

    read = 0
    written = 0
    seen_ids: set[str] = set()

    with (
        cities_path.open(encoding="utf-8") as source,
        out_path.open("w", encoding="utf-8", newline="\n") as target,
    ):
        for line in source:
            read += 1
            parts = line.rstrip("\n").split("\t")
            if len(parts) <= TIMEZONE:
                continue

            if parts[FEATURE_CLASS] != POPULATED_PLACE_CLASS:
                continue
            if parts[FEATURE_CODE] in EXCLUDED_FEATURE_CODES:
                continue

            timezone = parts[TIMEZONE].strip()
            if not timezone:
                # Без tz_id место бесполезно: историческое смещение
                # не определить, а значит и карту не посчитать.
                continue

            country = parts[COUNTRY_CODE]
            try:
                population = int(parts[POPULATION] or 0)
            except ValueError:
                population = 0

            if country not in full_countries and population < other_min_population:
                continue

            place_id = parts[GEONAMEID]
            if place_id in seen_ids:
                continue
            seen_ids.add(place_id)

            admin1_key = f"{country}.{parts[ADMIN1_CODE]}"

            record = {
                "place_id": place_id,
                "name": pick_display_name(parts[NAME], parts[ALTERNATENAMES]),
                "name_ascii": parts[ASCIINAME],
                "country": country,
                "admin1": admin1.get(admin1_key, ""),
                "latitude": float(parts[LATITUDE]),
                "longitude": float(parts[LONGITUDE]),
                "tz_id": timezone,
                "population": population,
            }

            target.write(json.dumps(record, ensure_ascii=False))
            target.write("\n")
            written += 1

    return read, written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cities", type=Path, required=True, help="cities1000.txt")
    parser.add_argument("--admin1", type=Path, default=None, help="admin1CodesASCII.txt")
    parser.add_argument("--out", type=Path, required=True, help="куда писать JSONL")
    parser.add_argument(
        "--full-countries",
        default="RU,UA,BY,KZ,MD,AM,GE,AZ,KG,UZ,TJ,TM,LT,LV,EE",
        help="коды стран, включаемые целиком, через запятую",
    )
    parser.add_argument(
        "--other-min-population",
        type=int,
        default=100_000,
        help="минимальное население для остальных стран",
    )
    args = parser.parse_args(argv)

    if not args.cities.exists():
        print(f"нет файла {args.cities}", file=sys.stderr)
        return 1
    if args.admin1 is not None and not args.admin1.exists():
        print(f"нет файла {args.admin1}", file=sys.stderr)
        return 1

    args.out.parent.mkdir(parents=True, exist_ok=True)

    read, written = build(
        cities_path=args.cities,
        admin1_path=args.admin1,
        out_path=args.out,
        full_countries=frozenset(
            code.strip().upper() for code in args.full_countries.split(",") if code.strip()
        ),
        other_min_population=args.other_min_population,
    )

    print(f"прочитано строк: {read}")
    print(f"записано мест:   {written}")
    print(f"каталог:         {args.out}")
    print()
    print("Не забыть атрибуцию GeoNames (CC BY) в интерфейсе продукта.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
