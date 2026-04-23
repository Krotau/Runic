from __future__ import annotations

import asyncio
import shlex
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from time import sleep as time_sleep
from typing import Protocol

from runic import DefaultError, Err, Ok, Result, Runic

from .controller import ModelController
from .embed_picker import EmbedPickerState
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


class CompletionDisplayMode(str, Enum):
    NONE = "none"
    GHOST = "ghost"
    MENU = "menu"


@dataclass(frozen=True, slots=True)
class ShellCompletionDisplay:
    mode: CompletionDisplayMode
    candidates: tuple[ShellCompletion, ...] = ()
    ghost_text: str = ""


@dataclass(frozen=True, slots=True)
class PaneState:
    title: str
    lines: Sequence[str] = ()
    footer: Sequence[str] = ()
    layout: str = "side"


@dataclass(frozen=True, slots=True)
class ShellFrame:
    title: str
    status: str
    output: Sequence[str]
    prompt: str
    pane: PaneState | None = None
    width: int = 80
    height: int = 18


@dataclass(slots=True)
class TuiShellState:
    output: list[str] = field(default_factory=lambda: ["Runic interactive shell"])
    pane: PaneState | None = None
    status: str = "runner: ollama ready"
    prompt: str = "runic> "
    pane_position: str = "right"
    pane_visible: bool = False
    chat_model: str | None = None
    launch_cwd: Path = field(default_factory=Path.cwd)
    embed_picker: EmbedPickerState | None = None

    def append(self, line: str) -> None:
        self.output.append(line)

    def set_pane(self, pane: PaneState | None) -> None:
        self.pane = pane
        self.pane_visible = pane is not None

    def hide_pane(self) -> None:
        self.pane_visible = False
        self.embed_picker = None

    def cycle_pane_position(self) -> None:
        match self.pane_position:
            case "right":
                self.pane_position = "top"
            case "top":
                self.pane_position = "right"
            case _:
                self.pane_position = "right"

    def enter_chat(self, model: str) -> None:
        self.chat_model = model
        self.status = f"model: {model}"
        self.prompt = f"chat:{model}> "
        self.set_pane(
            PaneState(
                title="Session",
                lines=(
                    f"model {model}",
                    "runner ollama",
                    "temp default",
                    "",
                    "Future Context",
                    "MCP disabled",
                    "RAG disabled",
                ),
            )
        )

    def exit_chat(self) -> None:
        self.chat_model = None
        self.status = "runner: ollama ready"
        self.prompt = "runic> "

    def open_embed_picker(self, model: str) -> None:
        self.embed_picker = EmbedPickerState.start(self.launch_cwd, model=model)
        self.set_pane(
            PaneState(
                title=f"Embed Files: {model}",
                lines=self.embed_picker.format_lines(),
                footer=("Space select", "Tab enter dir", "Enter embed selected", "Backspace up", "Esc cancel"),
                layout="wide",
            )
        )

    def close_embed_picker(self) -> None:
        self.embed_picker = None
        self.hide_pane()

    def refresh_embed_picker_pane(self) -> None:
        if self.embed_picker is None:
            return
        self.set_pane(
            PaneState(
                title=f"Embed Files: {self.embed_picker.model}",
                lines=self.embed_picker.format_lines(),
                footer=("Space select", "Tab enter dir", "Enter embed selected", "Backspace up", "Esc cancel"),
                layout="wide",
            )
        )

    def output_text(self) -> str:
        return "\n".join(self.output)

    def pane_text(self) -> str:
        if self.pane is None:
            return ""
        lines = [self.pane.title, *self.pane.lines]
        if self.pane.footer:
            lines.extend(["", *self.pane.footer])
        return "\n".join(str(line) for line in lines)

    def command_section_title(self) -> str:
        return "Command"

    def command_section_text(self) -> str:
        return self.prompt

    def footer_text(self) -> str:
        if self.embed_picker is not None:
            return "Up/Down move | Space select | Tab enter dir | Enter embed | Backspace up | Esc cancel | Ctrl-Q quit"
        return "Tab accept/next | Enter run/select | Shift-Tab previous | F6 focus | Esc hide pane | Ctrl-P move pane | Ctrl-Q quit"


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


