from __future__ import annotations

from .runic import RunicError


class ServiceNotFoundError(RunicError):
    """Raised when a named service cannot be resolved."""
