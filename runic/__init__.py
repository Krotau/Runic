from __future__ import annotations

__version__ = "0.1.0"

from .dispatcher import DispatchService, Dispatcher, DispatcherHandler, DispatcherKey, create_dispatcher
from .events import Event, EventBus, create_bus
from .handlers import Handler, SupportsAsk, SupportsInvoke
from .jobs import JobContext, JobLog, JobManager, JobRecord, JobStatus, JobStatusEvent
from .requests import Command, DefaultError, Query, Request
from .result import Err, Ok, Result
from .backends import InMemoryTaskBackend, TaskBackend
from .runtime import (
    AmbiguousQueryError,
    DuplicateRegistrationError,
    RegistryAdapter,
    Runic,
    RunicError,
    ServiceNotFoundError,
    TaskNotFoundError,
)

__all__ = [
    "AmbiguousQueryError",
    "Command",
    "DefaultError",
    "DispatchService",
    "Dispatcher",
    "DispatcherHandler",
    "DispatcherKey",
    "Err",
    "Event",
    "EventBus",
    "Handler",
    "InMemoryTaskBackend",
    "JobContext",
    "JobLog",
    "JobManager",
    "JobRecord",
    "JobStatus",
    "JobStatusEvent",
    "Ok",
    "Query",
    "RegistryAdapter",
    "Request",
    "Result",
    "Runic",
    "RunicError",
    "ServiceNotFoundError",
    "SupportsAsk",
    "SupportsInvoke",
    "TaskNotFoundError",
    "TaskBackend",
    "DuplicateRegistrationError",
    "__version__",
    "create_bus",
    "create_dispatcher",
]
