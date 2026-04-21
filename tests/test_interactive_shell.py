from __future__ import annotations

import importlib
import sys
import unittest

from runic.interactive.shell import ParsedCommand, ShellCommand, format_install_pane, parse_shell_command


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

    def test_import_runic_does_not_import_optional_cli_libraries(self) -> None:
        sys.modules.pop("prompt_toolkit", None)
        sys.modules.pop("rich", None)

        import runic

        importlib.reload(runic)

        self.assertNotIn("prompt_toolkit", sys.modules)
        self.assertNotIn("rich", sys.modules)
