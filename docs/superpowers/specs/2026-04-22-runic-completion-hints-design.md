# Runic Completion Hints Design

## Summary

Add prompt-first completion hints to the interactive Runic TUI. While the user types a command or a model name, the prompt should show a faint inline suggestion when there is one clear match. Pressing `Tab` accepts that suggestion. When there are multiple matches, Runic should show the selectable completion menu instead.

This applies to top-level commands and to installed model names for both `chat` and `embed`.

## Goals

- Show low-noise inline ghost hints for obvious command and model completions.
- Preserve the existing arrow-key completion menu when multiple choices are available.
- Make `Tab` accept the ghost hint or interact with the menu depending on the current completion state.
- Support installed model completion for both `chat <model>` and `embed <model>`.
- Stop model completion after `embed` already has a model and the user is entering text or a file path.
- Keep completion candidate logic testable outside the live TUI.

## Non-Goals

- No file-path completion for `embed` text/file arguments.
- No fuzzy search; matching stays prefix-based.
- No new provider-specific completion rules.
- No completion pane that competes with the install/session pane.

## User Experience

When one command matches:

```text
Command
runic> ch[at]
```

The bracketed text represents faint ghost text. Pressing `Tab` accepts `chat`.

When multiple commands match:

```text
Command
runic> e

embed
exit
```

Runic opens the existing selectable menu. Arrow keys move through choices, `Tab` cycles, and `Enter` accepts the selected option.

The same behavior applies after `chat ` and `embed `:

```text
Command
runic> chat qw[en3:8b]
```

If both `qwen3:8b` and `qwen3-embedding:8b` are installed, Runic shows the menu instead of a ghost hint.

## Completion Model

`complete_shell_input(text_before_cursor, installed_models)` remains the source of truth for candidates. It returns command candidates at the root prompt and model candidates after `chat ` or `embed `.

A new small classifier should interpret those candidates for the TUI:

- zero candidates: no hint and no menu
- one candidate: show a ghost suffix when the candidate extends the current token
- multiple candidates: show the completion menu

The classifier should not know about prompt-toolkit widgets. It should work with simple strings and `ShellCompletion` records so it can be unit-tested directly.

## Prompt Toolkit Integration

The real TUI should use prompt-toolkit's inline suggestion or processor hooks to render ghost text with a dim style. The ghost text is visual only; the command buffer should not change until the user accepts it.

`Tab` behavior should be:

- If a completion menu is open, cycle to the next menu option.
- If one ghost hint is available, insert the hinted suffix.
- If multiple candidates are available and the menu is closed, open the menu.
- If no candidates are available, do nothing.

`Shift-Tab` should keep the existing reverse menu behavior while the menu is visible. When the menu is not visible, it may continue to reverse-cycle focus as it does today.

## Chat And Embed Rules

At the root prompt, completions include:

```text
install
chat
embed
help
exit
```

After `chat `, completions should list installed models until a model token has been completed. Chat message text is not completed inside chat mode because the prompt is already scoped to one active model.

After `embed `, completions should list installed models only for the first argument. Once the model token is followed by whitespace and more text, Runic leaves the remaining text or file path untouched.

## Error Handling

Completion should degrade silently:

- If there are no installed models, no model hint appears.
- If model listing fails in a future implementation, the prompt should remain usable without completion.
- Rendering or completion state should not affect command parsing or execution.

## Testing

Add focused tests for:

- command ghost classification, such as `ch` producing `chat`
- ambiguous command classification, such as `e` producing menu candidates `embed` and `exit`
- single installed model hints for `chat`
- ambiguous installed model menu candidates for `chat`
- single installed model hints for `embed`
- ambiguous installed model menu candidates for `embed`
- no model completion after `embed <model> <text>`
- TUI application construction smoke test remains valid