def _pane_lines(pane: PaneState) -> list[str]:
    lines = [pane.title, *pane.lines]
    if pane.footer:
        lines.extend(["", *pane.footer])
    return [str(line) for line in lines]


def render_shell_frame(frame: ShellFrame) -> str:
    width = max(40, frame.width)
    height = max(7, frame.height)
    pane = frame.pane
    output = [str(line) for line in frame.output]

    if pane is not None and width < MIN_SIDE_PANE_WIDTH:
        rows: list[str] = [_border(width)]
        pane_lines = _pane_lines(pane)
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

    if pane.layout == "wide":
        pane_lines = _pane_lines(pane)
        content_height = max(0, height - 6)
        output_rows = min(len(output), max(1, content_height - 7)) if output else 0
        pane_rows = min(len(pane_lines), max(0, content_height - output_rows))
        if pane_rows < len(pane_lines):
            if pane_rows <= 1:
                pane_lines = pane_lines[:pane_rows]
            else:
                pane_lines = [pane_lines[0], *pane_lines[-(pane_rows - 1):]]
        else:
            pane_lines = pane_lines[:pane_rows]
        rows.append(_border(width))
        for line in pane_lines:
            rows.append(_row(line, width))
        for line in output[-output_rows:]:
            rows.append(_row(line, width))
        rows.append(_border(width))
        rows.append(_row(frame.prompt, width))
        rows.append(_border(width))
        return "\n".join(rows[:height])

    pane_width = min(28, max(22, width // 3))
    left_width = width - pane_width - 1
    pane_lines = _pane_lines(pane)
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


def render_startup_splash() -> str:
    return "\n".join(
        (
            "██████╗ ██╗   ██╗███╗   ██╗██╗ ██████╗",
            "██╔══██╗██║   ██║████╗  ██║██║██╔════╝",
            "██████╔╝██║   ██║██╔██╗ ██║██║██║     ",
            "██╔══██╗██║   ██║██║╚██╗██║██║██║     ",
            "██║  ██║╚██████╔╝██║ ╚████║██║╚██████╗",
            "╚═╝  ╚═╝ ╚═════╝ ╚═╝  ╚═══╝╚═╝ ╚═════╝",
            "RUNIC",
        )
    )


def render_colored_startup_splash() -> object:
    from rich.text import Text

    return Text(render_startup_splash(), style="bold blue")


def show_startup_splash(
    console: _Console,
    *,
    sleep_fn: Callable[[float], None] | None = None,
    delay_seconds: float = 1.0,
) -> None:
    try:
        splash: object = render_colored_startup_splash()
    except ImportError:
        splash = render_startup_splash()
    console.print(splash)
    if delay_seconds > 0:
        (sleep_fn or time_sleep)(delay_seconds)


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


def _completion_token_prefix(text_before_cursor: str, candidate: ShellCompletion) -> str:
    if candidate.start_position >= 0:
        return ""
    prefix_length = abs(candidate.start_position)
    if prefix_length == 0:
        return ""
    return text_before_cursor[-prefix_length:]


def classify_shell_completion(text_before_cursor: str, installed_models: Sequence[object]) -> ShellCompletionDisplay:
    candidates = complete_shell_input(text_before_cursor, installed_models)
    if not candidates:
        return ShellCompletionDisplay(mode=CompletionDisplayMode.NONE)
    if len(candidates) > 1:
        return ShellCompletionDisplay(mode=CompletionDisplayMode.MENU, candidates=candidates)

    candidate = candidates[0]
    prefix = _completion_token_prefix(text_before_cursor, candidate)
    if not prefix and candidate.start_position != 0:
        return ShellCompletionDisplay(mode=CompletionDisplayMode.MENU, candidates=candidates)
    if prefix and not candidate.text.startswith(prefix):
        return ShellCompletionDisplay(mode=CompletionDisplayMode.MENU, candidates=candidates)

    ghost_text = candidate.text[len(prefix) :]
    if not ghost_text:
        return ShellCompletionDisplay(mode=CompletionDisplayMode.NONE)
    return ShellCompletionDisplay(
        mode=CompletionDisplayMode.GHOST,
        candidates=candidates,
        ghost_text=ghost_text,
    )


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


def _run_tui_application(controller: ModelController) -> int:
    try:
        from prompt_toolkit.auto_suggest import AutoSuggest, Suggestion
        from prompt_toolkit.application import Application
        from prompt_toolkit.application.current import get_app
        from prompt_toolkit.completion import Completer, Completion
        from prompt_toolkit.formatted_text import FormattedText
        from prompt_toolkit.key_binding import KeyBindings
        from prompt_toolkit.layout import Layout
        from prompt_toolkit.layout.containers import ConditionalContainer, HSplit, VSplit, Window
        from prompt_toolkit.layout.controls import FormattedTextControl
        from prompt_toolkit.layout.dimension import Dimension
        from prompt_toolkit.shortcuts.prompt import CompleteStyle
        from prompt_toolkit.filters import Condition
        from prompt_toolkit.widgets import Frame, TextArea
    except ImportError:
        print(_cli_extras_message())
        return 1

    state = TuiShellState(launch_cwd=Path.cwd())
    output_area = TextArea(
        text=state.output_text(),
        read_only=True,
        focusable=True,
        scrollbar=True,
        wrap_lines=True,
    )
    pane_area = TextArea(
        text=state.pane_text(),
        read_only=True,
        focusable=True,
        scrollbar=True,
        wrap_lines=True,
    )

    def completion_display(text_before_cursor: str) -> ShellCompletionDisplay:
        if state.chat_model is not None:
            return ShellCompletionDisplay(mode=CompletionDisplayMode.NONE)
        return classify_shell_completion(text_before_cursor, controller.list_installed())

    class RunicCompleter(Completer):
        def get_completions(self, document, complete_event):  # type: ignore[no-untyped-def]
            display = completion_display(document.text_before_cursor)
            if display.mode is not CompletionDisplayMode.MENU:
                return
            for candidate in display.candidates:
                yield Completion(
                    candidate.text,
                    start_position=candidate.start_position,
                    display_meta=candidate.display_meta,
                )

    class RunicAutoSuggest(AutoSuggest):
        def get_suggestion(self, buffer, document):  # type: ignore[no-untyped-def]
            display = completion_display(document.text_before_cursor)
            if display.mode is not CompletionDisplayMode.GHOST:
                return None
            return Suggestion(display.ghost_text)

    command_area = TextArea(
        height=1,
        multiline=False,
        completer=RunicCompleter(),
        auto_suggest=RunicAutoSuggest(),
        complete_while_typing=True,
        focusable=True,
        wrap_lines=False,
    )
    key_bindings = KeyBindings()

    def refresh() -> None:
        output_area.text = state.output_text()
        pane_area.text = state.pane_text()
        get_app().invalidate()

    def set_install_pane(model: str, progress: str, *lines: str) -> None:
        state.set_pane(
            PaneState(
                title="Install",
                lines=(model, progress, *lines),
            )
        )

    async def handle_chat_message(text: str) -> None:
        model = state.chat_model
        if model is None:
            return
        if text.strip() == "/exit":
            state.exit_chat()
            state.append("Exited chat.")
            refresh()
            return

        state.append(f"You: {text}")
        refresh()
        try:
            response = ""
            async for chunk in controller.chat(model, (ChatMessage(role="user", content=text),)):
                response += chunk
                if response:
                    output_area.text = f"{state.output_text()}\nAssistant: {response}"
                    get_app().invalidate()
            state.append(f"Assistant: {response}")
        except LookupError as exc:
            state.append(str(exc))
        except RunnerChatError as exc:
            state.append(_format_error(exc.error))
        refresh()

    async def handle_command(text: str) -> None:
        command = parse_shell_command(text)
        match command.command:
            case ShellCommand.EXIT:
                get_app().exit(result=0)
            case ShellCommand.HELP:
                state.append("Commands: install <model>, chat <model>, embed <model> <text-or-file-path>, help, exit")
                refresh()
            case ShellCommand.UNKNOWN:
                state.append(f"Unknown command: {text.strip()}")
                refresh()
            case ShellCommand.INSTALL:
                if command.argument is None:
                    state.append("Model selection is not implemented yet. Use install <model>.")
                    refresh()
                    return
                state.append(f"> install {command.argument}")
                state.append("Resolving model reference...")
                set_install_pane(command.argument, "starting", "waiting for runner")
                refresh()
                result = await controller.install(command.argument)
                match result:
                    case Err(error=error):
                        state.append(_format_error(error))
                        set_install_pane(command.argument, "failed", _format_error(error))
                    case Ok(value=spell_id):
                        state.append(f"Installation scheduled: {spell_id}")
                        set_install_pane(command.argument, "running", f"spell {spell_id}")
                        refresh()
                        settled = await controller.wait_for_install(spell_id)
                        match settled:
                            case Ok():
                                state.append("Installation completed")
                                set_install_pane(command.argument, "############# 100%", "Installation completed")
                            case Err(error=error):
                                state.append(_format_error(error))
                                set_install_pane(command.argument, "failed", _format_error(error))
                refresh()
            case ShellCommand.CHAT:
                if command.argument is None:
                    state.append("Use chat <model>.")
                else:
                    state.append(f"> chat {command.argument}")
                    state.enter_chat(command.argument)
                refresh()
            case ShellCommand.EMBED:
                split = _split_embed_argument(command.argument)
                match split:
                    case Err(error=error):
                        state.append(_format_error(error))
                    case Ok(value=(model, None)):
                        state.append(f"> embed {model}")
                        state.open_embed_picker(model)
                        refresh()
                        get_app().layout.focus(pane_area)
                        return
                    case Ok(value=(model, value)):
                        embed_input = _read_embed_input(value)
                        match embed_input:
                            case Err(error=error):
                                state.append(_format_error(error))
                            case Ok(value=text_value):
                                state.append(f"> embed {model} {value}")
                                state.set_pane(
                                    PaneState(
                                        title="Session",
                                        lines=(f"model {model}", "runner ollama", "embedding mode"),
                                    )
                                )
                                refresh()
                                result = await controller.embed(model, text_value)
                                match result:
                                    case Ok(value=embedding):
                                        state.append(f"Embedding dimensions: {len(embedding)}")
                                        state.append(f"Embedding preview: {_format_embedding_preview(embedding)}")
                                    case Err(error=error):
                                        state.append(_format_error(error))
                refresh()

    async def accept_input() -> None:
        text = command_area.text
        command_area.text = ""
        if not text.strip():
            refresh()
            return
        if state.chat_model is not None:
            await handle_chat_message(text)
        else:
            await handle_command(text)

    @Condition
    def completion_menu_visible() -> bool:
        return bool(command_area.buffer.complete_state)

    @Condition
    def input_focused() -> bool:
        return get_app().layout.current_window is command_area.window

    @Condition
    def pane_focused() -> bool:
        return get_app().layout.current_window is pane_area.window

    @Condition
    def pane_visible() -> bool:
        return state.pane is not None and state.pane_visible

    @Condition
    def pane_right() -> bool:
        return state.pane_position == "right"

    @Condition
    def pane_top() -> bool:
        return state.pane_position == "top"

    @Condition
    def picker_active() -> bool:
        return state.embed_picker is not None

    @Condition
    def pane_wide() -> bool:
        return state.pane is not None and state.pane.layout == "wide"

    @key_bindings.add("enter", filter=completion_menu_visible & input_focused)
    def _(event):  # type: ignore[no-untyped-def]
        completion = command_area.buffer.complete_state.current_completion
        if completion is not None:
            command_area.buffer.apply_completion(completion)

    @key_bindings.add("enter", filter=input_focused & ~completion_menu_visible)
    def _(event):  # type: ignore[no-untyped-def]
        event.app.create_background_task(accept_input())

    @key_bindings.add("up", filter=pane_focused & picker_active)
    def _(event):  # type: ignore[no-untyped-def]
        if state.embed_picker is not None:
            state.embed_picker.move_up()
            state.refresh_embed_picker_pane()
            refresh()

    @key_bindings.add("down", filter=pane_focused & picker_active)
    def _(event):  # type: ignore[no-untyped-def]
        if state.embed_picker is not None:
            state.embed_picker.move_down()
            state.refresh_embed_picker_pane()
            refresh()

    @key_bindings.add(" ", filter=pane_focused & picker_active)
    def _(event):  # type: ignore[no-untyped-def]
        if state.embed_picker is not None:
            state.embed_picker.toggle_selection()
            state.refresh_embed_picker_pane()
            refresh()

    @key_bindings.add("tab", filter=pane_focused & picker_active)
    def _(event):  # type: ignore[no-untyped-def]
        if state.embed_picker is not None:
            state.embed_picker.enter_hovered_directory()
            state.refresh_embed_picker_pane()
            refresh()

    @key_bindings.add("backspace", filter=pane_focused & picker_active)
    def _(event):  # type: ignore[no-untyped-def]
        if state.embed_picker is not None:
            state.embed_picker.move_to_parent()
            state.refresh_embed_picker_pane()
            refresh()

    @key_bindings.add("escape", filter=pane_focused & picker_active)
    def _(event):  # type: ignore[no-untyped-def]
        state.close_embed_picker()
        refresh()
        event.app.layout.focus(command_area)

    @key_bindings.add("enter", filter=pane_focused & ~picker_active)
    def _(event):  # type: ignore[no-untyped-def]
        if state.pane is not None:
            state.append(f"Pane details: {state.pane.title}")
            refresh()

    @key_bindings.add("tab", filter=input_focused)
    def _(event):  # type: ignore[no-untyped-def]
        buffer = command_area.buffer
        if buffer.complete_state:
            buffer.complete_next()
            return
        display = completion_display(buffer.document.text_before_cursor)
        if display.mode is CompletionDisplayMode.GHOST:
            buffer.insert_text(display.ghost_text)
            return
        if display.mode is CompletionDisplayMode.MENU:
            buffer.start_completion(select_first=True)

    @key_bindings.add("s-tab", filter=input_focused & completion_menu_visible)
    def _(event):  # type: ignore[no-untyped-def]
        command_area.buffer.complete_previous()

    @key_bindings.add("f6")
    def _(event):  # type: ignore[no-untyped-def]
        focus_order = [command_area, output_area]
        if state.pane is not None and state.pane_visible:
            focus_order.append(pane_area)
        current = event.app.layout.current_window
        current_index = 0
        for index, area in enumerate(focus_order):
            if current is area.window:
                current_index = index
                break
        event.app.layout.focus(focus_order[(current_index + 1) % len(focus_order)])

    @key_bindings.add("s-tab", filter=~(input_focused & completion_menu_visible))
    def _(event):  # type: ignore[no-untyped-def]
        focus_order = [command_area, output_area]
        if state.pane is not None and state.pane_visible:
            focus_order.append(pane_area)
        current = event.app.layout.current_window
        current_index = 0
        for index, area in enumerate(focus_order):
            if current is area.window:
                current_index = index
                break
        event.app.layout.focus(focus_order[(current_index - 1) % len(focus_order)])

    @key_bindings.add("escape", filter=pane_visible & ~picker_active)
    def _(event):  # type: ignore[no-untyped-def]
        state.hide_pane()
        refresh()

    @key_bindings.add("c-p", filter=pane_visible)
    def _(event):  # type: ignore[no-untyped-def]
        state.cycle_pane_position()
        refresh()

    @key_bindings.add("c-c")
    @key_bindings.add("c-q")
    def _(event):  # type: ignore[no-untyped-def]
        event.app.exit(result=0)

    def header_text():
        return FormattedText([("class:header", _frame_line("Runic Interactive", state.status, 78))])

    def prompt_text():
        return FormattedText([("class:prompt", state.prompt)])

    def footer_text():
        return FormattedText([("class:footer", state.footer_text())])

    header = Window(FormattedTextControl(header_text), height=1)
    footer = Window(FormattedTextControl(footer_text), height=1)
    prompt_label = Window(FormattedTextControl(prompt_text), width=Dimension(min=8, max=32))
    input_row = VSplit([prompt_label, command_area])
    command_section = Frame(input_row, title=state.command_section_title())
    right_body = VSplit(
        [
            Frame(output_area, title="Output"),
            ConditionalContainer(Frame(pane_area, title="Pane"), filter=pane_visible & pane_right & ~pane_wide),
        ]
    )
    top_body = HSplit(
        [
            ConditionalContainer(Frame(pane_area, title="Pane"), filter=pane_visible & pane_top & ~pane_wide),
            Frame(output_area, title="Output"),
        ]
    )
    wide_body = HSplit(
        [
            ConditionalContainer(Frame(pane_area, title="Pane"), filter=pane_visible & pane_wide),
            Frame(output_area, title="Output"),
        ]
    )
    root = HSplit(
        [
            header,
            command_section,
            ConditionalContainer(wide_body, filter=pane_wide),
            ConditionalContainer(right_body, filter=~pane_top & ~pane_wide),
            ConditionalContainer(top_body, filter=pane_top & ~pane_wide),
            footer,
        ]
    )
    app = Application(
        layout=Layout(root, focused_element=command_area),
        key_bindings=key_bindings,
        full_screen=True,
        mouse_support=True,
        refresh_interval=0.25,
    )
    try:
        result = app.run()
    except (EOFError, KeyboardInterrupt):
        return 0
    return int(result or 0)


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
        detail = _format_error_detail(error.details)
        if detail:
            return f"{message} {detail}"
    return message


def _format_error_detail(details: dict[object, object]) -> str:
    for key in ("error", "stderr"):
        detail = details.get(key)
        if isinstance(detail, str) and detail.strip():
            return " ".join(detail.split())

    stdout = details.get("stdout")
    if isinstance(stdout, list):
        lines = [str(line).strip() for line in stdout if str(line).strip()]
        if lines:
            return " ".join(lines)

    return ""


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


def _split_embed_argument(argument: str | None) -> Result[tuple[str, str | None], DefaultError]:
    if argument is None:
        return Err(DefaultError(message="Use embed <model> <text-or-file-path>.", code="invalid_command"))

    try:
        parts = shlex.split(argument)
    except ValueError as exc:
        return Err(DefaultError(message=str(exc), code="invalid_command"))

    if not parts:
        return Err(DefaultError(message="Use embed <model> <text-or-file-path>.", code="invalid_command"))
    if len(parts) == 1:
        return Ok((parts[0], None))
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
    startup_delay: float | None = None,
    sleep_fn: Callable[[float], None] | None = None,
) -> int:
    active_controller = controller or _default_controller()

    if prompt_fn is None and console is None:
        delay_seconds = 1.0 if startup_delay is None else startup_delay
        try:
            splash_console = _load_console()
        except ImportError:
            print(render_startup_splash())
            if delay_seconds > 0:
                (sleep_fn or time_sleep)(delay_seconds)
        else:
            show_startup_splash(splash_console, sleep_fn=sleep_fn, delay_seconds=delay_seconds)
        return _run_tui_application(active_controller)

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

    delay_seconds = 0.0 if startup_delay is None else startup_delay
    show_startup_splash(active_console, sleep_fn=sleep_fn, delay_seconds=delay_seconds)

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
