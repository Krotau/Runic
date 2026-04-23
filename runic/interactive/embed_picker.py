from __future__ import annotations

from dataclasses import dataclass, field
from collections.abc import Sequence
from pathlib import Path

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


@dataclass(frozen=True, slots=True)
class EmbedReadableFile:
    path: Path
    text: str


@dataclass(frozen=True, slots=True)
class EmbedSelectionExpansion:
    files: tuple[EmbedReadableFile, ...]
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

    def selected_count_label(self) -> str:
        return _plural(len(self.selected_paths), "item")

    def format_lines(self) -> Sequence[str]:
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
    return f"{cursor} {selected} {entry.type_label} {entry.name:<32} {entry.size_label}"


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
                skipped += 1
                continue
            seen.add(path)
            text = _read_utf8_file(path)
            if text is None:
                skipped += 1
                continue
            readable.append(EmbedReadableFile(path=path, text=text))
    return EmbedSelectionExpansion(files=tuple(readable), skipped=skipped)


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
