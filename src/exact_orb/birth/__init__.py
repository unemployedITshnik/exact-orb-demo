"""Birth-data resolution public API."""

from __future__ import annotations

from exact_orb.birth.places import (
    LocalPlaceCatalog,
    PlaceCatalog,
    PlaceCatalogUnavailableError,
    PlaceNotFound,
    ResolvedPlace,
)
from exact_orb.birth.resolver import BirthDataResolver
from exact_orb.birth.types import BirthInput, ResolutionWarning, ResolvedBirthData
from exact_orb.birth.tz import (
    TzAmbiguous,
    TzNonexistent,
    TzOk,
    UnknownTimezoneError,
    local_date_exists,
    resolve_anomaly,
    resolve_historical_tz,
)


__all__ = [
    "BirthDataResolver",
    "BirthInput",
    "LocalPlaceCatalog",
    "PlaceCatalog",
    "PlaceCatalogUnavailableError",
    "PlaceNotFound",
    "ResolutionWarning",
    "ResolvedBirthData",
    "ResolvedPlace",
    "TzAmbiguous",
    "TzNonexistent",
    "TzOk",
    "UnknownTimezoneError",
    "local_date_exists",
    "resolve_anomaly",
    "resolve_historical_tz",
]
