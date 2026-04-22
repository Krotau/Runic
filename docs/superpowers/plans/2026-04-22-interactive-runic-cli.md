# Interactive Runic CLI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an optional prompt-first Runic CLI that can install and run local models through a provider-agnostic runtime, with Ollama as the first runner.

**Architecture:** Add a dependency-free `runic.interactive` core for model references, registry, runner protocols, and Runic/Conduit orchestration. Add `runic.cli` and `runic.interactive.shell` as the optional terminal layer where `prompt_toolkit` and `rich` are imported lazily. Keep `import runic` free of optional CLI imports.

**Tech Stack:** Python 3.12+, stdlib `unittest`, `asyncio`, `json`, `pathlib`, `urllib`; optional `prompt_toolkit>=3` and `rich>=13` for CLI mode.

---

## File Structure

- Create `runic/interactive/__init__.py`: exports the pure interactive runtime types.
- Create `runic/interactive/models.py`: model/provider/status/chat dataclasses and enums.
- Create `runic/interactive/parsing.py`: Ollama and Hugging Face input normalization.
- Create `runic/interactive/registry.py`: JSON model registry using a user config path or injected path.
- Create `runic/interactive/runners/__init__.py`: runner exports.
- Create `runic/interactive/runners/base.py`: `ModelRunner` protocol and runner result shapes.
- Create `runic/interactive/runners/ollama.py`: Ollama runner implementation with injectable subprocess/HTTP seams.
- Create `runic/interactive/controller.py`: Runic-backed install/run orchestration.
- Create `runic/interactive/shell.py`: optional prompt shell, command parsing, render helpers, and lazy UI imports.
- Create `runic/cli.py`: console entry point that imports shell lazily.
- Modify `pyproject.toml`: add `cli` optional extra and `runic` console script.
- Add tests:
  - `tests/test_interactive_parsing.py`
  - `tests/test_interactive_registry.py`
  - `tests/test_interactive_ollama.py`
  - `tests/test_interactive_controller.py`
  - `tests/test_interactive_shell.py`

---

### Task 1: Model Types, Parsing, And Registry

**Files:**
- Create: `runic/interactive/__init__.py`
- Create: `runic/interactive/models.py`
- Create: `runic/interactive/parsing.py`
- Create: `runic/interactive/registry.py`
- Test: `tests/test_interactive_parsing.py`
- Test: `tests/test_interactive_registry.py`

- [ ] **Step 1: Write parsing tests**

Add tests covering:

```python
from __future__ import annotations

import unittest

from runic.interactive.models import ModelProvider
from runic.interactive.parsing import parse_model_reference
from runic.result import Err, Ok


class TestInteractiveParsing(unittest.TestCase):
    def test_parse_plain_ollama_name(self) -> None:
        result = parse_model_reference("llama3.2")

        self.assertIsInstance(result, Ok)
        assert isinstance(result, Ok)
        self.assertEqual(ModelProvider.OLLAMA, result.value.provider)
        self.assertEqual("llama3.2", result.value.model)
        self.assertEqual("llama3.2", result.value.local_name)

    def test_parse_ollama_uri(self) -> None:
        result = parse_model_reference("ollama://llama3.2:1b")

        self.assertIsInstance(result, Ok)
        assert isinstance(result, Ok)
        self.assertEqual(ModelProvider.OLLAMA, result.value.provider)
        self.assertEqual("llama3.2:1b", result.value.model)
        self.assertEqual("ollama://llama3.2:1b", result.value.source)

    def test_parse_ollama_library_url(self) -> None:
        result = parse_model_reference("https://ollama.com/library/llama3.2")

        self.assertIsInstance(result, Ok)
        assert isinstance(result, Ok)
        self.assertEqual(ModelProvider.OLLAMA, result.value.provider)
        self.assertEqual("llama3.2", result.value.model)

    def test_parse_hugging_face_url(self) -> None:
        result = parse_model_reference("https://huggingface.co/meta-llama/Llama-3.2-1B")

        self.assertIsInstance(result, Ok)
        assert isinstance(result, Ok)
        self.assertEqual(ModelProvider.HUGGING_FACE, result.value.provider)
        self.assertEqual("meta-llama/Llama-3.2-1B", result.value.model)
        self.assertEqual("meta-llama-Llama-3.2-1B", result.value.local_name)

    def test_parse_rejects_empty_input(self) -> None:
        result = parse_model_reference(" ")

        self.assertIsInstance(result, Err)
        assert isinstance(result, Err)
        self.assertEqual("invalid_model_reference", result.error.code)
```

