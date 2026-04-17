from __future__ import annotations

from dataclasses import dataclass, field
from typing import Generic, TypeVar
from uuid import uuid4

from .errors import DefaultError


def new_id() -> str:
    """Return a unique request identifier."""

    return str(uuid4())


T = TypeVar("T")
E = TypeVar("E")


@dataclass(slots=True)
class Request(Generic[T, E]):
    """Base type for all Runic requests."""

    # Keep the request identifier keyword-only so call sites stay explicit.
    request_id: str = field(default_factory=new_id, kw_only=True)


class Query(Request[T, E], Generic[T, E]):
    """Marker type for read-only requests."""

    pass


class Command(Request[T, E], Generic[T, E]):
    """Marker type for requests that may change process state."""

    pass
