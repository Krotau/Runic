from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any, Awaitable, Generic, Protocol, TypeVar, cast
from uuid import uuid4

from .result import Result


TData = TypeVar("TData", contravariant=True)
TResult = TypeVar("TResult", covariant=True)
TError = TypeVar("TError", covariant=True)
# TService is covariant on handlers and keys so Pylance can bind
# DispatcherHandler[ExampleService] to DispatchService-shaped methods like
# handler.emit(...). The dispatcher still consumes services at registration
# time, so it uses a separate invariant TRegisteredService for that method.
# We intentionally avoid a TService/TService_co split because this library is
# centered on exact concrete service registration and retrieval, not on broader
# subtype-polymorphic handler APIs.
TService = TypeVar("TService", bound="DispatchService[Any, Any, Any]", covariant=True)
TRegisteredService = TypeVar("TRegisteredService", bound="DispatchService[Any, Any, Any]")


class DispatchService(Protocol[TData, TResult, TError]):
    """Service interface supported by the dispatcher registry."""

    def emit(self, data: TData) -> Result[TResult, TError] | Awaitable[Result[TResult, TError]]:
        """Handle input data and return an immediate or awaitable result."""

        ...


type AnyDispatchService = DispatchService[Any, Any, Any]


@dataclass(frozen=True, slots=True)
class DispatcherKey(Generic[TService]):
    """Stable key for a service stored in the dispatcher registry."""

    value: str


class DispatcherHandler(Generic[TService]):
    """Typed adapter for a registered dispatch service instance."""

    def __init__(self, service: AnyDispatchService) -> None:
        self._service = service

    @property
    def service(self) -> TService:
        """Expose the underlying registered service."""

        return cast(TService, self._service)

    async def emit(
        self: DispatcherHandler[DispatchService[TData, TResult, TError]],
        data: TData,
    ) -> Result[TResult, TError]:
        """Forward input to the underlying service."""

        maybe_result = self._service.emit(data)
        return cast(Result[TResult, TError], await maybe_result if inspect.isawaitable(maybe_result) else maybe_result)


class Dispatcher:
    """Registry for typed dispatch services that can be retrieved by key."""

    def __init__(self) -> None:
        self._services: dict[DispatcherKey[Any], object] = {}

    def register(
        self,
        service: TRegisteredService,
    ) -> tuple[DispatcherHandler[TRegisteredService], DispatcherKey[TRegisteredService]]:
        """Register a service instance and return its handler and key."""

        self._validate_service(service)
        key: DispatcherKey[TRegisteredService] = DispatcherKey(str(uuid4()))
        self._services[key] = service
        return DispatcherHandler(service), key

    def retrieve(self, key: DispatcherKey[TService]) -> DispatcherHandler[TService]:
        """Return a new handler for the service stored under `key`."""

        service = self._services.get(key)
        if service is None:
            raise KeyError(f"Unknown dispatcher key: {key.value}")
        self._validate_service(service)
        return DispatcherHandler(cast(TService, service))

    def unregister(self, key: DispatcherKey[Any]) -> bool:
        """Remove a registered service if it exists."""

        return self._services.pop(key, None) is not None

    @staticmethod
    def _validate_service(service: object) -> None:
        if not hasattr(service, "emit") or not callable(getattr(service, "emit")):
            raise TypeError("Registered services must define a callable emit(data) method")


def create_dispatcher() -> Dispatcher:
    """Create a dispatcher with its own internal registry."""

    return Dispatcher()
