from __future__ import annotations

import unittest
from dataclasses import dataclass

from wyvern import Command, DefaultError, Query


@dataclass(slots=True)
class ExampleQuery(Query[str, DefaultError]):
    value: str = ""


@dataclass(slots=True)
class ExampleCommand(Command[None, DefaultError]):
    enabled: bool = False


class TestRequests(unittest.TestCase):
    def test_query_and_command_markers(self) -> None:
        self.assertIsInstance(ExampleQuery(), Query)
        self.assertIsInstance(ExampleCommand(), Command)
        self.assertNotIsInstance(ExampleCommand(), Query)

    def test_request_defaults(self) -> None:
        query = ExampleQuery()
        self.assertEqual("", query.value)
        self.assertTrue(bool(query.request_id))

        command = ExampleCommand()
        self.assertFalse(command.enabled)

    def test_default_error_shape(self) -> None:
        error = DefaultError(message="boom", code="bad", details={"value": 1})
        self.assertEqual("boom", error.message)
        self.assertEqual("bad", error.code)
        self.assertEqual({"value": 1}, error.details)
