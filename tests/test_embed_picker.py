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
            (root / "Zoo").mkdir()
            (root / "a.txt").write_text("hello", encoding="utf-8")
            (root / "B.txt").write_text("print('hi')", encoding="utf-8")
            (root / "c.txt").write_text("print('bye')", encoding="utf-8")

            entries = list_embed_picker_entries(root, selected_paths={(root / "B.txt").resolve()}, cursor_index=2)

        self.assertEqual(["Zoo", "a.txt", "B.txt", "c.txt"], [entry.name for entry in entries])
        self.assertTrue(entries[0].is_dir)
        self.assertEqual("[dir]", entries[0].type_label)
        self.assertEqual("directory", entries[0].size_label)
        self.assertEqual(".txt", entries[1].type_label)
        self.assertEqual("5 B", entries[1].size_label)
        self.assertEqual(".txt", entries[2].type_label)
        self.assertEqual("11 B", entries[2].size_label)
        self.assertTrue(entries[2].selected)
        self.assertTrue(entries[2].hovered)
        self.assertEqual(".txt", entries[3].type_label)

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
