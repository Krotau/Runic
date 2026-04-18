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


_TERMINAL_SPELL_STATUSES = {SpellStatus.SUCCEEDED, SpellStatus.FAILED, SpellStatus.CANCELLED}
_TERMINAL_SPELL_STATUS_VALUES = {status.value for status in _TERMINAL_SPELL_STATUSES}


@dataclass(frozen=True, slots=True)
class SpellRetryPolicy:
    """Retry behavior for spell execution.

    `max_attempts` counts the initial run plus any retries. A value of `1`
    disables retries while still allowing the policy object to document intent.
    """

    max_attempts: int = 1
    delay: float = 0.0
    backoff_factor: float = 1.0
    retry_on_err: bool = True
    retry_on_exception: bool = True

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("Spell retry max_attempts must be at least 1")
        if self.delay < 0.0:
            raise ValueError("Spell retry delay must be non-negative")
        if self.backoff_factor < 1.0:
            raise ValueError("Spell retry backoff_factor must be at least 1.0")

    def delay_for_retry(self, attempt: int) -> float:
        """Return the backoff delay after a failed `attempt`."""

        if attempt < 1:
            raise ValueError("Retry attempts are 1-based")
        return self.delay * (self.backoff_factor ** (attempt - 1))


@dataclass(slots=True)
class SpellRecord:
    """Mutable in-memory state for a tracked spell."""

    spell_id: str
    status: SpellStatus = SpellStatus.PENDING
    progress: float = 0.0
    logs: list[str] = field(default_factory=list)
    result: Any | None = None
    error: str | None = None
    attempt: int = 0
    max_attempts: int = 1
    idempotency_key: str | None = None


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
    attempt: int = 0
    max_attempts: int = 1
    idempotency_key: str | None = None


