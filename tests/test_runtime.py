from __future__ import annotations

import asyncio
import unittest
from dataclasses import dataclass
from decimal import Decimal

from runic import (
    AmbiguousQueryError,
    Command,
    DuplicateRegistrationError,
    DispatcherKey,
    DispatchService,
    Handler,
    InMemoryTaskBackend,
    JobManager,
    Ok,
    Query,
    RegistryAdapter,
    Runic,
    ServiceNotFoundError,
    TaskNotFoundError,
    create_bus,
)


@dataclass(slots=True)
class Ping:
    value: str


@dataclass(slots=True)
class GetUser(Query[dict[str, str], object]):
    user_id: int


@dataclass(slots=True)
class GetBalance(Query[dict[str, Decimal], object]):
    user_id: int


@dataclass(slots=True)
class RenameUser(Command[str, object]):
    user_id: int
    name: str


@dataclass(slots=True)
class GenerateReport:
    report_id: str


@dataclass(slots=True)
class UserCreated:
    user_id: int


class AccountService:
    async def ask(self, query: GetBalance) -> Ok[dict[str, Decimal]]:
        return Ok({"balance": Decimal("10.50")})

    async def invoke(self, command: RenameUser) -> Ok[str]:
        return Ok(f"renamed:{command.user_id}:{command.name}")


class QueryOnlyService:
    async def ask(self, query: GetUser) -> Ok[dict[str, str]]:
        return Ok({"user_id": str(query.user_id)})


class CommandOnlyService:
    async def invoke(self, command: RenameUser) -> Ok[str]:
        return Ok(f"cmd:{command.name}")


