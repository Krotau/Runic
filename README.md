Runic
=====

Runic is a small async utility library for event-driven application code.

The package is published on PyPI as `runic-io` and imported in Python as `runic`.

It provides:
- a typed in-memory event bus
- a typed service dispatcher
- a simple in-process job runtime
- the `Runic` runtime facade for typed queries, commands, events, and background work
- generic request primitives
- generic `Ok` / `Err` result types

Runic targets small, composable building blocks rather than a large framework.

Requirements
------------

- Python 3.12+

Development Setup
-----------------

```bash
uv venv .venv
source .venv/bin/activate
uv pip install -e .[dev]
```

Installation
------------

```bash
uv add runic-io
uv pip install runic-io
pip install runic-io
```

```python
import runic
from runic import Runic
```

Run tests with:

```bash
python -m unittest discover -s tests -v
```

Example: event bus
------------------

```python
from runic import Event, create_bus

bus = create_bus(dict)
subscriber = bus.subscribe()
await bus.publish(Event(name="ready", data={"ok": True}))
event = await anext(subscriber)
```

Example: dispatcher
-------------------

```python
from dataclasses import dataclass

from runic import DefaultError, Ok, Result, create_dispatcher


@dataclass(slots=True)
class Ping:
    value: str


class PingService:
    async def emit(self, data: Ping) -> Result[str, DefaultError]:
        return Ok(f"pong:{data.value}")


dispatcher = create_dispatcher()
handler, key = dispatcher.register(PingService())
same_handler = dispatcher.retrieve(key)
result = await same_handler.emit(Ping(value="hello"))
```

Example: jobs
-------------

```python
from runic import JobManager, Ok, create_bus

bus = create_bus(object)
jobs = JobManager(bus)
status_events = jobs.status_events()
log_events = jobs.log_events()


async def work(ctx):
    await ctx.log("starting")
    await ctx.progress(1.0)
    return Ok({"done": True})


job_id = await jobs.start(work)
record = jobs.get_status(job_id)
status = await anext(status_events)
log = await anext(log_events)
```

`get_status(...)` returns `Ok(JobRecord(...))` for known jobs and `Err(DefaultError(...))` for unknown job ids.

You can also pass a task backend to share state across jobs:

```python
from runic import InMemoryTaskBackend, JobManager, create_bus

backend = InMemoryTaskBackend()
jobs = JobManager(create_bus(dict), backend=backend)


async def work(ctx):
    ctx.shared["runs"] = int(ctx.shared.get("runs", 0)) + 1
    return {"runs": ctx.shared["runs"]}
```

Example: object handler runtime
-------------------------------

```python
from dataclasses import dataclass
from decimal import Decimal

from runic import Command, DefaultError, Ok, Query, Runic


@dataclass(slots=True)
class GetUser(Query[dict[str, int], DefaultError]):
    user_id: int


@dataclass(slots=True)
class RenameUser(Command[str, DefaultError]):
    user_id: int
    name: str


@dataclass(slots=True)
class GetBalance(Query[dict[str, Decimal], DefaultError]):
    user_id: int


@dataclass(slots=True)
class UserRequested:
    user_id: int


class UserService:
    async def ask(self, query: GetUser) -> Ok[dict[str, int]]:
        return Ok({"user_id": query.user_id})

    async def invoke(self, command: RenameUser) -> Ok[str]:
        return Ok(f"renamed:{command.user_id}:{command.name}")


class BalanceService:
    async def ask(self, query: GetBalance) -> Ok[dict[str, Decimal]]:
        return Ok({"balance": Decimal("10.50")})


runic = Runic()
user_handler = runic.register(UserService())
balance_handler = runic.register(BalanceService())


@runic.on(UserRequested)
async def on_user_requested(event: UserRequested) -> None:
    print("user requested", event.user_id)


await runic.emit(UserRequested(user_id=1))
user_result = await user_handler.ask(GetUser(user_id=1))
rename_result = await user_handler.invoke(RenameUser(user_id=1, name="Ada"))
all_balances = await runic.publish(GetBalance(user_id=1))
direct_balance = await balance_handler.ask(GetBalance(user_id=1))
```

The runtime also still supports the older APIs:
- `register(name, ...)`
- `call(name, payload)`
- `query(...)`
- `task("name")`
- `dispatch(name, payload)`
- `emit("topic", payload)`

Public API
----------

- `create_bus(shape)` creates an in-memory event bus with runtime payload checks
- `Dispatcher` registers concrete services and retrieves typed handlers by key
- `JobManager` runs background jobs and publishes typed status/log streams
- `Handler[TService]` wraps object services and exposes typed `ask(...)` and `invoke(...)`
- `Runic` exposes typed `ask(...)`, broad-query `publish(...)`, event `emit(...)`, and `start(...)` helpers plus `register(...)`, `query(...)`, `task(...)`, and `on(...)`
- `Ok` and `Err` provide lightweight result containers
