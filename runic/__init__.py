from __future__ import annotations

__version__ = "0.1.0"

from .spellbooks import InMemorySpellBook, InMemoryTaskBackend, SpellBook, TaskBackend
from .conduit import Conduit, SpellContext, SpellLog, SpellRecord, SpellStatus, SpellStatusEvent
from .conjurer import Conjurable, Conjured, Conjurer, ConjurerKey, create_conjurer
from .errors import (
    AmbiguousQueryError,
    DefaultError,
    DuplicateRegistrationError,
    RunicError,
    ServiceNotFoundError,
    TaskNotFoundError,
)
from .events import Event, EventBus, create_bus
from .handlers import Handler, SupportsAsk, SupportsInvoke
from .requests import Command, Query, Request
from .result import Err, Ok, Pending, Result, ResultStatus
from .runtime import RegistryAdapter, Runic

__all__ = [
    "AmbiguousQueryError",
    "Command",
    "Conduit",
    "Conjurable",
    "Conjured",
    "Conjurer",
    "ConjurerKey",
    "DefaultError",
    "DuplicateRegistrationError",
    "Err",
    "Event",
    "EventBus",
    "Handler",
    "InMemoryTaskBackend",
    "InMemorySpellBook",
    "Ok",
    "Pending",
    "Query",
    "RegistryAdapter",
    "Request",
    "Result",
    "ResultStatus",
    "Runic",
    "RunicError",
    "ServiceNotFoundError",
    "SpellBook",
    "SpellContext",
    "SpellLog",
    "SpellRecord",
    "SpellStatus",
    "SpellStatusEvent",
    "SupportsAsk",
    "SupportsInvoke",
    "TaskBackend",
    "TaskNotFoundError",
    "__version__",
    "create_bus",
    "create_conjurer",
]
