"""External natal chart reference for 1985-09-01 20:45 UTC, Moscow."""

from __future__ import annotations

from datetime import datetime, timezone

import swisseph as swe


ARCSECOND_DEGREES = 1.0 / 3600.0
TRUE_NODE_TOLERANCE_DEGREES = 10.0 / 3600.0

REFERENCE = {
    "datetime_utc": datetime(1985, 9, 1, 20, 45, 0, tzinfo=timezone.utc),
    "latitude": 55.7522,
    "longitude": 37.6155,
    "house_system": "P",
    "source": "geocult.ru",
}

BODY_IDS = {
    "sun": swe.SUN,
    "moon": swe.MOON,
    "mercury": swe.MERCURY,
    "venus": swe.VENUS,
    "mars": swe.MARS,
    "jupiter": swe.JUPITER,
    "saturn": swe.SATURN,
    "uranus": swe.URANUS,
    "neptune": swe.NEPTUNE,
    "pluto": swe.PLUTO,
    "chiron": swe.CHIRON,
    "true_node": swe.TRUE_NODE,
    "mean_apog": swe.MEAN_APOG,
}

EXPECTED_BODY_LONGITUDES = {
    "sun": 159.340833,
    "moon": 7.665000,
    "mercury": 142.097778,
    "venus": 125.598889,
    "mars": 144.792778,
    "jupiter": 308.683611,
    "saturn": 232.615556,
    "uranus": 254.003611,
    "neptune": 270.877000,
    "pluto": 212.657778,
    "chiron": 74.445556,
    "true_node": 40.911389,
    "mean_apog": 40.201667,
}

EXPECTED_BODY_TOLERANCES = {
    # geocult.ru uses a different true-node definition/table than Swiss
    # Ephemeris in some configurations; keep the reference visible but allow
    # this known 10 arcsecond envelope.
    "true_node": TRUE_NODE_TOLERANCE_DEGREES,
}

EXPECTED_DERIVED_LONGITUDES = {
    "south_node": 220.911389,
    "pars_fortune": 249.781389,
}

EXPECTED_ANGLE_LONGITUDES = {
    "vertex": 229.738611,
}

EXPECTED_CUSPS = {
    1: 98.105833,
    2: 111.861111,
    3: 127.211389,
    4: 147.629444,
    5: 179.749167,
    6: 231.387222,
    7: 278.105833,
    8: 291.861111,
    9: 307.211389,
    10: 327.629444,
    11: 359.749167,
    12: 51.387222,
}

EXPECTED_BODY_HOUSES = {
    "sun": 4,
    "moon": 11,
    "mercury": 3,
    "venus": 2,
    "mars": 3,
    "jupiter": 9,
    "saturn": 6,
    "uranus": 6,
    "neptune": 6,
    "pluto": 5,
    "chiron": 12,
    "true_node": 11,
    "south_node": 5,
    "pars_fortune": 6,
}

EXPECTED_RETROGRADE = {
    "sun": False,
    "moon": False,
    "mercury": False,
    "venus": False,
    "mars": False,
    "jupiter": True,
    "saturn": False,
    "uranus": False,
    "neptune": True,
    "pluto": False,
    "true_node": True,
    "mean_apog": False,
}
