from __future__ import annotations

import asyncio
import unittest
from dataclasses import dataclass

from runic import (
    Conduit,
    DefaultError,
    Err,
    InMemorySpellBook,
    Ok,
    Pending,
    ResultStatus,
    SpellContext,
    SpellLog,
    SpellRecord,
    SpellRetryPolicy,
    SpellStatus,
    create_bus,
)


@dataclass(slots=True)
class ExamplePayload:
    done: bool


class CustomMessageError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class TestConduit(unittest.IsolatedAsyncioTestCase):
    async def test_spell_lifecycle_progress_logs_and_result(self) -> None:
        bus = create_bus(dict)
        conduit = Conduit(bus)
        subscriber = bus.subscribe()
        status_subscriber = conduit.status_events()
        log_subscriber = conduit.log_events()

        async def work(ctx):
            await ctx.log("started")
            await ctx.progress(0.5)
            await ctx.emit("progress", {"value": 0.5})
            await ctx.log("finished")
            await ctx.progress(1.0)
            return Ok({"done": True})

        try:
            spell_id = await conduit.invoke(work)
            status_event = await asyncio.wait_for(anext(status_subscriber), timeout=1.0)
            log_event = await asyncio.wait_for(anext(log_subscriber), timeout=1.0)
            observed_events = [await asyncio.wait_for(anext(subscriber), timeout=1.0)]
            await asyncio.wait_for(self._wait_for_status(conduit, spell_id, SpellStatus.SUCCEEDED), timeout=1.0)
        finally:
            await subscriber.aclose()
            await status_subscriber.aclose()
            await log_subscriber.aclose()

        record = conduit.get_status(spell_id)
        self.assertIsInstance(record, Ok)
        assert isinstance(record, Ok)
        job = record.value
        self.assertEqual("spell_status", status_event.name)
        self.assertEqual("running", status_event.data.status)
        self.assertEqual("spell_log", log_event.name)
        self.assertEqual("started", log_event.data.message)
        self.assertIn("progress", [event.name for event in observed_events])
        self.assertIs(SpellStatus.SUCCEEDED, job.status)
        self.assertEqual(1.0, job.progress)
        self.assertEqual(["started", "finished"], job.logs)
        self.assertEqual({"done": True}, job.result)

    async def test_stop_cancels_running_spell(self) -> None:
        conduit = Conduit(create_bus(dict))
        release = asyncio.Event()

        async def work(ctx):
            await ctx.log("waiting")
            await release.wait()

        spell_id = await conduit.invoke(work)
        await asyncio.sleep(0.01)
        stopped = await conduit.stop(spell_id)
        self.assertTrue(stopped)
        await asyncio.wait_for(self._wait_for_status(conduit, spell_id, SpellStatus.CANCELLED), timeout=1.0)

        record = conduit.get_status(spell_id)
        self.assertIsInstance(record, Ok)
        assert isinstance(record, Ok)
        self.assertIs(SpellStatus.CANCELLED, record.value.status)

    async def test_error_result_uses_message_field(self) -> None:
        conduit = Conduit(create_bus(dict))

        async def work(ctx):
            return CustomMessageError("custom failure")

        spell_id = await conduit.invoke(work)
        await asyncio.wait_for(self._wait_for_status(conduit, spell_id, SpellStatus.SUCCEEDED), timeout=1.0)

        record = conduit.get_status(spell_id)
        self.assertIsInstance(record, Ok)
        assert isinstance(record, Ok)
        self.assertEqual({"message": "custom failure"}, record.value.result)

    async def test_err_result_records_failure_message(self) -> None:
        conduit = Conduit(create_bus(dict))

        async def work(ctx):
            from runic import Err

            return Err(DefaultError(message="request failed", code="bad_request"))

        spell_id = await conduit.invoke(work)
        await asyncio.wait_for(self._wait_for_status(conduit, spell_id, SpellStatus.FAILED), timeout=1.0)

        record = conduit.get_status(spell_id)
        self.assertIsInstance(record, Ok)
        assert isinstance(record, Ok)
        self.assertEqual("request failed", record.value.error)
        self.assertIsNone(record.value.result)

    async def test_progress_is_clamped(self) -> None:
        conduit = Conduit(create_bus(dict))

        async def work(ctx):
            await ctx.progress(2.5)
            return Ok(ExamplePayload(done=True))

        spell_id = await conduit.invoke(work)
        await asyncio.wait_for(self._wait_for_status(conduit, spell_id, SpellStatus.SUCCEEDED), timeout=1.0)

        record = conduit.get_status(spell_id)
        self.assertIsInstance(record, Ok)
        assert isinstance(record, Ok)
        self.assertEqual(1.0, record.value.progress)
        self.assertEqual({"done": True}, record.value.result)

    async def test_start_passes_typed_context_data(self) -> None:
        conduit = Conduit(create_bus(dict))

        async def work(ctx):
            assert ctx.data is not None
            return Ok({"value": ctx.data["value"]})

        spell_id = await conduit.invoke(work, data={"value": 7})
        await asyncio.wait_for(self._wait_for_status(conduit, spell_id, SpellStatus.SUCCEEDED), timeout=1.0)

        record = conduit.get_status(spell_id)
        self.assertIsInstance(record, Ok)
        assert isinstance(record, Ok)
        self.assertEqual({"value": 7}, record.value.result)

    async def test_backend_shared_state_is_visible_across_spells(self) -> None:
        spellbook = InMemorySpellBook()
        conduit = Conduit(create_bus(dict), spellbook=spellbook)

        async def work(ctx):
            current = int(ctx.shared.get("runs", 0))
            ctx.shared["runs"] = current + 1
            return Ok({"runs": ctx.shared["runs"]})

        first_spell = await conduit.invoke(work)
        second_spell = await conduit.invoke(work)

        await asyncio.wait_for(self._wait_for_status(conduit, first_spell, SpellStatus.SUCCEEDED), timeout=1.0)
        await asyncio.wait_for(self._wait_for_status(conduit, second_spell, SpellStatus.SUCCEEDED), timeout=1.0)

        first = conduit.get_status(first_spell)
        second = conduit.get_status(second_spell)
        self.assertIsInstance(first, Ok)
        self.assertIsInstance(second, Ok)
        assert isinstance(first, Ok)
        assert isinstance(second, Ok)
        self.assertEqual({"runs": 1}, first.value.result)
        self.assertEqual({"runs": 2}, second.value.result)
        self.assertEqual(2, spellbook.shared["runs"])

    async def test_spell_context_defaults_shared_state_when_none_is_passed(self) -> None:
        context = SpellContext(
            spell_id="spell-1",
            bus=create_bus(dict),
            log_bus=create_bus(SpellLog),
            record=SpellRecord(spell_id="spell-1"),
        )

        self.assertEqual({}, context.shared)
        await context.progress(-1.5)
        self.assertEqual(0.0, context.record.progress)

    def test_get_status_returns_err_for_missing_spell(self) -> None:
        conduit = Conduit(create_bus(dict))

        result = conduit.get_status("missing")

        self.assertIsInstance(result, Err)
        assert isinstance(result, Err)
        self.assertEqual("Unknown spell: missing", result.error.message)
        self.assertEqual("spell_not_found", result.error.code)

    def test_get_spell_result_returns_err_for_missing_spell(self) -> None:
        conduit = Conduit(create_bus(dict))

        result = conduit.get_spell_result("missing")

        self.assertIsInstance(result, Err)
        assert isinstance(result, Err)
        self.assertEqual("Unknown spell: missing", result.error.message)
        self.assertEqual("spell_not_found", result.error.code)

    async def test_wait_returns_finished_result_without_polling(self) -> None:
        conduit = Conduit(create_bus(dict))
        release = asyncio.Event()

        async def work(ctx):
            await release.wait()
            return Ok({"done": True})

        spell_id = await conduit.invoke(work)
        waiter = asyncio.create_task(conduit.wait(spell_id))

        await asyncio.sleep(0.01)
        self.assertFalse(waiter.done())

        release.set()
        result = await asyncio.wait_for(waiter, timeout=1.0)

        self.assertEqual(Ok({"done": True}), result)

    async def test_wait_raises_timeout_for_incomplete_spell(self) -> None:
        conduit = Conduit(create_bus(dict))
        release = asyncio.Event()

        async def work(ctx):
            await release.wait()
            return Ok({"done": True})

        spell_id = await conduit.invoke(work)

        with self.assertRaises(asyncio.TimeoutError):
            await conduit.wait(spell_id, timeout=0.01)

        release.set()
        await asyncio.wait_for(conduit.wait(spell_id), timeout=1.0)

    async def test_wait_for_status_returns_final_record_when_target_is_unreachable(self) -> None:
        conduit = Conduit(create_bus(dict))

        async def work(ctx):
            return Err(DefaultError(message="failed", code="bad_request"))

        spell_id = await conduit.invoke(work)
        record = await asyncio.wait_for(conduit.wait_for_status(spell_id, SpellStatus.SUCCEEDED), timeout=1.0)

        self.assertIsInstance(record, Ok)
        assert isinstance(record, Ok)
        self.assertIs(SpellStatus.FAILED, record.value.status)
        self.assertEqual("failed", record.value.error)

    async def test_watch_yields_spell_specific_status_stream(self) -> None:
        conduit = Conduit(create_bus(dict))

        async def work(ctx):
            return Ok({"done": True})

        spell_id = await conduit.invoke(work, delay=0.05)
        events = await asyncio.wait_for(self._collect_watch(conduit, spell_id), timeout=1.0)

        self.assertTrue(events)
        self.assertEqual(spell_id, events[0].spell_id)
        self.assertEqual("pending", events[0].status)
        self.assertTrue(all(event.spell_id == spell_id for event in events))
        self.assertIn("running", [event.status for event in events])
        self.assertEqual("succeeded", events[-1].status)

    async def test_plain_results_are_persisted_and_cleared_from_futures(self) -> None:
        conduit = Conduit(create_bus(dict))

        def work(ctx):
            return {"done": True}

        spell_id = await conduit.invoke(work)
        await asyncio.wait_for(self._wait_for_status(conduit, spell_id, SpellStatus.SUCCEEDED), timeout=1.0)

        record = conduit.get_status(spell_id)
        self.assertIsInstance(record, Ok)
        assert isinstance(record, Ok)
        self.assertEqual(SpellStatus.SUCCEEDED, record.value.status)
        self.assertEqual({"done": True}, record.value.result)
        spell_result = conduit.get_spell_result(spell_id)
        self.assertIsInstance(spell_result, Ok)
        assert isinstance(spell_result, Ok)
        self.assertEqual({"done": True}, spell_result.value)
        await self._wait_for_finalization(conduit, spell_id)
        self.assertNotIn(spell_id, conduit._futures)

    async def test_err_results_without_message_attributes_use_string_fallback(self) -> None:
        conduit = Conduit(create_bus(dict))

        async def work(ctx):
            return Err(ValueError("broken"))

        spell_id = await conduit.invoke(work)
        await asyncio.wait_for(self._wait_for_status(conduit, spell_id, SpellStatus.FAILED), timeout=1.0)

        record = conduit.get_status(spell_id)
        self.assertIsInstance(record, Ok)
        assert isinstance(record, Ok)
        self.assertEqual("broken", record.value.error)
        self.assertIsNone(record.value.result)
        spell_result = conduit.get_spell_result(spell_id)
        self.assertIsInstance(spell_result, Err)
        assert isinstance(spell_result, Err)
        self.assertEqual("broken", spell_result.error.message)
        self.assertEqual("spell_failed", spell_result.error.code)

    async def test_err_results_preserve_default_error_code_and_details(self) -> None:
        conduit = Conduit(create_bus(dict))
        details = {"stderr": "pull model manifest: file does not exist"}

        async def work(ctx):
            return Err(DefaultError(message="Runner command failed.", code="runner_command_failed", details=details))

        spell_id = await conduit.invoke(work)
        await asyncio.wait_for(self._wait_for_status(conduit, spell_id, SpellStatus.FAILED), timeout=1.0)

        spell_result = conduit.get_spell_result(spell_id)

        self.assertIsInstance(spell_result, Err)
        assert isinstance(spell_result, Err)
        self.assertEqual("Runner command failed.", spell_result.error.message)
        self.assertEqual("runner_command_failed", spell_result.error.code)
        self.assertEqual(details, spell_result.error.details)

    async def test_failing_spells_record_exception_message_and_clear_future(self) -> None:
        conduit = Conduit(create_bus(dict))

        async def work(ctx):
            raise RuntimeError("boom")

        spell_id = await conduit.invoke(work)
        await asyncio.wait_for(self._wait_for_status(conduit, spell_id, SpellStatus.FAILED), timeout=1.0)

        record = conduit.get_status(spell_id)
        self.assertIsInstance(record, Ok)
        assert isinstance(record, Ok)
        self.assertEqual(SpellStatus.FAILED, record.value.status)
        self.assertEqual("boom", record.value.error)
        await self._wait_for_finalization(conduit, spell_id)
        self.assertNotIn(spell_id, conduit._futures)

    async def test_stop_returns_false_for_missing_or_completed_spells(self) -> None:
        conduit = Conduit(create_bus(dict))

        self.assertFalse(await conduit.stop("missing"))

        loop = asyncio.get_running_loop()
        future = loop.create_future()
        future.set_result(None)
        spell_id = "done"
        conduit._records[spell_id] = SpellRecord(spell_id=spell_id)
        conduit._futures[spell_id] = future

        self.assertFalse(await conduit.stop(spell_id))

    async def test_get_spell_result_returns_pending_for_incomplete_spell(self) -> None:
        conduit = Conduit(create_bus(dict))
        release = asyncio.Event()

        async def work(ctx):
            await release.wait()
            return Ok({"done": True})

        spell_id = await conduit.invoke(work)
        await asyncio.wait_for(self._wait_for_status(conduit, spell_id, SpellStatus.RUNNING), timeout=1.0)

        result = conduit.get_spell_result(spell_id)

        self.assertIsInstance(result, Pending)
        assert isinstance(result, Pending)
        self.assertIs(ResultStatus.PENDING, result.status)
        self.assertFalse(result)

        release.set()
        await asyncio.wait_for(self._wait_for_status(conduit, spell_id, SpellStatus.SUCCEEDED), timeout=1.0)

    async def test_get_spell_result_returns_err_for_cancelled_spell(self) -> None:
        conduit = Conduit(create_bus(dict))
        release = asyncio.Event()

        async def work(ctx):
            await release.wait()

        spell_id = await conduit.invoke(work)
        await asyncio.wait_for(self._wait_for_status(conduit, spell_id, SpellStatus.RUNNING), timeout=1.0)
        self.assertTrue(await conduit.stop(spell_id))

        result = conduit.get_spell_result(spell_id)

        self.assertIsInstance(result, Err)
        assert isinstance(result, Err)
        self.assertEqual(f"Spell cancelled: {spell_id}", result.error.message)
        self.assertEqual("spell_cancelled", result.error.code)

    async def test_retry_policy_retries_exceptions_until_success(self) -> None:
        conduit = Conduit(create_bus(dict))
        attempts = 0

        async def work(ctx):
            nonlocal attempts
            attempts += 1
            self.assertEqual(attempts, ctx.attempt)
            self.assertEqual(3, ctx.max_attempts)
            self.assertEqual("spell:report-1", ctx.idempotency_key)
            if attempts < 3:
                raise RuntimeError(f"boom:{attempts}")
            return Ok({"attempt": ctx.attempt})

        spell_id = await conduit.invoke(
            work,
            retry=SpellRetryPolicy(max_attempts=3),
            idempotency_key="spell:report-1",
        )
        await asyncio.wait_for(self._wait_for_status(conduit, spell_id, SpellStatus.SUCCEEDED), timeout=1.0)

        record = conduit.get_status(spell_id)
        self.assertIsInstance(record, Ok)
        assert isinstance(record, Ok)
        self.assertEqual(3, attempts)
        self.assertEqual(3, record.value.attempt)
        self.assertEqual(3, record.value.max_attempts)
        self.assertEqual("spell:report-1", record.value.idempotency_key)
        self.assertEqual({"attempt": 3}, record.value.result)

    async def test_retry_policy_retries_err_results_until_limit(self) -> None:
        conduit = Conduit(create_bus(dict))
        attempts = 0

        async def work(ctx):
            nonlocal attempts
            attempts += 1
            return Err(DefaultError(message=f"failed:{ctx.attempt}", code="bad_request"))

        spell_id = await conduit.invoke(work, retry=SpellRetryPolicy(max_attempts=2))
        await asyncio.wait_for(self._wait_for_status(conduit, spell_id, SpellStatus.FAILED), timeout=1.0)

        record = conduit.get_status(spell_id)
        self.assertIsInstance(record, Ok)
        assert isinstance(record, Ok)
        self.assertEqual(2, attempts)
        self.assertEqual(2, record.value.attempt)
        self.assertEqual(2, record.value.max_attempts)
        self.assertEqual("failed:2", record.value.error)

    async def test_delay_keeps_spell_pending_until_first_attempt(self) -> None:
        conduit = Conduit(create_bus(dict))
        started = asyncio.Event()

        async def work(ctx):
            started.set()
            return Ok({"done": True})

        spell_id = await conduit.invoke(work, delay=0.05)
        record = conduit.get_status(spell_id)
        self.assertIsInstance(record, Ok)
        assert isinstance(record, Ok)
        self.assertIs(SpellStatus.PENDING, record.value.status)
        self.assertEqual(0, record.value.attempt)

        await asyncio.sleep(0.01)
        self.assertFalse(started.is_set())

        await asyncio.wait_for(self._wait_for_status(conduit, spell_id, SpellStatus.SUCCEEDED), timeout=1.0)

    async def test_stop_cancels_delayed_spell_before_first_attempt(self) -> None:
        conduit = Conduit(create_bus(dict))
        started = False

        async def work(ctx):
            nonlocal started
            started = True
            return Ok({"done": True})

        spell_id = await conduit.invoke(work, delay=0.1)

        self.assertTrue(await conduit.stop(spell_id))

        record = conduit.get_status(spell_id)
        self.assertIsInstance(record, Ok)
        assert isinstance(record, Ok)
        self.assertIs(SpellStatus.CANCELLED, record.value.status)
        self.assertEqual(0, record.value.attempt)
        self.assertFalse(started)

    async def test_idempotency_key_reuses_existing_spell(self) -> None:
        conduit = Conduit(create_bus(dict))
        release = asyncio.Event()
        started = asyncio.Event()
        runs = 0

        async def work(ctx):
            nonlocal runs
            runs += 1
            started.set()
            await release.wait()
            return Ok({"runs": runs})

        first_spell_id = await conduit.invoke(work, idempotency_key="widget:1")
        second_spell_id = await conduit.invoke(work, idempotency_key="widget:1")

        self.assertEqual(first_spell_id, second_spell_id)
        await asyncio.wait_for(started.wait(), timeout=1.0)
        await asyncio.sleep(0.01)
        self.assertEqual(1, runs)

        release.set()
        await asyncio.wait_for(self._wait_for_status(conduit, first_spell_id, SpellStatus.SUCCEEDED), timeout=1.0)

        third_spell_id = await conduit.invoke(work, idempotency_key="widget:1")
        self.assertEqual(first_spell_id, third_spell_id)
        await asyncio.sleep(0.01)
        self.assertEqual(1, runs)

    async def _wait_for_status(self, conduit: Conduit, spell_id: str, expected: SpellStatus) -> None:
        record = await conduit.wait_for_status(spell_id, expected, timeout=1.0)
        self.assertIsInstance(record, Ok)
        assert isinstance(record, Ok)
        self.assertIs(expected, record.value.status)

    async def _wait_for_finalization(self, conduit: Conduit, spell_id: str) -> None:
        while spell_id in conduit._futures:
            await asyncio.sleep(0.01)

    async def _collect_watch(self, conduit: Conduit, spell_id: str) -> list[object]:
        return [event async for event in conduit.watch(spell_id)]
