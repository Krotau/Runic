# Embed File Picker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a focused wide file picker for `embed <model>` that supports directory navigation, multi-select, recursive directory embedding, per-file output, and batch progress.

**Architecture:** Put picker state, filesystem scanning, selection expansion, and picker formatting in a new `runic/interactive/embed_picker.py` module. Keep `ModelController` unchanged; `runic/interactive/shell.py` only coordinates command dispatch, prompt-toolkit focus/keybindings, wide pane layout, and calls to the existing `controller.embed(model, text)` API.

**Tech Stack:** Python 3.12+, `unittest`, `pathlib`, existing `runic.Result`/`DefaultError`, `prompt_toolkit` for the TUI, and optional `rich` styling for colored picker text where supported.

---

## File Structure

- Create `runic/interactive/embed_picker.py`
  - Owns `EmbedPickerEntry`, `EmbedPickerProgress`, `EmbedPickerState`, skip constants, directory listing, row formatting, progress formatting, and recursive expansion to readable UTF-8 files.
- Modify `runic/interactive/shell.py`
  - Adds a wide pane mode to `PaneState`/`render_shell_frame`.
  - Adds `TuiShellState.embed_picker`.
  - Splits embed parsing so `embed <model>` opens the picker while `embed <model> <value>` stays direct.
  - Adds picker keybindings and batch embedding orchestration in `_run_tui_application`.
  - Leaves the non-TUI prompt fallback direct-only.
- Create `tests/test_embed_picker.py`
  - Unit tests for picker state, directory listing, formatting, recursive expansion, skip rules, and progress formatting.
- Modify `tests/test_interactive_shell.py`
  - Unit tests for wide pane rendering and shell-level embed argument behavior.

## Task 1: Picker Data Model And Directory Listing

**Files:**
- Create: `runic/interactive/embed_picker.py`
- Create: `tests/test_embed_picker.py`

- [ ] **Step 1: Write failing tests for entries, size formatting, sorting, and cursor state**

Add this file:

```python
# tests/test_embed_picker.py
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from runic.interactive.embed_picker import (
    EmbedPickerState,
    format_file_size,
    list_embed_picker_entries,
)


class TestEmbedPicker(unittest.TestCase):
    def test_format_file_size_uses_compact_units(self) -> None:
        self.assertEqual("0 B", format_file_size(0))
        self.assertEqual("999 B", format_file_size(999))
        self.assertEqual("1.0 KB", format_file_size(1024))
        self.assertEqual("1.5 KB", format_file_size(1536))
        self.assertEqual("2.0 MB", format_file_size(2 * 1024 * 1024))

    def test_list_entries_sorts_directories_before_files_and_adds_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            (root / "src").mkdir()
            (root / "README.md").write_text("hello", encoding="utf-8")
            (root / "main.py").write_text("print('hi')", encoding="utf-8")

            entries = list_embed_picker_entries(root, selected_paths={(root / "README.md").resolve()}, cursor_index=2)

        self.assertEqual(["src", "main.py", "README.md"], [entry.name for entry in entries])
        self.assertTrue(entries[0].is_dir)
        self.assertEqual("[dir]", entries[0].type_label)
        self.assertEqual("directory", entries[0].size_label)
        self.assertEqual(".py", entries[1].type_label)
        self.assertEqual(".md", entries[2].type_label)
        self.assertEqual("5 B", entries[2].size_label)
        self.assertTrue(entries[2].selected)
        self.assertTrue(entries[2].hovered)

    def test_picker_state_starts_at_root_and_loads_entries(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            (root / "docs").mkdir()
            (root / "README.md").write_text("hello", encoding="utf-8")

            state = EmbedPickerState.start(root)

        self.assertEqual(root.resolve(), state.current_dir)
        self.assertEqual(0, state.cursor_index)
        self.assertEqual([], sorted(path.name for path in state.selected_paths))
        self.assertEqual(["docs", "README.md"], [entry.name for entry in state.entries])
        self.assertTrue(state.entries[0].hovered)
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
python -m unittest tests.test_embed_picker -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'runic.interactive.embed_picker'`.

- [ ] **Step 3: Implement the picker model and directory listing**

Create `runic/interactive/embed_picker.py`:

