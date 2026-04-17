from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class DefaultError:
    """Default generic error payload for request failures."""

    message: str
    code: str | None = None
    details: Any | None = None
