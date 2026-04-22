# Runic Completion Hints Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add faint inline completion hints for single command/model matches in the Runic TUI, while preserving selectable completion menus for ambiguous command/model matches.

**Architecture:** Keep `complete_shell_input(...)` as the single candidate source, add a pure classifier that decides ghost hint versus menu, then wire that classifier into prompt-toolkit with `AutoSuggest`. The prompt-toolkit completer will only expose menu candidates when the classifier says the input is ambiguous, and `Tab` will accept a ghost hint before opening/cycling menu choices.

**Tech Stack:** Python 3.12+, stdlib `unittest`, existing `runic.interactive.shell`, optional `prompt_toolkit>=3` for `TextArea`, `AutoSuggest`, `Suggestion`, `Completer`, and key bindings.

---

## File Structure

- Modify `runic/interactive/shell.py`: add a pure completion classifier, expose ghost suffixes, update TUI completer/autosuggest/keybinding behavior, and keep optional imports lazy inside `_run_tui_application`.
- Modify `tests/test_interactive_shell.py`: add unit tests for ghost/menu classification for commands, `chat`, and `embed`; keep the TUI construction smoke test intact.

No new runtime files are needed. The behavior is small enough to stay in `runic/interactive/shell.py` next to `ShellCompletion` and `complete_shell_input(...)`, and the classifier remains pure so tests do not need prompt-toolkit.

---

### Task 1: Pure Completion Classifier

**Files:**
- Modify: `runic/interactive/shell.py`
- Test: `tests/test_interactive_shell.py`

- [ ] **Step 1: Write failing tests for command ghost and menu classification**

In `tests/test_interactive_shell.py`, add `CompletionDisplayMode` and `classify_shell_completion` to the import from `runic.interactive.shell`:

```python
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
```

Add these tests inside `TestInteractiveShell`, near the existing `complete_shell_input` tests:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
python -m unittest tests.test_interactive_shell.TestInteractiveShell.test_classify_shell_completion_uses_ghost_for_single_command_match tests.test_interactive_shell.TestInteractiveShell.test_classify_shell_completion_uses_menu_for_ambiguous_command_match tests.test_interactive_shell.TestInteractiveShell.test_classify_shell_completion_returns_none_when_there_are_no_matches -v
```

Expected: FAIL with import errors for `CompletionDisplayMode` or `classify_shell_completion`.

- [ ] **Step 3: Implement the pure classifier**

In `runic/interactive/shell.py`, add this enum and dataclass after `ShellCompletion`:

```python
class CompletionDisplayMode(str, Enum):
    NONE = "none"
    GHOST = "ghost"
    MENU = "menu"


@dataclass(frozen=True, slots=True)
class ShellCompletionDisplay:
    mode: CompletionDisplayMode
    candidates: tuple[ShellCompletion, ...] = ()
    ghost_text: str = ""
```

Add this helper after `complete_shell_input(...)`:

```python
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
```

- [ ] **Step 4: Run command classifier tests to verify they pass**

Run:

```bash
python -m unittest tests.test_interactive_shell.TestInteractiveShell.test_classify_shell_completion_uses_ghost_for_single_command_match tests.test_interactive_shell.TestInteractiveShell.test_classify_shell_completion_uses_menu_for_ambiguous_command_match tests.test_interactive_shell.TestInteractiveShell.test_classify_shell_completion_returns_none_when_there_are_no_matches -v
```

Expected: PASS for all three tests.

- [ ] **Step 5: Commit Task 1**

Run:

```bash
git add runic/interactive/shell.py tests/test_interactive_shell.py
git commit -m "feat: classify runic completion hints"
```

Expected: commit succeeds and includes only `runic/interactive/shell.py` and `tests/test_interactive_shell.py`.

---

### Task 2: Chat And Embed Completion Classification

**Files:**
- Modify: `tests/test_interactive_shell.py`
- Modify: `runic/interactive/shell.py` only if Task 1 implementation does not satisfy these tests

- [ ] **Step 1: Write failing tests for `chat` and `embed` model ghost/menu behavior**

Add these tests inside `TestInteractiveShell`, near the classifier tests:

```python
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

    def test_classify_shell_completion_stops_after_embed_model_argument(self) -> None:
        controller = FakeController()

        display = classify_shell_completion("embed qwen3-embedding:8b text", controller.list_installed())

        self.assertEqual(CompletionDisplayMode.NONE, display.mode)
        self.assertEqual("", display.ghost_text)
        self.assertEqual([], list(display.candidates))
