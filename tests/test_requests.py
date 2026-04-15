from __future__ import annotations

import unittest
from dataclasses import dataclass
from uuid import UUID

from runic import Command, DefaultError, Query
from runic.requests import new_id


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

    def test_request_ids_are_generated_and_unique(self) -> None:
        query = ExampleQuery()
        command = ExampleCommand()

        self.assertEqual(query.request_id, str(UUID(query.request_id)))
        self.assertEqual(command.request_id, str(UUID(command.request_id)))
        self.assertNotEqual(query.request_id, command.request_id)

    def test_request_defaults(self) -> None:
        query = ExampleQuery()
        self.assertEqual("", query.value)

        command = ExampleCommand()
        self.assertFalse(command.enabled)

    def test_default_error_shape(self) -> None:
        error = DefaultError(message="boom", code="bad", details={"value": 1})
        self.assertEqual("boom", error.message)
        self.assertEqual("bad", error.code)
        self.assertEqual({"value": 1}, error.details)

    def test_new_id_returns_uuid_string(self) -> None:
        identifier = new_id()

        self.assertEqual(identifier, str(UUID(identifier)))
