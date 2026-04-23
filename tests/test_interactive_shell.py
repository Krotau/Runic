from __future__ import annotations

import asyncio
import contextlib
import io
import importlib
import sys
import tempfile
import unittest
from collections.abc import Callable
from pathlib import Path
from unittest.mock import patch

from runic import DefaultError, Err, Ok, Runic
from runic.interactive.controller import ModelController
from runic.interactive.models import ChatMessage, InstalledModel, ModelInstallStatus, ModelProvider
from runic.interactive.registry import ModelRegistry
from runic.interactive.runners.base import RunnerCapability, RunnerChatError
import runic.cli as cli
import runic.interactive.shell as shell
from runic.interactive.shell import (
    CompletionDisplayMode,
    PaneState,
    ParsedCommand,
    ShellCommand,
    ShellFrame,
    TuiShellState,
    classify_shell_completion,
    complete_shell_input,
    format_install_pane,
    parse_shell_command,
    render_shell_frame,
)


class FakeConsole:
    def __init__(self) -> None:
        self.calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def print(self, *objects: object, **kwargs: object) -> None:
        self.calls.append((objects, dict(kwargs)))

    def clear(self) -> None:
        self.calls.append((("[clear]",), {}))

    def text(self) -> str:
        parts: list[str] = []
        for objects, kwargs in self.calls:
            parts.append(" ".join(str(obj) for obj in objects))
            end = str(kwargs.get("end", "\n"))
            parts.append(end)
        return "".join(parts)


class FakeController:
    def __init__(self, *, install_result: object = Ok("spell-123"), chat_chunks: tuple[str, ...] = ("chunk-a", "chunk-b")) -> None:
        self.install_result = install_result
        self.chat_chunks = chat_chunks
        self.install_calls: list[str] = []
        self.chat_calls: list[tuple[str, tuple[ChatMessage, ...]]] = []
        self.embed_calls: list[tuple[str, str]] = []
        self.installed = (
            InstalledModel(
                name="llama3.2",
                provider=ModelProvider.OLLAMA,
                source="ollama://llama3.2",
                runner="ollama",
                status=ModelInstallStatus.INSTALLED,
            ),
            InstalledModel(
                name="qwen3-embedding:8b",
                provider=ModelProvider.OLLAMA,
                source="ollama://qwen3-embedding:8b",
                runner="ollama",
                status=ModelInstallStatus.INSTALLED,
            ),
        )

    async def install(self, model: str) -> object:
        self.install_calls.append(model)
        return self.install_result

    async def wait_for_install(self, spell_id: str) -> object:
        return Ok({"spell_id": spell_id})

    async def chat(self, model: str, messages: tuple[ChatMessage, ...]):
        self.chat_calls.append((model, messages))
        for chunk in self.chat_chunks:
            yield chunk

    async def embed(self, model: str, text: str) -> object:
        self.embed_calls.append((model, text))
        return Ok([0.1, 0.2, 0.3])

    def list_installed(self) -> tuple[InstalledModel, ...]:
        return self.installed


class FailingChatController(FakeController):
    async def chat(self, model: str, messages: tuple[ChatMessage, ...]):
        self.chat_calls.append((model, messages))
        raise RunnerChatError(
            DefaultError(
                message="Failed to chat with Ollama.",
                code="runner_chat_failed",
                details={"error": "qwen3-embedding:8b does not support chat"},
            )
        )
        if False:
            yield ""


class CompletingRunner:
    name = "ollama"
    capabilities = (RunnerCapability(provider=ModelProvider.OLLAMA, can_embed=True),)

    def __init__(self) -> None:
        self.installed: list[str] = []

    async def is_available(self) -> bool:
        return True

    async def install_runner(self):  # type: ignore[no-untyped-def]
        return Err(Exception("not used"))

    async def install_model(self, reference, context):  # type: ignore[no-untyped-def]
        await asyncio.sleep(0.01)
        await context.log(f"installing:{reference.model}")
        await context.progress(1.0)
        self.installed.append(reference.model)
        return Ok(
            InstalledModel(
                name=reference.local_name,
                provider=reference.provider,
                source=reference.source,
                runner=self.name,
                status=ModelInstallStatus.INSTALLED,
            )
        )

    async def list_models(self):  # type: ignore[no-untyped-def]
        return Ok([])

    async def chat(self, model: str, messages: tuple[ChatMessage, ...]):  # type: ignore[no-untyped-def]
        yield f"{model}:{messages[-1].content}"

    async def embed(self, model: str, text: str):  # type: ignore[no-untyped-def]
        return Ok([float(len(model)), float(len(text))])