def _status_payload(record: SpellRecord) -> SpellStatusEvent:
    """Convert a spell record into a status payload snapshot."""

    return SpellStatusEvent(
        spell_id=record.spell_id,
        status=record.status.value,
        progress=record.progress,
        result=_result_payload(record.result),
        error=record.error,
        attempt=record.attempt,
        max_attempts=record.max_attempts,
        idempotency_key=record.idempotency_key,
    )


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
        attempt: int = 1,
        max_attempts: int = 1,
        idempotency_key: str | None = None,
    ) -> None:
        self.spell_id = spell_id
        self.bus = bus
        self._log_bus = log_bus
        self.record = record
        self.data = data
        self.shared = shared if shared is not None else {}
        self.attempt = attempt
        self.max_attempts = max_attempts
        self.idempotency_key = idempotency_key

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
        self._idempotency_keys: dict[str, str] = {}

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

    def watch(self, spell_id: str) -> AsyncIterator[SpellStatusEvent]:
        """Yield the current status snapshot and future updates for `spell_id`.

        The iterator starts with the current in-memory record and then streams
        future status changes until the spell reaches a terminal state.
        Unknown spell ids raise `LookupError` when iteration starts.
        """

        async def iterator() -> AsyncIterator[SpellStatusEvent]:
            subscriber = self.status_events()
            try:
                match self.get_status(spell_id):
                    case Err(error=error):
                        raise LookupError(error.message)
                    case Ok(value=record):
                        snapshot = _status_payload(record)

                yield snapshot
                if record.status in _TERMINAL_SPELL_STATUSES:
                    return

                async for event in subscriber:
                    payload = event.data
                    if payload.spell_id != spell_id:
                        continue
                    yield payload
                    if payload.status in _TERMINAL_SPELL_STATUS_VALUES:
                        return
            finally:
                await subscriber.aclose()

        return iterator()

    async def wait(self, spell_id: str, timeout: float | None = None) -> Result[Any, DefaultError]:
        """Wait for a spell to settle and return its normalized final result."""

        current = self.get_spell_result(spell_id)
        if not isinstance(current, Pending):
            return current

        future = self._futures.get(spell_id)
        if future is None:
            return self.get_spell_result(spell_id)

        try:
            await asyncio.wait_for(asyncio.shield(future), timeout=timeout)
        except asyncio.TimeoutError:
            raise
        except asyncio.CancelledError:
            if not future.cancelled():
                raise
        except Exception:
            # Spell exceptions are already reflected into the spell record.
            pass

        return self.get_spell_result(spell_id)

    async def wait_for_status(
        self,
        spell_id: str,
        status: SpellStatus,
        timeout: float | None = None,
    ) -> Result[SpellRecord, DefaultError]:
        """Wait until a spell reaches `status` or settles in a terminal state."""

        async def await_status() -> Result[SpellRecord, DefaultError]:
            subscriber = self.status_events()
            try:
                match self.get_status(spell_id):
                    case Err(error=error):
                        return Err(error)
                    case Ok(value=record):
                        if record.status is status or record.status in _TERMINAL_SPELL_STATUSES:
                            return Ok(record)

                async for event in subscriber:
                    if event.data.spell_id != spell_id:
                        continue
                    match self.get_status(spell_id):
                        case Err(error=error):
                            return Err(error)
                        case Ok(value=record):
                            if record.status is status or record.status in _TERMINAL_SPELL_STATUSES:
                                return Ok(record)
            finally:
                await subscriber.aclose()

            raise AssertionError("Unreachable wait_for_status branch")

        return await asyncio.wait_for(await_status(), timeout=timeout)

    def _existing_spell_id(self, idempotency_key: str | None) -> str | None:
        if idempotency_key is None:
            return None
        spell_id = self._idempotency_keys.get(idempotency_key)
        if spell_id is None:
            return None
        if spell_id not in self._records:
            self._idempotency_keys.pop(idempotency_key, None)
            return None
        return spell_id

    async def invoke(
        self,
        work: SpellWork[TEvent],
        data: TEvent | None = None,
        *,
        delay: float = 0.0,
        retry: SpellRetryPolicy | None = None,
        idempotency_key: str | None = None,
    ) -> str:
        """Schedule background work and return the assigned spell id."""

        if delay < 0.0:
            raise ValueError("Spell delay must be non-negative")

        existing_spell_id = self._existing_spell_id(idempotency_key)
        if existing_spell_id is not None:
            return existing_spell_id

        retry_policy = retry or SpellRetryPolicy()
        spell_id = str(uuid4())
        record = SpellRecord(
            spell_id=spell_id,
            max_attempts=retry_policy.max_attempts,
            idempotency_key=idempotency_key,
        )
        self._records[spell_id] = record
        if idempotency_key is not None:
            self._idempotency_keys[idempotency_key] = spell_id
        ctx = SpellContext(
            spell_id=spell_id,
            bus=self._bus,
            log_bus=self._log_bus,
            record=record,
            data=data,
            shared=self.spellbook.shared,
            attempt=0,
            max_attempts=retry_policy.max_attempts,
            idempotency_key=idempotency_key,
        )

        async def publish_status() -> None:
            await self._status_bus.publish(
                Event(
                    name="spell_status",
                    data=_status_payload(record),
                )
            )

        async def wait_in_pending(seconds: float, *, publish_pending: bool) -> None:
            if publish_pending:
                record.status = SpellStatus.PENDING
                await publish_status()
            if seconds > 0.0:
                await asyncio.sleep(seconds)

        async def run_spell() -> None:
            try:
                if delay > 0.0:
                    await wait_in_pending(delay, publish_pending=True)

                while True:
                    record.attempt += 1
                    record.status = SpellStatus.RUNNING
                    record.progress = 0.0
                    record.result = None
                    record.error = None
                    ctx.attempt = record.attempt
                    ctx.max_attempts = record.max_attempts
                    await publish_status()
                    try:
                        maybe_result = work(ctx)
                        result = await maybe_result if inspect.isawaitable(maybe_result) else maybe_result
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        record.error = str(exc)
                        if not (retry_policy.retry_on_exception and record.attempt < retry_policy.max_attempts):
                            record.status = SpellStatus.FAILED
                            await publish_status()
                            raise
                        await wait_in_pending(
                            retry_policy.delay_for_retry(record.attempt),
                            publish_pending=True,
                        )
                        continue
                    else:
                        # A returned Result acts as an explicit spell outcome signal.
                        match result:
                            case Err(error=error):
                                record.error = _error_message(error)
                                record.result = None
                                if not (retry_policy.retry_on_err and record.attempt < retry_policy.max_attempts):
                                    record.status = SpellStatus.FAILED
                                    await publish_status()
                                    return
                                await wait_in_pending(
                                    retry_policy.delay_for_retry(record.attempt),
                                    publish_pending=True,
                                )
                                continue
                            case Ok(value=value):
                                record.status = SpellStatus.SUCCEEDED
                                record.error = None
                                record.result = _result_payload(value)
                                if record.progress < 1.0:
                                    record.progress = 1.0
                                await publish_status()
                                return
                            case _:
                                record.status = SpellStatus.SUCCEEDED
                                record.error = None
                                record.result = _result_payload(result)
                                if record.progress < 1.0:
                                    record.progress = 1.0
                                await publish_status()
                                return
            except asyncio.CancelledError:
                record.status = SpellStatus.CANCELLED
                await publish_status()
                raise

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
