from __future__ import annotations

import asyncio
from dataclasses import dataclass

from wyvern import DefaultError, Dispatcher, Ok
from wyvern.result import Result


@dataclass(slots=True)
class ExampleData:
    value: str


class ExampleService:
    async def emit(self, data: ExampleData) -> Result[str, DefaultError]:
        return Ok(f"handled:{data.value}")


async def main() -> None:
    dispatcher = Dispatcher()

    handler, key = dispatcher.register(ExampleService())
    result = await handler.emit(ExampleData(value="first"))

    handler_2 = dispatcher.retrieve(key)
    result_2 = await handler_2.emit(ExampleData(value="first"))

    print(type(handler).__name__, result)
    print(type(handler_2).__name__, result_2)
    print("equal:", result == result_2)


if __name__ == "__main__":
    asyncio.run(main())
