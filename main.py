from __future__ import annotations

import asyncio
from dataclasses import dataclass
from decimal import Decimal

from runic import Command, DefaultError, Ok, Query, Runic


@dataclass(slots=True)
class GetGreeting(Query[str, DefaultError]):
    name: str


@dataclass(slots=True)
class GetGreetingStats(Query[dict[str, Decimal], DefaultError]):
    name: str


@dataclass(slots=True)
class RenameGreeting(Command[str, DefaultError]):
    old_name: str
    new_name: str


@dataclass(slots=True)
class GenerateGreetingReport:
    name: str


@dataclass(slots=True)
class GreetingRequested:
    name: str


class GreetingService:
    async def ask(self, query: GetGreeting) -> Ok[str]:
        return Ok(f"hello:{query.name}")

    async def invoke(self, command: RenameGreeting) -> Ok[str]:
        return Ok(f"renamed:{command.old_name}:{command.new_name}")


class GreetingStatsService:
    async def ask(self, query: GetGreetingStats) -> Ok[dict[str, Decimal]]:
        return Ok({"count": Decimal("1.00")})


async def main() -> None:
    runic = Runic()

    greeting_handler = runic.register(GreetingService())
    stats_handler = runic.register(GreetingStatsService())

    @runic.on(GreetingRequested)
    async def on_greeting_requested(event: GreetingRequested) -> None:
        print("event", event)

    @runic.task(GenerateGreetingReport)
    async def generate_report(ctx, req: GenerateGreetingReport) -> Ok[dict[str, str]]:
        await ctx.log(f"building report for {req.name}")
        await ctx.progress(1.0)
        return Ok({"report": f"done:{req.name}"})

    await runic.emit(GreetingRequested(name="Ada"))

    asked = await greeting_handler.ask(GetGreeting(name="Ada"))
    invoked = await greeting_handler.invoke(RenameGreeting(old_name="Ada", new_name="Byron"))
    results = await runic.publish(GetGreetingStats(name="Ada"))
    direct = await stats_handler.ask(GetGreetingStats(name="Ada"))
    job_id = await runic.start(GenerateGreetingReport(name="Ada"))

    while True:
        record = runic.jobs.get_status(job_id)
        if isinstance(record, Ok) and record.value.result is not None:
            break
        await asyncio.sleep(0.01)

    print("asked", asked)
    print("invoked", invoked)
    print("published", results)
    print("direct", direct)
    print("job", runic.jobs.get_status(job_id))


if __name__ == "__main__":
    asyncio.run(main())
