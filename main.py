from __future__ import annotations

import asyncio
from dataclasses import dataclass

from runic import Command, DefaultError, Ok, Pending, Runic


@dataclass(slots=True)
class GenerateGreetingReport(Command[dict[str, str], DefaultError]):
    name: str


async def main() -> None:
    runic = Runic()

    @runic.spell(GenerateGreetingReport)
    async def generate_report(ctx, req: GenerateGreetingReport) -> dict[str, str]:
        # Spells can report progress while they run and still return plain data.
        await ctx.log(f"building report for {req.name}")
        await asyncio.sleep(0.5)
        await ctx.progress(1.0)
        return {"report": f"done:{req.name}"}

    # `invoke(...)` starts the spell and returns immediately with its id, so
    # callers can check back later.
    deferred_spell_id = await runic.invoke(GenerateGreetingReport(name="Waiting deferly..."))
    deferred_result = runic.conduit.get_spell_result(deferred_spell_id)

    print("registered spell", generate_report.__name__)
    print("deferred spell id", deferred_spell_id)
    match deferred_result:
        case Pending():
            print("deferred result", "not finished yet")
        case _:
            print("deferred result", deferred_result)

    # Once the deferred spell has had time to finish, its result is available.
    await asyncio.sleep(2)
    settled_result = runic.conduit.get_spell_result(deferred_spell_id)

    # `cast(...)` starts a spell and awaits the final typed result.
    awaited_result = await runic.cast(GenerateGreetingReport(name="Waiting awaitingly"))

    print("settled result", settled_result)

    match awaited_result:
        case Ok(value=report):
            print("cast result", report)
        case _:
            print("cast failed", awaited_result)


if __name__ == "__main__":
    asyncio.run(main())
