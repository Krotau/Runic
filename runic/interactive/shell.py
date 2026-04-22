from __future__ import annotations

import asyncio
import shlex
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Protocol

from runic import DefaultError, Err, Ok, Result, Runic

from .controller import ModelController
from .models import ChatMessage
from .registry import ModelRegistry, default_registry_path
from .runners.base import RunnerChatError
from .runners.ollama import OllamaRunner


class _Console(Protocol):
    def print(self, *objects: object, **kwargs: object) -> None: ...


PromptFn = Callable[[str], str]


class ShellCommand(str, Enum):
    INSTALL = "install"
    CHAT = "chat"
    EMBED = "embed"
    HELP = "help"
    EXIT = "exit"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class ParsedCommand:
    command: ShellCommand
    argument: str | None = None


def parse_shell_command(line: str) -> ParsedCommand:
    text = line.strip()
    if not text:
        return ParsedCommand(ShellCommand.UNKNOWN, None)

    command, _, remainder = text.partition(" ")
    normalized = command.lower()
    argument = remainder.strip() or None

    match normalized:
        case "install":
            return ParsedCommand(ShellCommand.INSTALL, argument)
        case "chat":
            return ParsedCommand(ShellCommand.CHAT, argument)
        case "embed":
            return ParsedCommand(ShellCommand.EMBED, argument)
        case "help" | "?":
            return ParsedCommand(ShellCommand.HELP, argument)
        case "exit" | "quit":
            return ParsedCommand(ShellCommand.EXIT, argument)
        case _:
            return ParsedCommand(ShellCommand.UNKNOWN, text)


def format_install_pane(model: str, progress: float, lines: Sequence[str]) -> str:
    clamped = max(0.0, min(progress, 1.0))
    percent = round(clamped * 100)
    body = [f"Install: {model}", f"Progress: {percent}%"]
    body.extend(str(line) for line in lines)
    width = max(len(line) for line in body)
    border = "+" + "-" * (width + 2) + "+"
    rendered = [border]
    rendered.extend(f"| {line.ljust(width)} |" for line in body)
    rendered.append(border)
    return "\n".join(rendered)


def _cli_extras_message() -> str:
    return 'Optional CLI extras are not installed. Install "runic-io[cli]" to use the interactive shell.'


def _load_prompt_fn() -> PromptFn:
    try:
        from prompt_toolkit import prompt
    except ImportError:
        raise

    return prompt


def _load_console() -> _Console:
    try:
        from rich.console import Console
    except ImportError:
        raise

    return Console()


def _default_controller() -> ModelController:
    runtime = Runic()
    registry = ModelRegistry(default_registry_path())
    return ModelController(runtime, registry, (OllamaRunner(),))


async def _stream_chat(controller: ModelController, model: str, prompt: str, console: _Console) -> None:
    async for chunk in controller.chat(model, (ChatMessage(role="user", content=prompt),)):
        console.print(chunk, end="")
    console.print()


def _format_error(error: DefaultError) -> str:
    code = error.code or "error"
    message = f"{code}: {error.message}"
    if isinstance(error.details, dict):
        detail = error.details.get("error")
        if isinstance(detail, str) and detail:
            return f"{message} {detail}"
    return message


def _split_model_and_value(argument: str | None, command: str) -> Result[tuple[str, str], DefaultError]:
    if argument is None:
        return Err(DefaultError(message=f"Use {command} <model> <text-or-file-path>.", code="invalid_command"))

    try:
        parts = shlex.split(argument)
    except ValueError as exc:
        return Err(DefaultError(message=str(exc), code="invalid_command"))

    if len(parts) < 2:
        return Err(DefaultError(message=f"Use {command} <model> <text-or-file-path>.", code="invalid_command"))

    return Ok((parts[0], " ".join(parts[1:])))


