from __future__ import annotations

import unittest
from dataclasses import dataclass

from runic import Err, Ok, Pending, Result, ResultStatus


@dataclass(slots=True)
class Node:
    name: str
    child: object | None = None


class TestResultComparisons(unittest.TestCase):
    def test_result_alias_typed_ok_uses_same_comparisons(self) -> None:
        result: Result[int, str] = Ok(5)

        self.assertEqual(result, 5)
        self.assertGreater(result, 4)
        self.assertTrue(result.compare(5))

    def test_result_alias_typed_err_uses_same_comparisons(self) -> None:
        result: Result[int, str] = Err("boom")

        self.assertEqual(result, "boom")
        self.assertLess(result, "z")
        self.assertTrue(result.compare("boom"))

    def test_pending_exposes_pending_status_and_falsey_truthiness(self) -> None:
        result: Result[int, str] = Pending()

        self.assertIs(ResultStatus.PENDING, result.status)
        self.assertFalse(result)
        self.assertEqual(Pending(), result)
        self.assertNotEqual(result, None)

    def test_shallow_equality_compares_inner_value(self) -> None:
        self.assertEqual(Ok(3), 3)
        self.assertEqual(Err("boom"), "boom")
        self.assertEqual(Ok(5), Ok(5))
        self.assertNotEqual(Ok(5), Err(5))

    def test_shallow_ordering_compares_inner_value(self) -> None:
        self.assertGreater(Ok(4), 3)
        self.assertGreaterEqual(Ok(4), 4)
        self.assertLess(Err("a"), "b")
        self.assertLessEqual(Err("a"), Err("a"))

    def test_logical_operators_use_inner_truthiness(self) -> None:
        self.assertEqual("fallback", Ok("") or "fallback")
        self.assertEqual("value", Ok("value") and "value")
        self.assertEqual(0, Err(0) or 0)
        self.assertEqual("next", Err("error") and "next")

    def test_rich_comparisons_are_shallow_for_nested_results(self) -> None:
        left = Ok({"payload": Ok(1)})
        right = {"payload": 1}

        self.assertNotEqual(left, right)

    def test_compare_recurses_through_nested_results(self) -> None:
        left = Ok({"payload": [Ok(1), Err({"code": Ok("bad")})]})
        right = {"payload": [1, {"code": "bad"}]}

        self.assertTrue(left.compare(right))

    def test_compare_handles_result_wrappers_on_both_sides(self) -> None:
        self.assertTrue(Ok(Ok(1)).compare(Ok(1)))
        self.assertTrue(Err(Err("boom")).compare(Err("boom")))
        self.assertFalse(Ok(1).compare(Err(1)))

    def test_compare_handles_nested_dataclasses(self) -> None:
        left = Ok(Node(name="root", child=Node(name="leaf", child=Ok(7))))
        right = Node(name="root", child=Node(name="leaf", child=7))

        self.assertTrue(left.compare(right))
