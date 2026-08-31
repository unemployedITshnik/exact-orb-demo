"""Память под запись кэша: объект в процессе vs JSON vs сжатый JSON."""

"""ВНИМАНИЕ: evidence trail замера, а не portable CI benchmark.

Пути к проекту и каталогу эфемерид зашиты; bench_artifact_size дополнительно
зависит от /proc/self/status (Linux). Перед превращением в проверяемый
benchmark параметризовать пути и заменить RSS на tracemalloc.
Контрольные данные рождения должны совпадать с тест-паком резолва
(exact-orb_birth_data_resolution.md): Europe/Moscow 1990-09-02 14:30 =
10:30 UTC, зона UTC+4.
"""


import gzip
import json
import os
import sys
import zlib
from datetime import UTC, datetime, timedelta

sys.path.insert(0, "/tmp/eo/src")

import swisseph as _swe  # noqa: E402

_h = _swe.houses_ex
_swe.houses_ex = lambda *a, **k: ((lambda c, m: ((0.0, *c), m) if len(c) == 12 else (c, m))(*_h(*a, **k)))
_c = _swe.calc_ut
_swe.calc_ut = lambda *a, **k: (lambda r: r if len(r) == 3 else (*r, ""))(_c(*a, **k))

from exact_orb.config import configure_ephemeris  # noqa: E402
from exact_orb.engine.charts.natal import calculate_natal  # noqa: E402

configure_ephemeris("/tmp/eo/ephe")


def rss_kb():
    with open("/proc/self/status") as fh:
        for line in fh:
            if line.startswith("VmRSS:"):
                return int(line.split()[1])
    return 0


N = 200
base = datetime(1985, 1, 1, 12, 0, tzinfo=UTC)

# прогрев
calculate_natal(birth_datetime=base, latitude=55.75, longitude=37.61, chart_kind="natal")

charts = []
before = rss_kb()
for i in range(N):
    charts.append(
        calculate_natal(
            birth_datetime=base + timedelta(days=i * 37, minutes=i * 7),
            latitude=55.75 + i * 0.001,
            longitude=37.61,
            chart_kind="natal",
        )
    )
after = rss_kb()

per_object_kb = (after - before) / N
payload = charts[0].model_dump_json().encode()
gz = gzip.compress(payload, 6)
zl = zlib.compress(payload, 6)

print(f"объектов в памяти:        {N}")
print(f"прирост RSS:              {(after - before) / 1024:.1f}МБ")
print(f"на один артефакт в памяти ~{per_object_kb:.1f}КБ")
print(f"JSON одного артефакта:     {len(payload) / 1024:.1f}КБ")
print(f"gzip-6:                    {len(gz) / 1024:.1f}КБ  ({100 * len(gz) / len(payload):.0f}%)")
print(f"zlib-6:                    {len(zl) / 1024:.1f}КБ  ({100 * len(zl) / len(payload):.0f}%)")
print()
for n in (500, 1000, 5000):
    print(
        f"{n:>5} записей:  in-memory ~{n * per_object_kb / 1024:.0f}МБ   "
        f"Redis JSON ~{n * len(payload) / 1024 / 1024:.0f}МБ   "
        f"Redis gzip ~{n * len(gz) / 1024 / 1024:.0f}МБ"
    )
print(f"\nPID {os.getpid()}")
