from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any, Awaitable, Generic, Protocol, TypeVar, cast
from uuid import uuid4

from .result import Err, Ok, Result


TData = TypeVar("TData", contravariant=True)
TResult = TypeVar("TResult", covariant=True)
TError = TypeVar("TError", covariant=True)
# The handler and key stay covariant so callers keep the exact registered type
# when they retrieve a service later.
TConjurable = TypeVar("TConjurable", bound="Conjurable[Any, Any, Any]", covariant=True)
TRegisteredConjurable = TypeVar("TRegisteredConjurable", bound="Conjurable[Any, Any, Any]")


class Conjurable(Protocol[TData, TResult, TError]):
    """Service interface supported by the conjurer registry."""

    def emit(self, data: TData) -> Result[TResult, TError] | Awaitable[Result[TResult, TError]]:
        """Handle input data and return an immediate or awaitable result."""

        ...


type AnyConjurable = Conjurable[Any, Any, Any]


async def _await_if_needed(value: Result[TResult, TError] | Awaitable[Result[TResult, TError]]) -> Result[TResult, TError]:
    if inspect.isawaitable(value):
        return await cast(Awaitable[Result[TResult, TError]], value)
    return value


@dataclass(frozen=True, slots=True)
class ConjurerKey(Generic[TConjurable]):
    """Stable key for a conjured service stored in the registry."""

    value: str


class Conjured(Generic[TConjurable]):
    """Typed adapter for a conjured service instance."""

    def __init__(self, service: AnyConjurable) -> None:
        self._service = service

    @property
    def service(self) -> TConjurable:
        """Expose the underlying conjured service."""

        return cast(TConjurable, self._service)

    async def emit(
        self: Conjured[Conjurable[TData, TResult, TError]],
        data: TData,
    ) -> Result[TResult, TError]:
        """Forward input to the underlying service."""

        return await _await_if_needed(self._service.emit(data))


class Conjurer:
    """Registry for typed conjurable services that can be retrieved by key."""

    def __init__(self) -> None:
        self._services: dict[ConjurerKey[Any], object] = {}

    def conjure(
        self,
        service: TRegisteredConjurable,
    ) -> tuple[Conjured[TRegisteredConjurable], ConjurerKey[TRegisteredConjurable]]:
        """Register a service instance and return its handler and key."""

        self._validate_service(service)
        key: ConjurerKey[TRegisteredConjurable] = ConjurerKey(str(uuid4()))
        self._services[key] = service
        return Conjured(service), key

    def retrieve(self, key: ConjurerKey[TConjurable]) -> Conjured[TConjurable]:
        """Return a new handler for the service stored under `key`."""

        match self._service_for(key):
            case Ok(value=service):
                return Conjured(cast(TConjurable, service))
            case Err(error=error):
                raise error

        raise AssertionError("Unreachable retrieve branch")

    def banish(self, key: ConjurerKey[Any]) -> bool:
        """Remove a conjured service if it exists."""

        match self._services.pop(key, None):
            case None:
                return False
            case _:
                return True

    @staticmethod
    def _validate_service(service: object) -> None:
        match getattr(service, "emit", None):
            case None:
                raise TypeError("Conjured services must define a callable emit(data) method")
            case emit if not callable(emit):
                raise TypeError("Conjured services must define a callable emit(data) method")

    def _service_for(self, key: ConjurerKey[Any]) -> Result[AnyConjurable, KeyError]:
        service = self._services.get(key)
        match service:
            case None:
                return Err(KeyError(f"Unknown conjurer key: {key.value}"))
            case _:
                self._validate_service(service)
                return Ok(cast(AnyConjurable, service))


def create_conjurer() -> Conjurer:
    """Create a conjurer with its own internal registry."""

    return Conjurer()