```

- [ ] **Step 2: Run model classifier tests**

Run:

```bash
python -m unittest tests.test_interactive_shell.TestInteractiveShell.test_classify_shell_completion_uses_ghost_for_single_chat_model_match tests.test_interactive_shell.TestInteractiveShell.test_classify_shell_completion_uses_menu_for_ambiguous_chat_model_match tests.test_interactive_shell.TestInteractiveShell.test_classify_shell_completion_uses_ghost_for_single_embed_model_match tests.test_interactive_shell.TestInteractiveShell.test_classify_shell_completion_uses_menu_for_ambiguous_embed_model_match tests.test_interactive_shell.TestInteractiveShell.test_classify_shell_completion_stops_after_embed_model_argument -v
```

Expected: PASS if Task 1's classifier correctly delegates to `complete_shell_input(...)`; otherwise FAIL with the specific mismatch.

- [ ] **Step 3: Fix classifier only if the model tests fail**

If the tests fail because empty model prefixes such as `chat ` or `embed ` are being treated as single ghost hints, replace `classify_shell_completion(...)` with this stricter implementation:

```python
def classify_shell_completion(text_before_cursor: str, installed_models: Sequence[object]) -> ShellCompletionDisplay:
    candidates = complete_shell_input(text_before_cursor, installed_models)
    if not candidates:
        return ShellCompletionDisplay(mode=CompletionDisplayMode.NONE)
    if len(candidates) > 1:
        return ShellCompletionDisplay(mode=CompletionDisplayMode.MENU, candidates=candidates)

    candidate = candidates[0]
    prefix = _completion_token_prefix(text_before_cursor, candidate)
    if candidate.start_position == 0:
        return ShellCompletionDisplay(mode=CompletionDisplayMode.MENU, candidates=candidates)
    if not prefix:
        return ShellCompletionDisplay(mode=CompletionDisplayMode.MENU, candidates=candidates)
    if not candidate.text.startswith(prefix):
        return ShellCompletionDisplay(mode=CompletionDisplayMode.MENU, candidates=candidates)

    ghost_text = candidate.text[len(prefix) :]
    if not ghost_text:
        return ShellCompletionDisplay(mode=CompletionDisplayMode.NONE)
    return ShellCompletionDisplay(
        mode=CompletionDisplayMode.GHOST,
        candidates=candidates,
        ghost_text=ghost_text,
    )
