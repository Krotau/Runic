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


@dataclass(frozen=True, slots=True)
class ShellCompletion:
    text: str
    start_position: int
    display_meta: str = ""


@dataclass(frozen=True, slots=True)
class PaneState:
    title: str
    lines: Sequence[str] = ()
    footer: Sequence[str] = ()


@dataclass(frozen=True, slots=True)
class ShellFrame:
    title: str
    status: str
    output: Sequence[str]
    prompt: str
    pane: PaneState | None = None
    width: int = 80
    height: int = 18


COMMAND_COMPLETIONS = ("install", "chat", "embed", "help", "exit")
MODEL_COMPLETION_COMMANDS = ("chat", "embed")
MIN_SIDE_PANE_WIDTH = 72


def _fit(text: object, width: int) -> str:
    value = str(text).replace("\n", " ")
    if width <= 0:
        return ""
    if len(value) > width:
        return value[: max(0, width - 1)] + "~"
    return value.ljust(width)


def _frame_line(left: str, right: str, width: int) -> str:
    gap = max(1, width - len(left) - len(right))
    return _fit(f"{left}{' ' * gap}{right}", width)


def _border(width: int) -> str:
    return "+" + "-" * max(0, width - 2) + "+"


def _row(text: object, width: int) -> str:
    return "|" + _fit(text, max(0, width - 2)) + "|"


