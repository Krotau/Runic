# Ollama HTTP Pull Install Design

Date: 2026-04-23
Status: Approved for planning

## Goal

Replace Runic's Ollama model install path from CLI-based `ollama pull` execution to Ollama's local HTTP API so installs:

- use Ollama as the single source of truth for model download state
- show live, normalized progress in the Runic install pane
- verify the model exists in Ollama after the pull completes
- only mark the model installed in Runic after verification succeeds

The installed model must remain usable through normal Ollama usage and through Runic.

## Scope

This design covers the Ollama-backed interactive install flow in Runic.

Included:

- streamed `POST /api/pull` integration in the Ollama runner
- normalized install phases for the UI
- follow-up `GET /api/tags` verification
- shell and TUI updates so the install pane redraws in place during progress
- tests for streamed progress, verification, and failure handling

Excluded:

- changes to chat or embedding behavior
- support for non-Ollama runners
- generalized cross-runner install event abstractions beyond what this Ollama integration needs

## Current Problem

Runic currently installs Ollama models by spawning `ollama pull` and waiting for the subprocess to finish. This means:

- progress is only visible after the command completes
- the install pane cannot update in place during the download
- Runic does not explicitly verify the model exists in Ollama before marking it installed
- failure feedback is less precise than it could be

## Recommended Approach

Runic should use Ollama's HTTP API as the only install path for Ollama models.

`OllamaRunner.install_model()` will:

1. connect to the local Ollama API
2. open a streamed `POST /api/pull` request for the requested model
3. consume streamed JSON updates from Ollama
4. normalize those updates into a small set of user-facing install phases
5. log raw Ollama messages into the spell log for debugging
6. perform a `GET /api/tags` verification check after the pull completes
7. only return a successful installed model after verification passes

This keeps the implementation aligned with Ollama's actual runtime behavior while letting Runic provide better user feedback.

## Architecture

### Ollama Runner

The Ollama runner remains the owner of install execution.

Responsibilities:

- issue the streamed pull request
- parse Ollama's NDJSON stream safely
- translate raw Ollama updates into normalized progress state
- emit `context.log(...)` updates with raw detail
- emit `context.progress(...)` updates when meaningful progress can be derived
- verify the pulled model exists through `GET /api/tags`
- return a precise failure when connect, pull, parse, or verification steps fail

The runner should no longer shell out to `ollama pull` for installs.

### Controller And Registry

The controller keeps its current responsibility boundaries.

- It still resolves the model reference and schedules the install spell.
- It still saves the installed registry record after runner success.
- It must only save the model as installed after the runner's verification step succeeds.

This keeps Runic's registry as a confirmed reflection of Ollama state rather than an optimistic guess.

### UI Layer

The shell and TUI install pane should move from a fixed start-and-finish display to a live-updating pane driven by spell state while installation is running.

The pane should update in place and show one normalized step at a time.

Target pane behavior:

- `connecting.... connected!`
- `downloading... [#######_______] 50%`
- `verifying.... verified!`
- `installing.... done!`

Only the active step should be shown in the main status line. When a step finishes, it is replaced by the next step rather than appended as a long scroll of transient states.

Raw Ollama detail should remain available through spell logs or install details, not as the primary pane copy.

For the first implementation, the UI should redraw from existing conduit state rather than introducing a new generic install event system. The runner should emit a structured log update whenever the normalized phase or visible progress meaningfully changes, and the UI should use those log events as its redraw trigger while reading the current spell record for the latest `progress` value.

## Install Lifecycle

### 1. Connecting

When the install starts, Runic should enter a `connecting` phase while opening the HTTP request to Ollama.

Success outcome:

- pane shows a connected state briefly before moving on

Failure outcome:

- install fails in `connecting`
- error should clearly say the local Ollama API could not be reached

### 2. Downloading

Once the stream is open, Runic enters `downloading`.

The runner should inspect each streamed pull update and derive:

- a human-readable normalized status
- a numeric progress value when Ollama provides enough information to compute one

The pane should redraw in place with a single progress bar and percentage whenever computable.

If Ollama supplies status text without enough information for a stable percentage, Runic should keep the `downloading` phase visible and continue logging the raw detail without inventing fake precision.

