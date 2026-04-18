from __future__ import annotations

import asyncio
import inspect
import logging
from collections import defaultdict
from collections.abc import Awaitable, Callable
from functools import update_wrapper
from typing import Any, Generic, ParamSpec, Protocol, TypeVar, cast, get_type_hints, overload, runtime_checkable

from .conduit import Conduit, SpellContext, SpellRetryPolicy
from .conjurer import AnyConjurable, Conjurable, Conjurer, ConjurerKey, create_conjurer
from .errors import (
    AmbiguousQueryError,
    DefaultError,
    DuplicateRegistrationError,
    ServiceNotFoundError,
    TaskNotFoundError,
)
from .events import Event, EventBus
from .handlers import Handler, SupportsAsk, SupportsInvoke, _ServiceMethodAdapter
from .requests import Command, Query, Request
from .result import Err, Ok, Result
from .spellbooks import SpellBook
from .spells import _FunctionSpellAdapter, _SpellDecorator


TEvent = TypeVar("TEvent")
TData = TypeVar("TData")
TResult = TypeVar("TResult", covariant=True)
TError = TypeVar("TError", covariant=True)
TService = TypeVar("TService")
TSpellData = TypeVar("TSpellData")
P = ParamSpec("P")
logger = logging.getLogger(__name__)

type ServiceResult[TResult, TError] = Result[TResult, TError] | Awaitable[Result[TResult, TError]]
type QueryCallable = Callable[[Any], ServiceResult[Any, Any]]
type CommandCallable = Callable[[Any], ServiceResult[Any, Any]]


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

    def get_key(self) -> ConjurerKey[AnyConjurable]:
        """Return the conjurer key assigned at registration time."""
        ...


class _RegistryAdapter(Generic[P, TResult, TError]):
    """Concrete homogeneous adapter used for all registered services."""

    def __init__(
        self,
        name: str,
        invoke: Callable[P, ServiceResult[TResult, TError]],
        conjurer_emit: Callable[[Any], ServiceResult[TResult, TError]],
        wrapped: Callable[..., Any] | None = None,
    ) -> None:
        self.name = name
        self._invoke = invoke
        self._conjurer_emit = conjurer_emit
        self._key: ConjurerKey[AnyConjurable] | None = None
        if wrapped is not None:
            update_wrapper(self, wrapped)

    def _set_key(self, key: ConjurerKey[AnyConjurable]) -> None:
        self._key = key

    def get_key(self) -> ConjurerKey[AnyConjurable]:
        if self._key is None:
            raise RuntimeError("Registry adapter has not been registered yet")
        return self._key

    async def emit(self, *args: P.args, **kwargs: P.kwargs) -> Result[TResult, TError]:
        return await _await_if_needed(self._invoke(*args, **kwargs))

    async def __call__(self, *args: P.args, **kwargs: P.kwargs) -> Result[TResult, TError]:
        return await self.emit(*args, **kwargs)

    async def emit_conjured(self, data: Any) -> Result[TResult, TError]:
        return await _await_if_needed(self._conjurer_emit(data))


class _RegisterDecorator:
    """Decorator object with overloads that preserve function signatures."""

    def __init__(self, runtime: Runic, name: str | None) -> None:
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
                conjurer_emit=lambda _data: fn(),
                wrapped=fn,
            )
        elif parameter_count == 1:
            adapter = _RegistryAdapter(
                name=resolved,
                invoke=fn,
                conjurer_emit=fn,
                wrapped=fn,
            )
        else:
            raise TypeError(f"Unsupported service signature for {fn.__name__}")
        
        return self._runtime._register_adapter(adapter)


class _QueryDecorator:
    """Decorator object with overloads that preserve typed query signatures."""

    def __init__(self, runtime: Runic, message_type: type[Any] | None) -> None:
        self._runtime = runtime
        self._message_type = message_type

    def __call__(self, fn: Callable[[TData], ServiceResult[TResult, TError]]) -> RegistryAdapter[[TData], TResult, TError]:
        resolved_type = self._message_type or _infer_annotated_message_type(fn)
        if resolved_type in self._runtime._decorated_query_types:
            raise DuplicateRegistrationError(f"Query already registered for type: {resolved_type.__name__}")
        adapter = cast(
            RegistryAdapter[[TData], TResult, TError],
            _RegisterDecorator(self._runtime, _type_key("query", resolved_type))(fn),
        )
        self._runtime._decorated_query_types.add(resolved_type)
        self._runtime._query_handlers[resolved_type].append(cast(QueryCallable, adapter.emit))
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
    match annotation:
        case inspect.Signature.empty:
            raise TypeError(f"Missing concrete message type annotation for {fn.__name__}")
        case type() as message_type:
            if message_type.__module__ == "typing":
                raise TypeError(f"Missing concrete message type annotation for {fn.__name__}")
            return message_type
        case _:
            raise TypeError(f"Missing concrete message type annotation for {fn.__name__}")


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


