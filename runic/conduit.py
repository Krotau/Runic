from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import MutableMapping
from dataclasses import asdict, dataclass, field, is_dataclass
from enum import Enum
from typing import Any, AsyncIterator, Awaitable, Callable, Generic, TypeVar, cast
from uuid import uuid4

from .spellbooks import InMemorySpellBook, SpellBook
from .errors import DefaultError
from .events import Event, EventBus
from .result import Err, Ok, Pending, Result


TEvent = TypeVar("TEvent")
SpellWork = Callable[["SpellContext[TEvent]"], Awaitable[Any] | Any]
logger = logging.getLogger(__name__)


class SpellStatus(str, Enum):
    """Closed set of states for tracked spells."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(slots=True)
class SpellRecord:
    """Mutable in-memory state for a tracked spell."""

    spell_id: str
    status: SpellStatus = SpellStatus.PENDING
    progress: float = 0.0
    logs: list[str] = field(default_factory=list)
    result: Any | None = None
    error: str | None = None


@dataclass(slots=True)
class SpellLog:
    """Typed log payload emitted by the conduit runtime."""

    spell_id: str
    message: str


@dataclass(slots=True)
class SpellStatusEvent:
    """Typed status payload emitted by the conduit runtime."""

    spell_id: str
    status: str
    progress: float
    result: Any | None = None
    error: str | None = None


def _error_message(error: object) -> str:
    """Normalize error payloads into a human-readable message."""

    message = getattr(error, "message", None)
    match message:
        case str() as text:
            return text
        case _:
            return str(error)


def _result_payload(value: object) -> Any | None:
    """Convert stored spell results into adapter-friendly data."""

    match value:
        case _ if _is_dataclass_instance(value):
            return asdict(cast(Any, value))
        case _ if hasattr(value, "__dict__"):
            return dict(value.__dict__)
        case _:
            return value


def _is_dataclass_instance(value: object) -> bool:
    # Dataclasses are serialized as plain data, but dataclass *classes* should
    # stay out of the payload path.
    match value:
        case type():
            return False
        case _:
            return is_dataclass(value)


class SpellContext(Generic[TEvent]):
    """Runtime helpers exposed to spell implementations."""

    def __init__(
        self,
        spell_id: str,
        bus: EventBus[TEvent],
        log_bus: EventBus[SpellLog],
        record: SpellRecord,
        data: TEvent | None = None,
        shared: MutableMapping[str, Any] | None = None,
    ) -> None:
        self.spell_id = spell_id
        self.bus = bus
        self._log_bus = log_bus
        self.record = record
        self.data = data
        self.shared = shared if shared is not None else {}

    async def emit(self, name: str, data: TEvent) -> None:
        """Publish a typed event on behalf of the running spell."""

        await self.bus.publish(Event(name=name, data=data))

    async def log(self, message: str) -> SpellLog:
        """Append a log line to the running spell record."""

        self.record.logs.append(message)
        logger.info("Spell %s: %s", self.spell_id, message)
        payload = SpellLog(spell_id=self.spell_id, message=message)
        await self._log_bus.publish(Event(name="spell_log", data=payload))
        return payload

    async def progress(self, value: float) -> None:
        """Clamp and store spell progress between `0.0` and `1.0`."""

        self.record.progress = max(0.0, min(1.0, value))


class Conduit(Generic[TEvent]):
    """Invoke, inspect, and cancel in-process background spells."""

    def __init__(self, bus: EventBus[TEvent], spellbook: SpellBook | None = None) -> None:
        self._bus = bus
        self.spellbook = spellbook or InMemorySpellBook()
        self._status_bus: EventBus[SpellStatusEvent] = EventBus(SpellStatusEvent)
        self._log_bus: EventBus[SpellLog] = EventBus(SpellLog)
        self._records: dict[str, SpellRecord] = {}
        self._futures: dict[str, asyncio.Future[Any]] = {}

    def get_status(self, spell_id: str) -> Result[SpellRecord, DefaultError]:
        """Return the current record for `spell_id` or an explicit lookup error."""

        match self._records.get(spell_id):
            case None:
                return Err(DefaultError(message=f"Unknown spell: {spell_id}", code="spell_not_found"))
            case record:
                return Ok(record)

    def get_spell_result(self, spell_id: str) -> Result[Any, DefaultError]:
        """Return the finished spell payload or a pending result.

        This is a convenience accessor over `get_status(...)` for callers that
        only care about the final spell outcome rather than the full record.
        A succeeded spell returns its stored payload directly, failures remain
        explicit `Err(DefaultError(...))`, and unfinished spells report
        `Pending()` until the spell settles.
        """

        match self.get_status(spell_id):
            case Err(error=error):
                return Err(error)
            case Ok(value=record):
                match record.status:
                    case SpellStatus.SUCCEEDED:
                        return Ok(record.result)
                    case SpellStatus.FAILED:
                        return Err(DefaultError(message=record.error or "Spell failed", code="spell_failed"))
                    case SpellStatus.CANCELLED:
                        return Err(DefaultError(message=f"Spell cancelled: {spell_id}", code="spell_cancelled"))
                    case SpellStatus.PENDING | SpellStatus.RUNNING:
                        return Pending()

        raise AssertionError("Unreachable spell result branch")

    def status_events(self) -> AsyncIterator[Event[SpellStatusEvent]]:
        """Create an async iterator for future spell status updates."""

        return self._status_bus.subscribe()

    def log_events(self) -> AsyncIterator[Event[SpellLog]]:
        """Create an async iterator for future spell log messages."""

        return self._log_bus.subscribe()

    async def invoke(self, work: SpellWork[TEvent], data: TEvent | None = None) -> str:
        """Schedule background work and return the assigned spell id."""

        spell_id = str(uuid4())
        record = SpellRecord(spell_id=spell_id)
        self._records[spell_id] = record
        ctx = SpellContext(
            spell_id=spell_id,
            bus=self._bus,
            log_bus=self._log_bus,
            record=record,
            data=data,
            shared=self.spellbook.shared,
        )

        async def publish_status() -> None:
            await self._status_bus.publish(
                Event(
                    name="spell_status",
                    data=SpellStatusEvent(
                        spell_id=spell_id,
                        status=record.status.value,
                        progress=record.progress,
                        result=_result_payload(record.result),
                        error=record.error,
                    ),
                )
            )

        async def run_spell() -> None:
            record.status = SpellStatus.RUNNING
            await publish_status()
            try:
                maybe_result = work(ctx)
                result = await maybe_result if inspect.isawaitable(maybe_result) else maybe_result
            except asyncio.CancelledError:
                record.status = SpellStatus.CANCELLED
                await publish_status()
                raise
            except Exception as exc:
                record.status = SpellStatus.FAILED
                record.error = str(exc)
                await publish_status()
                raise
            else:
                # A returned Result acts as an explicit spell outcome signal.
                match result:
                    case Err(error=error):
                        record.status = SpellStatus.FAILED
                        record.error = _error_message(error)
                        record.result = None
                        await publish_status()
                    case Ok(value=value):
                        record.status = SpellStatus.SUCCEEDED
                        record.result = _result_payload(value)
                        if record.progress < 1.0:
                            record.progress = 1.0
                        await publish_status()
                    case _:
                        record.status = SpellStatus.SUCCEEDED
                        record.result = _result_payload(result)
                        if record.progress < 1.0:
                            record.progress = 1.0
                        await publish_status()

        future = self.spellbook.submit(spell_id, run_spell)

        def finalize_spell(done_future: asyncio.Future[Any]) -> None:
            self._futures.pop(spell_id, None)
            if done_future.cancelled():
                logger.info("Spell %s cancelled.", spell_id)
                return

            exception = done_future.exception()
            if exception is not None:
                logger.exception("Spell %s crashed.", spell_id, exc_info=exception)
                return

            finished_record = self._records.get(spell_id)
            if finished_record is None:
                return
            match finished_record.status:
                case SpellStatus.FAILED:
                    logger.error("Spell %s failed: %s", spell_id, finished_record.error or "unknown error")
                case SpellStatus.SUCCEEDED:
                    logger.info("Spell %s completed successfully.", spell_id)

        future.add_done_callback(finalize_spell)
        self._futures[spell_id] = future
        return spell_id

    async def stop(self, spell_id: str) -> bool:
        """Cancel a running spell if it is still in flight."""

        match (self._futures.get(spell_id), self._records.get(spell_id)):
            case (None, _) | (_, None):
                return False
            case (future, _) if future.done():
                return False
            case (future, record):
                future.cancel()
                try:
                    await future
                except asyncio.CancelledError:
                    pass
                record.status = SpellStatus.CANCELLED
                return True
