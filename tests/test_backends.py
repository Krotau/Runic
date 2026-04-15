from __future__ import annotations

import asyncio
import unittest

from runic.backends import InMemoryTaskBackend


class TestBackends(unittest.IsolatedAsyncioTestCase):
    def test_in_memory_backend_allocates_a_fresh_shared_store(self) -> None:
        backend = InMemoryTaskBackend()

        self.assertEqual({}, backend.shared)
        backend.shared["step"] = 1
        self.assertEqual({"step": 1}, backend.shared)

    def test_in_memory_backend_reuses_provided_shared_store(self) -> None:
        shared = {"count": 1}
        backend = InMemoryTaskBackend(shared)

        self.assertIs(shared, backend.shared)
        backend.shared["count"] = 2
        self.assertEqual({"count": 2}, shared)

    async def test_submit_creates_named_task_and_runs_runner(self) -> None:
        backend = InMemoryTaskBackend()
        steps: list[str] = []

        async def runner() -> None:
            steps.append("ran")

        task = backend.submit("job-123", runner)

        self.assertIsInstance(task, asyncio.Task)
        self.assertEqual("runic-job:job-123", task.get_name())

        await task

        self.assertEqual(["ran"], steps)
