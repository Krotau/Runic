from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine, MutableMapping
from typing import Any, Protocol


type SpellRunner = Callable[[], Coroutine[Any, Any, None]]


class SpellBook(Protocol):
    """Contract for submitting background spell work and sharing spell state."""

    @property
    def shared(self) -> MutableMapping[str, Any]:
        """Mutable state shared across spells submitted to this spellbook."""
        ...

    def submit(self, spell_id: str, runner: SpellRunner) -> asyncio.Task[None]:
        """Schedule spell work and return the created task."""
        ...


class InMemorySpellBook:
    """Small spellbook that runs spells locally with a shared in-memory store."""

    def __init__(self, shared: MutableMapping[str, Any] | None = None) -> None:
        # Reuse a caller-provided mapping so every submitted spell sees the same store.
        self._shared = {} if shared is None else shared

    @property
    def shared(self) -> MutableMapping[str, Any]:
        return self._shared

    def submit(self, spell_id: str, runner: SpellRunner) -> asyncio.Task[None]:
        task_name = f"runic-spell:{spell_id}"
        return asyncio.create_task(runner(), name=task_name)


# Backward-compatible aliases while the public API shifts to spellbook naming.
TaskBackend = SpellBook
InMemoryTaskBackend = InMemorySpellBook

__all__ = ["SpellBook", "InMemorySpellBook", "TaskBackend", "InMemoryTaskBackend"]
