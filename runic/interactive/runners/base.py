from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from runic import DefaultError, Result

from ..models import ChatMessage, InstalledModel, ModelProvider, ModelReference


@dataclass(frozen=True, slots=True)
class RunnerCapability:
    provider: ModelProvider
    can_install: bool = True
    can_chat: bool = True


@runtime_checkable
class RunnerContext(Protocol):
    async def log(self, message: str) -> object: ...

    async def progress(self, value: float) -> object: ...


class RunnerChatError(RuntimeError):
    def __init__(self, error: DefaultError) -> None:
        super().__init__(error.message)
        self.error = error


@runtime_checkable
class ModelRunner(Protocol):
    name: str
    capabilities: tuple[RunnerCapability, ...]

    async def is_available(self) -> bool: ...

    async def install_runner(self) -> Result[str, DefaultError]: ...

    async def install_model(
        self, reference: ModelReference, context: RunnerContext
    ) -> Result[InstalledModel, DefaultError]: ...

    async def list_models(self) -> Result[list[InstalledModel], DefaultError]: ...

    def chat(self, model: str, messages: tuple[ChatMessage, ...]) -> AsyncIterator[str]: ...
