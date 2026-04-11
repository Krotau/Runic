from __future__ import annotations

import unittest
from dataclasses import dataclass

from wyvern import DefaultError, Err, Ok, create_dispatcher
from wyvern.result import Result


@dataclass(slots=True)
class ExampleData:
    value: str


class ExampleService:
    async def emit(self, data: ExampleData) -> Result[str, DefaultError]:
        return Ok(data.value.upper())


class ExampleErrorService:
    def emit(self, data: ExampleData) -> Result[str, DefaultError]:
        return Err(DefaultError(message=f"invalid:{data.value}", code="invalid"))


class TestDispatcher(unittest.IsolatedAsyncioTestCase):
    async def test_register_returns_handler_and_key(self) -> None:
        dispatcher = create_dispatcher()
        handler, key = dispatcher.register(ExampleService())

        result = await handler.emit(ExampleData(value="registered"))
        self.assertIsInstance(result, Ok)
        assert isinstance(result, Ok)
        self.assertEqual("REGISTERED", result.value)
        self.assertTrue(key.value)

    async def test_retrieve_returns_equivalent_handler(self) -> None:
        dispatcher = create_dispatcher()
        handler, key = dispatcher.register(ExampleService())
        retrieved = dispatcher.retrieve(key)

        first = await handler.emit(ExampleData(value="same"))
        second = await retrieved.emit(ExampleData(value="same"))

        self.assertEqual(first, second)

    async def test_unregister_removes_registered_service(self) -> None:
        dispatcher = create_dispatcher()
        _, key = dispatcher.register(ExampleService())

        removed = dispatcher.unregister(key)

        self.assertTrue(removed)
        with self.assertRaises(KeyError):
            dispatcher.retrieve(key)

    async def test_sync_services_are_supported(self) -> None:
        dispatcher = create_dispatcher()
        handler, _ = dispatcher.register(ExampleErrorService())

        result = await handler.emit(ExampleData(value="boom"))

        self.assertIsInstance(result, Err)
        assert isinstance(result, Err)
        self.assertEqual("invalid", result.error.code)
        self.assertEqual("invalid:boom", result.error.message)

    async def test_register_rejects_services_without_emit(self) -> None:
        dispatcher = create_dispatcher()

        class InvalidService:
            pass

        with self.assertRaises(TypeError):
            dispatcher.register(InvalidService())  # type: ignore[arg-type]
