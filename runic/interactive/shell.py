from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import Enum
from collections.abc import Callable, Sequence
from typing import Protocol

from runic import DefaultError, Err, Ok, Result, Runic

from .controller import ModelController
from .models import ChatMessage
from .registry import ModelRegistry, default_registry_path
from .runners.ollama import OllamaRunner


class _Console(Protocol):
    def print(self, *objects: object, **kwargs: object) -> None: ...


PromptFn = Callable[[str], str]


class ShellCommand(str, Enum):
    INSTALL = "install"
    RUN = "run"
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
        case "run":
            return ParsedCommand(ShellCommand.RUN, argument)
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


def _print_install_result(result: Result[str, DefaultError], console: _Console) -> None:
    match result:
        case Ok(value=spell_id):
            console.print(f"Installation scheduled: {spell_id}")
        case Err(error=error):
            console.print(f"{error.code}: {error.message}")


def _print_install_completion(result: Result[object, DefaultError], console: _Console) -> None:
    match result:
        case Ok():
            console.print("Installation completed")
        case Err(error=error):
            console.print(f"{error.code}: {error.message}")


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
                active_console.print("Commands: install <model>, run [model], help, exit")
            case ShellCommand.INSTALL:
                if command.argument is None:
                    active_console.print("Model selection is not implemented yet. Use install <model>.")
                else:
                    asyncio.run(_install_and_wait(active_controller, command.argument, active_console))
            case ShellCommand.RUN:
                if command.argument is None:
                    active_console.print("Model selection is not implemented yet. Use run <model>.")
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
            case ShellCommand.UNKNOWN:
                active_console.print(f"Unknown command: {line.strip()}")
