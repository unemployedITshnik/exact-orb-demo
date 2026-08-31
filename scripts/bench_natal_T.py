"""Замер T и размера артефакта для требований Chart Artifacts."""

"""ВНИМАНИЕ: evidence trail замера, а не portable CI benchmark.

Пути к проекту и каталогу эфемерид зашиты; bench_artifact_size дополнительно
зависит от /proc/self/status (Linux). Перед превращением в проверяемый
benchmark параметризовать пути и заменить RSS на tracemalloc.
Контрольные данные рождения должны совпадать с тест-паком резолва
(exact-orb_birth_data_resolution.md): Europe/Moscow 1990-09-02 14:30 =
10:30 UTC, зона UTC+4.
"""


import json
import statistics
import sys
import time
from datetime import UTC, datetime

sys.path.insert(0, "/tmp/eo/src")

import swisseph as _swe  # noqa: E402

# На PyPI доступен только pyswisseph 2.10.3.2, а pyproject требует >= 2.10.3.4.
# В 2.10.3.2 houses_ex отдаёт 12 куспидов, код ожидает 13 (с фиктивным нулевым).
# Шим приводит форму к ожидаемой; на измеряемое время влияния не оказывает.
_houses_ex_orig = _swe.houses_ex


def _houses_ex_13(*args, **kwargs):
    cusps, ascmc = _houses_ex_orig(*args, **kwargs)
    if len(cusps) == 12:
        cusps = (0.0, *cusps)
    return cusps, ascmc


_swe.houses_ex = _houses_ex_13

# В 2.10.3.4 calc_ut отдаёт (xx, retflags, serr); в 2.10.3.2 — (xx, retflags).
_calc_ut_orig = _swe.calc_ut


def _calc_ut_3(*args, **kwargs):
    result = _calc_ut_orig(*args, **kwargs)
    return result if len(result) == 3 else (*result, "")


_swe.calc_ut = _calc_ut_3

from exact_orb.config import configure_ephemeris  # noqa: E402
from exact_orb.engine.charts.natal import calculate_natal  # noqa: E402

EPHE = "/tmp/eo/ephe"
_status = configure_ephemeris(EPHE)
print("ephemeris:", _status)

CASES = [
    ("Москва 02.09.1990 14:30 MSD=UTC+4", datetime(1990, 9, 2, 10, 30, tzinfo=UTC), 55.7558, 37.6173),
    ("Питер 15.03.1985 03:10", datetime(1985, 3, 15, 0, 10, tzinfo=UTC), 59.9343, 30.3351),
    ("Сочи 21.06.2001 23:59", datetime(2001, 6, 21, 19, 59, tzinfo=UTC), 43.5855, 39.7231),
]

FULL = None  # DEFAULT_INCLUDE
COSMO = {"positions", "aspects", "configurations"}


def bench(label, include, chart_kind, repeats=12):
    rows = []
    for name, dt, lat, lon in CASES:
        # прогрев: первый вызов грузит .se1 в память процесса
        calculate_natal(
            birth_datetime=dt, latitude=lat, longitude=lon,
            chart_kind=chart_kind, ephemeris_path=EPHE, include=include,
        )
        times = []
        for _ in range(repeats):
            t0 = time.perf_counter()
            chart = calculate_natal(
                birth_datetime=dt, latitude=lat, longitude=lon,
                chart_kind=chart_kind, ephemeris_path=EPHE, include=include,
            )
            times.append((time.perf_counter() - t0) * 1000)
        payload = chart.model_dump_json()
        rows.append((name, statistics.median(times), min(times), max(times), len(payload.encode())))
    print(f"\n=== {label} ===")
    print(f"{'случай':<28} {'медиана':>9} {'min':>8} {'max':>8} {'JSON':>10}")
    for name, med, lo, hi, size in rows:
        print(f"{name:<28} {med:>7.1f}мс {lo:>6.1f}мс {hi:>6.1f}мс {size/1024:>8.1f}КБ")
    return rows


def cold_start():
    """Первый расчёт в процессе — с загрузкой .se1."""
    name, dt, lat, lon = CASES[0]
    t0 = time.perf_counter()
    calculate_natal(
        birth_datetime=dt, latitude=lat, longitude=lon,
        chart_kind="natal", ephemeris_path=EPHE,
    )
    return (time.perf_counter() - t0) * 1000


if __name__ == "__main__":
    cold = cold_start()
    print(f"холодный первый расчёт в процессе: {cold:.1f}мс")

    full = bench("natal, полный include", FULL, "natal")
    cosmo = bench("cosmogram, суженный include", COSMO, "cosmogram")

    # компоненты размера
    name, dt, lat, lon = CASES[0]
    chart = calculate_natal(
        birth_datetime=dt, latitude=lat, longitude=lon,
        chart_kind="natal", ephemeris_path=EPHE,
    )
    d = json.loads(chart.model_dump_json())
    print("\n=== состав JSON натальной карты ===")
    total = len(chart.model_dump_json().encode())
    for k, v in sorted(d.items(), key=lambda kv: -len(json.dumps(kv[1], ensure_ascii=False))):
        n = len(json.dumps(v, ensure_ascii=False).encode())
        if n > 200:
            cnt = len(v) if isinstance(v, (list, dict)) else ""
            print(f"  {k:<22} {n/1024:>7.1f}КБ  {100*n/total:>5.1f}%  элементов: {cnt}")
    print(f"  {'ИТОГО':<22} {total/1024:>7.1f}КБ")

    med_full = statistics.median([r[1] for r in full])
    size_full = statistics.median([r[4] for r in full])
    print("\n=== для конфигурации ===")
    print(f"T (медиана, натал):            {med_full:.0f}мс")
    print(f"размер артефакта (медиана):    {size_full/1024:.1f}КБ")
    print(f"пропускная способность 1/T:    {1000/med_full:.1f} карт/с при полной сериализации")
    print(f"5 одновременных промахов:      ~{5*med_full:.0f}мс для последнего")
    print(f"1000 записей в кэше:           ~{1000*size_full/1024/1024:.0f}МБ")
