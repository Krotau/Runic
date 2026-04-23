# Embed File Picker Review Log

Date: 2026-04-23
Branch: `codex/embed-file-picker`

## Task 1

Implementation commits:

- `8d5c91a feat: add embed picker state`
- `b738b27 fix: sort embed picker entries case-insensitively`
- `cc314c4 test: restore embed picker metadata coverage`
- `f0f8471 docs: correct embed picker plan sort test`

Review outcome:

- Initial spec review found raw-name sorting instead of lowercase-name sorting.
- Re-review found the test had drifted away from the requested README/main metadata coverage.
- A plan contradiction was identified: lowercase sorting cannot produce `[src, README.md, main.py]`. The plan was corrected to `[src, main.py, README.md]` with README selected at cursor index 2.
- Final spec compliance review passed.
- Code quality review passed with no issues.

## Task 2

Implementation commit:

- `c4e8442 feat: add embed picker navigation`

Review outcome:

- Spec compliance review passed.
- Code quality review passed with no Critical, Important, or Minor issues.

## Task 3

Implementation commit:

- `9920c57 feat: render embed picker rows`

Review outcome:

- Spec compliance review passed.
- Code quality review passed.
- Reviewer noted optional future tests for Rich import laziness and pluralization, but no blocking issues.

## Task 4

Implementation commits:

- `4e45566 feat: expand embed picker selections`
- `cfb6716 fix: restore embed expansion tuple shape`
- `31bbbcd fix: skip symlinked directories in embed picker`

Review outcome:

- Worker first reported `DONE_WITH_CONCERNS` because `EmbedSelectionExpansion.files` was a list rather than the specified tuple. This was fixed before review.
- Spec compliance review passed.
- Code quality review found an Important issue: recursive walking followed symlinked directories.
- The worker fixed this by skipping symlinked directories and adding regression coverage.
- Code quality re-review passed with no remaining issues.

## Task 5

Implementation commits:

- `4cdf453 feat: render wide interactive pane`
- `f16a068 fix: preserve wide pane frame borders`

Review outcome:

- Initial spec review found the wide-pane plain frame could slice off the final closing border at height 16.
- The worker fixed row budgeting so the final border and prompt are preserved.
- Spec compliance re-review passed.
- Code quality review is still pending.
