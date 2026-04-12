from __future__ import annotations

import asyncio
import inspect
import logging
from collections import defaultdict
from collections.abc import Awaitable, Callable
from functools import update_wrapper
from typing import Any, Generic, ParamSpec, Protocol, TypeVar, cast, get_type_hints, overload, runtime_checkable

from .dispatcher import AnyDispatchService, DispatchService, Dispatcher, DispatcherKey, create_dispatcher
from .events import Event, EventBus
from .jobs import JobContext, JobManager
from .requests import Query
from .result import Result


TEvent = TypeVar("TEvent")
TData = TypeVar("TData")
TResult = TypeVar("TResult", covariant=True)
TError = TypeVar("TError", covariant=True)
P = ParamSpec("P")
logger = logging.getLogger(__name__)
_MISSING = object()

type ServiceResult[TResult, TError] = Result[TResult, TError] | Awaitable[Result[TResult, TError]]


class WyvernError(Exception):
    """Base error raised by the Wyvern runtime facade."""


class DuplicateRegistrationError(WyvernError):
    """Raised when a named service or task is registered more than once."""


class ServiceNotFoundError(WyvernError):
    """Raised when a named service cannot be resolved."""


class TaskNotFoundError(WyvernError):
    """Raised when a named task cannot be resolved."""


@runtime_checkable
class RegistryAdapter(Protocol[P, TResult, TError]):
    """Unified public registration contract returned by the runtime facade."""

    name: str

    async def emit(self, *args: P.args, **kwargs: P.kwargs) -> Result[TResult, TError]:
        """Invoke the registered service."""
        ...

    async def __call__(self, *args: P.args, **kwargs: P.kwargs) -> Result[TResult, TError]:
        """Invoke the registered service."""
        ...

    def get_key(self) -> DispatcherKey[AnyDispatchService]:
        """Return the dispatcher key assigned at registration time."""
        ...


class _RegistryAdapter(Generic[P, TResult, TError]):
    """Concrete homogeneous adapter used for all registered services."""

    def __init__(
        self,
        name: str,
        invoke: Callable[P, ServiceResult[TResult, TError]],
        dispatcher_emit: Callable[[Any], ServiceResult[TResult, TError]],
        wrapped: Callable[..., Any] | None = None,
    ) -> None:
        self.name = name
        self._invoke = invoke
        self._dispatcher_emit = dispatcher_emit
        self._key: DispatcherKey[AnyDispatchService] | None = None
        if wrapped is not None:
            update_wrapper(self, wrapped)

    def _set_key(self, key: DispatcherKey[AnyDispatchService]) -> None:
        self._key = key

    def get_key(self) -> DispatcherKey[AnyDispatchService]:
        if self._key is None:
            raise RuntimeError("Registry adapter has not been registered yet")
        return self._key

    async def emit(self, *args: P.args, **kwargs: P.kwargs) -> Result[TResult, TError]:
        return await _await_if_needed(self._invoke(*args, **kwargs))

    async def __call__(self, *args: P.args, **kwargs: P.kwargs) -> Result[TResult, TError]:
        return await self.emit(*args, **kwargs)

    async def emit_dispatch(self, data: Any) -> Result[TResult, TError]:
        return await _await_if_needed(self._dispatcher_emit(data))


class _RegisterDecorator:
    """Decorator object with overloads that preserve function signatures."""

    def __init__(self, runtime: Wyvern, name: str | None) -> None:
        self._runtime = runtime
        self._name = name

    @overload
    def __call__(self, fn: Callable[[], ServiceResult[TResult, TError]]) -> RegistryAdapter[[], TResult, TError]: ...

    @overload
    def __call__(self, fn: Callable[[TData], ServiceResult[TResult, TError]]) -> RegistryAdapter[[TData], TResult, TError]: ...

    def __call__(self, fn: Callable[..., Any]) -> RegistryAdapter[..., Any, Any]:
        resolved = _infer_name(self._name, fn)
        parameter_count = _service_parameter_count(fn)
        if parameter_count == 0:
            adapter = _RegistryAdapter(
                name=resolved,
                invoke=fn,
                dispatcher_emit=lambda _data: fn(),
                wrapped=fn,
            )
        elif parameter_count == 1:
            adapter = _RegistryAdapter(
                name=resolved,
                invoke=fn,
                dispatcher_emit=fn,
                wrapped=fn,
            )
        else:
            raise TypeError(f"Unsupported service signature for {fn.__name__}")
        return self._runtime._register_adapter(adapter)