### 3. Verifying

After the pull stream reports completion, Runic enters `verifying`.

The runner should call `GET /api/tags` and confirm the requested model is present in Ollama's local model list.

Success outcome:

- pane shows `verifying.... verified!`

Failure outcome:

- install fails in `verifying`
- error explicitly says the pull finished but the model could not be confirmed in Ollama

### 4. Installing

After verification succeeds, Runic enters `installing`.

This phase represents Runic finalizing its own local registry record and settling the spell. It should be brief, but visible, so the user can tell the difference between Ollama download completion and Runic completion.

Success outcome:

- pane shows `installing.... done!`
- Runic saves the installed model in its registry
- spell settles successfully

## Normalized Status Model

The UI should use a small stable status model independent of Ollama's raw text.

Initial status set:

- `connecting`
- `downloading`
- `verifying`
- `installing`
- `done`
- `failed`

Each status may include:

- `label`: user-facing step text
- `detail`: optional short explanation
- `progress`: optional float from `0.0` to `1.0`

Raw Ollama stream messages should be preserved separately in spell logs and error details. The pane should prioritize consistency over raw fidelity.

For this first version, the normalized status should be encoded in a machine-readable log line format owned by the interactive install flow. That gives the UI a concrete event stream to react to without broadening the conduit API during this change.

## Data Flow

1. User runs `install <model>`.
2. The controller resolves the model reference and schedules the install spell.
3. The runner emits `connecting` and opens the Ollama pull stream.
4. The runner consumes streamed updates, maps them into normalized download progress, and emits structured log updates when the visible phase or progress changes.
5. The UI listens for spell log events, reads the current spell record, and redraws the install pane in place as state changes.
6. When the pull completes, the runner enters `verifying` and checks `GET /api/tags`.
7. If verification succeeds, the runner returns success.
8. The controller saves the model in the Runic registry.
9. The pane enters `installing`, then `done`.

## Error Handling

Failures must be phase-specific and clear.

### Connection Failure

If the local Ollama API cannot be reached, the install fails during `connecting`.

Expected user-facing behavior:

- pane stops on `connecting`
- message explains that Ollama's local HTTP API is unavailable

### Pull Stream Error

If Ollama returns an error payload during pull, the install fails during the active phase.

Expected behavior:

- preserve the Ollama error payload in `DefaultError.details`
- show a concise normalized failure in the pane

### Malformed Stream Data

If the pull stream cannot be parsed as expected JSON updates, fail explicitly instead of silently succeeding or hanging.

Expected behavior:

- mark install failed
- include enough raw parse detail for debugging

### Verification Failure

If the pull appears complete but the requested model is not present in `/api/tags`, fail in `verifying`.

Expected behavior:

- error clearly states verification failed after download
- model is not saved to the Runic registry as installed

## Testing Strategy

Add targeted tests around the runner and interactive shell/TUI behavior.

Runner tests:

- successful streamed pull with incremental progress updates
- successful pull followed by successful `/api/tags` verification
- HTTP connection failure before streaming begins
- Ollama error payload during pull
- malformed streamed JSON handling
- verification failure after apparent pull success

UI tests:

- install pane redraws in place as phases change
- downloading pane renders a progress bar and percentage
- verification step becomes visible after pull completion
- failure state preserves the correct phase and message

Regression tests:

- successful install still saves a usable installed model record
- failed verification does not save an installed record

## Implementation Notes

- Prefer official Ollama HTTP endpoints already used elsewhere in the runner instead of introducing a parallel install mechanism.
- Keep the first implementation scoped to Ollama rather than introducing a generic install event system prematurely.
- Keep raw logs for debugging, but ensure the pane copy remains normalized and concise.

## Success Criteria

This work is successful when:

- installing an Ollama model in Runic no longer shells out to `ollama pull`
- the install pane updates live during download
- the pane shows normalized in-place steps rather than raw append-only output
- the model is verified through Ollama after pull completion
- Runic only marks the model installed after verification succeeds
- failures clearly indicate whether the problem happened while connecting, downloading, verifying, or finalizing