- [ ] **Step 2: Write registry tests**

Add tests covering:

```python
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from runic.interactive.models import InstalledModel, ModelInstallStatus, ModelProvider
from runic.interactive.registry import ModelRegistry, default_registry_path


class TestInteractiveRegistry(unittest.TestCase):
    def test_registry_round_trips_models(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            path = Path(tempdir) / "models.json"
            registry = ModelRegistry(path)
            model = InstalledModel(
                name="llama3.2",
                provider=ModelProvider.OLLAMA,
                source="ollama://llama3.2",
                runner="ollama",
                status=ModelInstallStatus.INSTALLED,
                metadata={"size": "2GB"},
            )

            registry.save(model)

            loaded = ModelRegistry(path)
            self.assertEqual([model], loaded.list())
            self.assertEqual(model, loaded.get("llama3.2"))

    def test_registry_creates_parent_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            path = Path(tempdir) / "nested" / "models.json"
            registry = ModelRegistry(path)

            registry.save(
                InstalledModel(
                    name="pending",
                    provider=ModelProvider.HUGGING_FACE,
                    source="https://huggingface.co/org/model",
                    runner=None,
                    status=ModelInstallStatus.UNAVAILABLE,
                )
            )

            self.assertTrue(path.exists())

    def test_default_registry_path_uses_config_home(self) -> None:
        path = default_registry_path({"XDG_CONFIG_HOME": "/tmp/runic-test-config"})

        self.assertEqual(Path("/tmp/runic-test-config/runic/models.json"), path)
```

- [ ] **Step 3: Run tests and confirm failure**

Run:

```bash
python -m unittest tests.test_interactive_parsing tests.test_interactive_registry -v
```

Expected: fails because the modules do not exist.

- [ ] **Step 4: Implement models, parsing, and registry**

Implement focused dataclasses/enums:

```python
class ModelProvider(str, Enum):
    OLLAMA = "ollama"
    HUGGING_FACE = "hugging_face"


class ModelInstallStatus(str, Enum):
    PENDING = "pending"
    INSTALLED = "installed"
    FAILED = "failed"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class ModelReference:
    provider: ModelProvider
    source: str
    model: str
    local_name: str
    metadata: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class InstalledModel:
    name: str
    provider: ModelProvider
    source: str
    runner: str | None
    status: ModelInstallStatus
    metadata: Mapping[str, str] = field(default_factory=dict)
```

`parse_model_reference(...)` returns `Result[ModelReference, DefaultError]`.

`ModelRegistry` reads/writes JSON shaped as:

```json
{
  "models": [
    {
      "name": "llama3.2",
      "provider": "ollama",
      "source": "ollama://llama3.2",
      "runner": "ollama",
      "status": "installed",
      "metadata": {}
    }
  ]
}
```

- [ ] **Step 5: Verify task tests pass**

Run:

```bash
python -m unittest tests.test_interactive_parsing tests.test_interactive_registry -v
```

Expected: all tests pass.

- [ ] **Step 6: Commit Task 1**

```bash
git add runic/interactive tests/test_interactive_parsing.py tests/test_interactive_registry.py
git commit -m "feat: add interactive model registry"
```

---

### Task 2: Runner Protocol And Ollama Runner

**Files:**
- Create: `runic/interactive/runners/__init__.py`
- Create: `runic/interactive/runners/base.py`
- Create: `runic/interactive/runners/ollama.py`
- Test: `tests/test_interactive_ollama.py`

- [ ] **Step 1: Write Ollama runner tests**

Add tests covering:

