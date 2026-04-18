from __future__ import annotations

import asyncio
import unittest
from dataclasses import dataclass

from runic import (
    Command,
    DefaultError,
    DuplicateRegistrationError,
    Err,
    InMemorySpellBook,
    Ok,
    Runic,
    SpellRetryPolicy,
    TaskNotFoundError,
)


@dataclass(slots=True)
class Ping:
    value: str


@dataclass(slots=True)
class GenerateReport:
    report_id: str


@dataclass(slots=True)
class GenerateTypedReport(Command[dict[str, str], DefaultError]):
    report_id: str


class TestRunicSpells(unittest.IsolatedAsyncioTestCase):
    async def test_spell_and_invoke_start_tracked_work(self) -> None:
        runic = Runic()

        @runic.spell("report.generate")
        async def generate_report(ctx, req: Ping) -> Ok[dict[str, bool]]:
            await ctx.log(f"starting:{req.value}")
            await ctx.progress(1.0)
            return Ok({"done": True})

        spell_id = await runic.invoke("report.generate", Ping(value="job"))
        await asyncio.wait_for(self._wait_for_result(runic, spell_id), timeout=1.0)

        record = runic.conduit.get_status(spell_id)
        self.assertIsInstance(record, Ok)
        assert isinstance(record, Ok)
        self.assertEqual(["starting:job"], record.value.logs)
        self.assertEqual({"done": True}, record.value.result)

    async def test_spell_supports_payload_only_and_no_args_signatures(self) -> None:
        runic = Runic()

        @runic.spell("payload.only")
        def payload_only(req: Ping) -> Ok[str]:
            return Ok(req.value)

        @runic.spell()
        def no_args() -> Ok[str]:
            return Ok("done")

        first_job = await runic.invoke("payload.only", Ping(value="value"))
        second_job = await runic.invoke("no_args")

        await asyncio.wait_for(self._wait_for_result(runic, first_job), timeout=1.0)
        await asyncio.wait_for(self._wait_for_result(runic, second_job), timeout=1.0)

        first = runic.conduit.get_status(first_job)
        second = runic.conduit.get_status(second_job)
        self.assertIsInstance(first, Ok)
        self.assertIsInstance(second, Ok)
        assert isinstance(first, Ok)
        assert isinstance(second, Ok)
        self.assertEqual("value", first.value.result)
        self.assertEqual("done", second.value.result)

    def test_spell_rejects_unsupported_signatures_with_clear_error(self) -> None:
        runic = Runic()

        with self.assertRaisesRegex(
            TypeError,
            r"Unsupported spell signature for too_many\([^)]*first[^)]*second[^)]*third[^)]*\)\s*(->[^.]*)?\. "
            r"Supported spell forms are fn\(\), fn\(req\), and fn\(ctx, req\)\.",
        ):

            @runic.spell("too.many")
            def too_many(first: Ping, second: Ping, third: Ping) -> Ok[str]:
                return Ok(first.value + second.value + third.value)

        with self.assertRaisesRegex(
            TypeError,
            r"Unsupported spell signature for required_kw_only\([^)]*req[^)]*\)\s*(->[^.]*)?\. "
            r"Supported spell forms are fn\(\), fn\(req\), and fn\(ctx, req\)\.",
        ):

            @runic.spell("required.kw.only")
            def required_kw_only(*, req: Ping) -> Ok[str]:
                return Ok(req.value)

    async def test_typed_spell_and_invoke_use_payload_type_lookup(self) -> None:
        runic = Runic()

        @runic.spell(GenerateReport)
        async def generate_report(ctx, req: GenerateReport) -> Ok[dict[str, str]]:
            await ctx.log(f"starting:{req.report_id}")
            await ctx.progress(1.0)
            return Ok({"report_id": req.report_id})

        job_id = await runic.invoke(GenerateReport(report_id="r1"))
        await asyncio.wait_for(self._wait_for_result(runic, job_id), timeout=1.0)

        record = runic.conduit.get_status(job_id)
        self.assertIsInstance(record, Ok)
        assert isinstance(record, Ok)
        self.assertEqual(["starting:r1"], record.value.logs)
        self.assertEqual({"report_id": "r1"}, record.value.result)

    async def test_cast_returns_finished_spell_result_for_typed_request(self) -> None:
        runic = Runic()

        @runic.spell(GenerateTypedReport)
        async def generate_report(ctx, req: GenerateTypedReport) -> dict[str, str]:
            await ctx.progress(1.0)
            return {"report_id": req.report_id}

        result = await runic.cast(GenerateTypedReport(report_id="typed"))

        self.assertEqual(Ok({"report_id": "typed"}), result)

    async def test_cast_returns_err_when_spell_finishes_in_failed_state(self) -> None:
        runic = Runic()

        @runic.spell(GenerateTypedReport)
        async def generate_report(ctx, req: GenerateTypedReport):  # type: ignore[no-untyped-def]
            return Err(DefaultError(message=f"failed:{req.report_id}", code="spell_failed"))

        result = await runic.cast(GenerateTypedReport(report_id="typed"))

        self.assertEqual(Err(DefaultError(message="failed:typed", code="spell_failed")), result)

    async def test_cast_supports_retry_policy_for_typed_spells(self) -> None:
        runic = Runic()
        attempts = 0

        @runic.spell(GenerateTypedReport)
        async def generate_report(ctx, req: GenerateTypedReport):  # type: ignore[no-untyped-def]
            nonlocal attempts
            attempts += 1
            if ctx.attempt == 1:
                return Err(DefaultError(message=f"retry:{req.report_id}", code="retry"))
            return {"report_id": req.report_id, "attempt": ctx.attempt}

        result = await runic.cast(
            GenerateTypedReport(report_id="typed"),
            retry=SpellRetryPolicy(max_attempts=2),
        )

        self.assertEqual(2, attempts)
        self.assertEqual(Ok({"report_id": "typed", "attempt": 2}), result)

    async def test_runic_invoke_supports_idempotency_for_named_spells(self) -> None:
        runic = Runic()
        release = asyncio.Event()
        started = asyncio.Event()
        runs = 0

        @runic.spell("report.generate")
        async def generate_report(ctx, req: Ping) -> Ok[dict[str, int]]:
            nonlocal runs
            runs += 1
            started.set()
            await release.wait()
            return Ok({"runs": runs})

        first_job = await runic.invoke("report.generate", Ping(value="job"), idempotency_key="report:job")
        second_job = await runic.invoke("report.generate", Ping(value="job"), idempotency_key="report:job")

        self.assertEqual(first_job, second_job)
        await asyncio.wait_for(started.wait(), timeout=1.0)
        await asyncio.sleep(0.01)
        self.assertEqual(1, runs)

        release.set()
        await asyncio.wait_for(self._wait_for_result(runic, first_job), timeout=1.0)

        third_job = await runic.invoke("report.generate", Ping(value="job"), idempotency_key="report:job")
        self.assertEqual(first_job, third_job)
        await asyncio.sleep(0.01)
        self.assertEqual(1, runs)

    async def test_runic_threads_custom_spellbook_into_spell_context(self) -> None:
        spellbook = InMemorySpellBook()
        runic = Runic(spellbook=spellbook)

        @runic.spell(GenerateReport)
        async def generate_report(ctx, req: GenerateReport) -> Ok[dict[str, int]]:
            runs = int(ctx.shared.get("runs", 0)) + 1
            ctx.shared["runs"] = runs
            return Ok({"runs": runs})

        first_job = await runic.invoke(GenerateReport(report_id="one"))
        second_job = await runic.invoke(GenerateReport(report_id="two"))

        await asyncio.wait_for(self._wait_for_result(runic, first_job), timeout=1.0)
        await asyncio.wait_for(self._wait_for_result(runic, second_job), timeout=1.0)

        first = runic.conduit.get_status(first_job)
        second = runic.conduit.get_status(second_job)
        self.assertIsInstance(first, Ok)
        self.assertIsInstance(second, Ok)
        assert isinstance(first, Ok)
        assert isinstance(second, Ok)
        self.assertEqual({"runs": 1}, first.value.result)
        self.assertEqual({"runs": 2}, second.value.result)
        self.assertEqual(2, spellbook.shared["runs"])

    async def test_spell_duplicate_registration_errors(self) -> None:
        runic = Runic()

        @runic.spell("duplicate.task")
        async def task_one() -> Ok[str]:
            return Ok("one")

        with self.assertRaises(DuplicateRegistrationError):

            @runic.spell("duplicate.task")
            async def task_two() -> Ok[str]:
                return Ok("two")

        @runic.spell(GenerateReport)
        async def typed_task(ctx, req: GenerateReport) -> Ok[str]:
            return Ok(req.report_id)

        with self.assertRaises(DuplicateRegistrationError):

            @runic.spell(GenerateReport)
            async def second_typed_task(ctx, req: GenerateReport) -> Ok[str]:
                return Ok(req.report_id)

        self.assertEqual("task_one", task_one.__name__)
        self.assertEqual("typed_task", typed_task.__name__)

    async def test_missing_spell_errors(self) -> None:
        runic = Runic()

        with self.assertRaises(TaskNotFoundError):
            await runic.invoke("missing")

        with self.assertRaises(TaskNotFoundError):
            await runic.invoke(GenerateReport(report_id="missing"))

    async def _wait_for_result(self, runic: Runic, job_id: str) -> None:
        result = await runic.conduit.wait(job_id, timeout=1.0)
        self.assertIsInstance(result, Ok)
