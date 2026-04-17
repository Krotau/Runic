from __future__ import annotations

from .runic import RunicError


class TaskNotFoundError(RunicError):
    """Raised when a named spell cannot be resolved."""
