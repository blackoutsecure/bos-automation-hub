# Composite actions

Reusable composite actions consumed by the workflows in this repo and by
downstream callers that pin to this hub. Layout:

- `shared/<name>/` — consumed by multiple workflows, referenced as
  `blackoutsecure/bos-automation-hub/.github/actions/shared/<name>@<ref>`.
- `<name>/` — used by one workflow in this repo only.

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