```python
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True, slots=True)
class EmbedPickerEntry:
    path: Path
    name: str
    type_label: str
    size_label: str
    is_dir: bool
    selected: bool = False
    hovered: bool = False


@dataclass(frozen=True, slots=True)
class EmbedPickerProgress:
    total: int
    processed: int = 0
    succeeded: int = 0
    failed: int = 0
    skipped: int = 0


@dataclass(slots=True)
class EmbedPickerState:
    model: str
    current_dir: Path
    entries: tuple[EmbedPickerEntry, ...] = ()
    cursor_index: int = 0
    selected_paths: set[Path] = field(default_factory=set)
    message: str = ""
    progress: EmbedPickerProgress | None = None

    @classmethod
    def start(cls, root: Path, *, model: str = "") -> "EmbedPickerState":
        state = cls(model=model, current_dir=root.expanduser().resolve())
        state.reload()
        return state

    def reload(self) -> None:
        self.entries = list_embed_picker_entries(
            self.current_dir,
            selected_paths=self.selected_paths,
            cursor_index=self.cursor_index,
        )
        if self.entries:
            self.cursor_index = min(max(0, self.cursor_index), len(self.entries) - 1)
        else:
            self.cursor_index = 0
        self.entries = list_embed_picker_entries(
            self.current_dir,
            selected_paths=self.selected_paths,
            cursor_index=self.cursor_index,
        )


def format_file_size(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / (1024 * 1024):.1f} MB"


def _type_label(path: Path, *, is_dir: bool) -> str:
    if is_dir:
        return "[dir]"
    return path.suffix or "[file]"


def _size_label(path: Path, *, is_dir: bool) -> str:
    if is_dir:
        return "directory"
    try:
        return format_file_size(path.stat().st_size)
    except OSError:
        return "unreadable"


def list_embed_picker_entries(
    directory: Path,
    *,
    selected_paths: set[Path],
    cursor_index: int,
) -> tuple[EmbedPickerEntry, ...]:
    try:
        children = list(directory.iterdir())
    except OSError:
        return ()

    sorted_children = sorted(children, key=lambda path: (not path.is_dir(), path.name.lower(), path.name))
    entries: list[EmbedPickerEntry] = []
    for index, path in enumerate(sorted_children):
        resolved = path.resolve()
        is_dir = path.is_dir()
        entries.append(
            EmbedPickerEntry(
                path=resolved,
                name=path.name,
                type_label=_type_label(path, is_dir=is_dir),
                size_label=_size_label(path, is_dir=is_dir),
                is_dir=is_dir,
                selected=resolved in selected_paths,
                hovered=index == cursor_index,
            )
        )
    return tuple(entries)
```

- [ ] **Step 4: Run tests to verify they pass**

Run:

```bash
python -m unittest tests.test_embed_picker -v
```

Expected: PASS all 3 tests.

- [ ] **Step 5: Commit**

```bash
git add runic/interactive/embed_picker.py tests/test_embed_picker.py
git commit -m "feat: add embed picker state"
```

## Task 2: Picker Navigation And Selection

**Files:**
- Modify: `runic/interactive/embed_picker.py`
- Modify: `tests/test_embed_picker.py`

- [ ] **Step 1: Write failing tests for movement, selection, directory entry, parent navigation, and bad Tab feedback**

Append these tests inside `TestEmbedPicker`:

```python
    def test_picker_moves_cursor_and_toggles_selection(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            (root / "a.txt").write_text("a", encoding="utf-8")
            (root / "b.txt").write_text("b", encoding="utf-8")
            state = EmbedPickerState.start(root)

            state.move_down()
            state.toggle_selection()
            state.move_up()

        self.assertEqual(0, state.cursor_index)
        self.assertEqual(["b.txt"], sorted(path.name for path in state.selected_paths))
        self.assertTrue(state.entries[0].hovered)
        self.assertTrue(state.entries[1].selected)

    def test_picker_enters_directory_with_tab_and_moves_to_parent_with_backspace(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            nested = root / "docs"
            nested.mkdir()
            (nested / "guide.md").write_text("guide", encoding="utf-8")
            state = EmbedPickerState.start(root)

            state.enter_hovered_directory()
            self.assertEqual(nested.resolve(), state.current_dir)
            self.assertEqual(["guide.md"], [entry.name for entry in state.entries])

            state.move_to_parent()

        self.assertEqual(root.resolve(), state.current_dir)
        self.assertEqual(["docs"], [entry.name for entry in state.entries])

    def test_picker_tab_on_file_sets_message_without_changing_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            (root / "README.md").write_text("readme", encoding="utf-8")
            state = EmbedPickerState.start(root)

            state.enter_hovered_directory()

        self.assertEqual(root.resolve(), state.current_dir)
        self.assertEqual("Tab enters directories only.", state.message)
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
python -m unittest tests.test_embed_picker.TestEmbedPicker.test_picker_moves_cursor_and_toggles_selection tests.test_embed_picker.TestEmbedPicker.test_picker_enters_directory_with_tab_and_moves_to_parent_with_backspace tests.test_embed_picker.TestEmbedPicker.test_picker_tab_on_file_sets_message_without_changing_directory -v
```

Expected: FAIL with `AttributeError` for missing navigation methods.

- [ ] **Step 3: Implement navigation methods**

Add these methods to `EmbedPickerState` after `reload`:

```python
    def hovered_entry(self) -> EmbedPickerEntry | None:
        if not self.entries:
            return None
        return self.entries[self.cursor_index]

    def move_up(self) -> None:
        if self.entries:
            self.cursor_index = max(0, self.cursor_index - 1)
        self.message = ""
        self.reload()

    def move_down(self) -> None:
        if self.entries:
            self.cursor_index = min(len(self.entries) - 1, self.cursor_index + 1)
        self.message = ""
        self.reload()

    def toggle_selection(self) -> None:
        entry = self.hovered_entry()
        if entry is None:
            return
        if entry.path in self.selected_paths:
            self.selected_paths.remove(entry.path)
        else:
            self.selected_paths.add(entry.path)
        self.message = ""
        self.reload()

    def enter_hovered_directory(self) -> None:
        entry = self.hovered_entry()
        if entry is None:
            return
        if not entry.is_dir:
            self.message = "Tab enters directories only."
            self.reload()
            return
        self.current_dir = entry.path.resolve()
        self.cursor_index = 0
        self.message = ""
        self.reload()

    def move_to_parent(self) -> None:
        parent = self.current_dir.parent.resolve()
        if parent == self.current_dir:
            return
        self.current_dir = parent
        self.cursor_index = 0
        self.message = ""
        self.reload()
```

