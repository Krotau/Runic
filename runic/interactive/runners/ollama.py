from __future__ import annotations

import asyncio
import json
import shutil
import threading
import urllib.error
import urllib.request
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import dataclass, field

from runic import DefaultError, Err, Ok, Result

from ..install_status import InstallPhase, InstallPhaseState, InstallStatusUpdate, encode_install_status
from ..models import ChatMessage, InstalledModel, ModelInstallStatus, ModelProvider, ModelReference
from .base import ModelRunner, RunnerCapability, RunnerChatError, RunnerContext

CommandExists = Callable[[str], bool]
RunCommand = Callable[[tuple[str, ...]], Awaitable[Result[Sequence[str], DefaultError]]]
ChatHttp = Callable[[str, dict[str, object]], Awaitable[dict[str, object]]]
ListHttp = Callable[[str], Awaitable[dict[str, object]]]
EmbedHttp = Callable[[str, dict[str, object]], Awaitable[dict[str, object]]]
PullHttp = Callable[[str, dict[str, object]], AsyncIterator[dict[str, object]]]
ChatClient = Callable[[str, tuple[ChatMessage, ...]], AsyncIterator[str]]

DEFAULT_CHAT_URL = "http://127.0.0.1:11434/api/chat"
DEFAULT_EMBED_URL = "http://127.0.0.1:11434/api/embed"
DEFAULT_TAGS_URL = "http://127.0.0.1:11434/api/tags"
DEFAULT_PULL_URL = "http://127.0.0.1:11434/api/pull"


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


async def _default_http_pull(url: str, payload: dict[str, object]) -> AsyncIterator[dict[str, object]]:
    queue: asyncio.Queue[object] = asyncio.Queue()
    sentinel = object()
    loop = asyncio.get_running_loop()

    def _enqueue(item: object) -> None:
        asyncio.run_coroutine_threadsafe(queue.put(item), loop).result()

    def _stream_json_lines() -> None:
        try:
            data = json.dumps(payload).encode("utf-8")
            request = urllib.request.Request(
                url,
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=30) as response:
                for raw_line in response:
                    line = raw_line.decode("utf-8", errors="replace").strip()
                    if not line:
                        continue
                    decoded = json.loads(line)
                    _enqueue(decoded)
        except Exception as exc:  # pragma: no cover - surfaced via iterator below
            _enqueue(exc)
        finally:
            _enqueue(sentinel)

    threading.Thread(target=_stream_json_lines, daemon=True).start()

    while True:
        item = await queue.get()
        if item is sentinel:
            return
        if isinstance(item, Exception):
            raise item
        if not isinstance(item, dict):
            raise ValueError("Ollama pull response must be a JSON object")
        yield item


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


def _pull_progress(update: dict[str, object]) -> float | None:
    completed = update.get("completed")
    total = update.get("total")
    if not isinstance(completed, int | float):
        return None
    if not isinstance(total, int | float) or total <= 0:
        return None
    return min(1.0, max(0.0, float(completed) / float(total)))


@dataclass(slots=True)
class _PullProgressTracker:
    totals_by_digest: dict[str, float] = field(default_factory=dict)
    completed_by_digest: dict[str, float] = field(default_factory=dict)

    def progress(self, update: dict[str, object]) -> float | None:
        digest = update.get("digest")
        total = update.get("total")
        completed = update.get("completed")
        if isinstance(digest, str) and isinstance(total, int | float) and total > 0:
            total_value = float(total)
            self.totals_by_digest[digest] = total_value
            if isinstance(completed, int | float):
                self.completed_by_digest[digest] = min(total_value, max(0.0, float(completed)))
            elif digest not in self.completed_by_digest:
                self.completed_by_digest[digest] = 0.0

            total_bytes = sum(self.totals_by_digest.values())
            if total_bytes <= 0:
                return None
            completed_bytes = sum(
                min(self.completed_by_digest.get(name, 0.0), size)
                for name, size in self.totals_by_digest.items()
            )
            return min(1.0, max(0.0, completed_bytes / total_bytes))
        return _pull_progress(update)


def _phase_for_pull_status(status: str) -> InstallPhase:
    normalized = status.strip().lower()
    if normalized.startswith("verifying"):
        return InstallPhase.VERIFYING
    if normalized.startswith("writing manifest") or normalized.startswith("removing any unused layers"):
        return InstallPhase.INSTALLING
    return InstallPhase.DOWNLOADING


