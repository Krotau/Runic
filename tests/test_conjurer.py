from __future__ import annotations

import ast
import inspect
import unittest
from dataclasses import dataclass

from runic import ConjurerKey, DefaultError, Err, Ok, create_conjurer
from runic.conjurer import Conjurer
from runic.result import Result


@dataclass(slots=True)
class ExampleData:
    value: str


class ExampleService:
    async def emit(self, data: ExampleData) -> Result[str, DefaultError]:
        return Ok(data.value.upper())


class ExampleErrorService:
    def emit(self, data: ExampleData) -> Result[str, DefaultError]:
        return Err(DefaultError(message=f"invalid:{data.value}", code="invalid"))


def is_self_mutation_target(node: ast.expr) -> bool:
    match node:
        case ast.Attribute(value=ast.Name(id="self"), ctx=ast.Store()):
            return True
        case ast.Subscript(value=ast.Attribute(value=ast.Name(id="self")), ctx=ast.Store()):
            return True
        case _:
            return False


def mutation_lines_for_conjurer() -> set[int]:
    source_lines, start_line = inspect.getsourcelines(Conjurer)
    class_node = ast.parse("".join(source_lines)).body[0]
    assert isinstance(class_node, ast.ClassDef)

    mutation_lines: set[int] = set()
    mutating_methods = {"pop", "append", "extend", "update", "add", "remove", "discard", "clear", "insert"}

    for function in class_node.body:
        if not isinstance(function, ast.FunctionDef):
            continue
        for node in ast.walk(function):
            match node:
                case ast.Assign(targets=targets):
                    if any(is_self_mutation_target(target) for target in targets):
                        mutation_lines.add(start_line + node.lineno - 1)
                case ast.AnnAssign(target=target):
                    if is_self_mutation_target(target):
                        mutation_lines.add(start_line + node.lineno - 1)
                case ast.AugAssign(target=target):
                    if is_self_mutation_target(target):
                        mutation_lines.add(start_line + node.lineno - 1)
                case ast.Delete(targets=targets):
                    if any(is_self_mutation_target(target) for target in targets):
                        mutation_lines.add(start_line + node.lineno - 1)
                case ast.Call(func=ast.Attribute(value=ast.Attribute(value=ast.Name(id="self")), attr=method)):
                    if method in mutating_methods:
                        mutation_lines.add(start_line + node.lineno - 1)
                case _:
                    continue

    return mutation_lines


class TestConjurer(unittest.IsolatedAsyncioTestCase):
    def test_conjurer_ast_exposes_expected_mutation_lines(self) -> None:
        self.assertEqual({70, 80, 97}, mutation_lines_for_conjurer())

    def test_conjurer_starts_with_empty_service_registry(self) -> None:
        conjurer = create_conjurer()

        self.assertEqual({}, conjurer._services)

    async def test_conjure_returns_handler_and_key(self) -> None:
        conjurer = create_conjurer()
        handler, key = conjurer.conjure(ExampleService())

        result = await handler.emit(ExampleData(value="registered"))
        self.assertIsInstance(result, Ok)
        assert isinstance(result, Ok)
        self.assertEqual("REGISTERED", result.value)
        self.assertTrue(key.value)

    async def test_conjure_stores_service_under_generated_key(self) -> None:
        conjurer = create_conjurer()
        service = ExampleService()

        handler, key = conjurer.conjure(service)

        self.assertIs(conjurer._services[key], service)
        self.assertIs(handler.service, service)

    async def test_retrieve_returns_equivalent_handler(self) -> None:
        conjurer = create_conjurer()
        handler, key = conjurer.conjure(ExampleService())
        retrieved = conjurer.retrieve(key)

        first = await handler.emit(ExampleData(value="same"))
        second = await retrieved.emit(ExampleData(value="same"))

        self.assertEqual(first, second)

    def test_retrieve_rejects_unknown_key(self) -> None:
        conjurer = create_conjurer()

        with self.assertRaises(KeyError):
            conjurer.retrieve(ConjurerKey("missing"))

    async def test_banish_removes_registered_service(self) -> None:
        conjurer = create_conjurer()
        _, key = conjurer.conjure(ExampleService())

        removed = conjurer.banish(key)

        self.assertTrue(removed)
        with self.assertRaises(KeyError):
            conjurer.retrieve(key)

    def test_banish_returns_false_for_unknown_key(self) -> None:
        conjurer = create_conjurer()

        self.assertFalse(conjurer.banish(ConjurerKey("missing")))

    async def test_sync_services_are_supported(self) -> None:
        conjurer = create_conjurer()
        handler, _ = conjurer.conjure(ExampleErrorService())

        result = await handler.emit(ExampleData(value="boom"))

        self.assertIsInstance(result, Err)
        assert isinstance(result, Err)
        self.assertEqual("invalid", result.error.code)
        self.assertEqual("invalid:boom", result.error.message)

    async def test_conjure_rejects_services_without_emit(self) -> None:
        conjurer = create_conjurer()

        class InvalidService:
            pass

        with self.assertRaises(TypeError):
            conjurer.conjure(InvalidService())  # type: ignore[arg-type]
