Wyvern
======

Wyvern is a small async utility library for event-driven application code.

It provides:
- a typed in-memory event bus
- a typed service dispatcher
- a simple in-process job runtime
- a `Wyvern` runtime facade for typed queries, events, and background work
- generic request primitives
- generic `Ok` / `Err` result types

Wyvern targets small, composable building blocks rather than a large framework.

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

Run tests with:

```bash
python -m unittest discover -s tests -v
```

Example: event bus
------------------

```python
from wyvern import Event, create_bus

bus = create_bus(dict)
subscriber = bus.subscribe()
await bus.publish(Event(name="ready", data={"ok": True}))
event = await anext(subscriber)
```

Example: dispatcher
-------------------

```python
from dataclasses import dataclass

from wyvern import DefaultError, Ok, Result, create_dispatcher


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
from wyvern import JobManager, Ok, create_bus

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

Example: typed runtime facade
-----------------------------

```python
from dataclasses import dataclass

from wyvern import DefaultError, Ok, Query, Wyvern
from wyvern.result import Result


@dataclass(slots=True)
class GetUser(Query[dict[str, int], DefaultError]):
    user_id: int


@dataclass(slots=True)
class GenerateReport:
    user_id: int


@dataclass(slots=True)
class UserRequested:
    user_id: int


wyvern = Wyvern()


@wyvern.query(GetUser)
async def get_user(req: GetUser) -> Result[dict[str, int], DefaultError]:
    return Ok({"user_id": req.user_id})


@wyvern.on(UserRequested)
async def on_user_requested(event: UserRequested) -> None:
    print("user requested", event.user_id)


@wyvern.task(GenerateReport)
async def generate_report(ctx, req: GenerateReport) -> Ok[dict[str, bool]]:
    await ctx.log("starting")
    await ctx.progress(1.0)
    return Ok({"done": True})


await wyvern.publish(UserRequested(user_id=1))
result = await wyvern.ask(GetUser(user_id=1))
same_result = await get_user(GetUser(user_id=1))
emitted = await get_user.emit(GetUser(user_id=1))
job_id = await wyvern.start(GenerateReport(user_id=1))
key = get_user.get_key()
```

The runtime also still supports the older string-keyed APIs:
- `register(name, ...)`
- `call(name, payload)`
- `task("name")`
- `dispatch(name, payload)`
- `publish("topic", payload)`

Public API
----------

- `create_bus(shape)` creates an in-memory event bus with runtime payload checks
- `Dispatcher` registers concrete services and retrieves typed handlers by key
- `JobManager` runs background jobs and publishes typed status/log streams
- `Wyvern` exposes typed `ask(...)`, `start(...)`, and `publish(...)` helpers plus `query(...)`, `task(...)`, and `on(...)`
- `Ok` and `Err` provide lightweight result containers