```

- [ ] **Step 4: Run all shell tests**

Run:

```bash
python -m unittest tests.test_interactive_shell -v
```

Expected: PASS for all shell tests.

- [ ] **Step 5: Commit Task 2**

Run:

```bash
git add runic/interactive/shell.py tests/test_interactive_shell.py
git commit -m "test: cover chat and embed completion hints"
```

Expected: commit succeeds. If Task 1 already satisfied these tests and only tests changed, the commit should contain only `tests/test_interactive_shell.py`.

---

### Task 3: Wire Ghost Hints Into The Prompt Toolkit TUI

**Files:**
- Modify: `runic/interactive/shell.py`
- Test: `tests/test_interactive_shell.py`

- [ ] **Step 1: Write a smoke test for TUI construction with ghost hint wiring**

If `tests/test_interactive_shell.py` already has a TUI construction smoke test, extend it so it still patches `Application.run` and asserts `_run_tui_application(controller)` returns `0`. If the test is missing, add this test inside `TestInteractiveShell`:

```python
    def test_default_interactive_path_uses_tui_application(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            controller = ModelController(Runic(), ModelRegistry(Path(tempdir) / "models.json"), runners=())

            with patch("prompt_toolkit.application.application.Application.run", return_value=0):
                self.assertEqual(0, shell.run_interactive(controller=controller))
```

- [ ] **Step 2: Run the TUI smoke test before implementation**

Run:

```bash
python -m unittest tests.test_interactive_shell.TestInteractiveShell.test_default_interactive_path_uses_tui_application -v
```

Expected: PASS before the change. This is a guard test proving the application still constructs after the next steps.

- [ ] **Step 3: Import prompt-toolkit auto suggestion types lazily**

Inside `_run_tui_application(...)` in `runic/interactive/shell.py`, extend the existing optional import block with:

```python
        from prompt_toolkit.auto_suggest import AutoSuggest, Suggestion
```

Keep this import inside `_run_tui_application(...)` so `import runic` remains free of optional CLI dependencies.

- [ ] **Step 4: Replace the TUI completer with menu-only completion and ghost auto-suggest**

Inside `_run_tui_application(...)`, replace the existing `RunicCompleter` class with this implementation:

```python
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
```

- [ ] **Step 5: Attach auto-suggest to the command input**

Update the `command_area = TextArea(...)` construction in `_run_tui_application(...)` so it includes `auto_suggest=RunicAutoSuggest()`:

```python
    command_area = TextArea(
        height=1,
        multiline=False,
        completer=RunicCompleter(),
        auto_suggest=RunicAutoSuggest(),
        complete_while_typing=True,
        focusable=True,
        wrap_lines=False,
    )
```

- [ ] **Step 6: Update `Tab` to accept ghost suggestions before opening menus**

Replace the existing `@key_bindings.add("tab", filter=input_focused)` handler with:

```python
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
```

This preserves the current menu cycle behavior, adds ghost acceptance, and opens the menu only for ambiguous candidates.

- [ ] **Step 7: Run focused shell tests**

Run:

```bash
python -m unittest tests.test_interactive_shell -v
```

Expected: PASS for all shell tests.

- [ ] **Step 8: Run TUI construction in the project virtual environment**

Run:

```bash
.venv/bin/python - <<'PY'
from pathlib import Path
from unittest.mock import patch
from runic import Runic
from runic.interactive.controller import ModelController
from runic.interactive.registry import ModelRegistry
from runic.interactive.shell import _run_tui_application
import tempfile

with tempfile.TemporaryDirectory() as tempdir:
    controller = ModelController(Runic(), ModelRegistry(Path(tempdir) / "models.json"), runners=())
    with patch("prompt_toolkit.application.application.Application.run", return_value=0):
        print(_run_tui_application(controller))
PY
```

Expected: prints `0`. A warning like `Input is not a terminal (fd=0).` is acceptable.

- [ ] **Step 9: Commit Task 3**

Run:

```bash
git add runic/interactive/shell.py tests/test_interactive_shell.py
git commit -m "feat: add tui completion ghost hints"
```

Expected: commit succeeds and includes the TUI integration.

---

### Task 4: Final Verification

**Files:**
- Verify: full repository

- [ ] **Step 1: Run whitespace check**

Run:

```bash
git diff --check HEAD
```

Expected: no output and exit code `0`.

- [ ] **Step 2: Run the full test suite**

Run:

```bash
python -m unittest discover -s tests -v
```

Expected: all tests pass.

- [ ] **Step 3: Build the wheel**

Run:

```bash
uv build --wheel --out-dir /tmp/runic-completion-hints-dist
```

Expected: build succeeds and prints a wheel path under `/tmp/runic-completion-hints-dist`.

- [ ] **Step 4: Verify optional CLI imports remain lazy**

Run:

```bash
python - <<'PY'
import sys
for name in ("prompt_toolkit", "rich", "runic.interactive.runners.ollama"):
    sys.modules.pop(name, None)
import runic
import runic.cli
print("prompt_toolkit", "prompt_toolkit" in sys.modules)
print("rich", "rich" in sys.modules)
print("ollama_runner", "runic.interactive.runners.ollama" in sys.modules)
PY
```

Expected:

```text
prompt_toolkit False
rich False
ollama_runner False
```

- [ ] **Step 5: Check working tree and preserve unrelated changes**

Run:

```bash
git status --short
```

Expected: no unstaged changes from this feature. If `pyproject.toml` is still modified from earlier unrelated work, leave it unstaged and mention it in the handoff.

- [ ] **Step 6: Commit verification cleanup only if needed**

If verification created or modified tracked cache files, restore only those generated files:

```bash
git restore --source=HEAD -- tests/__pycache__ 2>/dev/null || true
git status --short
```

Expected: only intentional user changes remain.

Do not commit `pyproject.toml` unless it was intentionally changed for this completion-hints feature.

---

## Self-Review

- Spec coverage: Task 1 covers no/one/multiple candidate classification; Task 2 covers `chat` and `embed`; Task 3 covers prompt-toolkit ghost hints, menus, and `Tab`; Task 4 covers full verification and lazy imports.
- Scope: The plan stays inside shell completion behavior. It does not add fuzzy matching, file-path completion, provider-specific rules, or a new completion pane.
- Type consistency: `CompletionDisplayMode`, `ShellCompletionDisplay`, and `classify_shell_completion(...)` are defined before use. The TUI integration uses prompt-toolkit `AutoSuggest` and `Suggestion` lazily inside `_run_tui_application(...)`.
