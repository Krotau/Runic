# Embed File Picker Next Steps

Date: 2026-04-23
Branch: `codex/embed-file-picker`

## Resume Point

Resume with Task 5 code quality review.

Use the code quality review range:

```bash
git diff --stat 31bbbcd..f16a068 -- runic/interactive/shell.py tests/test_interactive_shell.py
git diff 31bbbcd..f16a068 -- runic/interactive/shell.py tests/test_interactive_shell.py
python -m unittest tests.test_interactive_shell.TestInteractiveShell.test_render_shell_frame_draws_wide_embed_picker_pane_above_output tests.test_interactive_shell.TestInteractiveShell.test_render_shell_frame_keeps_default_pane_on_right tests.test_interactive_shell.TestInteractiveShell.test_render_shell_frame_draws_install_side_pane tests.test_interactive_shell.TestInteractiveShell.test_render_shell_frame_draws_chat_session_pane -v
```

Task 5 quality review should verify:

- `PaneState.layout` is a clean extension and does not break existing pane call sites.
- `_pane_lines` removes duplicated pane line assembly without changing existing side/top pane behavior.
- Wide pane row budgeting preserves title, prompt, final border, and stable line widths.
- Tests protect the wide pane geometry and existing side pane compatibility.

## After Task 5 Quality Review

If Task 5 quality review passes, mark Task 5 complete and continue with Task 6 from `docs/superpowers/plans/2026-04-23-embed-file-picker.md`.

Task 6 summary:

- Modify `runic/interactive/shell.py`.
- Modify `tests/test_interactive_shell.py`.
- Add `_split_embed_argument(argument: str | None) -> Result[tuple[str, str | None], DefaultError]`.
- Preserve existing direct `embed <model> <value>` behavior.
- Allow `embed <model>` to parse as `(model, None)` for the future TUI picker trigger.
- Keep bare `embed` invalid.

## Execution Discipline

Continue using `superpowers:subagent-driven-development`:

1. Dispatch one fresh implementer worker per task.
2. Run spec compliance review after each task.
3. Run code quality review only after spec compliance passes.
4. Fix review issues before moving to the next task.
5. Keep implementation workers sequential, not parallel.

## Current Agent Cleanup

At the time this file was written:

- Task 5 implementer and spec-review agents may still be open in the current agent runtime.
- They can be closed after confirming no further Task 5 spec-review interaction is needed.
