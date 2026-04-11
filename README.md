Wyvern
======

Wyvern is a small async utility library for event-driven application code.

It provides:
- a typed in-memory event bus
- a typed service dispatcher
- a simple in-process job runtime
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
pytest -s
```

Example: event bus
```python
from wyvern import Event, create_bus

bus = create_bus(dict)
subscriber = bus.subscribe()
await bus.publish(Event(name="ready", data={"ok": True}))
event = await anext(subscriber)
```

Example: dispatcher
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

The dispatcher preserves the concrete service type across registration and
retrieval, so editors can keep `DispatcherHandler[PingService]` on hover while
still typing `emit(...)` from the service signature.

Example: jobs
```python
from wyvern import JobManager, Ok, create_bus

bus = create_bus(dict)
jobs = JobManager(bus)
status_events = jobs.status_events()
log_events = jobs.log_events()


async def work(ctx):
    await ctx.log("starting")
    await ctx.emit("job_output", {"line": "hello"})
    await ctx.progress(1.0)
    return Ok({"done": True})


job_id = await jobs.start(work)
record = jobs.get_status(job_id)
status = await anext(status_events)
log = await anext(log_events)
```

Public API
----------

- `create_bus(shape)` creates an in-memory event bus with runtime payload checks
- `Dispatcher` registers concrete services and retrieves typed handlers by key
- `JobManager` runs background jobs and publishes typed status/log streams
- `Ok` and `Err` provide lightweight result containers