```python
from __future__ import annotations

import unittest
from collections.abc import AsyncIterator

from runic import DefaultError, Err, Ok
from runic.interactive.models import ChatMessage, InstalledModel, ModelInstallStatus, ModelProvider, ModelReference
from runic.interactive.runners.ollama import OllamaRunner


class FakeContext:
    def __init__(self) -> None:
        self.logs: list[str] = []
        self.progress_values: list[float] = []

    async def log(self, message: str) -> None:
        self.logs.append(message)

    async def progress(self, value: float) -> None:
        self.progress_values.append(value)


async def fake_chat(_: str, __: tuple[ChatMessage, ...]) -> AsyncIterator[str]:
    yield "hello"
    yield " world"


class TestInteractiveOllamaRunner(unittest.IsolatedAsyncioTestCase):
    async def test_availability_uses_injected_checker(self) -> None:
        runner = OllamaRunner(command_exists=lambda _: True)

        self.assertTrue(await runner.is_available())

    async def test_missing_runner_install_returns_manual_notice(self) -> None:
        runner = OllamaRunner(command_exists=lambda _: False)

        result = await runner.install_runner()

        self.assertIsInstance(result, Err)
        assert isinstance(result, Err)
        self.assertEqual("runner_install_manual", result.error.code)

    async def test_install_model_runs_pull_and_records_installed_model(self) -> None:
        commands: list[tuple[str, ...]] = []

        async def run_command(command: tuple[str, ...]) -> Ok[list[str]]:
            commands.append(command)
            return Ok(["pulling manifest", "success"])

        runner = OllamaRunner(command_exists=lambda _: True, run_command=run_command)
        context = FakeContext()
        ref = ModelReference(
            provider=ModelProvider.OLLAMA,
            source="ollama://llama3.2",
            model="llama3.2",
            local_name="llama3.2",
        )

        result = await runner.install_model(ref, context)

        self.assertEqual([("ollama", "pull", "llama3.2")], commands)
        self.assertIsInstance(result, Ok)
        assert isinstance(result, Ok)
        self.assertEqual(
            InstalledModel(
                name="llama3.2",
                provider=ModelProvider.OLLAMA,
                source="ollama://llama3.2",
                runner="ollama",
                status=ModelInstallStatus.INSTALLED,
            ),
            result.value,
        )
        self.assertEqual(["pulling manifest", "success"], context.logs)
        self.assertEqual(1.0, context.progress_values[-1])

    async def test_chat_yields_injected_chunks(self) -> None:
        runner = OllamaRunner(command_exists=lambda _: True, chat_client=fake_chat)

        chunks = [
            chunk
            async for chunk in runner.chat(
                "llama3.2",
                (ChatMessage(role="user", content="hi"),),
            )
        ]

        self.assertEqual(["hello", " world"], chunks)
```

- [ ] **Step 2: Run tests and confirm failure**

Run:

```bash
python -m unittest tests.test_interactive_ollama -v
```

Expected: fails because runner modules do not exist.

- [ ] **Step 3: Implement runner protocol and Ollama runner**

Define:

```python
@dataclass(frozen=True, slots=True)
class RunnerCapability:
    provider: ModelProvider
    can_install: bool = True
    can_chat: bool = True


class ModelRunner(Protocol):
    name: str
    capabilities: tuple[RunnerCapability, ...]

    async def is_available(self) -> bool: ...
    async def install_runner(self) -> Result[str, DefaultError]: ...
    async def install_model(self, reference: ModelReference, context: RunnerContext) -> Result[InstalledModel, DefaultError]: ...
    async def list_models(self) -> Result[list[InstalledModel], DefaultError]: ...
    def chat(self, model: str, messages: tuple[ChatMessage, ...]) -> AsyncIterator[str]: ...
```

`OllamaRunner` must:

- accept injected `command_exists`, `run_command`, and `chat_client` callables
- avoid real Ollama calls in tests
- return a manual installer `Err(DefaultError(..., code="runner_install_manual"))` from `install_runner`
- wrap `ollama pull <model>` for `install_model`
- expose `chat(...)` as an async iterator

- [ ] **Step 4: Verify task tests pass**

Run:

```bash
python -m unittest tests.test_interactive_ollama -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit Task 2**

```bash
git add runic/interactive/runners tests/test_interactive_ollama.py
git commit -m "feat: add ollama model runner"
```

---

### Task 3: Runic-Backed Model Controller

**Files:**
- Create: `runic/interactive/controller.py`
- Test: `tests/test_interactive_controller.py`

- [ ] **Step 1: Write controller tests**

Add tests covering:

```python
from __future__ import annotations

import asyncio
import tempfile
import unittest
from collections.abc import AsyncIterator
from pathlib import Path

from runic import DefaultError, Err, Ok, Runic, SpellStatus
from runic.interactive.controller import InstallDecisionStatus, ModelController
from runic.interactive.models import ChatMessage, InstalledModel, ModelInstallStatus, ModelProvider
from runic.interactive.registry import ModelRegistry