- [ ] **Step 4: Run tests to verify they pass**

Run:

```bash
python -m unittest tests.test_embed_picker -v
```

Expected: PASS all picker tests.

- [ ] **Step 5: Commit**

```bash
git add runic/interactive/embed_picker.py tests/test_embed_picker.py
git commit -m "feat: add embed picker navigation"
```

## Task 3: Picker Row Rendering, Rich Styling, And Progress Formatting

**Files:**
- Modify: `runic/interactive/embed_picker.py`
- Modify: `tests/test_embed_picker.py`

- [ ] **Step 1: Write failing tests for plain rows, Rich rows, selected count, and progress bar**

Append these tests inside `TestEmbedPicker`:

```python
    def test_format_picker_lines_includes_instructions_rows_and_selection_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            (root / "src").mkdir()
            (root / "README.md").write_text("hello", encoding="utf-8")
            state = EmbedPickerState.start(root, model="qwen3-embedding:8b")
            state.toggle_selection()

            lines = state.format_lines()

        rendered = "\n".join(lines)
        self.assertIn("Pick files to parse. Space multi-select, Tab enters dirs, Enter embeds selected.", rendered)
        self.assertIn(f"cwd {root.resolve()}", rendered)
        self.assertIn("> [x] [dir] src", rendered)
        self.assertIn("directory", rendered)
        self.assertIn("  [ ] .md", rendered)
        self.assertIn("Selected: 1 item", rendered)

    def test_format_picker_lines_has_rich_text_variant_with_styles(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            (root / "src").mkdir()
            state = EmbedPickerState.start(root, model="qwen3-embedding:8b")

            text = state.format_rich_text()

        self.assertEqual("\n".join(state.format_lines()), text.plain)
        self.assertIn("bold cyan", str(text.spans[0].style))

    def test_format_progress_line_counts_processed_files(self) -> None:
        from runic.interactive.embed_picker import EmbedPickerProgress, format_progress_line

        progress = EmbedPickerProgress(total=24, processed=7, succeeded=6, failed=1, skipped=2)

        self.assertEqual("Embedding 7/24 files  [####----------]  29%", format_progress_line(progress, width=14))
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
python -m unittest tests.test_embed_picker.TestEmbedPicker.test_format_picker_lines_includes_instructions_rows_and_selection_summary tests.test_embed_picker.TestEmbedPicker.test_format_picker_lines_has_rich_text_variant_with_styles tests.test_embed_picker.TestEmbedPicker.test_format_progress_line_counts_processed_files -v
```

Expected: FAIL with missing `format_lines`, `format_rich_text`, and `format_progress_line`.

- [ ] **Step 3: Implement plain and Rich formatting**

Add this import near the top of `runic/interactive/embed_picker.py`:

```python
from collections.abc import Sequence
```

Add these helpers after `list_embed_picker_entries`:

```python
def _plural(count: int, singular: str, plural: str | None = None) -> str:
    if count == 1:
        return f"{count} {singular}"
    return f"{count} {plural or singular + 's'}"


def format_progress_line(progress: EmbedPickerProgress, *, width: int = 14) -> str:
    if progress.total <= 0:
        return "Embedding 0/0 files  [" + "-" * width + "]  0%"
    ratio = min(1.0, max(0.0, progress.processed / progress.total))
    filled = round(ratio * width)
    percent = round(ratio * 100)
    return f"Embedding {progress.processed}/{progress.total} files  [{'#' * filled}{'-' * (width - filled)}]  {percent}%"


def _entry_line(entry: EmbedPickerEntry) -> str:
    cursor = ">" if entry.hovered else " "
    selected = "[x]" if entry.selected else "[ ]"
    return f"{cursor} {selected} {entry.type_label:<6} {entry.name:<32} {entry.size_label}"
```

Add these methods to `EmbedPickerState`:

```python
    def selected_count_label(self) -> str:
        return _plural(len(self.selected_paths), "item")

    def format_lines(self) -> tuple[str, ...]:
        lines: list[str] = [
            "Pick files to parse. Space multi-select, Tab enters dirs, Enter embeds selected.",
            f"cwd {self.current_dir}",
            "",
        ]
        lines.extend(_entry_line(entry) for entry in self.entries)
        lines.append("")
        lines.append(f"Selected: {self.selected_count_label()}")
        if self.progress is not None:
            lines.append(format_progress_line(self.progress))
        if self.message:
            lines.append(self.message)
        return tuple(lines)

    def format_rich_text(self) -> object:
        from rich.text import Text

        text = Text()
        for line_index, line in enumerate(self.format_lines()):
            if line_index:
                text.append("\n")
            if "[dir]" in line:
                text.append(line, style="bold cyan")
            elif "[x]" in line:
                text.append(line, style="green")
            elif line.startswith(">"):
                text.append(line, style="reverse")
            else:
                text.append(line)
        return text
```

- [ ] **Step 4: Run tests to verify they pass**

Run:

```bash
python -m unittest tests.test_embed_picker -v
```

Expected: PASS all picker tests.

- [ ] **Step 5: Commit**

```bash
git add runic/interactive/embed_picker.py tests/test_embed_picker.py
git commit -m "feat: render embed picker rows"
```

## Task 4: Recursive Selection Expansion And Skip Rules

**Files:**
- Modify: `runic/interactive/embed_picker.py`
- Modify: `tests/test_embed_picker.py`

