# Interactive Runic CLI Design

## Summary

Add an optional interactive CLI mode to Runic for installing and running local models. The first implementation is provider-agnostic at the Runic boundary and ships Ollama as the first concrete runner. The CLI is prompt-first, with richer transient panes for model selection, install progress, runner confirmation, and chat context.

This first slice implements `install` and `run` with interactive chat. MCP and RAG are extension points only; this design does not include MCP tool exposure, document ingestion, embedding generation, vector storage, or retrieval.

## Goals

- Keep Runic's core package lightweight and preserve the current async runtime style.
- Add an optional interactive CLI extra using `prompt_toolkit` and `rich`.
- Support installing models from Ollama-style names/links first.
- Recognize Hugging Face links and normalize them into model references without claiming all of them can run locally.
- Run installed models through a runner protocol, starting with Ollama.
- Reuse `Runic`, `Conduit`, spell status, and spell logs for long-running install work.
- Provide clear recovery when a required runner is missing.
- Leave explicit extension points for future MCP and RAG support.

## Non-Goals

- No full dashboard TUI in the first version.
- No automatic silent installation of global runner software.
- No first-version Python-native execution for arbitrary Hugging Face models.
- No embeddings store, chunking, document ingestion, or retrieval pipeline.
- No MCP server/client implementation.

## Package Shape

The base package remains dependency-free. CLI dependencies are exposed through an optional extra:

```toml
[project.optional-dependencies]
cli = [
    "prompt_toolkit>=3",
    "rich>=13",
]
```

A console script should point to the CLI entry point:

```toml
[project.scripts]
runic = "runic.cli:main"
```

The implementation should keep import boundaries clean so `import runic` does not import `prompt_toolkit`, `rich`, or runner-specific modules unless the CLI is used.

## Architecture

The feature has three layers.

### Interactive Shell

`runic.cli` owns the terminal interaction. It uses `prompt_toolkit` for the application layout, command prompt, completions, keybindings, history, arrow-key selection, tab/enter navigation, and async redraw. It uses `rich` to render progress bars, tables, panels, logs, and chat output into the prompt toolkit display buffers.

The shell starts in command mode:

```text
runic> _
```

Initial commands:

```text
install <huggingface-or-ollama-link>
run [model]
help
exit
```

### Model Runtime

The model runtime contains provider-agnostic records and orchestration. It parses user input, stores model metadata, chooses a compatible runner, and exposes command objects that can be routed through `Runic`.

Core records:

- `ModelReference`: normalized user input from an Ollama name/link or Hugging Face URL.
- `InstalledModel`: registry record with local name, source provider, source URI, runner name, install status, and metadata.
- `RunnerCapability`: describes what a runner can install and run.
- `ModelRegistry`: reads and writes Runic's local model registry.

The registry is stored in a user-level Runic config directory, not in the project repository by default. It records what Runic knows about; Ollama remains the source of truth for actual Ollama model availability.

### Runner Layer

The runner layer defines a `ModelRunner` protocol. Ollama is the first implementation.

Required runner operations:

- `name`
- `capabilities`
- `is_available()`
- `install_runner()`
- `install_model(reference, context)`
- `list_models()`
- `chat(model, messages)`

`install_model` receives a Runic spell context or equivalent adapter so it can emit logs and progress through the existing `Conduit` workflow.

## CLI Layout

The first version supports one transient pane at a time. On a normal terminal, install progress appears in a top-right pane while the command prompt and logs remain usable:

```text
+------------------------------------------------------------------------------+
| Runic Interactive                                      runner: ollama ready   |
+------------------------------------------------------+-----------------------+
|                                                      | Install               |
| > install ollama://llama3.2                          | llama3.2              |
|                                                      | ###########..  82%    |
| Resolving model reference...                         | downloading layers    |
| Starting Ollama pull...                              | 1.8 GB / 2.2 GB       |
|                                                      | elapsed 02:14         |
|                                                      |                       |
|                                                      | Esc: hide pane        |
|                                                      | Enter: details        |
|                                                      |                       |
+------------------------------------------------------+-----------------------+
| runic> install https://ollama.com/library/llama3.2                            |
+------------------------------------------------------------------------------+
```

During chat, the same area can show session status and future context-provider state:

