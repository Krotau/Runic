from __future__ import annotations

import asyncio
import json
import shutil
import urllib.request
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence

from runic import DefaultError, Err, Ok, Result

from ..models import ChatMessage, InstalledModel, ModelInstallStatus, ModelProvider, ModelReference
from .base import ModelRunner, RunnerCapability, RunnerChatError, RunnerContext

CommandExists = Callable[[str], bool]
RunCommand = Callable[[tuple[str, ...]], Awaitable[Result[Sequence[str], DefaultError]]]
ChatHttp = Callable[[str, dict[str, object]], Awaitable[dict[str, object]]]
ChatClient = Callable[[str, tuple[ChatMessage, ...]], AsyncIterator[str]]

DEFAULT_CHAT_URL = "http://127.0.0.1:11434/api/chat"


def _default_command_exists(command: str) -> bool:
    return shutil.which(command) is not None


async def _default_run_command(command: tuple[str, ...]) -> Result[Sequence[str], DefaultError]:
    process = await asyncio.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    output = stdout.decode("utf-8", errors="replace").splitlines()
    if process.returncode == 0:
        return Ok(output)

    details = {
        "command": list(command),
        "stderr": stderr.decode("utf-8", errors="replace"),
        "stdout": output,
        "returncode": process.returncode,
    }
    return Err(DefaultError(message="Runner command failed.", code="runner_command_failed", details=details))


def _chat_failure(message: str, *, details: object | None = None) -> RunnerChatError:
    return RunnerChatError(DefaultError(message=message, code="runner_chat_failed", details=details))


def _chat_payload(model: str, messages: tuple[ChatMessage, ...]) -> dict[str, object]:
    return {
        "model": model,
        "messages": [{"role": message.role, "content": message.content} for message in messages],
        "stream": False,
    }


def _message_content(response: object) -> str | None:
    if not isinstance(response, dict):
        return None

    message = response.get("message")
    if not isinstance(message, dict):
        return None

    content = message.get("content")
    return content if isinstance(content, str) else None


async def _default_http_chat(url: str, payload: dict[str, object]) -> dict[str, object]:
    def _post_json() -> dict[str, object]:
        data = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read().decode("utf-8")
        if not raw.strip():
            return {}
        decoded = json.loads(raw)
        if not isinstance(decoded, dict):
            raise ValueError("Ollama chat response must be a JSON object")
        return decoded

    return await asyncio.to_thread(_post_json)


async def _default_chat_client(
    model: str,
    messages: tuple[ChatMessage, ...],
    *,
    chat_http: ChatHttp,
) -> AsyncIterator[str]:
    payload = _chat_payload(model, messages)
    try:
        response = await chat_http(DEFAULT_CHAT_URL, payload)
        content = _message_content(response)
    except RunnerChatError:
        raise
    except Exception as exc:
        raise _chat_failure(
            "Failed to chat with Ollama.",
            details={
                "model": model,
                "error": str(exc),
            },
        ) from exc

    if content is not None:
        yield content


def _manual_install_error() -> Err[DefaultError]:
    return Err(
        DefaultError(
            message="Install Ollama manually, then rerun the command.",
            code="runner_install_manual",
        )
    )


class OllamaRunner(ModelRunner):
    name = "ollama"
    capabilities = (RunnerCapability(provider=ModelProvider.OLLAMA),)

    def __init__(
        self,
        *,
        command_exists: CommandExists | None = None,
        run_command: RunCommand | None = None,
        chat_http: ChatHttp | None = None,
        chat_client: ChatClient | None = None,
    ) -> None:
        self._command_exists = command_exists or _default_command_exists
        self._run_command = run_command or _default_run_command
        self._chat_http = chat_http or _default_http_chat
        if chat_client is None:

            async def default_chat_client(model: str, messages: tuple[ChatMessage, ...]) -> AsyncIterator[str]:
                async for chunk in _default_chat_client(model, messages, chat_http=self._chat_http):
                    yield chunk

            self._chat_client = default_chat_client
        else:
            self._chat_client = chat_client

    async def is_available(self) -> bool:
        return self._command_exists(self.name)

    async def install_runner(self) -> Result[str, DefaultError]:
        return _manual_install_error()

    async def install_model(
        self, reference: ModelReference, context: RunnerContext
    ) -> Result[InstalledModel, DefaultError]:
        if not await self.is_available():
            return Err(DefaultError(message="Ollama is not installed.", code="runner_unavailable"))

        result = await self._run_command((self.name, "pull", reference.model))
        match result:
            case Err() as error:
                return error
            case Ok() as ok:
                for line in ok.value:
                    await context.log(line)
                await context.progress(1.0)
                return Ok(
                    InstalledModel(
                        name=reference.local_name,
                        provider=reference.provider,
                        source=reference.source,
                        runner=self.name,
                        status=ModelInstallStatus.INSTALLED,
                    )
                )

    async def list_models(self) -> Result[list[InstalledModel], DefaultError]:
        if not await self.is_available():
            return Err(DefaultError(message="Ollama is not installed.", code="runner_unavailable"))

        result = await self._run_command((self.name, "list", "--json"))
        match result:
            case Err() as error:
                return error
            case Ok() as ok:
                models: list[InstalledModel] = []
                for line in ok.value:
                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    name = str(data.get("name", "")).strip()
                    if not name:
                        continue
                    models.append(
                        InstalledModel(
                            name=name,
                            provider=ModelProvider.OLLAMA,
                            source=f"ollama://{name}",
                            runner=self.name,
                            status=ModelInstallStatus.INSTALLED,
                            metadata={key: str(value) for key, value in data.items() if key != "name"},
                        )
                    )
                return Ok(models)

    def chat(self, model: str, messages: tuple[ChatMessage, ...]) -> AsyncIterator[str]:
        return self._chat_client(model, messages)
