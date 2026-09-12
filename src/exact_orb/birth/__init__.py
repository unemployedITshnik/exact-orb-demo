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
from exact_orb.birth.types import (
    BirthInput,
    BirthTimeDomain,
    ResolutionWarning,
    ResolvedBirthData,
    UtcMinuteRange,
    birth_time_domain_digest,
)
from exact_orb.birth.tz import (
    TzAmbiguous,
    TzNonexistent,
    TzOk,
    UnknownTimezoneError,
    build_birth_time_domain,
    local_date_exists,
    resolve_anomaly,
    resolve_historical_tz,
    resolve_unknown_birth_time_for_migration,
)


__all__ = [
    "BirthDataResolver",
    "BirthInput",
    "BirthTimeDomain",
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
    "UtcMinuteRange",
    "birth_time_domain_digest",
    "build_birth_time_domain",
    "local_date_exists",
    "resolve_anomaly",
    "resolve_historical_tz",
    "resolve_unknown_birth_time_for_migration",
]
