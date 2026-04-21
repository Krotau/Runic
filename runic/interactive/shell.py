from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Sequence


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


def run_interactive() -> int:
    try:
        from prompt_toolkit import prompt
    except ImportError:
        print("Optional CLI extras are not installed. Install runic[cli] to use the interactive shell.")
        return 1

    try:
        from rich.console import Console
    except ImportError:
        print("Optional CLI extras are not installed. Install runic[cli] to use the interactive shell.")
        return 1

    console = Console()
    console.print("Runic interactive shell")

    while True:
        try:
            line = prompt("runic> ")
        except (EOFError, KeyboardInterrupt):
            console.print()
            return 0

        command = parse_shell_command(line)
        match command.command:
            case ShellCommand.EXIT:
                return 0
            case ShellCommand.HELP:
                console.print("Commands: install <model>, run [model], help, exit")
            case ShellCommand.INSTALL:
                if command.argument is None:
                    console.print("Usage: install <model>")
                else:
                    console.print(format_install_pane(command.argument, 0.0, ["waiting for controller"]))
            case ShellCommand.RUN:
                if command.argument is None:
                    console.print("Usage: run <model>")
                else:
                    console.print(f"Run: {command.argument}")
            case ShellCommand.UNKNOWN:
                console.print(f"Unknown command: {line.strip()}")

