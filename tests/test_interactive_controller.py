from __future__ import annotations

import tempfile
import unittest
from collections.abc import AsyncIterator
from pathlib import Path

from runic import DefaultError, Err, Ok, Runic, SpellStatus
from runic.interactive.controller import InstallDecisionStatus, ModelController
from runic.interactive.models import ChatMessage, InstalledModel, ModelInstallStatus, ModelProvider, ModelReference
from runic.interactive.registry import ModelRegistry


class FakeRunner:
    name = "ollama"

    def __init__(self, *, available: bool = True) -> None:
        from runic.interactive.runners.base import RunnerCapability

        self.available = available
        self.capabilities = (RunnerCapability(provider=ModelProvider.OLLAMA),)
        self.installed: list[str] = []

    async def is_available(self) -> bool:
        return self.available

    async def install_runner(self):  # type: ignore[no-untyped-def]
        return Err(DefaultError(message="manual install required", code="runner_install_manual"))

    async def install_model(self, reference, context):  # type: ignore[no-untyped-def]
        await context.log(f"installing:{reference.model}")
        await context.progress(1.0)
        self.installed.append(reference.model)
        return Ok(
            InstalledModel(
                name=reference.local_name,
                provider=reference.provider,
                source=reference.source,
                runner=self.name,
                status=ModelInstallStatus.INSTALLED,
            )
        )

    async def list_models(self):  # type: ignore[no-untyped-def]
        return Ok([])

    async def chat(self, model: str, messages: tuple[ChatMessage, ...]) -> AsyncIterator[str]:
        yield f"{model}:{messages[-1].content}"


class FailingRunner(FakeRunner):
    async def install_model(self, reference, context):  # type: ignore[no-untyped-def]
        await context.log(f"failed:{reference.model}")
        return Err(DefaultError(message="install failed", code="runner_install_failed"))


class TestInteractiveController(unittest.IsolatedAsyncioTestCase):
    async def test_prepare_install_reports_missing_runner(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            controller = ModelController(
                Runic(),
                ModelRegistry(Path(tempdir) / "models.json"),
                runners=(FakeRunner(available=False),),
            )

            decision = await controller.prepare_install("llama3.2")

            self.assertEqual(InstallDecisionStatus.MISSING_RUNNER, decision.status)
            self.assertEqual("ollama", decision.runner)

    async def test_prepare_install_reports_unsupported_hugging_face_model_without_compatible_runner(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            controller = ModelController(Runic(), ModelRegistry(Path(tempdir) / "models.json"), runners=(FakeRunner(),))

            decision = await controller.prepare_install("https://huggingface.co/org/model")

            self.assertEqual(InstallDecisionStatus.UNSUPPORTED, decision.status)
            self.assertIsNone(decision.runner)

    async def test_prepare_install_reports_invalid_input(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            controller = ModelController(Runic(), ModelRegistry(Path(tempdir) / "models.json"), runners=(FakeRunner(),))

            decision = await controller.prepare_install(" ")

            self.assertEqual(InstallDecisionStatus.INVALID, decision.status)
            self.assertIsNone(decision.reference)

    async def test_install_schedules_spell_and_saves_registry_on_success(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            registry = ModelRegistry(Path(tempdir) / "models.json")
            runic = Runic()
            runner = FakeRunner()
            controller = ModelController(runic, registry, runners=(runner,))

            result = await controller.install("llama3.2")

            self.assertIsInstance(result, Ok)
            assert isinstance(result, Ok)

            spell_id = result.value
            record = await runic.conduit.wait_for_status(spell_id, SpellStatus.SUCCEEDED, timeout=1.0)

            self.assertIsInstance(record, Ok)
            self.assertEqual(["llama3.2"], runner.installed)
            self.assertEqual("installing:llama3.2", registry.get("llama3.2").metadata["last_log"])
            self.assertEqual(ModelInstallStatus.INSTALLED, registry.get("llama3.2").status)

    async def test_install_returns_err_and_does_not_schedule_when_runner_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            runic = Runic()
            runner = FakeRunner(available=False)
            controller = ModelController(runic, ModelRegistry(Path(tempdir) / "models.json"), runners=(runner,))

            result = await controller.install("llama3.2")

            self.assertIsInstance(result, Err)
            assert isinstance(result, Err)
            self.assertEqual(InstallDecisionStatus.MISSING_RUNNER.value, result.error.code)
            self.assertEqual([], runner.installed)
            self.assertEqual({}, runic.conduit._futures)

    async def test_failed_install_saves_failed_registry_state(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            registry = ModelRegistry(Path(tempdir) / "models.json")
            runic = Runic()
            controller = ModelController(runic, registry, runners=(FailingRunner(),))

            result = await controller.install("llama3.2")

            self.assertIsInstance(result, Ok)
            assert isinstance(result, Ok)
            spell_id = result.value

            spell_result = await runic.conduit.wait(spell_id)

            self.assertIsInstance(spell_result, Err)
            assert isinstance(spell_result, Err)
            self.assertEqual(ModelInstallStatus.FAILED, registry.get("llama3.2").status)
            self.assertEqual("failed:llama3.2", registry.get("llama3.2").metadata["last_log"])

    async def test_chat_uses_registry_runner(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            registry = ModelRegistry(Path(tempdir) / "models.json")
            registry.save(
                InstalledModel(
                    name="llama3.2",
                    provider=ModelProvider.OLLAMA,
                    source="ollama://llama3.2",
                    runner="ollama",
                    status=ModelInstallStatus.INSTALLED,
                )
            )
            controller = ModelController(Runic(), registry, runners=(FakeRunner(),))

            chunks = [
                chunk
                async for chunk in controller.chat(
                    "llama3.2",
                    (ChatMessage(role="user", content="hello"),),
                )
            ]

            self.assertEqual(["llama3.2:hello"], chunks)
