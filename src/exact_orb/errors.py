"""Runtime error hierarchy for exact-orb."""

from __future__ import annotations


class EphemerisRuntimeError(RuntimeError):
    """Base class for ephemeris runtime failures."""


class EphemerisConfigurationError(EphemerisRuntimeError):
    """Base class for ephemeris configuration failures."""


class EphemerisNotInitializedError(EphemerisConfigurationError):
    """Raised when calculations run before explicit ephemeris startup."""


class EphemerisPathMismatchError(EphemerisConfigurationError):
    """Raised when runtime receives a path different from the frozen path."""


class EphemerisSessionRequiredError(EphemerisRuntimeError):
    """Raised when Swiss Ephemeris is called outside an ephemeris session."""

