from __future__ import annotations

import asyncio
import unittest
from dataclasses import dataclass

from wyvern import DefaultError, JobManager, JobStatus, Ok, create_bus


@dataclass(slots=True)
class ExamplePayload:
    done: bool


class CustomMessageError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class TestJobs(unittest.IsolatedAsyncioTestCase):
    async def test_job_lifecycle_progress_logs_and_result(self) -> None:
        bus = create_bus(dict)
        manager = JobManager(bus)
        subscriber = bus.subscribe()
        status_subscriber = manager.status_events()
        log_subscriber = manager.log_events()

        async def work(ctx):
            await ctx.log("started")
            await ctx.progress(0.5)
            await ctx.emit("progress", {"value": 0.5})
            await ctx.log("finished")
            await ctx.progress(1.0)
            return Ok({"done": True})

        try:
            job_id = await manager.start(work)
            status_event = await asyncio.wait_for(anext(status_subscriber), timeout=1.0)
            log_event = await asyncio.wait_for(anext(log_subscriber), timeout=1.0)
            observed_events = [await asyncio.wait_for(anext(subscriber), timeout=1.0)]
            await asyncio.wait_for(self._wait_for_status(manager, job_id, JobStatus.SUCCEEDED), timeout=1.0)
        finally:
            await subscriber.aclose()
            await status_subscriber.aclose()
            await log_subscriber.aclose()

        record = manager.get_status(job_id)
        assert record is not None
        self.assertEqual("job_status", status_event.name)
        self.assertEqual("running", status_event.data.status)
        self.assertEqual("job_log", log_event.name)
        self.assertEqual("started", log_event.data.message)
        self.assertIn("progress", [event.name for event in observed_events])
        self.assertIs(JobStatus.SUCCEEDED, record.status)
        self.assertEqual(1.0, record.progress)
        self.assertEqual(["started", "finished"], record.logs)
        self.assertEqual({"done": True}, record.result)

    async def test_stop_cancels_running_job(self) -> None:
        manager = JobManager(create_bus(dict))
        release = asyncio.Event()

        async def work(ctx):
            await ctx.log("waiting")
            await release.wait()

        job_id = await manager.start(work)
        await asyncio.sleep(0.01)
        stopped = await manager.stop(job_id)
        self.assertTrue(stopped)
        await asyncio.wait_for(self._wait_for_status(manager, job_id, JobStatus.CANCELLED), timeout=1.0)

        record = manager.get_status(job_id)
        assert record is not None
        self.assertIs(JobStatus.CANCELLED, record.status)

    async def test_error_result_uses_message_field(self) -> None:
        manager = JobManager(create_bus(dict))

        async def work(ctx):
            return CustomMessageError("custom failure")

        job_id = await manager.start(work)
        await asyncio.wait_for(self._wait_for_status(manager, job_id, JobStatus.SUCCEEDED), timeout=1.0)

        record = manager.get_status(job_id)
        assert record is not None
        self.assertEqual({"message": "custom failure"}, record.result)

    async def test_err_result_records_failure_message(self) -> None:
        manager = JobManager(create_bus(dict))

        async def work(ctx):
            from wyvern import Err

            return Err(DefaultError(message="request failed", code="bad_request"))

        job_id = await manager.start(work)
        await asyncio.wait_for(self._wait_for_status(manager, job_id, JobStatus.FAILED), timeout=1.0)

        record = manager.get_status(job_id)
        assert record is not None
        self.assertEqual("request failed", record.error)
        self.assertIsNone(record.result)

    async def test_progress_is_clamped(self) -> None:
        manager = JobManager(create_bus(dict))

        async def work(ctx):
            await ctx.progress(2.5)
            return Ok(ExamplePayload(done=True))

        job_id = await manager.start(work)
        await asyncio.wait_for(self._wait_for_status(manager, job_id, JobStatus.SUCCEEDED), timeout=1.0)

        record = manager.get_status(job_id)
        assert record is not None
        self.assertEqual(1.0, record.progress)
        self.assertEqual({"done": True}, record.result)

    async def test_start_passes_typed_context_data(self) -> None:
        manager = JobManager(create_bus(dict))

        async def work(ctx):
            assert ctx.data is not None
            return Ok({"value": ctx.data["value"]})

        job_id = await manager.start(work, data={"value": 7})
        await asyncio.wait_for(self._wait_for_status(manager, job_id, JobStatus.SUCCEEDED), timeout=1.0)

        record = manager.get_status(job_id)
        assert record is not None
        self.assertEqual({"value": 7}, record.result)

    async def _wait_for_status(self, manager: JobManager, job_id: str, expected: JobStatus) -> None:
        while True:
            record = manager.get_status(job_id)
            if record and record.status is expected:
                return
            await asyncio.sleep(0.01)