class _ConjurerServiceBridge:
    """Small bridge that exposes emit(data) for conjurer registration."""

    def __init__(self, emit: Callable[[Any], Awaitable[Any] | Any]) -> None:
        self._emit = emit

    def emit(self, data: Any) -> Awaitable[Any] | Any:
        return self._emit(data)


class Runic:
    """Small runtime facade that composes the bus, conjurer, and conduit."""

    def __init__(
        self,
        *,
        bus: EventBus[Any] | None = None,
        conjurer: Conjurer | None = None,
        conduit: Conduit[Any] | None = None,
        spellbook: SpellBook | None = None,
    ) -> None:
        if conduit is not None and spellbook is not None:
            raise ValueError("Pass either conduit or spellbook, not both")
        self.bus = bus or EventBus(object)
        self.conjurer = conjurer or create_conjurer()
        self.conduit = conduit or Conduit(self.bus, spellbook=spellbook)
        self._service_keys: dict[str, ConjurerKey[AnyConjurable]] = {}
        self._spells: dict[str, _FunctionSpellAdapter] = {}
        self._event_handlers: dict[str, list[Callable[..., Any]]] = defaultdict(list)
        self._query_handlers: dict[type[Any], list[QueryCallable]] = defaultdict(list)
        self._decorated_query_types: set[type[Any]] = set()
        self._command_handlers: dict[type[Any], CommandCallable] = {}
        self._typed_spells: dict[type[Any], _FunctionSpellAdapter] = {}
        self._typed_event_handlers: dict[type[Any], list[Callable[..., Any]]] = defaultdict(list)
        self._background_tasks: set[asyncio.Task[None]] = set()

    def _lookup_service_key(self, name: str) -> Result[ConjurerKey[AnyConjurable], ServiceNotFoundError]:
        # Keep resolution explicit so missing registrations stay a normal Result
        # branch instead of a nested if/raise path.
        match self._service_keys.get(name):
            case ConjurerKey() as key:
                return Ok(key)
            case _:
                return Err(ServiceNotFoundError(f"Unknown service: {name}"))

    def _lookup_spell(self, name: str) -> Result[_FunctionSpellAdapter, TaskNotFoundError]:
        # The same Result shape keeps spell lookup and spell invocation aligned.
        match self._spells.get(name):
            case _FunctionSpellAdapter() as spell:
                return Ok(spell)
            case _:
                return Err(TaskNotFoundError(f"Unknown spell: {name}"))

    def _lookup_typed_spell(self, payload_type: type[Any]) -> Result[_FunctionSpellAdapter, TaskNotFoundError]:
        # Typed spells use the concrete payload type as the lookup key.
        match self._typed_spells.get(payload_type):
            case _FunctionSpellAdapter() as spell:
                return Ok(spell)
            case _:
                return Err(TaskNotFoundError(f"Unknown spell type: {payload_type.__name__}"))

    @overload
    async def emit(self, topic: Any) -> None: ...

    @overload
    async def emit(self, topic: str, event: Any) -> None: ...

    async def emit(self, topic: str | Any, event: Any | None = None) -> None:
        """Broadcast a named or typed event to the bus and local handlers."""

        match topic:
            case str(topic_name):
                await self.bus.publish(Event(name=topic_name, data=event))
                for handler in self._event_handlers.get(topic_name, ()):
                    self._spawn_handler(handler, event)
            case _:
                payload = topic
                topic_name = _type_key("event", type(payload))
                await self.bus.publish(Event(name=topic_name, data=payload))
                for handler in self._typed_event_handlers.get(type(payload), ()):
                    self._spawn_handler(handler, payload)

    async def publish(self, request: Query[TResult, TError]) -> list[Result[TResult, TError]]:
        """Fan out a typed query to every registered handler for that query type."""

        handlers = self._query_handlers.get(type(request), [])
        results: list[Result[TResult, TError]] = []
        for handler in handlers:
            results.append(cast(Result[TResult, TError], await _await_if_needed(handler(request))))
        return results

    async def ask(self, request: Query[TResult, TError]) -> Result[TResult, TError]:
        """Invoke a typed request handler using the request object's concrete type."""

        handlers = self._query_handlers.get(type(request), [])
        if not handlers:
            raise ServiceNotFoundError(f"Unknown query type: {type(request).__name__}")
        if len(handlers) > 1:
            raise AmbiguousQueryError(f"More than one service is registered for query type: {type(request).__name__}")
        return cast(Result[TResult, TError], await _await_if_needed(handlers[0](request)))

    async def execute(self, command: Command[TResult, TError]) -> Result[TResult, TError]:
        """Invoke a typed command handler using the command object's concrete type."""

        handler = self._command_handlers.get(type(command))
        if handler is None:
            raise ServiceNotFoundError(f"Unknown command type: {type(command).__name__}")
        return cast(Result[TResult, TError], await _await_if_needed(handler(command)))

    async def call(self, name: str, payload: Any = None) -> Any:
        """Invoke a named service and return its reply."""

        match self._lookup_service_key(name):
            case Ok(value=key):
                handler = self.conjurer.retrieve(key)
                return await handler.emit(payload)
            case Err(error=error):
                raise error

    async def invoke(
        self,
        target: str | Any,
        payload: Any = None,
        *,
        delay: float = 0.0,
        retry: SpellRetryPolicy | None = None,
        idempotency_key: str | None = None,
    ) -> str:
        """Invoke a named or typed spell as tracked background work."""

        match target:
            case str(spell_name):
                match self._lookup_spell(spell_name):
                    case Ok(value=spell):
                        return await self.conduit.invoke(
                            spell,
                            data=payload,
                            delay=delay,
                            retry=retry,
                            idempotency_key=idempotency_key,
                        )
                    case Err(error=error):
                        raise error
            case _:
                typed_payload = target
                match self._lookup_typed_spell(type(typed_payload)):
                    case Ok(value=spell):
                        return await self.conduit.invoke(
                            spell,
                            data=typed_payload,
                            delay=delay,
                            retry=retry,
                            idempotency_key=idempotency_key,
                        )
                    case Err(error=error):
                        raise error

        raise AssertionError("Unreachable invoke branch")

    @overload
    async def cast(self, payload: Request[TResult, Any]) -> Result[TResult, DefaultError]: ...

    @overload
    async def cast(self, payload: Any) -> Result[Any, DefaultError]: ...

    async def cast(
        self,
        payload: Any,
        *,
        delay: float = 0.0,
        retry: SpellRetryPolicy | None = None,
        idempotency_key: str | None = None,
    ) -> Result[Any, DefaultError]:
        """Invoke a typed spell and await its finished result."""

        spell_id = await self.invoke(
            payload,
            delay=delay,
            retry=retry,
            idempotency_key=idempotency_key,
        )
        return await self._await_spell_result(spell_id)

    def on(self, topic: str | type[Any]) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """Register a sync or async handler for a topic name or event type."""

        def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
            match topic:
                case str(topic_name):
                    self._event_handlers[topic_name].append(fn)
                case _:
                    self._typed_event_handlers[topic].append(fn)
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

        match target:
            case None:
                return _QueryDecorator(self, None)
            case type() as message_type:
                return _QueryDecorator(self, message_type)
            case _ if callable(target):
                return _QueryDecorator(self, None)(target)
            case _:
                return _QueryDecorator(self, cast(type[Any] | None, target))

    @overload
    def conjure(self, name: TService) -> Handler[TService]: ...

    @overload
    def conjure(self, name: str | None = None) -> _RegisterDecorator: ...

    @overload
    def conjure(self, name: str, service: Conjurable[TData, TResult, TError]) -> RegistryAdapter[[TData], TResult, TError]: ...

    def conjure(
        self,
        name: str | object | None = None,
        service: AnyConjurable | None = None,
    ) -> _RegisterDecorator | RegistryAdapter[..., Any, Any] | Handler[Any]:
        """Conjure an object service, decorated function, or named service."""

        match service:
            case None:
                match name:
                    case None | str():
                        return _RegisterDecorator(self, cast(str | None, name))
                    case _:
                        return self._register_handler_service(name)
            case _:
                match name:
                    case str(service_name):
                        typed_service = cast(Conjurable[Any, Any, Any], service)
                        return self._register_adapter(
                            _RegistryAdapter(
                                name=service_name,
                                invoke=typed_service.emit,
                                conjurer_emit=typed_service.emit,
                                wrapped=typed_service.emit,
                            )
                        )
                    case _:
                        raise ValueError("A service name is required when conjuring a service object")

    @overload
    def spell(self, name: None = None) -> _SpellDecorator[Any]: ...

    @overload
    def spell(self, name: str) -> _SpellDecorator[Any]: ...

    @overload
    def spell(self, name: type[TSpellData]) -> _SpellDecorator[TSpellData]: ...

    def spell(self, name: str | type[TSpellData] | None = None) -> _SpellDecorator[Any] | _SpellDecorator[TSpellData]:
        """Register a named legacy spell or a typed spell request handler."""

        match name:
            case type() as message_type:
                return _SpellDecorator(self, None, message_type)
            case _:
                return _SpellDecorator(self, cast(str | None, name), None)

    def _register_adapter(self, adapter: _RegistryAdapter[Any, Any, Any]) -> RegistryAdapter[..., Any, Any]:
        if adapter.name in self._service_keys:
            raise DuplicateRegistrationError(f"Service already registered: {adapter.name}")
        service = cast(AnyConjurable, _ConjurerServiceBridge(adapter.emit_conjured))
        _, key = self.conjurer.conjure(service)
        adapter._set_key(key)
        self._service_keys[adapter.name] = key
        return cast(RegistryAdapter[..., Any, Any], adapter)

    def _register_handler_service(self, service: Any) -> Handler[Any]:
        query_adapter = self._build_service_method_adapter(service, "ask")
        command_adapter = self._build_service_method_adapter(service, "invoke")
        if query_adapter is None and command_adapter is None:
            raise TypeError("Registered services must define callable ask(query) and/or invoke(command) methods")
        if query_adapter is not None:
            self._query_handlers[query_adapter.message_type].append(cast(QueryCallable, query_adapter.call))
        if command_adapter is not None:
            if command_adapter.message_type in self._command_handlers:
                raise DuplicateRegistrationError(
                    f"Command already registered for type: {command_adapter.message_type.__name__}"
                )
            self._command_handlers[command_adapter.message_type] = cast(CommandCallable, command_adapter.call)
        return Handler(service, query_adapter=query_adapter, command_adapter=command_adapter)

    def _build_service_method_adapter(
        self,
        service: Any,
        method_name: str,
    ) -> _ServiceMethodAdapter[Any, Any, Any] | None:
        method = getattr(service, method_name, None)
        if method is None:
            return None
        if not callable(method):
            raise TypeError(f"Registered services must define a callable {method_name}(...) method")
        message_type = _infer_annotated_message_type(method)
        typed_method = cast(Callable[[Any], ServiceResult[Any, Any]], method)
        return _ServiceMethodAdapter(message_type, typed_method, _await_if_needed)

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

    async def _await_spell_result(self, spell_id: str) -> Result[Any, DefaultError]:
        # Delegate settling to the conduit so spell waiting stays event/future driven.
        return await self.conduit.wait(spell_id)

    def register(
        self,
        name: str | object | None = None,
        service: AnyConjurable | None = None,
    ) -> _RegisterDecorator | RegistryAdapter[..., Any, Any] | Handler[Any]:
        """Backward-compatible alias for `conjure(...)`."""

        conjure = cast(
            Callable[[str | object | None, AnyConjurable | None], _RegisterDecorator | RegistryAdapter[..., Any, Any] | Handler[Any]],
            self.conjure,
        )
        return conjure(name, service)

    def task(self, name: str | type[Any] | None = None) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """Backward-compatible alias for `spell(...)`."""

        return self.spell(name)

    async def dispatch(
        self,
        name: str,
        payload: Any = None,
        *,
        delay: float = 0.0,
        retry: SpellRetryPolicy | None = None,
        idempotency_key: str | None = None,
    ) -> str:
        """Backward-compatible alias for named `invoke(...)` calls."""

        return await self.invoke(
            name,
            payload,
            delay=delay,
            retry=retry,
            idempotency_key=idempotency_key,
        )

    async def start(
        self,
        payload: Any,
        *,
        delay: float = 0.0,
        retry: SpellRetryPolicy | None = None,
        idempotency_key: str | None = None,
    ) -> str:
        """Backward-compatible alias for typed `invoke(...)` calls."""

        return await self.invoke(
            payload,
            delay=delay,
            retry=retry,
            idempotency_key=idempotency_key,
        )
