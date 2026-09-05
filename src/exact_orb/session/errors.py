"""Typed errors exposed by the session contract layer."""

from __future__ import annotations


class SessionPersistenceError(Exception):
    """Base class for infrastructure failures in session persistence."""

    error_code: str

    def __init__(self, error_code: str) -> None:
        self.error_code = error_code
        super().__init__(error_code)


class StateReadError(SessionPersistenceError):
    """The store could not determine whether session state exists."""


class StateWriteError(SessionPersistenceError):
    """The store could not persist a requested state mutation."""


class ExpiredSessionTransitionError(ValueError):
    """A transition was requested for state whose lifetime has ended."""

    error_code: str

    def __init__(self) -> None:
        self.error_code = "SESSION_EXPIRED"
        super().__init__(self.error_code)


__all__ = [
    "ExpiredSessionTransitionError",
    "SessionPersistenceError",
    "StateReadError",
    "StateWriteError",
]
