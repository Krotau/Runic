from __future__ import annotations

import asyncio
import inspect
import logging
from dataclasses import asdict, dataclass, field, is_dataclass
from enum import Enum
from typing import Any, AsyncIterator, Awaitable, Callable, Generic, TypeVar, cast
from uuid import uuid4

from .events import Event, EventBus
from .requests import DefaultError
from .result import Err, Ok, Result


TEvent = TypeVar("TEvent")
JobWork = Callable[["JobContext[TEvent]"], Awaitable[Any] | Any]
logger = logging.getLogger(__name__)


class JobStatus(str, Enum):
    """Closed set of states for tracked jobs."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(slots=True)
class JobRecord:
    """Mutable in-memory state for a tracked job."""

    job_id: str
    status: JobStatus = JobStatus.PENDING
    progress: float = 0.0
    logs: list[str] = field(default_factory=list)
    result: Any | None = None
    error: str | None = None


@dataclass(slots=True)
class JobLog:
    """Typed log payload emitted by the job runtime."""

    job_id: str
    message: str


@dataclass(slots=True)
class JobStatusEvent:
    """Typed status payload emitted by the job runtime."""

    job_id: str
    status: str
    progress: float
    result: Any | None = None
    error: str | None = None


def _error_message(error: object) -> str:
    """Normalize error payloads into a human-readable message."""

    if hasattr(error, "message") and isinstance(getattr(error, "message"), str):
        return getattr(error, "message")
    return str(error)


def _result_payload(value: object) -> Any | None:
    """Convert stored job results into adapter-friendly data."""

    if is_dataclass(value) and not isinstance(value, type):
        return asdict(cast(Any, value))
    if hasattr(value, "__dict__"):
        return dict(value.__dict__)
    return value


class JobContext(Generic[TEvent]):
    """Runtime helpers exposed to job implementations."""

    def __init__(
        self,
        job_id: str,
        bus: EventBus[TEvent],
        log_bus: EventBus[JobLog],
        record: JobRecord,
        data: TEvent | None = None,
    ) -> None:
        self.job_id = job_id
        self.bus = bus
        self._log_bus = log_bus
        self.record = record
        self.data = data

    async def emit(self, name: str, data: TEvent) -> None:
        """Publish a typed event on behalf of the running job."""

        await self.bus.publish(Event(name=name, data=data))

    async def log(self, message: str) -> JobLog:
        """Append a log line to the running job record."""

        self.record.logs.append(message)
        logger.info("Job %s: %s", self.job_id, message)
        payload = JobLog(job_id=self.job_id, message=message)
        await self._log_bus.publish(Event(name="job_log", data=payload))
        return payload

    async def progress(self, value: float) -> None:
        """Clamp and store job progress between `0.0` and `1.0`."""

        self.record.progress = max(0.0, min(1.0, value))


class JobManager(Generic[TEvent]):
    """Start, inspect, and cancel in-process background jobs."""

    def __init__(self, bus: EventBus[TEvent]) -> None:
        self._bus = bus
        self._status_bus: EventBus[JobStatusEvent] = EventBus(JobStatusEvent)
        self._log_bus: EventBus[JobLog] = EventBus(JobLog)
        self._records: dict[str, JobRecord] = {}
        self._futures: dict[str, asyncio.Future[Any]] = {}

    def get_status(self, job_id: str) -> Result[JobRecord, DefaultError]:
        """Return the current record for `job_id` or an explicit lookup error."""

        record = self._records.get(job_id)
        if record is None:
            return Err(DefaultError(message=f"Unknown job: {job_id}", code="job_not_found"))
        return Ok(record)

    def status_events(self) -> AsyncIterator[Event[JobStatusEvent]]:
        """Create an async iterator for future job status updates."""

        return self._status_bus.subscribe()

    def log_events(self) -> AsyncIterator[Event[JobLog]]:
        """Create an async iterator for future job log messages."""

        return self._log_bus.subscribe()

    async def start(self, work: JobWork[TEvent], data: TEvent | None = None) -> str:
        """Schedule background work and return the assigned job id."""

        job_id = str(uuid4())
        record = JobRecord(job_id=job_id)
        self._records[job_id] = record
        ctx = JobContext(job_id=job_id, bus=self._bus, log_bus=self._log_bus, record=record, data=data)

        async def publish_status() -> None:
            await self._status_bus.publish(
                Event(
                    name="job_status",
                    data=JobStatusEvent(
                        job_id=job_id,
                        status=record.status.value,
                        progress=record.progress,
                        result=_result_payload(record.result),
                        error=record.error,
                    ),
                )
            )

        async def run_job() -> None:
            record.status = JobStatus.RUNNING
            await publish_status()
            try:
                maybe_result = work(ctx)
                result = await maybe_result if inspect.isawaitable(maybe_result) else maybe_result
            except asyncio.CancelledError:
                record.status = JobStatus.CANCELLED
                await publish_status()
                raise
            except Exception as exc:
                record.status = JobStatus.FAILED
                record.error = str(exc)
                await publish_status()
                raise
            else:
                match result:
                    case Err(error=error):
                        record.status = JobStatus.FAILED
                        record.error = _error_message(error)
                        record.result = None
                        await publish_status()
                    case Ok(value=value):
                        record.status = JobStatus.SUCCEEDED
                        record.result = _result_payload(value)
                        if record.progress < 1.0:
                            record.progress = 1.0
                        await publish_status()
                    case _:
                        record.status = JobStatus.SUCCEEDED
                        record.result = _result_payload(result)
                        if record.progress < 1.0:
                            record.progress = 1.0
                        await publish_status()

        future = asyncio.create_task(run_job())

        def finalize_job(done_future: asyncio.Future[Any]) -> None:
            self._futures.pop(job_id, None)
            if done_future.cancelled():
                logger.info("Job %s cancelled.", job_id)
                return

            exception = done_future.exception()
            if exception is not None:
                logger.exception("Job %s crashed.", job_id, exc_info=exception)
                return

            finished_record = self._records.get(job_id)
            if finished_record is None:
                return
            if finished_record.status is JobStatus.FAILED:
                logger.error("Job %s failed: %s", job_id, finished_record.error or "unknown error")
            elif finished_record.status is JobStatus.SUCCEEDED:
                logger.info("Job %s completed successfully.", job_id)

        future.add_done_callback(finalize_job)
        self._futures[job_id] = future
        return job_id

    async def stop(self, job_id: str) -> bool:
        """Cancel a running job if it is still in flight."""

        future = self._futures.get(job_id)
        record = self._records.get(job_id)
        if future is None or record is None:
            return False
        if future.done():
            return False
        future.cancel()
        try:
            await future
        except asyncio.CancelledError:
            pass
        record.status = JobStatus.CANCELLED
        return True
