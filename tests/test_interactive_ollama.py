from __future__ import annotations

import io
import json
import unittest
import urllib.error
from collections.abc import AsyncIterator

from runic import DefaultError, Err, Ok
from runic.interactive.install_status import InstallPhase, InstallPhaseState, parse_install_status
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

    async def test_install_model_streams_pull_updates_and_verifies_tags(self) -> None:
        captured: dict[str, object] = {}

        async def pull_http(url: str, payload: dict[str, object]) -> AsyncIterator[dict[str, object]]:
            captured["url"] = url
            captured["payload"] = payload
            yield {"status": "pulling manifest"}
            yield {"status": "downloading", "completed": 50, "total": 100}
            yield {"status": "success"}

        async def list_http(_: str) -> dict[str, object]:
            return {"models": [{"name": "llama3.2"}]}

        runner = OllamaRunner(command_exists=lambda _: True, pull_http=pull_http, list_http=list_http)
        context = FakeContext()
        ref = ModelReference(
            provider=ModelProvider.OLLAMA,
            source="ollama://llama3.2",
            model="llama3.2",
            local_name="llama3.2",
        )

        result = await runner.install_model(ref, context)

        self.assertIsInstance(result, Ok)
        assert isinstance(result, Ok)
        self.assertEqual("http://127.0.0.1:11434/api/pull", captured["url"])
        self.assertEqual({"model": "llama3.2", "stream": True}, captured["payload"])
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
        self.assertIn("pulling manifest", context.logs)
        structured = [parse_install_status(message) for message in context.logs]
        structured = [message for message in structured if message is not None]
        self.assertIn(
            InstallPhase.CONNECTING,
            [message.phase for message in structured],
        )
        self.assertIn(
            InstallPhase.VERIFYING,
            [message.phase for message in structured],
        )
        self.assertIn(
            InstallPhase.INSTALLING,
            [message.phase for message in structured],
        )
        self.assertIn(
            InstallPhaseState.DONE,
            [message.state for message in structured if message.phase is InstallPhase.VERIFYING],
        )
        self.assertIn(0.5, context.progress_values)
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

    async def test_embed_posts_to_ollama_embed_endpoint(self) -> None:
        captured: dict[str, object] = {}

        async def embed_http(url: str, payload: dict[str, object]) -> dict[str, object]:
            captured["url"] = url
            captured["payload"] = payload
            return {"embeddings": [[0.1, 0.2, 0.3]]}

        runner = OllamaRunner(command_exists=lambda _: True, embed_http=embed_http)

        result = await runner.embed("qwen3-embedding:8b", "hello")

        self.assertIsInstance(result, Ok)
        assert isinstance(result, Ok)
        self.assertEqual([0.1, 0.2, 0.3], result.value)
        self.assertEqual("http://127.0.0.1:11434/api/embed", captured["url"])
        self.assertEqual({"model": "qwen3-embedding:8b", "input": "hello"}, captured["payload"])

    async def test_embed_returns_error_payloads(self) -> None:
        async def embed_http(_: str, __: dict[str, object]) -> dict[str, object]:
            return {"error": "model does not support embeddings"}

        runner = OllamaRunner(command_exists=lambda _: True, embed_http=embed_http)

        result = await runner.embed("llama3.2", "hello")

        self.assertIsInstance(result, Err)
        assert isinstance(result, Err)
        self.assertEqual("runner_embed_failed", result.error.code)
        self.assertEqual({"error": "model does not support embeddings"}, result.error.details)

    async def test_default_chat_wraps_http_failures(self) -> None:
        async def chat_http(_: str, __: dict[str, object]) -> dict[str, object]:
            raise RuntimeError("boom")

        runner = OllamaRunner(command_exists=lambda _: True, chat_http=chat_http)

        with self.assertRaises(RunnerChatError) as cm:
            await collect(runner.chat("llama3.2", (ChatMessage(role="user", content="hi"),)))

        self.assertEqual("runner_chat_failed", cm.exception.error.code)
        self.assertEqual({"model": "llama3.2", "error": "boom"}, cm.exception.error.details)

    async def test_default_chat_preserves_ollama_http_error_body(self) -> None:
        async def chat_http(_: str, __: dict[str, object]) -> dict[str, object]:
            raise urllib.error.HTTPError(
                "http://127.0.0.1:11434/api/chat",
                400,
                "Bad Request",
                {},
                io.BytesIO(b'{"error":"qwen3-embedding:8b does not support chat"}'),
            )

        runner = OllamaRunner(command_exists=lambda _: True, chat_http=chat_http)

        with self.assertRaises(RunnerChatError) as cm:
            await collect(runner.chat("qwen3-embedding:8b", (ChatMessage(role="user", content="hi"),)))

        self.assertEqual("runner_chat_failed", cm.exception.error.code)
        self.assertEqual({"error": "qwen3-embedding:8b does not support chat"}, cm.exception.error.details)

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

    async def test_install_model_returns_error_when_pull_stream_contains_error_payload(self) -> None:
        async def pull_http(_: str, __: dict[str, object]) -> AsyncIterator[dict[str, object]]:
            yield {"error": "model not found"}

        runner = OllamaRunner(command_exists=lambda _: True, pull_http=pull_http)
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
        self.assertEqual("runner_install_failed", result.error.code)
        self.assertEqual({"error": "model not found"}, result.error.details)

    async def test_install_model_returns_error_when_verification_does_not_find_model(self) -> None:
        async def pull_http(_: str, __: dict[str, object]) -> AsyncIterator[dict[str, object]]:
            yield {"status": "success"}

        async def list_http(_: str) -> dict[str, object]:
            return {"models": [{"name": "other-model"}]}

        runner = OllamaRunner(command_exists=lambda _: True, pull_http=pull_http, list_http=list_http)
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
        self.assertEqual("runner_install_verify_failed", result.error.code)
