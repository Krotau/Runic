# Embed File Picker Design

## Summary

Add a focused file picker for the interactive `embed` command. When the user enters `embed <model>` with no text or file argument, Runic opens a wide file browser pane, focuses it automatically, and lets the user select files or directories to embed. Existing direct usage, `embed <model> <text-or-file-path>`, keeps its current behavior.

This slice does not add embedding persistence, retrieval, a vector index, or output files. Embeddings are parsed and previewed in the interactive Output area only.

## Goals

- Open a file picker when `embed <model>` is entered without a text or file value.
- Keep bare `embed` invalid and keep `embed <model> <text-or-file-path>` behavior unchanged.
- Start the picker at the process working directory used to launch Runic.
- Use a wide picker pane above Output so paths, extensions, sizes, and progress remain readable.
- Focus the picker automatically and show concise instructions.
- Support multi-select with Space and batch embedding with Enter.
- Let users enter directories with Tab and move to the parent directory with Backspace.
- Let selected directories recursively expand to readable files for embedding.
- Show file extensions and sizes, and show a distinct directory label/icon.
- Use Rich styling for readable color differences where supported, with plain ASCII fallback.
- Show a batch progress bar based on the number of concrete readable files processed.

## Non-Goals

- No persistent embedding store or retrieval workflow.
- No vector database integration.
- No output file format for embeddings.
- No model selection in the picker; the model must already be supplied as `embed <model>`.
- No file picker for bare `embed`.
- No file path completion in the command prompt.

## Command Behavior

`embed <model> <text-or-file-path>` keeps the existing direct path:

1. Parse the model and remaining value.
2. If the value points to a readable file, read that file as UTF-8.
3. Otherwise embed the value as literal text.
4. Print dimensions and a preview in Output.

`embed <model>` opens the picker:

1. Parse the model.
2. Create an embed picker state rooted at the launch working directory.
3. Set the pane to `Embed Files: <model>`.
4. Focus the pane automatically.
5. Clear the command input.

Bare `embed` remains invalid and reports usage guidance.

## Picker UI

The picker uses a wide pane mode above Output instead of the existing narrow right pane. It shows:

- title: `Embed Files: <model>`
- instruction line: `Pick files to parse. Space multi-select, Tab enters dirs, Enter embeds selected.`
- current directory
- scrollable row list
- selected item count and selected root summary
- validation or progress message

Each row includes:

- cursor marker: `>` for the hovered row
- selection marker: `[ ]` or `[x]`
- type label: `[dir]` for directories, or the file extension such as `.py`, `.md`, `.csv`
- display name
- file size for files
- `directory` for directories

Directory and file rows must remain distinguishable without color. Where Rich rendering is available, directories render in a distinct color such as bold cyan, selected state uses a stronger style such as green marker or reverse row styling, and the hovered row is highlighted. Plain renderers and tests use the same textual markers without color.

## Picker Navigation

Picker-specific key handling applies while the pane is focused:

- Up moves the hovered row up.
- Down moves the hovered row down.
- Space toggles selection for the hovered file or directory.
- Tab enters the hovered directory when the hovered row is a directory.
- Backspace moves to the parent directory.
- Enter embeds selected files and directories.
- Esc cancels the picker and returns focus to the command input.

If Tab is pressed while the hovered row is not a directory, the picker stays open and shows a short message such as `Tab enters directories only.`

## Selection Expansion

When Enter is pressed, Runic expands selected paths into concrete readable files:

1. Selected files are candidates directly.
2. Selected directories are traversed recursively.
3. Duplicate files are de-duplicated by resolved path.
4. Noisy or generated paths are skipped by default.
5. Binary, unreadable, or non-UTF-8 files are skipped.

Default skipped directory names are `.git`, `__pycache__`, `node_modules`, `.mypy_cache`, `.pytest_cache`, `.ruff_cache`, `dist`, `build`, and `.venv`. Default skipped file extensions are `.pyc`, `.pyo`, `.so`, `.dll`, `.dylib`, `.exe`, `.bin`, `.png`, `.jpg`, `.jpeg`, `.gif`, `.webp`, `.ico`, `.pdf`, `.zip`, `.tar`, `.gz`, `.7z`, `.mp3`, `.mp4`, and `.mov`. Skips are not fatal; the batch proceeds with readable files.

If no items are selected, Enter keeps the picker open and shows `Select at least one file or directory.`

If expansion finds zero readable files, Output reports that nothing was embedded and the picker returns to selection mode.

## Embedding Flow

After selection expansion finds one or more readable files, the wide picker switches from selection mode to embedding-progress mode for the batch. It keeps the model and selected root summary visible and shows progress based on concrete readable files:

```text
Embedding 7/24 files  [########------]  29%
```

For each readable file:

1. Read the file as UTF-8.
2. Call the existing `controller.embed(model, text)` path.
3. Append a per-file result to Output.
4. Advance the progress count.

Successful results show the file path, embedding dimensions, and existing embedding preview format. Failed results show the file path and formatted error. One file failure does not stop the rest of the batch.

When the batch completes, the picker shows a final summary with processed, succeeded, failed, and skipped counts, then returns focus to the command input.

## Architecture

Keep the feature inside the interactive shell boundary. The model controller remains responsible for embedding a single text payload with a model. The picker owns file browsing, selection, expansion, display state, and batch orchestration.

Add small focused units:

- `EmbedPickerEntry`: immutable row metadata for files and directories, including path, name, extension/type label, size, directory flag, selected flag, and hover state.
- `EmbedPickerState`: current directory, cursor index, selected paths, current message, progress state, and navigation/toggle methods.
- Directory scanning helpers for listing current directory rows, sorting directories before files, formatting sizes, and preserving deterministic order.
- Expansion helpers for recursive directory traversal, skip classification, duplicate removal, UTF-8 readability checks, and readable file collection.
- Pane formatting helpers for wide picker rendering with Rich styling when available and ASCII fallback for frame tests.
- TUI integration for picker-specific keybindings, focus management, and batch embedding.

The non-TUI prompt fallback keeps direct `embed <model> <value>` support only. It does not implement the picker because the picker depends on focusable panes and keybindings.

## Error Handling

- Invalid `embed` usage still returns the existing invalid command message.
- Directory listing failures show a picker message and keep the user in the previous usable directory when possible.
- File read failures during expansion or embedding are reported per file and do not abort the batch.
- Decode failures are treated as skipped unreadable files.
- Controller embed failures are reported per file and do not abort the batch.
- Empty selections and zero-readable-file expansions keep the picker usable.

## Testing

Add focused tests for:

- `embed <model>` opens picker instead of returning invalid usage.
- `embed <model> <value>` remains unchanged.
- The picker starts at the launch working directory.
- Navigation moves the cursor, enters directories, and moves to the parent directory.
- Space toggles files and directories.
- Row rendering includes directory labels, file extensions, sizes, selected state, and cursor state.
- Rich/color rendering has a plain textual fallback.
- Recursive directory expansion skips `.git`, `__pycache__`, `node_modules`, binary files, unreadable files, and non-UTF-8 files.
- Duplicate files are de-duplicated when selected directly and through a directory.
- Enter with selected readable files calls `controller.embed` once per readable file.
- Per-file failures do not stop later files.
- Progress count updates across a batch.
- Enter with no selection and Enter with zero readable expanded files keep the picker usable and show clear feedback.

## Open Decisions

No open decisions remain for this design. The selected approach is the wide focused picker mode for `embed <model>`, rooted at launch cwd, preview-only output, recursive directory embedding with default skips, and batch progress in the pane.