def render_shell_frame(frame: ShellFrame) -> str:
    width = max(40, frame.width)
    height = max(7, frame.height)
    pane = frame.pane
    output = [str(line) for line in frame.output]

    if pane is not None and width < MIN_SIDE_PANE_WIDTH:
        rows: list[str] = [_border(width)]
        pane_lines = [pane.title, *pane.lines, *pane.footer]
        pane_height = min(max(1, len(pane_lines)), max(1, height - 6))
        for line in pane_lines[:pane_height]:
            rows.append(_row(line, width))
        rows.append(_border(width))
        body_height = max(1, height - len(rows) - 2)
        for line in output[-body_height:]:
            rows.append(_row(line, width))
        while len(rows) < height - 2:
            rows.append(_row("", width))
        rows.append(_border(width))
        rows.append(_row(frame.prompt, width))
        rows.append(_border(width))
        return "\n".join(rows[:height])

    rows = [_border(width), _row(_frame_line(frame.title, frame.status, width - 2), width)]

    if pane is None:
        rows.append(_border(width))
        body_height = max(1, height - 5)
        for line in output[-body_height:]:
            rows.append(_row(line, width))
        while len(rows) < height - 2:
            rows.append(_row("", width))
        rows.append(_border(width))
        rows.append(_row(frame.prompt, width))
        rows.append(_border(width))
        return "\n".join(rows[:height])

    pane_width = min(28, max(22, width // 3))
    left_width = width - pane_width - 1
    pane_lines = [pane.title, *pane.lines]
    if pane.footer:
        pane_lines.extend(["", *pane.footer])
    body_height = max(1, height - 5)
    rows.append("+" + "-" * max(0, left_width - 2) + "+" + "-" * max(0, pane_width - 2) + "+")
    visible_output = output[-body_height:]
    for index in range(body_height):
        left = visible_output[index] if index < len(visible_output) else ""
        right = pane_lines[index] if index < len(pane_lines) else ""
        rows.append("|" + _fit(left, left_width - 2) + "|" + _fit(right, pane_width - 2) + "|")
    rows.append("+" + "-" * max(0, left_width - 2) + "+" + "-" * max(0, pane_width - 2) + "+")
    rows.append(_row(frame.prompt, width))
    rows.append(_border(width))
    return "\n".join(rows[:height])


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


def complete_shell_input(text_before_cursor: str, installed_models: Sequence[object]) -> tuple[ShellCompletion, ...]:
    if not text_before_cursor.strip() or " " not in text_before_cursor:
        prefix = text_before_cursor.strip()
        return tuple(
            ShellCompletion(text=command, start_position=-len(prefix))
            for command in COMMAND_COMPLETIONS
            if command.startswith(prefix)
        )

    for command in MODEL_COMPLETION_COMMANDS:
        command_prefix = f"{command} "
        if not text_before_cursor.startswith(command_prefix):
            continue

        prefix = text_before_cursor[len(command_prefix) :]
        if prefix.endswith(" ") and prefix.strip():
            return ()
        if any(character.isspace() for character in prefix.strip()):
            return ()

        candidates: list[ShellCompletion] = []
        for model in installed_models:
            name = str(getattr(model, "name", ""))
            if not name or not name.startswith(prefix):
                continue
            runner = getattr(model, "runner", None) or "-"
            status = getattr(getattr(model, "status", None), "value", "installed")
            candidates.append(
                ShellCompletion(
                    text=name,
                    start_position=-len(prefix),
                    display_meta=f"{runner}  {status}",
                )
            )
        return tuple(sorted(candidates, key=lambda candidate: candidate.text))

    return ()


def _load_prompt_fn(controller: ModelController) -> PromptFn:
    try:
        from prompt_toolkit import prompt
        from prompt_toolkit.application.current import get_app
        from prompt_toolkit.completion import Completer, Completion
        from prompt_toolkit.shortcuts.prompt import CompleteStyle
        from prompt_toolkit.filters import Condition
        from prompt_toolkit.key_binding import KeyBindings
    except ImportError:
        raise

    class RunicCompleter(Completer):
        def get_completions(self, document, complete_event):  # type: ignore[no-untyped-def]
            for candidate in complete_shell_input(document.text_before_cursor, controller.list_installed()):
                yield Completion(
                    candidate.text,
                    start_position=candidate.start_position,
                    display_meta=candidate.display_meta,
                )

    key_bindings = KeyBindings()

    @Condition
    def completion_menu_visible() -> bool:
        return bool(get_app().current_buffer.complete_state)

    @key_bindings.add("enter", filter=completion_menu_visible)
    def _(event):  # type: ignore[no-untyped-def]
        buffer = event.app.current_buffer
        if buffer.complete_state and buffer.complete_state.current_completion is not None:
            buffer.apply_completion(buffer.complete_state.current_completion)
        else:
            buffer.validate_and_handle()

    def prompt_with_completions(message: str) -> str:
        if not message.startswith("runic>"):
            return prompt(message)

        return prompt(
            message,
            completer=RunicCompleter(),
            complete_while_typing=True,
            complete_in_thread=False,
            complete_style=CompleteStyle.COLUMN,
            key_bindings=key_bindings,
            reserve_space_for_menu=6,
        )

    return prompt_with_completions


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


async def _stream_chat(controller: ModelController, model: str, prompt: str, console: _Console) -> str:
    response = ""
    async for chunk in controller.chat(model, (ChatMessage(role="user", content=prompt),)):
        response += chunk
        console.print(chunk, end="")
    console.print()
    return response


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


def _console_width(console: _Console) -> int:
    width = getattr(console, "width", 80)
    return width if isinstance(width, int) else 80


def _console_height(console: _Console) -> int:
    height = getattr(console, "height", 18)
    return height if isinstance(height, int) else 18


def _redraw_frame(
    console: _Console,
    *,
    output: Sequence[str],
    prompt: str,
    status: str = "runner: ollama ready",
    pane: PaneState | None = None,
) -> None:
    clear = getattr(console, "clear", None)
    if callable(clear):
        clear()
    console.print(
        render_shell_frame(
            ShellFrame(
                title="Runic Interactive",
                status=status,
                output=output,
                prompt=prompt,
                pane=pane,
                width=_console_width(console),
                height=_console_height(console),
            )
        )
    )


def run_interactive(
    *,
    controller: ModelController | None = None,
    prompt_fn: PromptFn | None = None,
    console: _Console | None = None,
) -> int:
    active_controller = controller or _default_controller()

    try:
        active_prompt_fn = prompt_fn or _load_prompt_fn(active_controller)
    except ImportError:
        print(_cli_extras_message())
        return 1

    try:
        active_console = console or _load_console()
    except ImportError:
        print(_cli_extras_message())
        return 1

    output_lines: list[str] = ["Runic interactive shell"]
    active_pane: PaneState | None = None
    active_status = "runner: ollama ready"

    while True:
        try:
            _redraw_frame(
                active_console,
                output=output_lines,
                prompt="runic> _",
                status=active_status,
                pane=active_pane,
            )
            line = active_prompt_fn("runic> ")
        except (EOFError, KeyboardInterrupt):
            active_console.print()
            return 0

        command = parse_shell_command(line)
        match command.command:
            case ShellCommand.EXIT:
                return 0
            case ShellCommand.HELP:
                message = "Commands: install <model>, chat <model>, embed <model> <text-or-file-path>, help, exit"
                output_lines.append(message)
                active_console.print(message)
            case ShellCommand.INSTALL:
                if command.argument is None:
                    message = "Model selection is not implemented yet. Use install <model>."
                    output_lines.append(message)
                    active_console.print(message)
                else:
                    output_lines.append(f"> install {command.argument}")
                    output_lines.append("Resolving model reference...")
                    active_pane = PaneState(
                        title="Install",
                        lines=(command.argument, "starting", "waiting for runner"),
                        footer=("Esc: hide pane", "Enter: details"),
                    )
                    _redraw_frame(
                        active_console,
                        output=output_lines,
                        prompt=f"runic> install {command.argument}",
                        status=active_status,
                        pane=active_pane,
                    )
                    asyncio.run(_install_and_wait(active_controller, command.argument, active_console))
                    output_lines.append("Installation completed")
                    active_pane = PaneState(
                        title="Install",
                        lines=(command.argument, "############# 100%", "Installation completed"),
                        footer=("Esc: hide pane", "Enter: details"),
                    )
            case ShellCommand.CHAT:
                if command.argument is None:
                    message = "Use chat <model>."
                    output_lines.append(message)
                    active_console.print(message)
                else:
                    active_status = f"model: {command.argument}"
                    active_pane = PaneState(
                        title="Session",
                        lines=(
                            f"model {command.argument}",
                            "runner ollama",
                            "temp default",
                            "",
                            "Future Context",
                            "MCP disabled",
                            "RAG disabled",
                        ),
                    )
                    try:
                        _redraw_frame(
                            active_console,
                            output=output_lines,
                            prompt=f"chat:{command.argument}> _",
                            status=active_status,
                            pane=active_pane,
                        )
                        chat_prompt = active_prompt_fn(f"chat:{command.argument}> ")
                    except (EOFError, KeyboardInterrupt):
                        continue
                    if chat_prompt.strip() == "/exit":
                        continue
                    try:
                        output_lines.append(f"You: {chat_prompt}")
                        response = asyncio.run(_stream_chat(active_controller, command.argument, chat_prompt, active_console))
                        output_lines.append(f"Assistant: {response}")
                    except LookupError as exc:
                        output_lines.append(str(exc))
                        active_console.print(str(exc))
                    except RunnerChatError as exc:
                        message = _format_error(exc.error)
                        output_lines.append(message)
                        active_console.print(message)
            case ShellCommand.EMBED:
                split = _split_model_and_value(command.argument, "embed")
                match split:
                    case Err(error=error):
                        message = _format_error(error)
                        output_lines.append(message)
                        active_console.print(message)
                    case Ok(value=(model, value)):
                        embed_input = _read_embed_input(value)
                        match embed_input:
                            case Err(error=error):
                                message = _format_error(error)
                                output_lines.append(message)
                                active_console.print(message)
                            case Ok(value=text):
                                output_lines.append(f"> embed {model} {value}")
                                active_pane = PaneState(
                                    title="Session",
                                    lines=(f"model {model}", "runner ollama", "embedding mode"),
                                )
                                asyncio.run(_embed_and_print(active_controller, model, text, active_console))
                                output_lines.append("Embedding completed")
            case ShellCommand.UNKNOWN:
                message = f"Unknown command: {line.strip()}"
                output_lines.append(message)
                active_console.print(message)
