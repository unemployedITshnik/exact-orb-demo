"""Golden values for Selena strategies on the 1985 Moscow reference chart."""

from __future__ import annotations

from datetime import datetime, timezone


DATETIME_UTC = datetime(1985, 9, 1, 20, 45, 0, tzinfo=timezone.utc)
JULIAN_DAY_UT = 2446310.3645833335

GEOCULT_SELENA = 225.374167

EXPECTED_SELENA = {
    "mean_perigee": 220.20155113295235,
    "true_perigee": 225.50257012821265,
}