class _QueryDecorator:
    """Decorator object with overloads that preserve typed query signatures."""

    def __init__(self, runtime: Wyvern, message_type: type[Any] | None) -> None:
        self._runtime = runtime
        self._message_type = message_type

    def __call__(self, fn: Callable[[TData], ServiceResult[TResult, TError]]) -> RegistryAdapter[[TData], TResult, TError]:
        resolved_type = self._message_type or _infer_annotated_message_type(fn)
        if resolved_type in self._runtime._query_handlers:
            raise DuplicateRegistrationError(f"Query already registered for type: {resolved_type.__name__}")
        adapter = cast(
            RegistryAdapter[[TData], TResult, TError],
            _RegisterDecorator(self._runtime, _type_key("query", resolved_type))(fn),
        )
        self._runtime._query_handlers[resolved_type] = adapter
        return adapter


def _infer_name(name: str | None, fn: Callable[..., Any]) -> str:
    resolved = name or fn.__name__
    if not resolved:
        raise ValueError("Registration name could not be inferred")
    return resolved


def _type_key(prefix: str, message_type: type[Any]) -> str:
    return f"{prefix}:{message_type.__module__}.{message_type.__qualname__}"


def _infer_annotated_message_type(fn: Callable[..., Any], *, skip: int = 0) -> type[Any]:
    signature = inspect.signature(fn)
    positional = [
        parameter
        for parameter in signature.parameters.values()
        if parameter.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
    ]
    target = positional[skip : skip + 1]
    if len(target) != 1:
        raise TypeError(f"Could not infer message type for {fn.__name__}")
    parameter = target[0]
    hints = get_type_hints(fn)
    annotation = hints.get(parameter.name, parameter.annotation)
    if not isinstance(annotation, type):
        raise TypeError(f"Missing concrete message type annotation for {fn.__name__}")
    return annotation


def _bind_supported_signature(fn: Callable[..., Any], *candidates: tuple[Any, ...]) -> tuple[Any, ...]:
    signature = inspect.signature(fn)
    for args in candidates:
        try:
            signature.bind(*args)
        except TypeError:
            continue
        return args
    raise TypeError(f"Unsupported callable signature for {fn.__name__}")


def _service_parameter_count(fn: Callable[..., Any]) -> int:
    signature = inspect.signature(fn)
    positional = [
        parameter
        for parameter in signature.parameters.values()
        if parameter.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
    ]
    return len(positional)


