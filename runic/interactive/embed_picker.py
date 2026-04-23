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
