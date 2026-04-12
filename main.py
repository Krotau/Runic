from __future__ import annotations

import asyncio
from dataclasses import dataclass

from wyvern import DefaultError, Ok, Query, Wyvern
from wyvern.result import Result


@dataclass(slots=True)
class GetGreeting(Query[str, DefaultError]):
    name: str


@dataclass(slots=True)
class GenerateGreetingReport:
    name: str


@dataclass(slots=True)
class GreetingRequested:
    name: str


async def main() -> None:
    wyvern = Wyvern()

    @wyvern.query(GetGreeting)
    async def get_greeting(req: GetGreeting) -> Result[str, DefaultError]:
        return Ok(f"hello:{req.name}")

    @wyvern.on(GreetingRequested)
    async def on_greeting_requested(event: GreetingRequested) -> None:
        print("event", event)

    @wyvern.task(GenerateGreetingReport)
    async def generate_report(ctx, req: GenerateGreetingReport) -> Ok[dict[str, str]]:
        await ctx.log(f"building report for {req.name}")
        await ctx.progress(1.0)
        return Ok({"report": f"done:{req.name}"})

    await wyvern.publish(GreetingRequested(name="Ada"))

    asked = await wyvern.ask(GetGreeting(name="Ada"))
    direct = await get_greeting(GetGreeting(name="Byron"))
    emitted = await get_greeting.emit(GetGreeting(name="Lovelace"))
    job_id = await wyvern.start(GenerateGreetingReport(name="Ada"))

    while True:
        record = wyvern.jobs.get_status(job_id)
        if isinstance(record, Ok) and record.value.result is not None:
            break
        await asyncio.sleep(0.01)

    print("asked", asked)
    print("direct", direct)
    print("emitted", emitted)
    print("key", get_greeting.get_key().value)
    print("job", wyvern.jobs.get_status(job_id))


if __name__ == "__main__":
    asyncio.run(main())
