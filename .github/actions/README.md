# Composite actions

Reusable composite actions consumed by the workflows in this repo and by
downstream callers that pin to this hub. Layout:

- `shared/<name>/` — consumed by multiple workflows, referenced as
  `blackoutsecure/bos-automation-hub/.github/actions/shared/<name>@<ref>`.
- `<name>/` — used by one workflow in this repo only.

The shared `resolve-hub-ref` action centralizes the small amount of branch
routing required by managed kickers. Kicker files should keep only event
triggers, resolver inputs, static `@dev`/`@main` jobs, and secret inheritance;
configuration and execution belong in the reusable backend workflow.

## Published orchestration actions

[`repo-metadata`](repo-metadata/action.yml) is the shared composite that
resolves and writes repository descriptions, homepages, topics, and best-effort
sidebar widget preferences. Prefer the reusable
[`repo-metadata-sync.yml`](../workflows/repo-metadata-sync.yml) workflow for
normal consumers; it adds released-ref checkout, token fallback, concurrency,
soft skip behavior, and reusable outputs around the composite. Direct action
calls remain supported for custom orchestration.

The reusable workflow keeps credentials purpose-specific: the selected
Administration PAT is used only for repository PATCH/PUT calls, while the
job-scoped `GITHUB_TOKEN` with `models: read` is used for optional inference.

## Rules

1. **Inputs go through `env:`, never `${{ … }}` in `run:` bodies.** Bash
   reads the input as `"${VAR}"`. Template expansion inside `run:` is a
   shell-injection bug.
2. **Every bash `run:` starts with `set -euo pipefail`.**
3. **Validation helpers (`die`, `validate_tag`, `check_singleline`) stay
   inlined per action.** Total duplication is ~30 lines and keeping each
   `action.yml` self-contained is worth more than the saving.
4. **Python > ~20 lines moves to a sibling `.py` file**, invoked as
   `python3 "${GITHUB_ACTION_PATH}/script.py"`. `${GITHUB_ACTION_PATH}`
   resolves correctly cross-repo. Inputs still go through `env:`.
5. **Third-party actions are SHA-pinned** with a trailing version comment.
   Dependabot bumps both.
6. **`persist-credentials: false` on every `actions/checkout`** unless the
   step needs to push back.

## Lint

`actionlint` + `shellcheck` run on every PR via
[`.github/workflows/lint.yml`](../workflows/lint.yml). Locally:

```bash
brew install actionlint shellcheck
actionlint
```
