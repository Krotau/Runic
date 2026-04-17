from __future__ import annotations

from .runic import RunicError


class AmbiguousQueryError(RunicError):
    """Raised when more than one service can answer the same query type."""