def _read_embed_input(value: str) -> Result[str, DefaultError]:
    path = Path(value).expanduser()
    if not path.is_file():
        return Ok(value)

    try:
        return Ok(path.read_text(encoding="utf-8"))
    except OSError as exc:
        return Err(
            DefaultError(
                message=f"Failed to read embed input file: {value}",
                code="embed_file_read_failed",
                details={"error": str(exc)},
            )
        )


def _format_embedding_preview(embedding: Sequence[float], *, limit: int = 8) -> str:
    preview = ", ".join(f"{value:.6g}" for value in embedding[:limit])
    if len(embedding) > limit:
        preview = f"{preview}, ..."
    return f"[{preview}]"


async def _embed_and_print(controller: ModelController, model: str, text: str, console: _Console) -> None:
    result = await controller.embed(model, text)
    match result:
        case Ok(value=embedding):
            console.print(f"Embedding dimensions: {len(embedding)}")
            console.print(f"Embedding preview: {_format_embedding_preview(embedding)}")
        case Err(error=error):
            console.print(_format_error(error))


def _print_install_result(result: Result[str, DefaultError], console: _Console) -> None:
    match result:
        case Ok(value=spell_id):
            console.print(f"Installation scheduled: {spell_id}")
        case Err(error=error):
            console.print(_format_error(error))


def _print_install_completion(result: Result[object, DefaultError], console: _Console) -> None:
    match result:
        case Ok():
            console.print("Installation completed")
        case Err(error=error):
            console.print(_format_error(error))


async def _install_and_wait(controller: ModelController, source: str, console: _Console) -> None:
    result = await controller.install(source)
    _print_install_result(result, console)
    match result:
        case Ok(value=spell_id):
            settled = await controller.wait_for_install(spell_id)
            _print_install_completion(settled, console)
        case Err():
            return


def run_interactive(
    *,
    controller: ModelController | None = None,
    prompt_fn: PromptFn | None = None,
    console: _Console | None = None,
) -> int:
    try:
        active_prompt_fn = prompt_fn or _load_prompt_fn()
    except ImportError:
        print(_cli_extras_message())
        return 1

    try:
        active_console = console or _load_console()
    except ImportError:
        print(_cli_extras_message())
        return 1

    active_controller = controller or _default_controller()
    active_console.print("Runic interactive shell")

    while True:
        try:
            line = active_prompt_fn("runic> ")
        except (EOFError, KeyboardInterrupt):
            active_console.print()
            return 0

        command = parse_shell_command(line)
        match command.command:
            case ShellCommand.EXIT:
                return 0
            case ShellCommand.HELP:
                active_console.print("Commands: install <model>, chat <model>, embed <model> <text-or-file-path>, help, exit")
            case ShellCommand.INSTALL:
                if command.argument is None:
                    active_console.print("Model selection is not implemented yet. Use install <model>.")
                else:
                    asyncio.run(_install_and_wait(active_controller, command.argument, active_console))
            case ShellCommand.CHAT:
                if command.argument is None:
                    active_console.print("Use chat <model>.")
                else:
                    try:
                        chat_prompt = active_prompt_fn(f"chat:{command.argument}> ")
                    except (EOFError, KeyboardInterrupt):
                        continue
                    if chat_prompt.strip() == "/exit":
                        continue
                    try:
                        asyncio.run(_stream_chat(active_controller, command.argument, chat_prompt, active_console))
                    except LookupError as exc:
                        active_console.print(str(exc))
                    except RunnerChatError as exc:
                        active_console.print(_format_error(exc.error))
            case ShellCommand.EMBED:
                split = _split_model_and_value(command.argument, "embed")
                match split:
                    case Err(error=error):
                        active_console.print(_format_error(error))
                    case Ok(value=(model, value)):
                        embed_input = _read_embed_input(value)
                        match embed_input:
                            case Err(error=error):
                                active_console.print(_format_error(error))
                            case Ok(value=text):
                                asyncio.run(_embed_and_print(active_controller, model, text, active_console))
            case ShellCommand.UNKNOWN:
                active_console.print(f"Unknown command: {line.strip()}")
