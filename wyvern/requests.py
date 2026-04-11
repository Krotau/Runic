from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar
from uuid import uuid4


def new_id() -> str:
    """Return a unique request identifier."""

    return str(uuid4())


T = TypeVar("T")
E = TypeVar("E")


@dataclass(slots=True)
class Request(Generic[T, E]):
    """Base type for all dispatcher requests."""

    request_id: str = field(default_factory=new_id)


class Query(Request[T, E], Generic[T, E]):
    """Marker type for read-only requests."""

    pass


class Command(Request[T, E], Generic[T, E]):
    """Marker type for requests that may change process state."""

    pass


@dataclass(slots=True)
class DefaultError:
    """Default generic error payload for request failures."""

    message: str
    code: str | None = None
    details: Any | None = None