- [ ] **Step 1: Write failing tests for recursive expansion, skips, UTF-8 filtering, and de-duplication**

Append these tests inside `TestEmbedPicker`:

```python
    def test_expand_selected_paths_recurses_and_skips_noisy_binary_and_non_utf8_files(self) -> None:
        from runic.interactive.embed_picker import expand_selected_paths

        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            docs = root / "docs"
            docs.mkdir()
            keep = docs / "keep.md"
            keep.write_text("keep", encoding="utf-8")
            duplicate = root / "duplicate.txt"
            duplicate.write_text("duplicate", encoding="utf-8")
            skipped_dir = root / "node_modules"
            skipped_dir.mkdir()
            (skipped_dir / "package.txt").write_text("skip", encoding="utf-8")
            (root / "image.png").write_bytes(b"\x89PNG\r\n")
            (root / "bad.txt").write_bytes(b"\xff\xfe\x00")

            expanded = expand_selected_paths([docs, duplicate, duplicate, root / "image.png", root / "bad.txt"])

        self.assertEqual([keep.resolve(), duplicate.resolve()], [item.path for item in expanded.files])
        self.assertEqual(3, expanded.skipped)

    def test_expand_selected_paths_returns_zero_files_for_only_skipped_directory(self) -> None:
        from runic.interactive.embed_picker import expand_selected_paths

        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            skipped_dir = root / ".git"
            skipped_dir.mkdir()
            (skipped_dir / "config").write_text("config", encoding="utf-8")

            expanded = expand_selected_paths([skipped_dir])

        self.assertEqual([], expanded.files)
        self.assertEqual(1, expanded.skipped)
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
python -m unittest tests.test_embed_picker.TestEmbedPicker.test_expand_selected_paths_recurses_and_skips_noisy_binary_and_non_utf8_files tests.test_embed_picker.TestEmbedPicker.test_expand_selected_paths_returns_zero_files_for_only_skipped_directory -v
```

Expected: FAIL with missing `expand_selected_paths`.

- [ ] **Step 3: Implement expansion helpers**

Add these constants and dataclasses to `runic/interactive/embed_picker.py`:

```python
DEFAULT_SKIPPED_DIRECTORY_NAMES = frozenset(
    {".git", "__pycache__", "node_modules", ".mypy_cache", ".pytest_cache", ".ruff_cache", "dist", "build", ".venv"}
)
DEFAULT_SKIPPED_FILE_EXTENSIONS = frozenset(
    {
        ".pyc",
        ".pyo",
        ".so",
        ".dll",
        ".dylib",
        ".exe",
        ".bin",
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".webp",
        ".ico",
        ".pdf",
        ".zip",
        ".tar",
        ".gz",
        ".7z",
        ".mp3",
        ".mp4",
        ".mov",
    }
)


@dataclass(frozen=True, slots=True)
class EmbedReadableFile:
    path: Path
    text: str


@dataclass(frozen=True, slots=True)
class EmbedSelectionExpansion:
    files: tuple[EmbedReadableFile, ...]
    skipped: int = 0
```

Add these helper functions after formatting helpers:

```python
def should_skip_path(path: Path) -> bool:
    if path.is_dir():
        return path.name in DEFAULT_SKIPPED_DIRECTORY_NAMES
    return path.suffix.lower() in DEFAULT_SKIPPED_FILE_EXTENSIONS


def _read_utf8_file(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _walk_candidate(path: Path) -> tuple[list[Path], int]:
    if should_skip_path(path):
        return ([], 1)
    if path.is_file():
        return ([path.resolve()], 0)
    if not path.is_dir():
        return ([], 1)

    files: list[Path] = []
    skipped = 0
    try:
        children = sorted(path.iterdir(), key=lambda child: (not child.is_dir(), child.name.lower(), child.name))
    except OSError:
        return ([], 1)
    for child in children:
        child_files, child_skipped = _walk_candidate(child)
        files.extend(child_files)
        skipped += child_skipped
    return (files, skipped)


def expand_selected_paths(paths: Sequence[Path]) -> EmbedSelectionExpansion:
    seen: set[Path] = set()
    readable: list[EmbedReadableFile] = []
    skipped = 0
    for selected_path in paths:
        candidate_files, candidate_skipped = _walk_candidate(selected_path.expanduser())
        skipped += candidate_skipped
        for path in candidate_files:
            if path in seen:
                continue
            seen.add(path)
            text = _read_utf8_file(path)
            if text is None:
                skipped += 1
                continue
            readable.append(EmbedReadableFile(path=path, text=text))
    return EmbedSelectionExpansion(files=tuple(readable), skipped=skipped)
```

- [ ] **Step 4: Run tests to verify they pass**

Run:

```bash
python -m unittest tests.test_embed_picker -v
```

Expected: PASS all picker tests.

- [ ] **Step 5: Commit**

```bash
git add runic/interactive/embed_picker.py tests/test_embed_picker.py
git commit -m "feat: expand embed picker selections"
```

## Task 5: Wide Pane Rendering In The Plain Frame

**Files:**
- Modify: `runic/interactive/shell.py`
- Modify: `tests/test_interactive_shell.py`

- [ ] **Step 1: Write failing tests for wide pane layout and default side pane compatibility**

Add these tests near the existing `render_shell_frame` tests in `tests/test_interactive_shell.py`:

