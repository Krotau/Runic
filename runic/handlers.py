from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Generic, Protocol, TypeVar

from .requests import Command, Query
from .result import Result


TData = TypeVar("TData")
TResult = TypeVar("TResult", covariant=True)
TError = TypeVar("TError", covariant=True)
TService = TypeVar("TService")
TQuery = TypeVar("TQuery", bound=Query[Any, Any], contravariant=True)
TCommand = TypeVar("TCommand", bound=Command[Any, Any], contravariant=True)
TQueryResult = TypeVar("TQueryResult", covariant=True)
TQueryError = TypeVar("TQueryError", covariant=True)
TCommandResult = TypeVar("TCommandResult", covariant=True)
TCommandError = TypeVar("TCommandError", covariant=True)

type ServiceResult[TResult, TError] = Result[TResult, TError] | Awaitable[Result[TResult, TError]]


class SupportsAsk(Protocol[TQuery, TResult, TError]):
    """Service protocol for query-capable registered objects."""

    def ask(self, query: TQuery) -> ServiceResult[TResult, TError]:
        ...


class SupportsInvoke(Protocol[TCommand, TResult, TError]):
    """Service protocol for command-capable registered objects."""

    def invoke(self, command: TCommand) -> ServiceResult[TResult, TError]:
        ...


class _ServiceMethodAdapter(Generic[TData, TResult, TError]):
    """Internal wrapper around a bound ask/invoke method."""

    def __init__(
        self,
        message_type: type[TData],
        method: Callable[[TData], ServiceResult[TResult, TError]],
        await_result: Callable[[Any], Awaitable[Any]],
    ) -> None:
        self.message_type = message_type
        self._method = method
        self._await_result = await_result

    async def call(self, data: TData) -> Result[TResult, TError]:
        return await self._await_result(self._method(data))


class Handler(Generic[TService]):
    """Public facade returned when registering an object service."""

    def __init__(
        self,
        service: TService,
        *,
        query_adapter: _ServiceMethodAdapter[Any, Any, Any] | None = None,
        command_adapter: _ServiceMethodAdapter[Any, Any, Any] | None = None,
    ) -> None:
        self._service = service
        self._query_adapter = query_adapter
        self._command_adapter = command_adapter

    @property
    def service(self) -> TService:
        return self._service

    async def ask(self, query: Query[TQueryResult, TQueryError]) -> Result[TQueryResult, TQueryError]:
        # A service may expose only one capability; missing adapters are a
        # registration-time capability mismatch, not a recoverable runtime event.
        match self._query_adapter:
            case None:
                raise TypeError("Registered service does not support ask(query)")
            case adapter:
                return await adapter.call(query)

    async def invoke(
        self,
        command: Command[TCommandResult, TCommandError],
    ) -> Result[TCommandResult, TCommandError]:
        # Same capability split as `ask(...)`: only call the adapter when it exists.
        match self._command_adapter:
            case None:
                raise TypeError("Registered service does not support invoke(command)")
            case adapter:
                return await adapter.call(command)
