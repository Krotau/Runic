from __future__ import annotations

import unittest
from collections.abc import AsyncIterator

from runic import DefaultError, Err, Ok
from runic.interactive.models import ChatMessage, InstalledModel, ModelInstallStatus, ModelProvider, ModelReference
from runic.interactive.runners.ollama import OllamaRunner


class FakeContext:
    def __init__(self) -> None:
        self.logs: list[str] = []
        self.progress_values: list[float] = []

    async def log(self, message: str) -> None:
        self.logs.append(message)

    async def progress(self, value: float) -> None:
        self.progress_values.append(value)


async def fake_chat(_: str, __: tuple[ChatMessage, ...]) -> AsyncIterator[str]:
    yield "hello"
    yield " world"


class TestInteractiveOllamaRunner(unittest.IsolatedAsyncioTestCase):
    async def test_availability_uses_injected_checker(self) -> None:
        runner = OllamaRunner(command_exists=lambda _: True)

        self.assertTrue(await runner.is_available())

    async def test_missing_runner_install_returns_manual_notice(self) -> None:
        runner = OllamaRunner(command_exists=lambda _: False)

        result = await runner.install_runner()

        self.assertIsInstance(result, Err)
        assert isinstance(result, Err)
        self.assertEqual("runner_install_manual", result.error.code)

    async def test_install_model_runs_pull_and_records_installed_model(self) -> None:
        commands: list[tuple[str, ...]] = []

        async def run_command(command: tuple[str, ...]) -> Ok[list[str]]:
            commands.append(command)
            return Ok(["pulling manifest", "success"])

        runner = OllamaRunner(command_exists=lambda _: True, run_command=run_command)
        context = FakeContext()
        ref = ModelReference(
            provider=ModelProvider.OLLAMA,
            source="ollama://llama3.2",
            model="llama3.2",
            local_name="llama3.2",
        )

        result = await runner.install_model(ref, context)

        self.assertEqual([("ollama", "pull", "llama3.2")], commands)
        self.assertIsInstance(result, Ok)
        assert isinstance(result, Ok)
        self.assertEqual(
            InstalledModel(
                name="llama3.2",
                provider=ModelProvider.OLLAMA,
                source="ollama://llama3.2",
                runner="ollama",
                status=ModelInstallStatus.INSTALLED,
            ),
            result.value,
        )
        self.assertEqual(["pulling manifest", "success"], context.logs)
        self.assertEqual(1.0, context.progress_values[-1])

    async def test_chat_yields_injected_chunks(self) -> None:
        runner = OllamaRunner(command_exists=lambda _: True, chat_client=fake_chat)

        chunks = [
            chunk
            async for chunk in runner.chat(
                "llama3.2",
                (ChatMessage(role="user", content="hi"),),
            )
        ]

        self.assertEqual(["hello", " world"], chunks)