```python
    def test_render_shell_frame_draws_wide_embed_picker_pane_above_output(self) -> None:
        frame = render_shell_frame(
            ShellFrame(
                title="Runic Interactive",
                status="runner: ollama ready",
                output=("Runic interactive shell", "previous output"),
                prompt="runic> _",
                pane=PaneState(
                    title="Embed Files: qwen3-embedding:8b",
                    lines=(
                        "Pick files to parse. Space multi-select, Tab enters dirs, Enter embeds selected.",
                        "cwd /home/arc/Wyvern",
                        "> [ ] [dir] runic directory",
                        "  [ ] .md README.md 4.0 KB",
                    ),
                    footer=("Space select", "Tab enter dir", "Enter embed selected"),
                    layout="wide",
                ),
                width=90,
                height=16,
            )
        )

        lines = frame.splitlines()
        self.assertIn("Embed Files: qwen3-embedding:8b", frame)
        self.assertLess(frame.index("Embed Files: qwen3-embedding:8b"), frame.index("Runic interactive shell"))
        self.assertIn("Enter embed selected", frame)
        self.assertTrue(all(len(line) == 90 for line in lines))

    def test_render_shell_frame_keeps_default_pane_on_right(self) -> None:
        frame = render_shell_frame(
            ShellFrame(
                title="Runic Interactive",
                status="runner: ollama ready",
                output=("Resolving model reference...",),
                prompt="runic> _",
                pane=PaneState(title="Install", lines=("llama3.2",), layout="side"),
                width=78,
                height=12,
            )
        )

        self.assertIn("|Resolving model reference", frame)
        self.assertIn("|Install", frame)
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
python -m unittest tests.test_interactive_shell.TestInteractiveShell.test_render_shell_frame_draws_wide_embed_picker_pane_above_output tests.test_interactive_shell.TestInteractiveShell.test_render_shell_frame_keeps_default_pane_on_right -v
```

Expected: FAIL with `TypeError: PaneState.__init__() got an unexpected keyword argument 'layout'`.

- [ ] **Step 3: Implement wide pane rendering**

Change `PaneState` in `runic/interactive/shell.py`:

```python
@dataclass(frozen=True, slots=True)
class PaneState:
    title: str
    lines: Sequence[str] = ()
    footer: Sequence[str] = ()
    layout: str = "side"
```

Add this helper near `_row`:

```python
def _pane_lines(pane: PaneState) -> list[str]:
    lines = [pane.title, *pane.lines]
    if pane.footer:
        lines.extend(["", *pane.footer])
    return [str(line) for line in lines]
```

Update existing `render_shell_frame` pane line construction to use `_pane_lines(pane)` instead of repeating `[pane.title, *pane.lines, *pane.footer]`. Then add this branch after the `pane is None` branch and before side-by-side rendering:

