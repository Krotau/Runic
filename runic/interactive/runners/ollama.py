from __future__ import annotations

import asyncio
import json
import shutil
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence

from runic import DefaultError, Err, Ok, Result

from ..models import ChatMessage, InstalledModel, ModelInstallStatus, ModelProvider, ModelReference
from .base import ModelRunner, RunnerCapability, RunnerContext

CommandExists = Callable[[str], bool]
RunCommand = Callable[[tuple[str, ...]], Awaitable[Result[Sequence[str], DefaultError]]]
ChatClient = Callable[[str, tuple[ChatMessage, ...]], AsyncIterator[str]]


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


async def _default_chat_client(model: str, messages: tuple[ChatMessage, ...]) -> AsyncIterator[str]:
    prompt = "\n".join(message.content for message in messages)
    process = await asyncio.create_subprocess_exec(
        "ollama",
        "run",
        model,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    assert process.stdin is not None
    assert process.stdout is not None
    process.stdin.write(prompt.encode("utf-8"))
    await process.stdin.drain()
    process.stdin.close()

    while True:
        line = await process.stdout.readline()
        if not line:
            break
        text = line.decode("utf-8", errors="replace")
        if text:
            yield text

    await process.wait()


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
        chat_client: ChatClient | None = None,
    ) -> None:
        self._command_exists = command_exists or _default_command_exists
        self._run_command = run_command or _default_run_command
        self._chat_client = chat_client or _default_chat_client

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