```text
+------------------------------------------------------------------------------+
| Runic Interactive                                      model: llama3.2        |
+------------------------------------------------------+-----------------------+
| You: summarize this design                           | Session               |
|                                                      | model llama3.2        |
| Assistant: The design adds a prompt-first CLI...     | runner ollama         |
|                                                      | temp default          |
| You: what are the extension points?                  |                       |
|                                                      | Future Context        |
| Assistant: The main extension points are...          | MCP disabled          |
|                                                      | RAG disabled          |
|                                                      |                       |
+------------------------------------------------------+-----------------------+
| chat:llama3.2> _                                                              |
+------------------------------------------------------------------------------+
```

On small terminals, the transient pane collapses above the prompt:

```text
+----------------------------------------------------------------+
| Install llama3.2  ###########.. 82%  1.8 GB / 2.2 GB           |
+----------------------------------------------------------------+
| Resolving model reference...                                   |
| Starting Ollama pull...                                        |
+----------------------------------------------------------------+
| runic> _                                                       |
+----------------------------------------------------------------+
```

## Install Flow

`install <link>`:

1. Parse the input into a `ModelReference`.
2. If the input is an Ollama name or Ollama link, choose the Ollama runner.
3. If the input is a Hugging Face link, store the normalized reference and ask installed runners whether they can handle it.
4. If no runner can handle the model, show a clear unsupported message. The registry may keep the reference as unavailable, but it must not mark it installed.
5. If the selected runner is missing, show a confirmation pane explaining that the runner is required.
6. If the user accepts runner installation, use the runner's installer flow. If automatic installation is unsupported for the platform, show manual installation guidance.
7. Schedule model installation as a Runic spell through `Conduit`.
8. Stream spell logs and status into the install pane.
9. Mark the registry record installed only after the runner reports success.

Missing runner is a recoverable state, not a crash.

## Run Flow

`run [model]`:

1. If no model is supplied, show an arrow-key model picker.
2. Resolve the selected model from the registry and confirm the runner is available.
3. If the runner is missing, show the same runner installation confirmation pane.
4. Enter chat mode for the selected model.

Chat mode accepts normal prompts and slash commands:

```text
/exit
/model
/clear
/help
```

The runner protocol must support streaming responses in its shape. The first Ollama implementation may begin with non-streaming if that keeps the first implementation reliable, but the protocol must not block future streaming.

## Ollama Runner

The Ollama runner should prefer the local Ollama HTTP API when available because it integrates well with async Python and streaming. It may fall back to subprocess calls for operations where the CLI is simpler or more stable.

Behavior:

- `is_available()` checks for the Ollama service and/or `ollama` command.
- `install_runner()` does not silently install global software. It can provide platform-specific instructions and, only after confirmation, run a supported installer flow.
- `install_model()` wraps Ollama pull behavior and emits Runic spell logs/progress.
- `list_models()` returns local Ollama models and reconciles them with the Runic registry.
- `chat()` sends messages to the selected local model.

## Hugging Face Handling

Hugging Face URLs are recognized and normalized into `ModelReference` records. The first version does not promise Python-native execution of arbitrary Hugging Face models.

If a Hugging Face reference cannot be handled by an installed runner, the CLI should say that the model is recognized but unsupported by the currently installed runners. This keeps future support open without pretending there is a universal local runner.

## Runtime Integration

The CLI should register command/spell handlers with `Runic` instead of bypassing the runtime:

- Install orchestration is a command that schedules a spell.
- The install spell emits progress and logs through `Conduit`.
- Chat uses the runner protocol directly or through a command handler, depending on the cleanest boundary during implementation.

This keeps long-running work consistent with the existing async workflow and avoids adding a parallel task system.

## MCP And RAG Extension Points

The first version leaves room for future context providers:

- An MCP adapter can later expose installed models or chat actions as tools.
- A RAG provider can later supply context chunks before a chat request.
- Chat orchestration should have a narrow place where future context can be injected before calling the runner.

No MCP or RAG behavior is implemented in this first design.

## Error Handling

- User-facing failures should use explicit `Err(DefaultError(...))` values where they cross Runic command/spell boundaries.
- Background install failures emit spell logs and terminal status updates.
- Missing runner opens a confirmation flow.
- Unsupported provider/model yields a clear unsupported message.
- Interrupted install/chat cancels active work and leaves the registry in pending or failed state.
- The registry must not mark a model installed unless the runner confirms success.

## Testing

Tests should cover:

- Ollama name and link parsing.
- Hugging Face URL parsing.
- Registry read/write behavior.
- Runner availability branches.
- Missing-runner decision flow.
- Install command scheduling through `Conduit`.
- Install spell status/log emission.
- Chat model selection and runner invocation.
- CLI adapter behavior without requiring a real terminal where practical.

Tests should mock Ollama process/API calls. The default test suite must not require Ollama to be installed.
