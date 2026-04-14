from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine, MutableMapping
from typing import Any, Protocol


type JobRunner = Callable[[], Coroutine[Any, Any, None]]


class TaskBackend(Protocol):
    """Backend contract for submitting background work and sharing task state."""

    @property
    def shared(self) -> MutableMapping[str, Any]:
        """Mutable state shared across tasks submitted to this backend."""
        ...

    def submit(self, job_id: str, runner: JobRunner) -> asyncio.Task[None]:
        """Schedule background work and return the created task."""
        ...


class InMemoryTaskBackend:
    """Small backend that runs tasks locally and shares a mutable in-memory store."""

    def __init__(self, shared: MutableMapping[str, Any] | None = None) -> None:
        self._shared = shared if shared is not None else {}

    @property
    def shared(self) -> MutableMapping[str, Any]:
        return self._shared

    def submit(self, job_id: str, runner: JobRunner) -> asyncio.Task[None]:
        return asyncio.create_task(runner(), name=f"runic-job:{job_id}")
