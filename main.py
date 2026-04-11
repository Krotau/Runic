from __future__ import annotations

import asyncio
from dataclasses import dataclass

from wyvern import DefaultError, Ok, Wyvern
from wyvern.result import Result


@dataclass(slots=True)
class ExampleData:
    value: str


class ExampleService:
    async def emit(self, data: ExampleData) -> Result[str, DefaultError]:
        return Ok(f"handled:{data.value}")


async def main() -> None:
    wyvern = Wyvern()

    service = wyvern.register("example.handle", ExampleService())
    result = await wyvern.call("example.handle", ExampleData(value="first"))
    result_2 = await wyvern.call("example.handle", ExampleData(value="first"))
    direct = await service.emit(ExampleData(value="first"))

    @wyvern.register()
    async def decorated(asset: ExampleData) -> Result[str, DefaultError]:
        return Ok(f"decorated:{asset.value}")

    decorated_emit = await decorated.emit(ExampleData(value="second"))
    decorated_call = await decorated(ExampleData(value="second"))

    print("Wyvern", result)
    print("Wyvern", result_2)
    print("Object adapter", direct, service.get_key().value)
    print("equal:", result == result_2)
    print("Decorated emit", decorated_emit, decorated.get_key().value)
    print("Decorated call", decorated_call)


if __name__ == "__main__":
    asyncio.run(main())
