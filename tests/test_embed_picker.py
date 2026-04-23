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

    def test_list_entries_sorts_mixed_case_names_after_directories(self) -> None:
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

    def test_list_entries_adds_requested_metadata_for_readme_and_main(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            (root / "src").mkdir()
            (root / "README.md").write_text("hello", encoding="utf-8")
            (root / "main.py").write_text("print('hi')", encoding="utf-8")

            entries = list_embed_picker_entries(
                root,
                selected_paths={(root / "README.md").resolve()},
                cursor_index=2,
            )

        self.assertEqual(["src", "main.py", "README.md"], [entry.name for entry in entries])

        entries_by_name = {entry.name: entry for entry in entries}
        self.assertTrue(entries_by_name["src"].is_dir)
        self.assertEqual("[dir]", entries_by_name["src"].type_label)
        self.assertEqual("directory", entries_by_name["src"].size_label)
        self.assertEqual(".md", entries_by_name["README.md"].type_label)
        self.assertEqual("5 B", entries_by_name["README.md"].size_label)
        self.assertTrue(entries_by_name["README.md"].selected)
        self.assertTrue(entries_by_name["README.md"].hovered)
        self.assertEqual(".py", entries_by_name["main.py"].type_label)

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

    def test_format_picker_lines_includes_instructions_rows_and_selection_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            (root / "src").mkdir()
            (root / "README.md").write_text("hello", encoding="utf-8")
            state = EmbedPickerState.start(root, model="qwen3-embedding:8b")
            state.toggle_selection()

            lines = state.format_lines()

        rendered = "\n".join(lines)
        self.assertIn(
            "Pick files to parse. Space multi-select, Tab enters dirs, Enter embeds selected.",
            rendered,
        )
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
