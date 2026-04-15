from __future__ import annotations

import asyncio
import unittest
from dataclasses import dataclass

from runic import DefaultError, Err, InMemoryTaskBackend, JobContext, JobLog, JobManager, JobRecord, JobStatus, Ok, create_bus


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
        self.assertIsInstance(record, Ok)
        assert isinstance(record, Ok)
        job = record.value
        self.assertEqual("job_status", status_event.name)
        self.assertEqual("running", status_event.data.status)
        self.assertEqual("job_log", log_event.name)
        self.assertEqual("started", log_event.data.message)
        self.assertIn("progress", [event.name for event in observed_events])
        self.assertIs(JobStatus.SUCCEEDED, job.status)
        self.assertEqual(1.0, job.progress)
        self.assertEqual(["started", "finished"], job.logs)
        self.assertEqual({"done": True}, job.result)

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
        self.assertIsInstance(record, Ok)
        assert isinstance(record, Ok)
        self.assertIs(JobStatus.CANCELLED, record.value.status)

    async def test_error_result_uses_message_field(self) -> None:
        manager = JobManager(create_bus(dict))

        async def work(ctx):
            return CustomMessageError("custom failure")

        job_id = await manager.start(work)
        await asyncio.wait_for(self._wait_for_status(manager, job_id, JobStatus.SUCCEEDED), timeout=1.0)

        record = manager.get_status(job_id)
        self.assertIsInstance(record, Ok)
        assert isinstance(record, Ok)
        self.assertEqual({"message": "custom failure"}, record.value.result)

    async def test_err_result_records_failure_message(self) -> None:
        manager = JobManager(create_bus(dict))

        async def work(ctx):
            from runic import Err

            return Err(DefaultError(message="request failed", code="bad_request"))

        job_id = await manager.start(work)
        await asyncio.wait_for(self._wait_for_status(manager, job_id, JobStatus.FAILED), timeout=1.0)

        record = manager.get_status(job_id)
        self.assertIsInstance(record, Ok)
        assert isinstance(record, Ok)
        self.assertEqual("request failed", record.value.error)
        self.assertIsNone(record.value.result)

    async def test_progress_is_clamped(self) -> None:
        manager = JobManager(create_bus(dict))

        async def work(ctx):
            await ctx.progress(2.5)
            return Ok(ExamplePayload(done=True))

        job_id = await manager.start(work)
        await asyncio.wait_for(self._wait_for_status(manager, job_id, JobStatus.SUCCEEDED), timeout=1.0)

        record = manager.get_status(job_id)
        self.assertIsInstance(record, Ok)
        assert isinstance(record, Ok)
        self.assertEqual(1.0, record.value.progress)
        self.assertEqual({"done": True}, record.value.result)

    async def test_start_passes_typed_context_data(self) -> None:
        manager = JobManager(create_bus(dict))

        async def work(ctx):
            assert ctx.data is not None
            return Ok({"value": ctx.data["value"]})

        job_id = await manager.start(work, data={"value": 7})
        await asyncio.wait_for(self._wait_for_status(manager, job_id, JobStatus.SUCCEEDED), timeout=1.0)

        record = manager.get_status(job_id)
        self.assertIsInstance(record, Ok)
        assert isinstance(record, Ok)
        self.assertEqual({"value": 7}, record.value.result)

    async def test_backend_shared_state_is_visible_across_jobs(self) -> None:
        backend = InMemoryTaskBackend()
        manager = JobManager(create_bus(dict), backend=backend)

        async def work(ctx):
            current = int(ctx.shared.get("runs", 0))
            ctx.shared["runs"] = current + 1
            return Ok({"runs": ctx.shared["runs"]})

        first_job = await manager.start(work)
        second_job = await manager.start(work)

        await asyncio.wait_for(self._wait_for_status(manager, first_job, JobStatus.SUCCEEDED), timeout=1.0)
        await asyncio.wait_for(self._wait_for_status(manager, second_job, JobStatus.SUCCEEDED), timeout=1.0)

        first = manager.get_status(first_job)
        second = manager.get_status(second_job)
        self.assertIsInstance(first, Ok)
        self.assertIsInstance(second, Ok)
        assert isinstance(first, Ok)
        assert isinstance(second, Ok)
        self.assertEqual({"runs": 1}, first.value.result)
        self.assertEqual({"runs": 2}, second.value.result)
        self.assertEqual(2, backend.shared["runs"])

    async def test_job_context_defaults_shared_state_when_none_is_passed(self) -> None:
        context = JobContext(
            job_id="job-1",
            bus=create_bus(dict),
            log_bus=create_bus(JobLog),
            record=JobRecord(job_id="job-1"),
        )

        self.assertEqual({}, context.shared)
        await context.progress(-1.5)
        self.assertEqual(0.0, context.record.progress)

    def test_get_status_returns_err_for_missing_job(self) -> None:
        manager = JobManager(create_bus(dict))

        result = manager.get_status("missing")

        self.assertIsInstance(result, Err)
        assert isinstance(result, Err)
        self.assertEqual("Unknown job: missing", result.error.message)
        self.assertEqual("job_not_found", result.error.code)

    async def test_plain_results_are_persisted_and_cleared_from_futures(self) -> None:
        manager = JobManager(create_bus(dict))

        def work(ctx):
            return {"done": True}

        job_id = await manager.start(work)
        await asyncio.wait_for(self._wait_for_status(manager, job_id, JobStatus.SUCCEEDED), timeout=1.0)

        record = manager.get_status(job_id)
        self.assertIsInstance(record, Ok)
        assert isinstance(record, Ok)
        self.assertEqual(JobStatus.SUCCEEDED, record.value.status)
        self.assertEqual({"done": True}, record.value.result)
        await self._wait_for_finalization(manager, job_id)
        self.assertNotIn(job_id, manager._futures)

    async def test_err_results_without_message_attributes_use_string_fallback(self) -> None:
        manager = JobManager(create_bus(dict))

        async def work(ctx):
            return Err(ValueError("broken"))

        job_id = await manager.start(work)
        await asyncio.wait_for(self._wait_for_status(manager, job_id, JobStatus.FAILED), timeout=1.0)

        record = manager.get_status(job_id)
        self.assertIsInstance(record, Ok)
        assert isinstance(record, Ok)
        self.assertEqual("broken", record.value.error)
        self.assertIsNone(record.value.result)

    async def test_failing_jobs_record_exception_message_and_clear_future(self) -> None:
        manager = JobManager(create_bus(dict))

        async def work(ctx):
            raise RuntimeError("boom")

        job_id = await manager.start(work)
        await asyncio.wait_for(self._wait_for_status(manager, job_id, JobStatus.FAILED), timeout=1.0)

        record = manager.get_status(job_id)
        self.assertIsInstance(record, Ok)
        assert isinstance(record, Ok)
        self.assertEqual(JobStatus.FAILED, record.value.status)
        self.assertEqual("boom", record.value.error)
        await self._wait_for_finalization(manager, job_id)
        self.assertNotIn(job_id, manager._futures)

    async def test_stop_returns_false_for_missing_or_completed_jobs(self) -> None:
        manager = JobManager(create_bus(dict))

        self.assertFalse(await manager.stop("missing"))

        loop = asyncio.get_running_loop()
        future = loop.create_future()
        future.set_result(None)
        job_id = "done"
        manager._records[job_id] = JobRecord(job_id=job_id)
        manager._futures[job_id] = future

        self.assertFalse(await manager.stop(job_id))

    async def _wait_for_status(self, manager: JobManager, job_id: str, expected: JobStatus) -> None:
        while True:
            record = manager.get_status(job_id)
            if isinstance(record, Ok) and record.value.status is expected:
                return
            await asyncio.sleep(0.01)

    async def _wait_for_finalization(self, manager: JobManager, job_id: str) -> None:
        while job_id in manager._futures:
            await asyncio.sleep(0.01)