class FakeRunner:
    name = "ollama"

    def __init__(self, *, available: bool = True) -> None:
        from runic.interactive.runners.base import RunnerCapability

        self.available = available
        self.capabilities = (RunnerCapability(provider=ModelProvider.OLLAMA),)
        self.installed: list[str] = []

    async def is_available(self) -> bool:
        return self.available

    async def install_runner(self):  # type: ignore[no-untyped-def]
        return Err(DefaultError(message="manual install required", code="runner_install_manual"))

    async def install_model(self, reference, context):  # type: ignore[no-untyped-def]
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

    async def chat(self, model: str, messages: tuple[ChatMessage, ...]) -> AsyncIterator[str]:
        yield f"{model}:{messages[-1].content}"


class TestInteractiveController(unittest.IsolatedAsyncioTestCase):
    async def test_prepare_install_reports_missing_runner(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            controller = ModelController(
                Runic(),
                ModelRegistry(Path(tempdir) / "models.json"),
                runners=(FakeRunner(available=False),),
            )

            decision = await controller.prepare_install("llama3.2")

            self.assertEqual(InstallDecisionStatus.MISSING_RUNNER, decision.status)
            self.assertEqual("ollama", decision.runner)

    async def test_install_schedules_spell_and_saves_registry_on_success(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            registry = ModelRegistry(Path(tempdir) / "models.json")
            runic = Runic()
            runner = FakeRunner()
            controller = ModelController(runic, registry, runners=(runner,))

            result = await controller.install("llama3.2")

            self.assertIsInstance(result, Ok)
            assert isinstance(result, Ok)
            spell_id = result.value
            record = await runic.conduit.wait_for_status(spell_id, SpellStatus.SUCCEEDED, timeout=1.0)
            self.assertIsInstance(record, Ok)
            self.assertEqual(["llama3.2"], runner.installed)
            self.assertEqual("installing:llama3.2", registry.get("llama3.2").metadata["last_log"])
            self.assertEqual(ModelInstallStatus.INSTALLED, registry.get("llama3.2").status)

    async def test_hugging_face_reference_without_runner_is_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            controller = ModelController(Runic(), ModelRegistry(Path(tempdir) / "models.json"), runners=(FakeRunner(),))

            decision = await controller.prepare_install("https://huggingface.co/org/model")

            self.assertEqual(InstallDecisionStatus.UNSUPPORTED, decision.status)

    async def test_chat_uses_registry_runner(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            registry = ModelRegistry(Path(tempdir) / "models.json")
            registry.save(
                InstalledModel(
                    name="llama3.2",
                    provider=ModelProvider.OLLAMA,
                    source="ollama://llama3.2",
                    runner="ollama",
                    status=ModelInstallStatus.INSTALLED,
                )
            )
            controller = ModelController(Runic(), registry, runners=(FakeRunner(),))

            chunks = [
                chunk
                async for chunk in controller.chat(
                    "llama3.2",
                    (ChatMessage(role="user", content="hello"),),
                )
            ]

            self.assertEqual(["llama3.2:hello"], chunks)
```

- [ ] **Step 2: Run tests and confirm failure**

Run:

```bash
python -m unittest tests.test_interactive_controller -v
```

Expected: fails because controller module does not exist.

- [ ] **Step 3: Implement controller**

Implement:

- `InstallModel` command dataclass with `source: str`
- `InstallDecisionStatus` enum: `READY`, `MISSING_RUNNER`, `UNSUPPORTED`, `INVALID`
- `InstallDecision` dataclass with `status`, `reference`, `runner`, `message`
- `ModelController.__init__(runtime, registry, runners)` registers a typed spell for `InstallModel`
- `prepare_install(source)` parses the source, chooses a runner by capability, checks availability, and returns a decision
- `install(source)` returns `Result[str, DefaultError]` where `Ok` contains the spell id
- `chat(model, messages)` resolves the installed model and delegates to the selected runner

The install spell must:

- call the selected runner
- log/save the last install log in registry metadata when available
- save `INSTALLED` only after `Ok(InstalledModel)`
- save `FAILED` after `Err(DefaultError)`

- [ ] **Step 4: Verify task tests pass**

Run:

```bash
python -m unittest tests.test_interactive_controller -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit Task 3**

```bash
git add runic/interactive/controller.py tests/test_interactive_controller.py
git commit -m "feat: orchestrate interactive model installs"
```

---

### Task 4: CLI Shell And Optional Entrypoint

**Files:**
- Create: `runic/interactive/shell.py`
- Create: `runic/cli.py`
- Modify: `pyproject.toml`
- Test: `tests/test_interactive_shell.py`

- [ ] **Step 1: Write shell tests**

Add tests covering:

```python
from __future__ import annotations

import asyncio
import importlib
import sys
import unittest

from runic import Ok
from runic.interactive.models import InstalledModel, ModelInstallStatus, ModelProvider
from runic.interactive.shell import (
    ParsedCommand,
    ShellCommand,
    format_install_pane,
    parse_shell_command,
)


class TestInteractiveShell(unittest.IsolatedAsyncioTestCase):
    def test_parse_install_command(self) -> None:
        self.assertEqual(
            ParsedCommand(ShellCommand.INSTALL, "llama3.2"),
            parse_shell_command("install llama3.2"),
        )

    def test_parse_run_command_without_model(self) -> None:
        self.assertEqual(
            ParsedCommand(ShellCommand.RUN, None),
            parse_shell_command("run"),
        )

    def test_parse_exit_command(self) -> None:
        self.assertEqual(
            ParsedCommand(ShellCommand.EXIT, None),
            parse_shell_command("exit"),
        )

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
```

- [ ] **Step 2: Run tests and confirm failure**

Run:

```bash
python -m unittest tests.test_interactive_shell -v
```

Expected: fails because shell/CLI modules do not exist.

- [ ] **Step 3: Implement shell parser and render helpers**

Implement:

- `ShellCommand` enum: `INSTALL`, `RUN`, `HELP`, `EXIT`, `UNKNOWN`
- `ParsedCommand` dataclass
- `parse_shell_command(line: str) -> ParsedCommand`
- `format_install_pane(model: str, progress: float, lines: Sequence[str]) -> str`
- `run_interactive(...)` imports `prompt_toolkit` and `rich` inside the function
- graceful `RuntimeError` or printed message if CLI extras are missing

`run_interactive(...)` can begin as a prompt-loop adapter that delegates to `ModelController`; it must keep the parser and formatting independently testable without a real terminal.

- [ ] **Step 4: Implement CLI entrypoint and packaging**

Add `runic/cli.py`:

```python
from __future__ import annotations


def main() -> int:
    from .interactive.shell import run_interactive

    return run_interactive()
```

Update `pyproject.toml`:

```toml
[project.optional-dependencies]
cli = ["prompt_toolkit>=3", "rich>=13"]
dev = ["pytest>=9.0.0"]
release = ["build>=1.2.2", "twine>=6.1.0"]

[project.scripts]
runic = "runic.cli:main"
```

- [ ] **Step 5: Verify shell tests pass**

Run:

```bash
python -m unittest tests.test_interactive_shell -v
```

Expected: all tests pass.

- [ ] **Step 6: Commit Task 4**

```bash
git add runic/interactive/shell.py runic/cli.py pyproject.toml tests/test_interactive_shell.py
git commit -m "feat: add optional interactive cli shell"
```

---

### Task 5: Documentation And Full Verification

**Files:**
- Modify: `README.md`
- Modify: `runic/interactive/__init__.py`

- [ ] **Step 1: Update README**

Add a short optional CLI section:

```markdown
Optional interactive CLI
------------------------

Install the CLI extra when you want the prompt-first model workflow:

```bash
pip install "runic-io[cli]"
runic
```

The first runner is Ollama. `install <model-or-link>` schedules model installation through Runic's async spell workflow, and `run [model]` opens an interactive chat session for an installed model. Hugging Face links are recognized, but arbitrary Hugging Face models require a compatible runner that is not included in the first version.
```

- [ ] **Step 2: Verify public interactive exports**

Ensure `runic/interactive/__init__.py` exports only dependency-free types/functions useful to callers:

```python
from .controller import InstallDecision, InstallDecisionStatus, InstallModel, ModelController
from .models import ChatMessage, InstalledModel, ModelInstallStatus, ModelProvider, ModelReference
from .parsing import parse_model_reference
from .registry import ModelRegistry, default_registry_path
```

Do not import `prompt_toolkit`, `rich`, or `OllamaRunner` from `runic/__init__.py`.

- [ ] **Step 3: Run focused interactive tests**

Run:

```bash
python -m unittest \
  tests.test_interactive_parsing \
  tests.test_interactive_registry \
  tests.test_interactive_ollama \
  tests.test_interactive_controller \
  tests.test_interactive_shell \
  -v
```

Expected: all tests pass.

- [ ] **Step 4: Run full test suite**

Run:

```bash
python -m unittest discover -s tests -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit Task 5**

```bash
git add README.md runic/interactive/__init__.py
git commit -m "docs: document interactive cli"
```
