"""Compatibility exports for the process-wide ephemeris runtime lock.

The real lock module lives at ``exact_orb.ephemeris_runtime`` so ``config`` can
depend on it without importing back into the ``engine`` package. This shim keeps
the established engine import path available.
"""

from __future__ import annotations

from exact_orb.ephemeris_runtime import ephemeris_session, require_ephemeris_session


__all__ = ["ephemeris_session", "require_ephemeris_session"]