def make_prompt_fn(commands: list[str], chat_prompts: list[str]) -> Callable[[str], str]:
    command_iter = iter(commands)
    chat_iter = iter(chat_prompts)

    def prompt_fn(text: str) -> str:
        if text.startswith("chat:"):
            return next(chat_iter)
        return next(command_iter)

    return prompt_fn


class TestInteractiveShell(unittest.TestCase):
    def test_parse_install_command(self) -> None:
        self.assertEqual(ParsedCommand(ShellCommand.INSTALL, "llama3.2"), parse_shell_command("install llama3.2"))

    def test_parse_chat_command_without_model(self) -> None:
        self.assertEqual(ParsedCommand(ShellCommand.CHAT, None), parse_shell_command("chat"))

    def test_parse_embed_command(self) -> None:
        self.assertEqual(
            ParsedCommand(ShellCommand.EMBED, 'qwen3-embedding:8b "hello world"'),
            parse_shell_command('embed qwen3-embedding:8b "hello world"'),
        )

    def test_parse_run_is_unknown(self) -> None:
        self.assertEqual(ParsedCommand(ShellCommand.UNKNOWN, "run llama3.2"), parse_shell_command("run llama3.2"))

    def test_parse_exit_command(self) -> None:
        self.assertEqual(ParsedCommand(ShellCommand.EXIT, None), parse_shell_command("exit"))

    def test_complete_shell_input_suggests_commands(self) -> None:
        candidates = complete_shell_input("ch", ())

        self.assertEqual(["chat"], [candidate.text for candidate in candidates])
        self.assertEqual([-2], [candidate.start_position for candidate in candidates])

    def test_classify_shell_completion_uses_ghost_for_single_command_match(self) -> None:
        display = classify_shell_completion("ch", ())

        self.assertEqual(CompletionDisplayMode.GHOST, display.mode)
        self.assertEqual("at", display.ghost_text)
        self.assertEqual(["chat"], [candidate.text for candidate in display.candidates])

    def test_classify_shell_completion_uses_menu_for_ambiguous_command_match(self) -> None:
        display = classify_shell_completion("e", ())

        self.assertEqual(CompletionDisplayMode.MENU, display.mode)
        self.assertEqual("", display.ghost_text)
        self.assertEqual(["embed", "exit"], [candidate.text for candidate in display.candidates])

    def test_classify_shell_completion_returns_none_when_there_are_no_matches(self) -> None:
        display = classify_shell_completion("z", ())

        self.assertEqual(CompletionDisplayMode.NONE, display.mode)
        self.assertEqual("", display.ghost_text)
        self.assertEqual([], list(display.candidates))

    def test_complete_shell_input_suggests_installed_models_after_chat(self) -> None:
        controller = FakeController()

        candidates = complete_shell_input("chat qw", controller.list_installed())

        self.assertEqual(["qwen3-embedding:8b"], [candidate.text for candidate in candidates])
        self.assertEqual([-2], [candidate.start_position for candidate in candidates])
        self.assertIn("ollama", candidates[0].display_meta)

    def test_classify_shell_completion_uses_ghost_for_single_chat_model_match(self) -> None:
        controller = FakeController()

        display = classify_shell_completion("chat qw", controller.list_installed())

        self.assertEqual(CompletionDisplayMode.GHOST, display.mode)
        self.assertEqual("en3-embedding:8b", display.ghost_text)
        self.assertEqual(["qwen3-embedding:8b"], [candidate.text for candidate in display.candidates])

    def test_classify_shell_completion_uses_menu_for_ambiguous_chat_model_match(self) -> None:
        controller = FakeController()

        display = classify_shell_completion("chat ", controller.list_installed())

        self.assertEqual(CompletionDisplayMode.MENU, display.mode)
        self.assertEqual("", display.ghost_text)
        self.assertEqual(["llama3.2", "qwen3-embedding:8b"], [candidate.text for candidate in display.candidates])

    def test_complete_shell_input_suggests_installed_models_after_embed(self) -> None:
        controller = FakeController()

        candidates = complete_shell_input("embed ", controller.list_installed())

        self.assertEqual(["llama3.2", "qwen3-embedding:8b"], [candidate.text for candidate in candidates])

    def test_classify_shell_completion_uses_ghost_for_single_embed_model_match(self) -> None:
        controller = FakeController()

        display = classify_shell_completion("embed qw", controller.list_installed())

        self.assertEqual(CompletionDisplayMode.GHOST, display.mode)
        self.assertEqual("en3-embedding:8b", display.ghost_text)
        self.assertEqual(["qwen3-embedding:8b"], [candidate.text for candidate in display.candidates])

    def test_classify_shell_completion_uses_menu_for_ambiguous_embed_model_match(self) -> None:
        controller = FakeController()

        display = classify_shell_completion("embed ", controller.list_installed())

        self.assertEqual(CompletionDisplayMode.MENU, display.mode)
        self.assertEqual("", display.ghost_text)
        self.assertEqual(["llama3.2", "qwen3-embedding:8b"], [candidate.text for candidate in display.candidates])

    def test_complete_shell_input_stops_model_completion_after_embed_model(self) -> None:
        controller = FakeController()

        candidates = complete_shell_input("embed qwen3-embedding:8b text", controller.list_installed())

        self.assertEqual((), candidates)

    def test_classify_shell_completion_stops_after_embed_model_argument(self) -> None:
        controller = FakeController()

        display = classify_shell_completion("embed qwen3-embedding:8b text", controller.list_installed())

        self.assertEqual(CompletionDisplayMode.NONE, display.mode)
        self.assertEqual("", display.ghost_text)
        self.assertEqual([], list(display.candidates))

    def test_format_install_pane_is_ascii(self) -> None:
        pane = format_install_pane("llama3.2", 0.82, ["downloading layers", "1.8 GB / 2.2 GB"])

        self.assertIn("Install", pane)
        self.assertIn("llama3.2", pane)
        self.assertIn("82%", pane)
        self.assertTrue(all(ord(character) < 128 for character in pane))

    def test_render_startup_splash_contains_large_special_character_runic_banner(self) -> None:
        splash = shell.render_startup_splash()

        self.assertIn("RUNIC", splash)
        self.assertNotIn("\x1b[", splash)
        self.assertNotIn("[38;5;", splash)
        self.assertTrue(any(character in splash for character in "█╗╔║╝╚═"))

    def test_render_colored_startup_splash_uses_blue_rich_style(self) -> None:
        splash = shell.render_colored_startup_splash()

        self.assertEqual(shell.render_startup_splash(), splash.plain)
        self.assertEqual("bold blue", str(splash.style))

    def test_show_startup_splash_prints_banner_and_waits_one_second(self) -> None:
        console = FakeConsole()
        waits: list[float] = []

        shell.show_startup_splash(console, sleep_fn=waits.append)

        self.assertIn("RUNIC", console.text())
        self.assertEqual([1.0], waits)

    def test_render_shell_frame_draws_install_side_pane(self) -> None:
        frame = render_shell_frame(
            ShellFrame(
                title="Runic Interactive",
                status="runner: ollama ready",
                output=("> install ollama://llama3.2", "Resolving model reference...", "Starting Ollama pull..."),
                prompt="runic> install https://ollama.com/library/llama3.2",
                pane=PaneState(
                    title="Install",
                    lines=("llama3.2", "###########.. 82%", "downloading layers", "1.8 GB / 2.2 GB"),
                    footer=("Esc: hide pane", "Enter: details"),
                ),
                width=78,
                height=15,
            )
        )

        self.assertIn("Runic Interactive", frame)
        self.assertIn("runner: ollama ready", frame)
        self.assertIn("Install", frame)
        self.assertIn("llama3.2", frame)
        self.assertIn("downloading layers", frame)
        self.assertIn("runic> install", frame)
        self.assertTrue(all(ord(character) < 128 for character in frame))

    def test_render_shell_frame_collapses_pane_on_small_width(self) -> None:
        frame = render_shell_frame(
            ShellFrame(
                title="Runic Interactive",
                status="runner: ollama ready",
                output=("Resolving model reference...",),
                prompt="runic> _",
                pane=PaneState(title="Install", lines=("llama3.2  ###########.. 82%", "1.8 GB / 2.2 GB")),
                width=64,
                height=9,
            )
        )

        lines = frame.splitlines()
        self.assertIn("Install", lines[1])
        self.assertIn("llama3.2", frame)
        self.assertIn("runic> _", frame)
        self.assertTrue(all(len(line) == 64 for line in lines))

    def test_render_shell_frame_draws_chat_session_pane(self) -> None:
        frame = render_shell_frame(
            ShellFrame(
                title="Runic Interactive",
                status="model: llama3.2",
                output=("You: summarize this design", "Assistant: The design adds panes..."),
                prompt="chat:llama3.2> _",
                pane=PaneState(
                    title="Session",
                    lines=("model llama3.2", "runner ollama", "temp default", "", "Future Context", "MCP disabled", "RAG disabled"),
                ),
                width=78,
                height=14,
            )
        )

        self.assertIn("Session", frame)
        self.assertIn("Future Context", frame)
        self.assertIn("chat:llama3.2> _", frame)

    def test_tui_shell_state_tracks_focusable_pane_state(self) -> None:
        state = TuiShellState()

        state.enter_chat("llama3.2")
        self.assertEqual("chat:llama3.2> ", state.prompt)
        self.assertTrue(state.pane_visible)
        self.assertIn("Session", state.pane_text())
        self.assertIn("model llama3.2", state.pane_text())
        self.assertNotIn("F6: focus pane", state.pane_text())
        self.assertNotIn("Esc: hide pane", state.pane_text())
        self.assertNotIn("Ctrl-P: move pane", state.pane_text())

        state.hide_pane()
        self.assertFalse(state.pane_visible)

        state.cycle_pane_position()
        self.assertEqual("top", state.pane_position)

    def test_tui_shell_state_footer_lists_shortcuts(self) -> None:
        state = TuiShellState()

        footer = state.footer_text()

        self.assertIn("Tab accept/next", footer)
        self.assertIn("Enter run/select", footer)
        self.assertIn("Shift-Tab previous", footer)
        self.assertIn("F6 focus", footer)
        self.assertIn("Esc hide pane", footer)
        self.assertIn("Ctrl-P move pane", footer)
        self.assertIn("Ctrl-Q quit", footer)

    def test_tui_shell_state_command_section_labels(self) -> None:
        state = TuiShellState()

        self.assertEqual("Command", state.command_section_title())
        self.assertIn("runic>", state.command_section_text())

        state.enter_chat("llama3.2")

        self.assertEqual("Command", state.command_section_title())
        self.assertIn("chat:llama3.2>", state.command_section_text())

    def test_install_command_schedules_through_controller(self) -> None:
        controller = FakeController(install_result=Ok("spell-123"))
        console = FakeConsole()

        result = shell.run_interactive(
            controller=controller,
            prompt_fn=make_prompt_fn(["install llama3.2", "exit"], []),
            console=console,
        )

        self.assertEqual(0, result)
        self.assertEqual(["llama3.2"], controller.install_calls)
        self.assertIn("spell-123", console.text())
        self.assertIn("Installation scheduled", console.text())
        self.assertIn("Install", console.text())

    def test_install_command_keeps_spell_alive_until_runner_completes(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            registry = ModelRegistry(Path(tempdir) / "models.json")
            runner = CompletingRunner()
            controller = ModelController(Runic(), registry, runners=(runner,))
            console = FakeConsole()

            result = shell.run_interactive(
                controller=controller,
                prompt_fn=make_prompt_fn(["install llama3.2", "exit"], []),
                console=console,
            )

            self.assertEqual(0, result)
            self.assertEqual(["llama3.2"], runner.installed)
            self.assertEqual(ModelInstallStatus.INSTALLED, registry.get("llama3.2").status)
            self.assertIn("Installation completed", console.text())

    def test_help_lists_chat_and_embed_commands(self) -> None:
        controller = FakeController()
        console = FakeConsole()

        result = shell.run_interactive(
            controller=controller,
            prompt_fn=make_prompt_fn(["help", "exit"], []),
            console=console,
        )

        self.assertEqual(0, result)
        self.assertIn("chat <model>", console.text())
        self.assertIn("embed <model> <text-or-file-path>", console.text())
        self.assertNotIn("run [model]", console.text())

    def test_chat_command_prompts_chat_and_streams_chunks(self) -> None:
        controller = FakeController(chat_chunks=("hello ", "world"))
        console = FakeConsole()

        result = shell.run_interactive(
            controller=controller,
            prompt_fn=make_prompt_fn(["chat llama3.2", "exit"], ["Tell me more"]),
            console=console,
        )

        self.assertEqual(0, result)
        self.assertEqual(1, len(controller.chat_calls))
        self.assertEqual("llama3.2", controller.chat_calls[0][0])
        self.assertEqual((ChatMessage(role="user", content="Tell me more"),), controller.chat_calls[0][1])
        self.assertIn("hello world", console.text())
        self.assertIn("Session", console.text())
        self.assertIn("model llama3.2", console.text())

    def test_chat_command_prints_runner_chat_errors_without_crashing(self) -> None:
        controller = FailingChatController()
        console = FakeConsole()

        result = shell.run_interactive(
            controller=controller,
            prompt_fn=make_prompt_fn(["chat qwen3-embedding:8b", "exit"], ["Embed this"]),
            console=console,
        )

        self.assertEqual(0, result)
        self.assertEqual(1, len(controller.chat_calls))
        self.assertIn("runner_chat_failed: Failed to chat with Ollama.", console.text())
        self.assertIn("qwen3-embedding:8b does not support chat", console.text())

    def test_format_error_includes_runner_command_stderr(self) -> None:
        error = DefaultError(
            message="Runner command failed.",
            code="runner_command_failed",
            details={
                "command": ["ollama", "pull", "gemma4:e4"],
                "stderr": "pull model manifest: file does not exist",
            },
        )

        message = shell._format_error(error)

        self.assertIn("runner_command_failed: Runner command failed.", message)
        self.assertIn("pull model manifest: file does not exist", message)

    def test_chat_exit_leaves_chat_without_controller_call(self) -> None:
        controller = FakeController(chat_chunks=("should-not-print",))
        console = FakeConsole()

        result = shell.run_interactive(
            controller=controller,
            prompt_fn=make_prompt_fn(["chat llama3.2", "exit"], ["/exit"]),
            console=console,
        )

        self.assertEqual(0, result)
        self.assertEqual([], controller.chat_calls)
        self.assertNotIn("should-not-print", console.text())

    def test_embed_command_embeds_literal_text(self) -> None:
        controller = FakeController()
        console = FakeConsole()

        result = shell.run_interactive(
            controller=controller,
            prompt_fn=make_prompt_fn(['embed qwen3-embedding:8b "hello world"', "exit"], []),
            console=console,
        )

        self.assertEqual(0, result)
        self.assertEqual([("qwen3-embedding:8b", "hello world")], controller.embed_calls)
        self.assertIn("Embedding dimensions: 3", console.text())

    def test_embed_command_reads_existing_file_path(self) -> None:
        controller = FakeController()
        console = FakeConsole()
        with tempfile.TemporaryDirectory() as tempdir:
            path = Path(tempdir) / "input.txt"
            path.write_text("file text", encoding="utf-8")

            result = shell.run_interactive(
                controller=controller,
                prompt_fn=make_prompt_fn([f"embed qwen3-embedding:8b {path}", "exit"], []),
                console=console,
            )

        self.assertEqual(0, result)
        self.assertEqual([("qwen3-embedding:8b", "file text")], controller.embed_calls)

    def test_missing_optional_cli_extras_prints_hint_and_returns_one(self) -> None:
        controller = FakeController()
        output = io.StringIO()

        with patch.object(shell, "_run_tui_application", side_effect=lambda _: print(shell._cli_extras_message()) or 1):
            with contextlib.redirect_stdout(output):
                result = shell.run_interactive(controller=controller, startup_delay=0)

        self.assertEqual(1, result)
        self.assertIn('runic-io[cli]', output.getvalue())

    def test_default_interactive_path_uses_tui_application(self) -> None:
        controller = FakeController()
        console = FakeConsole()

        with patch.object(shell, "_load_console", return_value=console):
            with patch.object(shell, "time_sleep") as sleep:
                with patch.object(shell, "_run_tui_application", return_value=5) as run_tui:
                    self.assertEqual(5, shell.run_interactive(controller=controller))

        self.assertIn("RUNIC", console.text())
        sleep.assert_called_once_with(1.0)
        run_tui.assert_called_once_with(controller)

    def test_default_interactive_path_can_skip_startup_delay(self) -> None:
        controller = FakeController()

        with patch.object(shell, "_run_tui_application", return_value=5) as run_tui:
            self.assertEqual(5, shell.run_interactive(controller=controller, startup_delay=0))

        run_tui.assert_called_once_with(controller)

    def test_cli_main_delegates_lazily(self) -> None:
        with patch.object(shell, "run_interactive", return_value=7) as run_interactive:
            self.assertEqual(7, cli.main())
            run_interactive.assert_called_once_with()

    def test_import_runic_does_not_import_optional_cli_libraries(self) -> None:
        sys.modules.pop("prompt_toolkit", None)
        sys.modules.pop("rich", None)

        import runic

        importlib.reload(runic)

        self.assertNotIn("prompt_toolkit", sys.modules)
        self.assertNotIn("rich", sys.modules)