class TestRunicRuntime(unittest.IsolatedAsyncioTestCase):
    def test_runic_rejects_conflicting_job_backend_configuration(self) -> None:
        with self.assertRaises(ValueError):
            Runic(jobs=JobManager(create_bus(dict)), task_backend=InMemoryTaskBackend())

    def test_register_rejects_non_string_service_names(self) -> None:
        runic = Runic()

        with self.assertRaises(ValueError):
            runic.register(123, service=object())

    async def test_register_returns_handler_for_object_service(self) -> None:
        runic = Runic()

        handler = runic.register(AccountService())

        self.assertIsInstance(handler, Handler)
        result = await handler.ask(GetBalance(user_id=1))
        renamed = await handler.invoke(RenameUser(user_id=1, name="Ada"))

        self.assertEqual(Ok({"balance": Decimal("10.50")}), result)
        self.assertEqual(Ok("renamed:1:Ada"), renamed)
        self.assertIsInstance(handler.service, AccountService)

    async def test_handler_supports_query_only_and_command_only_services(self) -> None:
        runic = Runic()

        query_handler = runic.register(QueryOnlyService())
        command_handler = runic.register(CommandOnlyService())

        self.assertEqual(Ok({"user_id": "7"}), await query_handler.ask(GetUser(user_id=7)))
        self.assertEqual(Ok("cmd:Ada"), await command_handler.invoke(RenameUser(user_id=1, name="Ada")))

        with self.assertRaises(TypeError):
            await query_handler.invoke(RenameUser(user_id=1, name="Ada"))

        with self.assertRaises(TypeError):
            await command_handler.ask(GetUser(user_id=1))

    async def test_publish_fans_out_query_to_all_matching_services(self) -> None:
        runic = Runic()
        first = runic.register(QueryOnlyService())

        class SecondQueryService:
            async def ask(self, query: GetUser) -> Ok[dict[str, str]]:
                return Ok({"user_id": f"secondary:{query.user_id}"})

        second = runic.register(SecondQueryService())

        results = await runic.publish(GetUser(user_id=4))

        self.assertEqual([Ok({"user_id": "4"}), Ok({"user_id": "secondary:4"})], results)
        self.assertIsInstance(first, Handler)
        self.assertIsInstance(second, Handler)

    async def test_publish_returns_empty_list_for_unhandled_query(self) -> None:
        runic = Runic()

        results = await runic.publish(GetUser(user_id=1))

        self.assertEqual([], results)

    async def test_ask_raises_for_missing_or_ambiguous_query(self) -> None:
        runic = Runic()

        with self.assertRaises(ServiceNotFoundError):
            await runic.ask(GetUser(user_id=1))

        runic.register(QueryOnlyService())

        class DuplicateQueryService:
            async def ask(self, query: GetUser) -> Ok[dict[str, str]]:
                return Ok({"user_id": f"duplicate:{query.user_id}"})

        runic.register(DuplicateQueryService())

        with self.assertRaises(AmbiguousQueryError):
            await runic.ask(GetUser(user_id=2))

    async def test_register_rejects_invalid_or_duplicate_object_services(self) -> None:
        runic = Runic()

        class InvalidService:
            pass

        class NonCallableAskService:
            ask = 1

        class NonCallableInvokeService:
            async def ask(self, query: GetUser) -> Ok[dict[str, str]]:
                return Ok({"user_id": str(query.user_id)})

            invoke = 1

        class MissingAnnotationService:
            async def ask(self, query):  # type: ignore[no-untyped-def]
                return Ok("bad")

        class RenameService:
            async def invoke(self, command: RenameUser) -> Ok[str]:
                return Ok(command.name)

        with self.assertRaises(TypeError):
            runic.register(InvalidService())

        with self.assertRaises(TypeError):
            runic.register(NonCallableAskService())

        with self.assertRaises(TypeError):
            runic.register(NonCallableInvokeService())

        with self.assertRaises(TypeError):
            runic.register(MissingAnnotationService())

        runic.register(RenameService())

        with self.assertRaises(DuplicateRegistrationError):
            runic.register(RenameService())

    async def test_query_and_ask_use_request_type_lookup(self) -> None:
        runic = Runic()

        @runic.query(GetUser)
        async def get_user(req: GetUser) -> Ok[dict[str, str]]:
            return Ok({"user_id": str(req.user_id)})

        result = await runic.ask(GetUser(user_id=7))
        direct = await get_user.emit(GetUser(user_id=7))

        self.assertEqual(Ok({"user_id": "7"}), result)
        self.assertEqual(Ok({"user_id": "7"}), direct)
        self.assertIsInstance(get_user.get_key(), DispatcherKey)

    async def test_query_supports_inferred_request_type(self) -> None:
        runic = Runic()

        @runic.query
        async def get_user(req: GetUser) -> Ok[dict[str, str]]:
            return Ok({"user_id": str(req.user_id)})

        result = await runic.ask(GetUser(user_id=11))

        self.assertEqual(Ok({"user_id": "11"}), result)

    async def test_service_decorator_registers_async_function_with_inferred_name(self) -> None:
        runic = Runic()

        @runic.register()
        async def ping(req: Ping) -> Ok[str]:
            return Ok(f"pong:{req.value}")

        self.assertIsInstance(ping, RegistryAdapter)
        direct = await ping(Ping(value="hello"))
        result = await runic.call("ping", Ping(value="hello"))

        self.assertEqual(Ok("pong:hello"), direct)
        self.assertEqual(Ok("pong:hello"), result)
        self.assertIsInstance(ping.get_key(), DispatcherKey)

    async def test_register_rejects_decorated_services_with_unsupported_signatures(self) -> None:
        runic = Runic()

        with self.assertRaises(TypeError):

            @runic.register("bad.signature")
            def bad(first: Ping, second: Ping) -> Ok[str]:
                return Ok(first.value + second.value)

    async def test_register_decorator_registers_sync_function_with_explicit_name(self) -> None:
        runic = Runic()

        @runic.register("users.get")
        def get_user(req: Ping) -> Ok[dict[str, str]]:
            return Ok({"value": req.value.upper()})

        emitted = await get_user.emit(Ping(value="alice"))
        called = await get_user(Ping(value="alice"))
        result = await runic.call("users.get", Ping(value="alice"))

        self.assertEqual(Ok({"value": "ALICE"}), emitted)
        self.assertEqual(Ok({"value": "ALICE"}), called)
        self.assertEqual(Ok({"value": "ALICE"}), result)

    async def test_call_invokes_existing_object_service(self) -> None:
        runic = Runic()

        class PingService(DispatchService[Ping, str, object]):
            async def emit(self, data: Ping) -> Ok[str]:
                return Ok(f"service:{data.value}")

        adapter = runic.register("ping.object", PingService())
        result = await runic.call("ping.object", Ping(value="ok"))
        direct = await adapter.emit(Ping(value="ok"))

        self.assertEqual(Ok("service:ok"), result)
        self.assertEqual(Ok("service:ok"), direct)
        self.assertIsInstance(adapter.get_key(), DispatcherKey)

    async def test_on_and_emit_route_topic_handlers(self) -> None:
        runic = Runic()
        seen: list[Ping] = []
        received = asyncio.Event()

        @runic.on("user.created")
        async def handle(event: Ping) -> None:
            seen.append(event)
            received.set()

        await runic.emit("user.created", Ping(value="event"))
        await asyncio.wait_for(received.wait(), timeout=1.0)

        self.assertEqual([Ping(value="event")], seen)

    async def test_on_and_emit_route_typed_events(self) -> None:
        runic = Runic()
        seen: list[UserCreated] = []
        received = asyncio.Event()

        @runic.on(UserCreated)
        async def handle(event: UserCreated) -> None:
            seen.append(event)
            received.set()

        await runic.emit(UserCreated(user_id=3))
        await asyncio.wait_for(received.wait(), timeout=1.0)

        self.assertEqual([UserCreated(user_id=3)], seen)

    async def test_event_handler_failures_do_not_leave_background_tasks_behind(self) -> None:
        runic = Runic()
        received = asyncio.Event()

        @runic.on("boom")
        async def handle(event: Ping) -> None:
            received.set()
            raise RuntimeError("handler failed")

        await runic.emit("boom", Ping(value="event"))
        await asyncio.wait_for(received.wait(), timeout=1.0)
        await self._wait_for_background_tasks(runic)

        self.assertEqual(set(), runic._background_tasks)

    async def test_task_and_dispatch_start_tracked_work(self) -> None:
        runic = Runic()

        @runic.task("report.generate")
        async def generate_report(ctx, req: Ping) -> Ok[dict[str, bool]]:
            await ctx.log(f"starting:{req.value}")
            await ctx.progress(1.0)
            return Ok({"done": True})

        job_id = await runic.dispatch("report.generate", Ping(value="job"))
        await asyncio.wait_for(self._wait_for_result(runic, job_id), timeout=1.0)

        record = runic.jobs.get_status(job_id)
        self.assertIsInstance(record, Ok)
        assert isinstance(record, Ok)
        self.assertEqual(["starting:job"], record.value.logs)
        self.assertEqual({"done": True}, record.value.result)

    async def test_task_supports_payload_only_and_no_args_signatures(self) -> None:
        runic = Runic()

        @runic.task("payload.only")
        def payload_only(req: Ping) -> Ok[str]:
            return Ok(req.value)

        @runic.task()
        def no_args() -> Ok[str]:
            return Ok("done")

        first_job = await runic.dispatch("payload.only", Ping(value="value"))
        second_job = await runic.dispatch("no_args")

        await asyncio.wait_for(self._wait_for_result(runic, first_job), timeout=1.0)
        await asyncio.wait_for(self._wait_for_result(runic, second_job), timeout=1.0)

        first = runic.jobs.get_status(first_job)
        second = runic.jobs.get_status(second_job)
        self.assertIsInstance(first, Ok)
        self.assertIsInstance(second, Ok)
        assert isinstance(first, Ok)
        assert isinstance(second, Ok)
        self.assertEqual("value", first.value.result)
        self.assertEqual("done", second.value.result)

    async def test_typed_task_and_start_use_payload_type_lookup(self) -> None:
        runic = Runic()

        @runic.task(GenerateReport)
        async def generate_report(ctx, req: GenerateReport) -> Ok[dict[str, str]]:
            await ctx.log(f"starting:{req.report_id}")
            await ctx.progress(1.0)
            return Ok({"report_id": req.report_id})

        job_id = await runic.start(GenerateReport(report_id="r1"))
        await asyncio.wait_for(self._wait_for_result(runic, job_id), timeout=1.0)

        record = runic.jobs.get_status(job_id)
        self.assertIsInstance(record, Ok)
        assert isinstance(record, Ok)
        self.assertEqual(["starting:r1"], record.value.logs)
        self.assertEqual({"report_id": "r1"}, record.value.result)

    async def test_runic_threads_custom_task_backend_into_task_context(self) -> None:
        backend = InMemoryTaskBackend()
        runic = Runic(task_backend=backend)

        @runic.task(GenerateReport)
        async def generate_report(ctx, req: GenerateReport) -> Ok[dict[str, int]]:
            runs = int(ctx.shared.get("runs", 0)) + 1
            ctx.shared["runs"] = runs
            return Ok({"runs": runs})

        first_job = await runic.start(GenerateReport(report_id="one"))
        second_job = await runic.start(GenerateReport(report_id="two"))

        await asyncio.wait_for(self._wait_for_result(runic, first_job), timeout=1.0)
        await asyncio.wait_for(self._wait_for_result(runic, second_job), timeout=1.0)

        first = runic.jobs.get_status(first_job)
        second = runic.jobs.get_status(second_job)
        self.assertIsInstance(first, Ok)
        self.assertIsInstance(second, Ok)
        assert isinstance(first, Ok)
        assert isinstance(second, Ok)
        self.assertEqual({"runs": 1}, first.value.result)
        self.assertEqual({"runs": 2}, second.value.result)
        self.assertEqual(2, backend.shared["runs"])

    async def test_duplicate_registration_errors(self) -> None:
        runic = Runic()

        @runic.register("duplicate")
        async def first(req: Ping) -> Ok[str]:
            return Ok(req.value)

        with self.assertRaises(DuplicateRegistrationError):

            @runic.register("duplicate")
            async def second(req: Ping) -> Ok[str]:
                return Ok(req.value)

        @runic.task("duplicate.task")
        async def task_one() -> Ok[str]:
            return Ok("one")

        with self.assertRaises(DuplicateRegistrationError):

            @runic.task("duplicate.task")
            async def task_two() -> Ok[str]:
                return Ok("two")

        @runic.query(GetUser)
        async def get_user(req: GetUser) -> Ok[str]:
            return Ok(str(req.user_id))

        with self.assertRaises(DuplicateRegistrationError):

            @runic.query(GetUser)
            async def second_query(req: GetUser) -> Ok[str]:
                return Ok(str(req.user_id))

        @runic.task(GenerateReport)
        async def typed_task(ctx, req: GenerateReport) -> Ok[str]:
            return Ok(req.report_id)

        with self.assertRaises(DuplicateRegistrationError):

            @runic.task(GenerateReport)
            async def second_typed_task(ctx, req: GenerateReport) -> Ok[str]:
                return Ok(req.report_id)

        self.assertEqual("first", first.__name__)
        self.assertEqual("task_one", task_one.__name__)
        self.assertEqual("get_user", get_user.__name__)
        self.assertEqual("typed_task", typed_task.__name__)

    async def test_missing_service_and_task_errors(self) -> None:
        runic = Runic()

        with self.assertRaises(ServiceNotFoundError):
            await runic.call("missing")

        with self.assertRaises(TaskNotFoundError):
            await runic.dispatch("missing")

        with self.assertRaises(ServiceNotFoundError):
            await runic.ask(GetUser(user_id=1))

        with self.assertRaises(TaskNotFoundError):
            await runic.start(GenerateReport(report_id="missing"))

    async def test_register_supports_no_arg_services(self) -> None:
        runic = Runic()

        @runic.register()
        def ping() -> Ok[str]:
            return Ok("pong")

        self.assertEqual(Ok("pong"), await ping())
        self.assertEqual(Ok("pong"), await ping.emit())
        self.assertIsInstance(ping.get_key(), DispatcherKey)

    async def _wait_for_result(self, runic: Runic, job_id: str) -> None:
        while True:
            record = runic.jobs.get_status(job_id)
            if isinstance(record, Ok) and record.value.result is not None:
                return
            await asyncio.sleep(0.01)

    async def _wait_for_background_tasks(self, runic: Runic) -> None:
        while runic._background_tasks:
            await asyncio.sleep(0.01)
