from __future__ import annotations

import contextlib
import io
import importlib
import sys
import unittest
from collections.abc import Callable
from unittest.mock import patch

from runic import Ok
from runic.interactive.models import ChatMessage
import runic.cli as cli
import runic.interactive.shell as shell
from runic.interactive.shell import ParsedCommand, ShellCommand, format_install_pane, parse_shell_command


class FakeConsole:
    def __init__(self) -> None:
        self.calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def print(self, *objects: object, **kwargs: object) -> None:
        self.calls.append((objects, dict(kwargs)))

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

    async def install(self, model: str) -> object:
        self.install_calls.append(model)
        return self.install_result

    async def chat(self, model: str, messages: tuple[ChatMessage, ...]):
        self.chat_calls.append((model, messages))
        for chunk in self.chat_chunks:
            yield chunk


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

    def test_parse_run_command_without_model(self) -> None:
        self.assertEqual(ParsedCommand(ShellCommand.RUN, None), parse_shell_command("run"))

    def test_parse_exit_command(self) -> None:
        self.assertEqual(ParsedCommand(ShellCommand.EXIT, None), parse_shell_command("exit"))

    def test_format_install_pane_is_ascii(self) -> None:
        pane = format_install_pane("llama3.2", 0.82, ["downloading layers", "1.8 GB / 2.2 GB"])

        self.assertIn("Install", pane)
        self.assertIn("llama3.2", pane)
        self.assertIn("82%", pane)
        self.assertTrue(all(ord(character) < 128 for character in pane))

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

    def test_run_command_prompts_chat_and_streams_chunks(self) -> None:
        controller = FakeController(chat_chunks=("hello ", "world"))
        console = FakeConsole()

        result = shell.run_interactive(
            controller=controller,
            prompt_fn=make_prompt_fn(["run llama3.2", "exit"], ["Tell me more"]),
            console=console,
        )

        self.assertEqual(0, result)
        self.assertEqual(1, len(controller.chat_calls))
        self.assertEqual("llama3.2", controller.chat_calls[0][0])
        self.assertEqual((ChatMessage(role="user", content="Tell me more"),), controller.chat_calls[0][1])
        self.assertIn("hello world", console.text())

    def test_run_exit_leaves_chat_without_controller_call(self) -> None:
        controller = FakeController(chat_chunks=("should-not-print",))
        console = FakeConsole()

        result = shell.run_interactive(
            controller=controller,
            prompt_fn=make_prompt_fn(["run llama3.2", "exit"], ["/exit"]),
            console=console,
        )

        self.assertEqual(0, result)
        self.assertEqual([], controller.chat_calls)
        self.assertNotIn("should-not-print", console.text())

    def test_missing_optional_cli_extras_prints_hint_and_returns_one(self) -> None:
        controller = FakeController()
        output = io.StringIO()

        with patch.object(shell, "_load_prompt_fn", side_effect=ImportError):
            with contextlib.redirect_stdout(output):
                result = shell.run_interactive(controller=controller)

        self.assertEqual(1, result)
        self.assertIn('runic-io[cli]', output.getvalue())

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
