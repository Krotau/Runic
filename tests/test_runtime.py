from __future__ import annotations

import asyncio
import unittest
from dataclasses import dataclass
from decimal import Decimal

from runic import (
    AmbiguousQueryError,
    Command,
    Conjurable,
    Conduit,
    ConjurerKey,
    DuplicateRegistrationError,
    Handler,
    InMemorySpellBook,
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
    def test_runic_rejects_conflicting_conduit_spellbook_configuration(self) -> None:
        with self.assertRaises(ValueError):
            Runic(conduit=Conduit(create_bus(dict)), spellbook=InMemorySpellBook())

    def test_conjure_rejects_non_string_service_names(self) -> None:
        runic = Runic()

        with self.assertRaises(ValueError):
            runic.conjure(123, service=object())

    async def test_conjure_returns_handler_for_object_service(self) -> None:
        runic = Runic()

        handler = runic.conjure(AccountService())

        self.assertIsInstance(handler, Handler)
        result = await handler.ask(GetBalance(user_id=1))
        renamed = await handler.invoke(RenameUser(user_id=1, name="Ada"))

        self.assertEqual(Ok({"balance": Decimal("10.50")}), result)
        self.assertEqual(Ok("renamed:1:Ada"), renamed)
        self.assertIsInstance(handler.service, AccountService)

    async def test_handler_supports_query_only_and_command_only_services(self) -> None:
        runic = Runic()

        query_handler = runic.conjure(QueryOnlyService())
        command_handler = runic.conjure(CommandOnlyService())

        self.assertEqual(Ok({"user_id": "7"}), await query_handler.ask(GetUser(user_id=7)))
        self.assertEqual(Ok("cmd:Ada"), await command_handler.invoke(RenameUser(user_id=1, name="Ada")))

        with self.assertRaises(TypeError):
            await query_handler.invoke(RenameUser(user_id=1, name="Ada"))

        with self.assertRaises(TypeError):
            await command_handler.ask(GetUser(user_id=1))

    async def test_publish_fans_out_query_to_all_matching_services(self) -> None:
        runic = Runic()
        first = runic.conjure(QueryOnlyService())

        class SecondQueryService:
            async def ask(self, query: GetUser) -> Ok[dict[str, str]]:
                return Ok({"user_id": f"secondary:{query.user_id}"})

        second = runic.conjure(SecondQueryService())

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

        runic.conjure(QueryOnlyService())

        class DuplicateQueryService:
            async def ask(self, query: GetUser) -> Ok[dict[str, str]]:
                return Ok({"user_id": f"duplicate:{query.user_id}"})

        runic.conjure(DuplicateQueryService())

        with self.assertRaises(AmbiguousQueryError):
            await runic.ask(GetUser(user_id=2))

    async def test_execute_routes_typed_commands(self) -> None:
        runic = Runic()
        runic.conjure(AccountService())

        result = await runic.execute(RenameUser(user_id=1, name="Ada"))

        self.assertEqual(Ok("renamed:1:Ada"), result)

    async def test_execute_raises_for_missing_command_handler(self) -> None:
        runic = Runic()

        with self.assertRaises(ServiceNotFoundError):
            await runic.execute(RenameUser(user_id=1, name="Ada"))

    async def test_conjure_rejects_invalid_or_duplicate_object_services(self) -> None:
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
            runic.conjure(InvalidService())

        with self.assertRaises(TypeError):
            runic.conjure(NonCallableAskService())

        with self.assertRaises(TypeError):
            runic.conjure(NonCallableInvokeService())

        with self.assertRaises(TypeError):
            runic.conjure(MissingAnnotationService())

        runic.conjure(RenameService())

        with self.assertRaises(DuplicateRegistrationError):
            runic.conjure(RenameService())

    async def test_query_and_ask_use_request_type_lookup(self) -> None:
        runic = Runic()

        @runic.query(GetUser)
        async def get_user(req: GetUser) -> Ok[dict[str, str]]:
            return Ok({"user_id": str(req.user_id)})

        result = await runic.ask(GetUser(user_id=7))
        direct = await get_user.emit(GetUser(user_id=7))

        self.assertEqual(Ok({"user_id": "7"}), result)
        self.assertEqual(Ok({"user_id": "7"}), direct)
        self.assertIsInstance(get_user.get_key(), ConjurerKey)

    async def test_query_supports_inferred_request_type(self) -> None:
        runic = Runic()

        @runic.query
        async def get_user(req: GetUser) -> Ok[dict[str, str]]:
            return Ok({"user_id": str(req.user_id)})

        result = await runic.ask(GetUser(user_id=11))

        self.assertEqual(Ok({"user_id": "11"}), result)

    async def test_conjure_decorator_registers_async_function_with_inferred_name(self) -> None:
        runic = Runic()

        @runic.conjure()
        async def ping(req: Ping) -> Ok[str]:
            return Ok(f"pong:{req.value}")

        self.assertIsInstance(ping, RegistryAdapter)
        direct = await ping(Ping(value="hello"))
        result = await runic.call("ping", Ping(value="hello"))

        self.assertEqual(Ok("pong:hello"), direct)
        self.assertEqual(Ok("pong:hello"), result)
        self.assertIsInstance(ping.get_key(), ConjurerKey)

    async def test_conjure_rejects_decorated_services_with_unsupported_signatures(self) -> None:
        runic = Runic()

        with self.assertRaises(TypeError):

            @runic.conjure("bad.signature")
            def bad(first: Ping, second: Ping) -> Ok[str]:
                return Ok(first.value + second.value)

    async def test_conjure_decorator_registers_sync_function_with_explicit_name(self) -> None:
        runic = Runic()

        @runic.conjure("users.get")
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

        class PingService(Conjurable[Ping, str, object]):
            async def emit(self, data: Ping) -> Ok[str]:
                return Ok(f"service:{data.value}")

        adapter = runic.conjure("ping.object", PingService())
        result = await runic.call("ping.object", Ping(value="ok"))
        direct = await adapter.emit(Ping(value="ok"))

        self.assertEqual(Ok("service:ok"), result)
        self.assertEqual(Ok("service:ok"), direct)
        self.assertIsInstance(adapter.get_key(), ConjurerKey)

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

    async def test_duplicate_registration_errors(self) -> None:
        runic = Runic()

        @runic.conjure("duplicate")
        async def first(req: Ping) -> Ok[str]:
            return Ok(req.value)

        with self.assertRaises(DuplicateRegistrationError):

            @runic.conjure("duplicate")
            async def second(req: Ping) -> Ok[str]:
                return Ok(req.value)

        @runic.query(GetUser)
        async def get_user(req: GetUser) -> Ok[str]:
            return Ok(str(req.user_id))

        with self.assertRaises(DuplicateRegistrationError):

            @runic.query(GetUser)
            async def second_query(req: GetUser) -> Ok[str]:
                return Ok(str(req.user_id))

        self.assertEqual("first", first.__name__)
        self.assertEqual("get_user", get_user.__name__)

    async def test_missing_service_and_spell_errors(self) -> None:
        runic = Runic()

        with self.assertRaises(ServiceNotFoundError):
            await runic.call("missing")

        with self.assertRaises(TaskNotFoundError):
            await runic.invoke("missing")

        with self.assertRaises(ServiceNotFoundError):
            await runic.ask(GetUser(user_id=1))

    async def test_conjure_supports_no_arg_services(self) -> None:
        runic = Runic()

        @runic.conjure()
        def ping() -> Ok[str]:
            return Ok("pong")

        self.assertEqual(Ok("pong"), await ping())
        self.assertEqual(Ok("pong"), await ping.emit())
        self.assertIsInstance(ping.get_key(), ConjurerKey)

    async def _wait_for_background_tasks(self, runic: Runic) -> None:
        while runic._background_tasks:
            await asyncio.sleep(0.01)