async def _await_if_needed(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


class _FunctionTaskAdapter:
    """Wrap a plain callable so it matches the job manager work shape."""

    def __init__(self, fn: Callable[..., Any]) -> None:
        self._fn = fn

    async def __call__(self, ctx: JobContext[Any]) -> Any:
        args = _bind_supported_signature(self._fn, (ctx, ctx.data), (ctx.data,), ())
        return await _await_if_needed(self._fn(*args))


class _DispatcherServiceBridge:
    """Small bridge that exposes emit(data) for dispatcher registration."""

    def __init__(self, emit: Callable[[Any], Awaitable[Any] | Any]) -> None:
        self._emit = emit

    def emit(self, data: Any) -> Awaitable[Any] | Any:
        return self._emit(data)


class Wyvern:
    """Small runtime facade that composes the bus, dispatcher, and jobs."""

    def __init__(
        self,
        *,
        bus: EventBus[Any] | None = None,
        dispatcher: Dispatcher | None = None,
        jobs: JobManager[Any] | None = None,
    ) -> None:
        self.bus = bus or EventBus(object)
        self.dispatcher = dispatcher or create_dispatcher()
        self.jobs = jobs or JobManager(self.bus)
        self._service_keys: dict[str, DispatcherKey[AnyDispatchService]] = {}
        self._tasks: dict[str, _FunctionTaskAdapter] = {}
        self._handlers: dict[str, list[Callable[..., Any]]] = defaultdict(list)
        self._query_handlers: dict[type[Any], RegistryAdapter[..., Any, Any]] = {}
        self._typed_tasks: dict[type[Any], _FunctionTaskAdapter] = {}
        self._typed_handlers: dict[type[Any], list[Callable[..., Any]]] = defaultdict(list)
        self._background_tasks: set[asyncio.Task[None]] = set()

    async def publish(self, topic: str | Any, event: Any = _MISSING) -> None:
        """Broadcast a named or typed event to the bus and local handlers."""

        if event is _MISSING:
            payload = topic
            topic_name = _type_key("event", type(payload))
            await self.bus.publish(Event(name=topic_name, data=payload))
            for handler in self._typed_handlers.get(type(payload), ()):
                self._spawn_handler(handler, payload)
            return

        await self.bus.publish(Event(name=cast(str, topic), data=event))
        for handler in self._handlers.get(cast(str, topic), ()):
            self._spawn_handler(handler, event)

    async def ask(self, request: Query[TResult, TError]) -> Result[TResult, TError]:
        """Invoke a typed request handler using the request object's concrete type."""

        handler = self._query_handlers.get(type(request))
        if handler is None:
            raise ServiceNotFoundError(f"Unknown query type: {type(request).__name__}")
        return cast(Result[TResult, TError], await handler.emit(request))

    async def call(self, name: str, payload: Any = None) -> Any:
        """Invoke a named service and return its reply."""

        key = self._service_keys.get(name)
        if key is None:
            raise ServiceNotFoundError(f"Unknown service: {name}")
        handler = self.dispatcher.retrieve(key)
        return await handler.emit(payload)

    async def dispatch(self, name: str, payload: Any = None) -> str:
        """Start a named task as tracked background work."""

        task = self._tasks.get(name)
        if task is None:
            raise TaskNotFoundError(f"Unknown task: {name}")
        return await self.jobs.start(task, data=payload)

    async def start(self, payload: Any) -> str:
        """Start a typed task using the payload object's concrete type."""

        task = self._typed_tasks.get(type(payload))
        if task is None:
            raise TaskNotFoundError(f"Unknown task type: {type(payload).__name__}")
        return await self.jobs.start(task, data=payload)

    def on(self, topic: str | type[Any]) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """Register a sync or async handler for a topic name or event type."""

        def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
            if isinstance(topic, str):
                self._handlers[topic].append(fn)
                return fn
            self._typed_handlers[topic].append(fn)
            return fn

        return decorator

    @overload
    def query(self, target: type[TData]) -> _QueryDecorator: ...

    @overload
    def query(self, target: Callable[[TData], ServiceResult[TResult, TError]]) -> RegistryAdapter[[TData], TResult, TError]: ...

    @overload
    def query(self) -> _QueryDecorator: ...

    def query(
        self,
        target: type[Any] | Callable[..., Any] | None = None,
    ) -> _QueryDecorator | RegistryAdapter[..., Any, Any]:
        """Register a typed query handler resolved by request object type."""

        if callable(target) and not isinstance(target, type):
            return _QueryDecorator(self, None)(target)
        return _QueryDecorator(self, cast(type[Any] | None, target))

    @overload
    def register(self, name: str | None = None) -> _RegisterDecorator: ...

    @overload
    def register(self, name: str, service: DispatchService[TData, TResult, TError]) -> RegistryAdapter[[TData], TResult, TError]: ...

    def register(
        self,
        name: str | None = None,
        service: AnyDispatchService | None = None,
    ) -> _RegisterDecorator | RegistryAdapter[..., Any, Any]:
        """Register either a decorated function service or an existing service object."""

        if service is not None:
            if name is None:
                raise ValueError("A service name is required when registering a service object")
            typed_service = cast(DispatchService[Any, Any, Any], service)
            return self._register_adapter(
                _RegistryAdapter(
                    name=name,
                    invoke=typed_service.emit,
                    dispatcher_emit=typed_service.emit,
                    wrapped=typed_service.emit,
                )
            )
        return _RegisterDecorator(self, name)

    def task(self, name: str | type[Any] | None = None) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """Register a named legacy task or a typed task request handler."""

        if isinstance(name, type):
            message_type = name

            def typed_decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
                if message_type in self._typed_tasks:
                    raise DuplicateRegistrationError(f"Task already registered for type: {message_type.__name__}")
                self._typed_tasks[message_type] = _FunctionTaskAdapter(fn)
                return fn

            return typed_decorator

        def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
            resolved = _infer_name(cast(str | None, name), fn)
            if resolved in self._tasks:
                raise DuplicateRegistrationError(f"Task already registered: {resolved}")
            self._tasks[resolved] = _FunctionTaskAdapter(fn)
            return fn

        return decorator

    def _register_adapter(self, adapter: _RegistryAdapter[Any, Any, Any]) -> RegistryAdapter[..., Any, Any]:
        if adapter.name in self._service_keys:
            raise DuplicateRegistrationError(f"Service already registered: {adapter.name}")
        service = cast(AnyDispatchService, _DispatcherServiceBridge(adapter.emit_dispatch))
        _, key = self.dispatcher.register(service)
        adapter._set_key(key)
        self._service_keys[adapter.name] = key
        return cast(RegistryAdapter[..., Any, Any], adapter)

    def _spawn_handler(self, fn: Callable[..., Any], event: Any) -> None:
        async def run_handler() -> None:
            args = _bind_supported_signature(fn, (event,), ())
            await _await_if_needed(fn(*args))

        task = asyncio.create_task(run_handler())
        self._background_tasks.add(task)

        def finalize(done_task: asyncio.Task[None]) -> None:
            self._background_tasks.discard(done_task)
            if done_task.cancelled():
                return
            error = done_task.exception()
            if error is not None:
                logger.exception("Event handler failed.", exc_info=error)

        task.add_done_callback(finalize)
