from __future__ import annotations

from .runic import RunicError


class DuplicateRegistrationError(RunicError):
    """Raised when a named service or spell is registered more than once."""
