from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any, Generic, TypeVar, cast, overload

from .conduit import SpellContext
from .errors import DuplicateRegistrationError


if TYPE_CHECKING:
    from .runtime import Runic


TSpellData = TypeVar("TSpellData")
TSpellResult = TypeVar("TSpellResult")

type SpellResult[TSpellResult] = TSpellResult | Awaitable[TSpellResult]


def _infer_spell_name(name: str | None, fn: Callable[..., Any]) -> str:
    resolved = name or fn.__name__
    if not resolved:
        raise ValueError("Registration name could not be inferred")
    return resolved


def _bind_supported_signature(fn: Callable[..., Any], *candidates: tuple[Any, ...]) -> tuple[Any, ...]:
    signature = inspect.signature(fn)
    for args in candidates:
        try:
            signature.bind(*args)
        except TypeError:
            continue
        return args
    raise TypeError(f"Unsupported callable signature for {fn.__name__}")


def _spell_signature_error(fn: Callable[..., Any]) -> TypeError:
    signature = inspect.signature(fn)
    return TypeError(
        f"Unsupported spell signature for {fn.__name__}{signature}. "
        "Supported spell forms are fn(), fn(req), and fn(ctx, req)."
    )


def _validate_spell_signature(fn: Callable[..., Any]) -> None:
    signature = inspect.signature(fn)
    parameters = tuple(signature.parameters.values())

    if any(parameter.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD) for parameter in parameters):
        raise _spell_signature_error(fn)

    positional = [
        parameter
        for parameter in parameters
        if parameter.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
    ]
    keyword_only = [parameter for parameter in parameters if parameter.kind is inspect.Parameter.KEYWORD_ONLY]

    if len(positional) > 2:
        raise _spell_signature_error(fn)

    if any(parameter.default is inspect.Signature.empty for parameter in keyword_only):
        raise _spell_signature_error(fn)

    for args in ((), (object(),), (object(), object())):
        try:
            signature.bind(*args)
        except TypeError:
            continue
        return

    raise _spell_signature_error(fn)


async def _await_if_needed(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


class _FunctionSpellAdapter:
    """Wrap a plain callable so it matches the conduit work shape."""

    def __init__(self, fn: Callable[..., Any]) -> None:
        _validate_spell_signature(fn)
        self._fn = fn

    async def __call__(self, ctx: SpellContext[Any]) -> Any:
        args = _bind_supported_signature(self._fn, (ctx, ctx.data), (ctx.data,), ())
        return await _await_if_needed(self._fn(*args))


class _SpellDecorator(Generic[TSpellData]):
    """Decorator object with overloads for the supported spell signatures."""

    def __init__(self, runtime: Runic, name: str | None, message_type: type[TSpellData] | None) -> None:
        self._runtime = runtime
        self._name = name
        self._message_type = message_type

    @overload
    def __call__(self, fn: Callable[[], SpellResult[TSpellResult]]) -> Callable[[], SpellResult[TSpellResult]]: ...

    @overload
    def __call__(
        self, fn: Callable[[TSpellData], SpellResult[TSpellResult]]
    ) -> Callable[[TSpellData], SpellResult[TSpellResult]]: ...

    @overload
    def __call__(
        self, fn: Callable[[SpellContext[TSpellData], TSpellData], SpellResult[TSpellResult]]
    ) -> Callable[[SpellContext[TSpellData], TSpellData], SpellResult[TSpellResult]]: ...

    def __call__(self, fn: Callable[..., Any]) -> Callable[..., Any]:
        if self._message_type is not None:
            if self._message_type in self._runtime._typed_spells:
                raise DuplicateRegistrationError(f"Spell already registered for type: {self._message_type.__name__}")
            self._runtime._typed_spells[self._message_type] = _FunctionSpellAdapter(fn)
            return fn

        resolved = _infer_spell_name(self._name, fn)
        if resolved in self._runtime._spells:
            raise DuplicateRegistrationError(f"Spell already registered: {resolved}")
        self._runtime._spells[resolved] = _FunctionSpellAdapter(fn)
        return fn


__all__ = ["SpellResult", "_FunctionSpellAdapter", "_SpellDecorator"]
