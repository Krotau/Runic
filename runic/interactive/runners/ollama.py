from __future__ import annotations

import asyncio
import json
import shutil
import urllib.error
import urllib.request
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence

from runic import DefaultError, Err, Ok, Result

from ..models import ChatMessage, InstalledModel, ModelInstallStatus, ModelProvider, ModelReference
from .base import ModelRunner, RunnerCapability, RunnerChatError, RunnerContext

CommandExists = Callable[[str], bool]
RunCommand = Callable[[tuple[str, ...]], Awaitable[Result[Sequence[str], DefaultError]]]
ChatHttp = Callable[[str, dict[str, object]], Awaitable[dict[str, object]]]
ListHttp = Callable[[str], Awaitable[dict[str, object]]]
EmbedHttp = Callable[[str, dict[str, object]], Awaitable[dict[str, object]]]
ChatClient = Callable[[str, tuple[ChatMessage, ...]], AsyncIterator[str]]

DEFAULT_CHAT_URL = "http://127.0.0.1:11434/api/chat"
DEFAULT_EMBED_URL = "http://127.0.0.1:11434/api/embed"
DEFAULT_TAGS_URL = "http://127.0.0.1:11434/api/tags"


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


def _list_failure(message: str, *, details: object | None = None) -> Err[DefaultError]:
    return Err(DefaultError(message=message, code="runner_list_failed", details=details))


def _embed_failure(message: str, *, details: object | None = None) -> Err[DefaultError]:
    return Err(DefaultError(message=message, code="runner_embed_failed", details=details))


def _chat_payload(model: str, messages: tuple[ChatMessage, ...]) -> dict[str, object]:
    return {
        "model": model,
        "messages": [{"role": message.role, "content": message.content} for message in messages],
        "stream": False,
    }


def _embed_payload(model: str, text: str) -> dict[str, object]:
    return {"model": model, "input": text}


def _chat_content_from_response(response: object) -> str:
    if not isinstance(response, dict):
        raise _chat_failure("Failed to chat with Ollama.", details={"response": response})

    if not response:
        raise _chat_failure("Failed to chat with Ollama.", details={"response": response})

    if "error" in response:
        raise _chat_failure("Failed to chat with Ollama.", details=response)

    message = response.get("message")
    if not isinstance(message, dict):
        raise _chat_failure("Failed to chat with Ollama.", details={"response": response})

    content = message.get("content")
    if not isinstance(content, str):
        raise _chat_failure("Failed to chat with Ollama.", details={"response": response})

    return content


def _coerce_embedding(values: object, *, response: object) -> Result[list[float], DefaultError]:
    if not isinstance(values, list):
        return _embed_failure("Failed to embed with Ollama.", details={"response": response})

    embedding: list[float] = []
    for value in values:
        if not isinstance(value, int | float):
            return _embed_failure("Failed to embed with Ollama.", details={"response": response})
        embedding.append(float(value))
    return Ok(embedding)


def _embedding_from_response(response: object) -> Result[list[float], DefaultError]:
    if not isinstance(response, dict):
        return _embed_failure("Failed to embed with Ollama.", details={"response": response})

    if "error" in response:
        return _embed_failure("Failed to embed with Ollama.", details=response)

    embeddings = response.get("embeddings")
    if isinstance(embeddings, list) and embeddings:
        return _coerce_embedding(embeddings[0], response=response)

    embedding = response.get("embedding")
    return _coerce_embedding(embedding, response=response)


def _http_error_details(exc: urllib.error.HTTPError) -> object:
    try:
        raw = exc.read().decode("utf-8", errors="replace")
    except Exception:
        raw = ""

    if raw.strip():
        try:
            decoded = json.loads(raw)
        except json.JSONDecodeError:
            return {"status": exc.code, "reason": exc.msg, "body": raw}
        if isinstance(decoded, dict):
            return decoded
        return {"status": exc.code, "reason": exc.msg, "response": decoded}

    return {"status": exc.code, "reason": exc.msg}


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


async def _default_http_embed(url: str, payload: dict[str, object]) -> dict[str, object]:
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
            raise ValueError("Ollama embed response must be a JSON object")
        return decoded

    return await asyncio.to_thread(_post_json)


async def _default_http_list_models(url: str) -> dict[str, object]:
    def _get_json() -> dict[str, object]:
        request = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read().decode("utf-8")
        if not raw.strip():
            return {}
        decoded = json.loads(raw)
        if not isinstance(decoded, dict):
            raise ValueError("Ollama tags response must be a JSON object")
        return decoded

    return await asyncio.to_thread(_get_json)


async def _default_chat_client(
    model: str,
    messages: tuple[ChatMessage, ...],
    *,
    chat_http: ChatHttp,
) -> AsyncIterator[str]:
    payload = _chat_payload(model, messages)
    try:
        response = await chat_http(DEFAULT_CHAT_URL, payload)
        content = _chat_content_from_response(response)
    except RunnerChatError:
        raise
    except urllib.error.HTTPError as exc:
        raise _chat_failure("Failed to chat with Ollama.", details=_http_error_details(exc)) from exc
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
    capabilities = (RunnerCapability(provider=ModelProvider.OLLAMA, can_embed=True),)

    def __init__(
        self,
        *,
        command_exists: CommandExists | None = None,
        run_command: RunCommand | None = None,
        chat_http: ChatHttp | None = None,
        list_http: ListHttp | None = None,
        embed_http: EmbedHttp | None = None,
        chat_client: ChatClient | None = None,
    ) -> None:
        self._command_exists = command_exists or _default_command_exists
        self._run_command = run_command or _default_run_command
        self._chat_http = chat_http or _default_http_chat
        self._list_http = list_http or _default_http_list_models
        self._embed_http = embed_http or _default_http_embed
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

        try:
            response = await self._list_http(DEFAULT_TAGS_URL)
        except Exception as exc:
            return _list_failure(
                "Failed to list Ollama models.",
                details={"error": str(exc)},
            )

        if not isinstance(response, dict):
            return _list_failure(
                "Failed to list Ollama models.",
                details={"response": response},
            )

        if "error" in response:
            return _list_failure(
                "Failed to list Ollama models.",
                details=response,
            )

        models_data = response.get("models")
        if not isinstance(models_data, list):
            return _list_failure(
                "Failed to list Ollama models.",
                details={"response": response},
            )

        models: list[InstalledModel] = []
        for item in models_data:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", "")).strip()
            if not name:
                continue
            models.append(
                InstalledModel(
                    name=name,
                    provider=ModelProvider.OLLAMA,
                    source=f"ollama://{name}",
                    runner=self.name,
                    status=ModelInstallStatus.INSTALLED,
                    metadata={key: str(value) for key, value in item.items() if key != "name"},
                )
            )
        return Ok(models)

    def chat(self, model: str, messages: tuple[ChatMessage, ...]) -> AsyncIterator[str]:
        return self._chat_client(model, messages)

    async def embed(self, model: str, text: str) -> Result[list[float], DefaultError]:
        if not await self.is_available():
            return Err(DefaultError(message="Ollama is not installed.", code="runner_unavailable"))

        try:
            response = await self._embed_http(DEFAULT_EMBED_URL, _embed_payload(model, text))
        except urllib.error.HTTPError as exc:
            return _embed_failure("Failed to embed with Ollama.", details=_http_error_details(exc))
        except Exception as exc:
            return _embed_failure(
                "Failed to embed with Ollama.",
                details={"model": model, "error": str(exc)},
            )

        return _embedding_from_response(response)