```python
    if pane.layout == "wide":
        pane_lines = _pane_lines(pane)
        pane_height = min(max(4, len(pane_lines) + 2), max(4, height // 2))
        rows.append(_border(width))
        for line in pane_lines[: max(1, pane_height - 2)]:
            rows.append(_row(line, width))
        while len(rows) < pane_height:
            rows.append(_row("", width))
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run:

```bash
python -m unittest tests.test_interactive_shell.TestInteractiveShell.test_render_shell_frame_draws_wide_embed_picker_pane_above_output tests.test_interactive_shell.TestInteractiveShell.test_render_shell_frame_keeps_default_pane_on_right tests.test_interactive_shell.TestInteractiveShell.test_render_shell_frame_draws_install_side_pane tests.test_interactive_shell.TestInteractiveShell.test_render_shell_frame_draws_chat_session_pane -v
```

Expected: PASS all listed tests.

- [ ] **Step 5: Commit**

```bash
git add runic/interactive/shell.py tests/test_interactive_shell.py
git commit -m "feat: render wide interactive pane"
```

## Task 6: Shell-Level Embed Parsing For Picker Trigger

**Files:**
- Modify: `runic/interactive/shell.py`
- Modify: `tests/test_interactive_shell.py`

- [ ] **Step 1: Write failing tests for `embed <model>` parsing and existing direct embed behavior**

Add these tests near the existing embed command tests:

```python
    def test_split_embed_argument_accepts_model_without_value(self) -> None:
        split = shell._split_embed_argument("qwen3-embedding:8b")

        self.assertEqual(Ok(("qwen3-embedding:8b", None)), split)

    def test_split_embed_argument_keeps_model_and_text_value(self) -> None:
        split = shell._split_embed_argument('qwen3-embedding:8b "hello world"')

        self.assertEqual(Ok(("qwen3-embedding:8b", "hello world")), split)

    def test_split_embed_argument_rejects_bare_embed(self) -> None:
        split = shell._split_embed_argument(None)

        self.assertIsInstance(split, Err)
        self.assertIn("Use embed <model>", shell._format_error(split.error))
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
python -m unittest tests.test_interactive_shell.TestInteractiveShell.test_split_embed_argument_accepts_model_without_value tests.test_interactive_shell.TestInteractiveShell.test_split_embed_argument_keeps_model_and_text_value tests.test_interactive_shell.TestInteractiveShell.test_split_embed_argument_rejects_bare_embed -v
```

Expected: FAIL with `AttributeError: module 'runic.interactive.shell' has no attribute '_split_embed_argument'`.

- [ ] **Step 3: Implement embed-specific parser**

Add this function below `_split_model_and_value`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run:

```bash
python -m unittest tests.test_interactive_shell.TestInteractiveShell.test_split_embed_argument_accepts_model_without_value tests.test_interactive_shell.TestInteractiveShell.test_split_embed_argument_keeps_model_and_text_value tests.test_interactive_shell.TestInteractiveShell.test_split_embed_argument_rejects_bare_embed tests.test_interactive_shell.TestInteractiveShell.test_embed_command_embeds_literal_text tests.test_interactive_shell.TestInteractiveShell.test_embed_command_reads_existing_file_path -v
```

Expected: PASS all listed tests.

- [ ] **Step 5: Commit**

```bash
git add runic/interactive/shell.py tests/test_interactive_shell.py
git commit -m "feat: parse embed picker command"
```

## Task 7: TUI Picker Opening, Focus, And Keybindings

**Files:**
- Modify: `runic/interactive/shell.py`
- Modify: `tests/test_interactive_shell.py`

- [ ] **Step 1: Write failing tests for state opening and picker shortcuts**

Add these tests near the `TuiShellState` tests:

```python
    def test_tui_shell_state_opens_embed_picker_at_launch_cwd(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            (root / "README.md").write_text("hello", encoding="utf-8")
            state = TuiShellState(launch_cwd=root)

            state.open_embed_picker("qwen3-embedding:8b")

        self.assertIsNotNone(state.embed_picker)
        self.assertEqual(root.resolve(), state.embed_picker.current_dir)
        self.assertEqual("Embed Files: qwen3-embedding:8b", state.pane.title)
        self.assertEqual("wide", state.pane.layout)
        self.assertTrue(state.pane_visible)
        self.assertIn("README.md", state.pane_text())

    def test_tui_shell_state_footer_switches_to_picker_shortcuts(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            state = TuiShellState(launch_cwd=Path(tempdir))
            state.open_embed_picker("qwen3-embedding:8b")

            footer = state.footer_text()

        self.assertIn("Space select", footer)
        self.assertIn("Tab enter dir", footer)
        self.assertIn("Enter embed", footer)
        self.assertIn("Backspace up", footer)
        self.assertIn("Esc cancel", footer)
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
python -m unittest tests.test_interactive_shell.TestInteractiveShell.test_tui_shell_state_opens_embed_picker_at_launch_cwd tests.test_interactive_shell.TestInteractiveShell.test_tui_shell_state_footer_switches_to_picker_shortcuts -v
```

Expected: FAIL with `TypeError` or `AttributeError` because `launch_cwd`, `embed_picker`, and `open_embed_picker` do not exist.

- [ ] **Step 3: Implement state support**

Add import near the other relative imports in `runic/interactive/shell.py`:

```python
from .embed_picker import EmbedPickerState
```

Update `TuiShellState` fields:

```python
    launch_cwd: Path = field(default_factory=Path.cwd)
    embed_picker: EmbedPickerState | None = None
```

Add these methods to `TuiShellState`:

```python
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
```

Update `hide_pane`:

```python
    def hide_pane(self) -> None:
        self.pane_visible = False
        self.embed_picker = None
```

Update `footer_text`:

```python
    def footer_text(self) -> str:
        if self.embed_picker is not None:
            return "Up/Down move | Space select | Tab enter dir | Enter embed | Backspace up | Esc cancel | Ctrl-Q quit"
        return "Tab accept/next | Enter run/select | Shift-Tab previous | F6 focus | Esc hide pane | Ctrl-P move pane | Ctrl-Q quit"
```

- [ ] **Step 4: Wire picker command and TUI keybindings**

In `_run_tui_application`, change `state = TuiShellState()` to:

```python
    state = TuiShellState(launch_cwd=Path.cwd())
```

Change the `ShellCommand.EMBED` case inside `handle_command` to use `_split_embed_argument`:

```python
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
```

Add these conditions beside the existing `pane_focused`, `pane_visible`, `pane_right`, and `pane_top` conditions, before any keybinding decorators that reference them:

```python
    @Condition
    def picker_active() -> bool:
        return state.embed_picker is not None

    @Condition
    def pane_wide() -> bool:
        return state.pane is not None and state.pane.layout == "wide"
```

Add these picker keybindings before the generic pane Enter binding. These replace the generic pane Enter behavior for picker state:

```python

    @key_bindings.add("up", filter=pane_focused & picker_active)
    def _(event):  # type: ignore[no-untyped-def]
        state.embed_picker.move_up()
        state.refresh_embed_picker_pane()
        refresh()

    @key_bindings.add("down", filter=pane_focused & picker_active)
    def _(event):  # type: ignore[no-untyped-def]
        state.embed_picker.move_down()
        state.refresh_embed_picker_pane()
        refresh()

    @key_bindings.add(" ", filter=pane_focused & picker_active)
    def _(event):  # type: ignore[no-untyped-def]
        state.embed_picker.toggle_selection()
        state.refresh_embed_picker_pane()
        refresh()

    @key_bindings.add("tab", filter=pane_focused & picker_active)
    def _(event):  # type: ignore[no-untyped-def]
        state.embed_picker.enter_hovered_directory()
        state.refresh_embed_picker_pane()
        refresh()

    @key_bindings.add("backspace", filter=pane_focused & picker_active)
    def _(event):  # type: ignore[no-untyped-def]
        state.embed_picker.move_to_parent()
        state.refresh_embed_picker_pane()
        refresh()

    @key_bindings.add("escape", filter=pane_focused & picker_active)
    def _(event):  # type: ignore[no-untyped-def]
        state.close_embed_picker()
        refresh()
        event.app.layout.focus(command_area)
```

Change the existing generic pane Enter binding filter from:

```python
    @key_bindings.add("enter", filter=pane_focused)
```

to:

```python
    @key_bindings.add("enter", filter=pane_focused & ~picker_active)
```

Change the existing Escape binding filter from:

```python
    @key_bindings.add("escape", filter=pane_visible)
```

to:

```python
    @key_bindings.add("escape", filter=pane_visible & ~picker_active)
```

Change `right_body`, `top_body`, and `root` so wide panes render above Output:

```python
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
```

- [ ] **Step 5: Run tests to verify they pass**

Run:

```bash
python -m unittest tests.test_interactive_shell.TestInteractiveShell.test_tui_shell_state_opens_embed_picker_at_launch_cwd tests.test_interactive_shell.TestInteractiveShell.test_tui_shell_state_footer_switches_to_picker_shortcuts tests.test_interactive_shell.TestInteractiveShell.test_tui_shell_state_tracks_focusable_pane_state tests.test_interactive_shell.TestInteractiveShell.test_tui_shell_state_footer_lists_shortcuts -v
```

Expected: PASS all listed tests.

- [ ] **Step 6: Commit**

```bash
git add runic/interactive/shell.py tests/test_interactive_shell.py
git commit -m "feat: open embed picker from tui"
```

## Task 8: Batch Embedding From Picker Selection

**Files:**
- Modify: `runic/interactive/shell.py`
- Modify: `tests/test_interactive_shell.py`

- [ ] **Step 1: Write failing unit test for the batch helper**

Add this controller near `FailingChatController`:

```python
class PartiallyFailingEmbedController(FakeController):
    async def embed(self, model: str, text: str) -> object:
        self.embed_calls.append((model, text))
        if text == "fail":
            return Err(DefaultError(message="Failed to embed with Ollama.", code="runner_embed_failed"))
        return Ok([1.0, 2.0, float(len(text))])
```

Add this test near embed command tests:

```python
    def test_embed_picker_batch_embeds_each_file_and_continues_after_failure(self) -> None:
        controller = PartiallyFailingEmbedController()
        state = TuiShellState()
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            good = root / "good.txt"
            bad = root / "bad.txt"
            good.write_text("good", encoding="utf-8")
            bad.write_text("fail", encoding="utf-8")
            state.open_embed_picker("qwen3-embedding:8b")
            state.embed_picker.selected_paths = {good.resolve(), bad.resolve()}

            asyncio.run(shell._embed_picker_selection(controller, state))

        self.assertEqual([("qwen3-embedding:8b", "fail"), ("qwen3-embedding:8b", "good")], sorted(controller.embed_calls))
        output = "\n".join(state.output)
        self.assertIn("good.txt", output)
        self.assertIn("Embedding dimensions: 3", output)
        self.assertIn("bad.txt", output)
        self.assertIn("runner_embed_failed: Failed to embed with Ollama.", output)
        self.assertIn("Embedding completed: 1 succeeded, 1 failed, 0 skipped", output)
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
python -m unittest tests.test_interactive_shell.TestInteractiveShell.test_embed_picker_batch_embeds_each_file_and_continues_after_failure -v
```

Expected: FAIL with missing `_embed_picker_selection`.

- [ ] **Step 3: Implement the batch helper**

Add imports to `runic/interactive/shell.py`:

```python
from .embed_picker import EmbedPickerProgress, EmbedPickerState, expand_selected_paths
```

Add this helper below `_embed_and_print`:

```python
async def _embed_picker_selection(controller: ModelController, state: TuiShellState) -> None:
    picker = state.embed_picker
    if picker is None:
        return
    if not picker.selected_paths:
        picker.message = "Select at least one file or directory."
        state.refresh_embed_picker_pane()
        return

    expansion = expand_selected_paths(sorted(picker.selected_paths, key=lambda path: str(path)))
    if not expansion.files:
        state.append("No readable files selected for embedding.")
        picker.message = "No readable files found."
        state.refresh_embed_picker_pane()
        return

    progress = EmbedPickerProgress(total=len(expansion.files), skipped=expansion.skipped)
    picker.progress = progress
    picker.message = ""
    state.refresh_embed_picker_pane()

    succeeded = 0
    failed = 0
    for readable_file in expansion.files:
        state.append(f"Embedding file: {readable_file.path}")
        result = await controller.embed(picker.model, readable_file.text)
        match result:
            case Ok(value=embedding):
                succeeded += 1
                state.append(f"{readable_file.path}: Embedding dimensions: {len(embedding)}")
                state.append(f"{readable_file.path}: Embedding preview: {_format_embedding_preview(embedding)}")
            case Err(error=error):
                failed += 1
                state.append(f"{readable_file.path}: {_format_error(error)}")
        picker.progress = EmbedPickerProgress(
            total=len(expansion.files),
            processed=succeeded + failed,
            succeeded=succeeded,
            failed=failed,
            skipped=expansion.skipped,
        )
        state.refresh_embed_picker_pane()

    picker.message = f"Embedding completed: {succeeded} succeeded, {failed} failed, {expansion.skipped} skipped"
    state.append(picker.message)
    state.refresh_embed_picker_pane()
```

- [ ] **Step 4: Wire Enter in picker to the batch helper**

Add this keybinding after the Backspace picker binding:

```python
    @key_bindings.add("enter", filter=pane_focused & picker_active)
    def _(event):  # type: ignore[no-untyped-def]
        async def embed_selected() -> None:
            await _embed_picker_selection(controller, state)
            refresh()
            if state.embed_picker is not None and state.embed_picker.progress is not None:
                event.app.layout.focus(command_area)

        event.app.create_background_task(embed_selected())
```

- [ ] **Step 5: Run tests to verify they pass**

Run:

```bash
python -m unittest tests.test_interactive_shell.TestInteractiveShell.test_embed_picker_batch_embeds_each_file_and_continues_after_failure tests.test_embed_picker.TestEmbedPicker.test_format_progress_line_counts_processed_files -v
```

Expected: PASS all listed tests.

- [ ] **Step 6: Commit**

```bash
git add runic/interactive/shell.py tests/test_interactive_shell.py
git commit -m "feat: embed selected picker files"
```

## Task 9: Preserve Non-TUI Fallback And Direct Embed Behavior

**Files:**
- Modify: `runic/interactive/shell.py`
- Modify: `tests/test_interactive_shell.py`

- [ ] **Step 1: Write failing test that non-TUI `embed <model>` remains direct-only**

Add this test near existing non-TUI embed tests:

```python
    def test_non_tui_embed_model_without_value_keeps_usage_error(self) -> None:
        controller = FakeController()
        console = FakeConsole()

        result = shell.run_interactive(
            controller=controller,
            prompt_fn=make_prompt_fn(["embed qwen3-embedding:8b", "exit"], []),
            console=console,
        )

        self.assertEqual(0, result)
        self.assertEqual([], controller.embed_calls)
        self.assertIn("invalid_command: Use embed <model> <text-or-file-path>.", console.text())
```

- [ ] **Step 2: Run regression tests**

Run:

```bash
python -m unittest tests.test_interactive_shell.TestInteractiveShell.test_non_tui_embed_model_without_value_keeps_usage_error tests.test_interactive_shell.TestInteractiveShell.test_embed_command_embeds_literal_text tests.test_interactive_shell.TestInteractiveShell.test_embed_command_reads_existing_file_path -v
```

Expected: PASS all listed tests. If `test_non_tui_embed_model_without_value_keeps_usage_error` fails, keep the non-TUI `run_interactive` loop using `_split_model_and_value(command.argument, "embed")`; do not switch it to `_split_embed_argument`.

- [ ] **Step 3: Commit**

```bash
git add runic/interactive/shell.py tests/test_interactive_shell.py
git commit -m "test: preserve direct embed fallback"
```

## Task 10: Full Verification And Cleanup

**Files:**
- Modify only if verification exposes a defect:
  - `runic/interactive/embed_picker.py`
  - `runic/interactive/shell.py`
  - `tests/test_embed_picker.py`
  - `tests/test_interactive_shell.py`

- [ ] **Step 1: Run all tests**

Run:

```bash
python -m unittest -v
```

Expected: PASS all tests.

- [ ] **Step 2: Run import laziness check explicitly**

Run:

```bash
python -m unittest tests.test_interactive_shell.TestInteractiveShell.test_import_runic_does_not_import_optional_cli_libraries -v
```

Expected: PASS. This verifies the new picker module did not import `prompt_toolkit` or `rich` at package import time. `rich` must stay inside `format_rich_text`.

- [ ] **Step 3: Run diff checks**

Run:

```bash
git diff --check
git status --short
```

Expected: `git diff --check` prints nothing. `git status --short` shows only intended files for this feature.

- [ ] **Step 4: Manually smoke-test the TUI**

Run:

```bash
python -m runic.cli
```

Manual steps:

1. Type `embed qwen3-embedding:8b`.
2. Confirm the wide `Embed Files: qwen3-embedding:8b` pane opens above Output.
3. Confirm focus is in the pane.
4. Use Up/Down, Space, Tab on a directory, Backspace, and Esc.
5. Select one small UTF-8 file and press Enter.
6. Confirm Output shows the file path, dimensions, preview, and completion summary.

Expected: The TUI remains responsive, picker keys work only while the picker is focused, and direct prompt completion still works for `chat`/`embed` model names.

- [ ] **Step 5: Commit final cleanup if needed**

If Step 1-4 required any code changes:

```bash
git add runic/interactive/embed_picker.py runic/interactive/shell.py tests/test_embed_picker.py tests/test_interactive_shell.py
git commit -m "fix: polish embed picker integration"
```

If no changes were required, do not create an empty commit.

## Self-Review Notes

- Spec coverage: Tasks 1-4 cover picker entries, navigation, row metadata, Rich/plain formatting, recursive expansion, skip rules, duplicate removal, and progress formatting. Tasks 5-8 cover wide pane layout, `embed <model>` picker trigger, focus/keybindings, selected file embedding, per-file output, and progress updates. Task 9 covers the non-TUI direct-only boundary and preserves existing direct embed behavior. Task 10 covers full verification and manual TUI smoke testing.
- Placeholder scan: No `TBD`, `TODO`, `implement later`, or vague edge-case steps remain. Every task includes concrete tests, implementation snippets, commands, and expected results.
- Type consistency: `EmbedPickerEntry`, `EmbedPickerProgress`, `EmbedPickerState`, `EmbedReadableFile`, `EmbedSelectionExpansion`, `expand_selected_paths`, `format_progress_line`, and `_split_embed_argument` are introduced before later tasks reference them.
