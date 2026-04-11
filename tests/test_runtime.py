from __future__ import annotations

import asyncio
import unittest
from dataclasses import dataclass

from wyvern import (
    DuplicateRegistrationError,
    DispatcherKey,
    DispatchService,
    RegistryAdapter,
    Ok,
    ServiceNotFoundError,
    TaskNotFoundError,
    Wyvern,
)


@dataclass(slots=True)
class Ping:
    value: str


class TestWyvernRuntime(unittest.IsolatedAsyncioTestCase):
    async def test_service_decorator_registers_async_function_with_inferred_name(self) -> None:
        wyvern = Wyvern()

        @wyvern.register()
        async def ping(req: Ping) -> Ok[str]:
            return Ok(f"pong:{req.value}")

        self.assertIsInstance(ping, RegistryAdapter)
        direct = await ping(Ping(value="hello"))
        result = await wyvern.call("ping", Ping(value="hello"))

        self.assertEqual(Ok("pong:hello"), direct)
        self.assertEqual(Ok("pong:hello"), result)
        self.assertIsInstance(ping.get_key(), DispatcherKey)

    async def test_register_decorator_registers_sync_function_with_explicit_name(self) -> None:
        wyvern = Wyvern()

        @wyvern.register("users.get")
        def get_user(req: Ping) -> Ok[dict[str, str]]:
            return Ok({"value": req.value.upper()})

        emitted = await get_user.emit(Ping(value="alice"))
        called = await get_user(Ping(value="alice"))
        result = await wyvern.call("users.get", Ping(value="alice"))

        self.assertEqual(Ok({"value": "ALICE"}), emitted)
        self.assertEqual(Ok({"value": "ALICE"}), called)
        self.assertEqual(Ok({"value": "ALICE"}), result)

    async def test_call_invokes_existing_object_service(self) -> None:
        wyvern = Wyvern()

        class PingService(DispatchService[Ping, str, object]):
            async def emit(self, data: Ping) -> Ok[str]:
                return Ok(f"service:{data.value}")

        adapter = wyvern.register("ping.object", PingService())
        result = await wyvern.call("ping.object", Ping(value="ok"))
        direct = await adapter.emit(Ping(value="ok"))

        self.assertEqual(Ok("service:ok"), result)
        self.assertEqual(Ok("service:ok"), direct)
        self.assertIsInstance(adapter.get_key(), DispatcherKey)

    async def test_on_and_publish_route_topic_handlers(self) -> None:
        wyvern = Wyvern()
        seen: list[Ping] = []
        received = asyncio.Event()

        @wyvern.on("user.created")
        async def handle(event: Ping) -> None:
            seen.append(event)
            received.set()

        await wyvern.publish("user.created", Ping(value="event"))
        await asyncio.wait_for(received.wait(), timeout=1.0)

        self.assertEqual([Ping(value="event")], seen)

    async def test_task_and_dispatch_start_tracked_work(self) -> None:
        wyvern = Wyvern()

        @wyvern.task("report.generate")
        async def generate_report(ctx, req: Ping) -> Ok[dict[str, bool]]:
            await ctx.log(f"starting:{req.value}")
            await ctx.progress(1.0)
            return Ok({"done": True})

        job_id = await wyvern.dispatch("report.generate", Ping(value="job"))
        await asyncio.wait_for(self._wait_for_result(wyvern, job_id), timeout=1.0)

        record = wyvern.jobs.get_status(job_id)
        assert record is not None
        self.assertEqual(["starting:job"], record.logs)
        self.assertEqual({"done": True}, record.result)

    async def test_task_supports_payload_only_and_no_args_signatures(self) -> None:
        wyvern = Wyvern()

        @wyvern.task("payload.only")
        def payload_only(req: Ping) -> Ok[str]:
            return Ok(req.value)

        @wyvern.task()
        def no_args() -> Ok[str]:
            return Ok("done")

        first_job = await wyvern.dispatch("payload.only", Ping(value="value"))
        second_job = await wyvern.dispatch("no_args")

        await asyncio.wait_for(self._wait_for_result(wyvern, first_job), timeout=1.0)
        await asyncio.wait_for(self._wait_for_result(wyvern, second_job), timeout=1.0)

        first = wyvern.jobs.get_status(first_job)
        second = wyvern.jobs.get_status(second_job)
        assert first is not None
        assert second is not None
        self.assertEqual("value", first.result)
        self.assertEqual("done", second.result)

    async def test_duplicate_registration_errors(self) -> None:
        wyvern = Wyvern()

        @wyvern.register("duplicate")
        async def first(req: Ping) -> Ok[str]:
            return Ok(req.value)

        with self.assertRaises(DuplicateRegistrationError):

            @wyvern.register("duplicate")
            async def second(req: Ping) -> Ok[str]:
                return Ok(req.value)

        @wyvern.task("duplicate.task")
        async def task_one() -> Ok[str]:
            return Ok("one")

        with self.assertRaises(DuplicateRegistrationError):

            @wyvern.task("duplicate.task")
            async def task_two() -> Ok[str]:
                return Ok("two")

        self.assertEqual("first", first.__name__)
        self.assertEqual("task_one", task_one.__name__)

    async def test_missing_service_and_task_errors(self) -> None:
        wyvern = Wyvern()

        with self.assertRaises(ServiceNotFoundError):
            await wyvern.call("missing")

        with self.assertRaises(TaskNotFoundError):
            await wyvern.dispatch("missing")

    async def test_register_supports_no_arg_services(self) -> None:
        wyvern = Wyvern()

        @wyvern.register()
        def ping() -> Ok[str]:
            return Ok("pong")

        self.assertEqual(Ok("pong"), await ping())
        self.assertEqual(Ok("pong"), await ping.emit())
        self.assertIsInstance(ping.get_key(), DispatcherKey)

    async def _wait_for_result(self, wyvern: Wyvern, job_id: str) -> None:
        while True:
            record = wyvern.jobs.get_status(job_id)
            if record is not None and record.result is not None:
                return
            await asyncio.sleep(0.01)
