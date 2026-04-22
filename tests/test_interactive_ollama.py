from __future__ import annotations

import json
import unittest
from collections.abc import AsyncIterator

from runic import DefaultError, Err, Ok
from runic.interactive.models import ChatMessage, InstalledModel, ModelInstallStatus, ModelProvider, ModelReference
from runic.interactive.runners.base import RunnerChatError
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


async def collect(iterator: AsyncIterator[str]) -> list[str]:
    return [chunk async for chunk in iterator]


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

    async def test_default_chat_preserves_roles_and_content_in_http_payload(self) -> None:
        captured: dict[str, object] = {}

        async def chat_http(url: str, payload: dict[str, object]) -> dict[str, object]:
            captured["url"] = url
            captured["payload"] = payload
            return {"message": {"content": "ready"}}

        runner = OllamaRunner(command_exists=lambda _: True, chat_http=chat_http)

        chunks = await collect(
            runner.chat(
                "llama3.2",
                (
                    ChatMessage(role="system", content="You are terse."),
                    ChatMessage(role="user", content="Hello"),
                ),
            )
        )

        self.assertEqual(["ready"], chunks)
        self.assertEqual("http://127.0.0.1:11434/api/chat", captured["url"])
        self.assertEqual(
            {
                "model": "llama3.2",
                "messages": [
                    {"role": "system", "content": "You are terse."},
                    {"role": "user", "content": "Hello"},
                ],
                "stream": False,
            },
            captured["payload"],
        )

    async def test_default_chat_wraps_http_failures(self) -> None:
        async def chat_http(_: str, __: dict[str, object]) -> dict[str, object]:
            raise RuntimeError("boom")

        runner = OllamaRunner(command_exists=lambda _: True, chat_http=chat_http)

        with self.assertRaises(RunnerChatError) as cm:
            await collect(runner.chat("llama3.2", (ChatMessage(role="user", content="hi"),)))

        self.assertEqual("runner_chat_failed", cm.exception.error.code)
        self.assertEqual({"model": "llama3.2", "error": "boom"}, cm.exception.error.details)

    async def test_default_chat_raises_for_error_payloads(self) -> None:
        async def chat_http(_: str, __: dict[str, object]) -> dict[str, object]:
            return {"error": "model unavailable"}

        runner = OllamaRunner(command_exists=lambda _: True, chat_http=chat_http)

        with self.assertRaises(RunnerChatError) as cm:
            await collect(runner.chat("llama3.2", (ChatMessage(role="user", content="hi"),)))

        self.assertEqual("runner_chat_failed", cm.exception.error.code)
        self.assertEqual({"error": "model unavailable"}, cm.exception.error.details)

    async def test_default_chat_raises_for_missing_content(self) -> None:
        async def chat_http(_: str, __: dict[str, object]) -> dict[str, object]:
            return {"message": {"role": "assistant"}}

        runner = OllamaRunner(command_exists=lambda _: True, chat_http=chat_http)

        with self.assertRaises(RunnerChatError) as cm:
            await collect(runner.chat("llama3.2", (ChatMessage(role="user", content="hi"),)))

        self.assertEqual("runner_chat_failed", cm.exception.error.code)
        self.assertEqual({"response": {"message": {"role": "assistant"}}}, cm.exception.error.details)

    async def test_list_models_parses_successful_json_lines(self) -> None:
        async def list_http(url: str) -> dict[str, object]:
            self.assertEqual("http://127.0.0.1:11434/api/tags", url)
            return {"models": [{"name": "llama3.2", "size": 123}]}

        runner = OllamaRunner(command_exists=lambda _: True, list_http=list_http)

        result = await runner.list_models()

        self.assertIsInstance(result, Ok)
        assert isinstance(result, Ok)
        self.assertEqual(
            [
                InstalledModel(
                    name="llama3.2",
                    provider=ModelProvider.OLLAMA,
                    source="ollama://llama3.2",
                    runner="ollama",
                    status=ModelInstallStatus.INSTALLED,
                    metadata={"size": "123"},
                )
            ],
            result.value,
        )

    async def test_list_models_wraps_http_failures(self) -> None:
        async def list_http(_: str) -> dict[str, object]:
            raise RuntimeError("tags unavailable")

        runner = OllamaRunner(command_exists=lambda _: True, list_http=list_http)

        result = await runner.list_models()

        self.assertIsInstance(result, Err)
        assert isinstance(result, Err)
        self.assertEqual("runner_list_failed", result.error.code)
        self.assertEqual({"error": "tags unavailable"}, result.error.details)

    async def test_install_model_propagates_pull_failure(self) -> None:
        async def run_command(_: tuple[str, ...]) -> Err[DefaultError]:
            return Err(DefaultError(message="command failed", code="runner_command_failed"))

        runner = OllamaRunner(command_exists=lambda _: True, run_command=run_command)
        context = FakeContext()
        ref = ModelReference(
            provider=ModelProvider.OLLAMA,
            source="ollama://llama3.2",
            model="llama3.2",
            local_name="llama3.2",
        )

        result = await runner.install_model(ref, context)

        self.assertIsInstance(result, Err)
        assert isinstance(result, Err)
        self.assertEqual("runner_command_failed", result.error.code)
