from __future__ import annotations

import asyncio
import unittest

from runic.spellbooks import InMemorySpellBook


class TestSpellBooks(unittest.IsolatedAsyncioTestCase):
    def test_in_memory_spellbook_allocates_a_fresh_shared_store(self) -> None:
        spellbook = InMemorySpellBook()

        self.assertEqual({}, spellbook.shared)
        spellbook.shared["step"] = 1
        self.assertEqual({"step": 1}, spellbook.shared)

    def test_in_memory_spellbook_reuses_provided_shared_store(self) -> None:
        shared = {"count": 1}
        spellbook = InMemorySpellBook(shared)

        self.assertIs(shared, spellbook.shared)
        spellbook.shared["count"] = 2
        self.assertEqual({"count": 2}, shared)

    async def test_submit_creates_named_task_and_runs_runner(self) -> None:
        spellbook = InMemorySpellBook()
        steps: list[str] = []

        async def runner() -> None:
            steps.append("ran")

        task = spellbook.submit("spell-123", runner)

        self.assertIsInstance(task, asyncio.Task)
        self.assertEqual("runic-spell:spell-123", task.get_name())

        await task

        self.assertEqual(["ran"], steps)
