from __future__ import annotations

import inspect
import unittest
from dataclasses import dataclass

from runic import Command, Handler, Ok, Query
from runic.handlers import _ServiceMethodAdapter


@dataclass(slots=True)
class Lookup(Query[str, str]):
    value: str


@dataclass(slots=True)
class Update(Command[str, str]):
    value: str


async def _await_if_needed(value: object) -> object:
    return await value if inspect.isawaitable(value) else value


class AsyncQueryService:
    async def ask(self, query: Lookup) -> Ok[str]:
        return Ok(f"query:{query.value}")


class SyncCommandService:
    def invoke(self, command: Update) -> Ok[str]:
        return Ok(f"command:{command.value}")


class TestHandlers(unittest.IsolatedAsyncioTestCase):
    async def test_handler_uses_registered_query_and_command_adapters(self) -> None:
        service = object()
        handler = Handler(
            service,
            query_adapter=_ServiceMethodAdapter(Lookup, AsyncQueryService().ask, _await_if_needed),
            command_adapter=_ServiceMethodAdapter(Update, SyncCommandService().invoke, _await_if_needed),
        )

        self.assertIs(handler.service, service)
        self.assertEqual(Ok("query:alpha"), await handler.ask(Lookup(value="alpha")))
        self.assertEqual(Ok("command:beta"), await handler.invoke(Update(value="beta")))

    async def test_handler_requires_matching_adapters(self) -> None:
        handler = Handler(object())

        with self.assertRaises(TypeError):
            await handler.ask(Lookup(value="missing"))

        with self.assertRaises(TypeError):
            await handler.invoke(Update(value="missing"))