async def _log_install_update(
    context: RunnerContext,
    phase: InstallPhase,
    state: InstallPhaseState,
    *,
    detail: str = "",
    progress: float | None = None,
) -> None:
    await context.log(
        encode_install_status(
            InstallStatusUpdate(
                phase=phase,
                state=state,
                detail=detail,
                progress=progress,
            )
        )
    )


class OllamaRunner(ModelRunner):
    name = "ollama"
    capabilities = (RunnerCapability(provider=ModelProvider.OLLAMA, can_embed=True),)

    def __init__(
        self,
        *,
        command_exists: CommandExists | None = None,
        chat_http: ChatHttp | None = None,
        list_http: ListHttp | None = None,
        embed_http: EmbedHttp | None = None,
        pull_http: PullHttp | None = None,
        chat_client: ChatClient | None = None,
    ) -> None:
        self._command_exists = command_exists or _default_command_exists
        self._chat_http = chat_http or _default_http_chat
        self._list_http = list_http or _default_http_list_models
        self._embed_http = embed_http or _default_http_embed
        self._pull_http = pull_http or _default_http_pull
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

        connected = False
        seen_success = False
        progress_tracker = _PullProgressTracker()
        await _log_install_update(context, InstallPhase.CONNECTING, InstallPhaseState.ACTIVE)

        try:
            async for update in self._pull_http(DEFAULT_PULL_URL, {"model": reference.model, "stream": True}):
                if "error" in update:
                    return Err(
                        DefaultError(
                            message="Failed to install model with Ollama.",
                            code="runner_install_failed",
                            details=update,
                        )
                    )

                status = str(update.get("status", "")).strip()
                if status:
                    await context.log(status)

                if not connected:
                    await _log_install_update(context, InstallPhase.CONNECTING, InstallPhaseState.DONE)
                    connected = True

                if status.lower() == "success":
                    seen_success = True
                    continue

                phase = _phase_for_pull_status(status)
                progress = progress_tracker.progress(update) if phase is InstallPhase.DOWNLOADING else None
                await _log_install_update(
                    context,
                    phase,
                    InstallPhaseState.ACTIVE,
                    detail=status,
                    progress=progress,
                )
                if phase is InstallPhase.DOWNLOADING and progress is not None:
                    await context.progress(progress)

            if not connected:
                return Err(
                    DefaultError(
                        message="Failed to connect to Ollama.",
                        code="runner_install_connect_failed",
                    )
                )
            if not seen_success:
                return Err(
                    DefaultError(
                        message="Ollama pull did not report success.",
                        code="runner_install_failed",
                    )
                )

            await _log_install_update(context, InstallPhase.VERIFYING, InstallPhaseState.ACTIVE)
            models = await self.list_models()
            match models:
                case Err(error=error):
                    return Err(
                        DefaultError(
                            message="Failed to verify installed model with Ollama.",
                            code="runner_install_verify_failed",
                            details=error.details,
                        )
                    )
                case Ok(value=installed_models):
                    if not any(model.name == reference.local_name for model in installed_models):
                        return Err(
                            DefaultError(
                                message="Model download finished but verification failed.",
                                code="runner_install_verify_failed",
                                details={"model": reference.local_name},
                            )
                        )

            await _log_install_update(context, InstallPhase.VERIFYING, InstallPhaseState.DONE)
            await _log_install_update(context, InstallPhase.INSTALLING, InstallPhaseState.ACTIVE)
            await context.progress(1.0)
            await _log_install_update(context, InstallPhase.INSTALLING, InstallPhaseState.DONE)
            return Ok(
                InstalledModel(
                    name=reference.local_name,
                    provider=reference.provider,
                    source=reference.source,
                    runner=self.name,
                    status=ModelInstallStatus.INSTALLED,
                )
            )
        except urllib.error.HTTPError as exc:
            return Err(
                DefaultError(
                    message="Failed to connect to Ollama.",
                    code="runner_install_connect_failed",
                    details=_http_error_details(exc),
                )
            )
        except Exception as exc:
            return Err(
                DefaultError(
                    message="Failed to install model with Ollama.",
                    code="runner_install_failed",
                    details={"model": reference.model, "error": str(exc)},
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
