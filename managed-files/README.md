# `managed-files/` — canonical source-of-truth for hub-managed files

This directory is the **target home** for canonical content of every file
the hub pushes into consumer repos via the
[`sync-managed-files`](../.github/actions/sync-managed-files/) action.

The folder name matches the rest of the vocabulary:

| Surface                                        | Name                         |
| ---------------------------------------------- | ---------------------------- |
| Composite action                               | `sync-managed-files`         |
| Reusable workflow                              | `sync-managed-files.yml`     |
| Per-consumer config (lives in consumer repo)   | `bos-managed-files.yaml`     |
| Source-of-truth content (lives here, in hub)   | `managed-files/` ← this dir  |

## Why this folder exists

Today every file body the hub distributes — LICENSE text, NOTICE template,
CODEOWNERS default, `log-functions.sh`, `.prettierrc.yaml`, the launchpad
kicker workflows, the init-if-missing starter workflows — lives **inline
as Python string constants** inside
[`sync.py`](../.github/actions/sync-managed-files/sync.py)
(`_LICENSE_APACHE2`, `_NOTICE_TEMPLATE`, `_BOS_LAUNCHPAD_RELEASE_YML`, …).

That made bootstrapping fast but has downsides as the action grew past
2 000 lines:

- **Reviewability.** A 200-line YAML diff inside a Python triple-quoted
  string is reviewed as a Python diff, not a YAML diff. Reviewers lose
  syntax highlighting, IDE schema validation, and `git blame` granularity
  on the workflow content itself.
- **Linting.** The embedded YAML cannot be linted by `yamllint` /
  `actionlint` in place — only after `sync.py` has written it to a
  consumer repo. Drift between "what sync.py emits" and "what the linters
  validate downstream" is only caught after the fact.
- **Maintainability.** Editing the Apache-2.0 text means scrolling past
  200 lines of legalese in the middle of action code. Editing a launchpad
  workflow means navigating triple-quoted Python strings instead of
  opening a `.yml` file.

Moving these bodies onto disk fixes all three. The trade-off is one
extra disk read per file at sync time, which is negligible.

## Authority — read carefully

> **As of this directory's creation, `sync.py`'s `_*` string constants
> remain the single source of truth.** Nothing in this folder is read by
> `sync.py` yet.

This is deliberate. Migration is a separate, reviewable change per file —
not a big-bang flip. The folder + README exist first so the convention is
agreed before any extraction happens.

## Naming convention (when content lands here)

Each file's relative path under `managed-files/` mirrors the path it
will be written to in the **consumer** repo, with two exceptions:

1. **Multi-variant content** uses a subdirectory keyed by the variant
   discriminator. The four SPDX licenses become
   `managed-files/licenses/apache-2.0.txt`,
   `managed-files/licenses/mit.txt`, etc. — `sync.py`'s
   `_LICENSE_REGISTRY` lookup becomes a `licenses/<spdx-id>.txt` file
   read.
2. **Templated files** (those containing `{{KEY}}` placeholders
   substituted from `bos-managed-files.yaml` at sync time) keep their
   `{{KEY}}` syntax verbatim on disk. The placeholder substitution is
   `sync.py`'s job at sync time, not authoring time.

Current + planned layout:

```text
managed-files/
├── README.md                              ← you are here
│
│   # === LANDED (extracted, not yet wired into sync.py) ===
│
├── community-health/                      ← org-wide defaults (inherit-able from .github org repo)
│   ├── CODE_OF_CONDUCT.md
│   ├── CONTRIBUTING.md
│   ├── SECURITY.md
│   ├── SUPPORT.md
│   └── FUNDING.yml
├── github-meta/                           ← org-default `.github/` content
│   ├── PULL_REQUEST_TEMPLATE.md
│   └── ISSUE_TEMPLATE/
│       ├── bug_report.md
│       ├── feature_request.md
│       └── config.yml
├── org-profile/                           ← org public profile page
│   └── README.md                          ← target: blackoutsecure/.github profile/README.md
│
│   # === PLANNED (still inline in sync.py as `_*` string constants) ===
│
├── licenses/
│   ├── apache-2.0.txt                     ← from _LICENSE_APACHE2
│   ├── mit.txt                            ← from _LICENSE_MIT
│   ├── bsd-3-clause.txt                   ← from _LICENSE_BSD_3_CLAUSE
│   └── isc.txt                            ← from _LICENSE_ISC
├── notice.apache2.txt                     ← from _NOTICE_TEMPLATE
├── codeowners.txt                         ← from _CODEOWNERS_TEMPLATE
├── prettierrc.yaml                        ← from _PRETTIERRC_YAML
├── log-functions.sh                       ← from _LOG_FUNCTIONS_SH
└── workflows/
    ├── bos-launchpad-release.yml          ← from _BOS_LAUNCHPAD_RELEASE_YML
    ├── bos-launchpad-cf-pages.yml         ← from _BOS_LAUNCHPAD_CF_PAGES_YML
    ├── bos-launchpad-sync-files.yml       ← from _BOS_LAUNCHPAD_SYNC_FILES_YML
    ├── bos-launchpad-org-default.yml      ← (NEW) kicker for the `.github` org repo (TBD)
    ├── sync-managed-files.yml             ← from _GHA_SYNC_COMMIT_YML
    ├── sync-drift-check.yml               ← from _GHA_SYNC_DRIFT_CHECK_YML
    ├── lint.node.yml                      ← from _GHA_LINT_NODE_YML
    ├── lint.python.yml                    ← from _GHA_LINT_PYTHON_YML
    └── lint.shell.yml                     ← from _GHA_LINT_SHELL_YML
```

## Per-consumer scope — `target_repo_role`

Not every consumer receives every file. The `.github` **org repo** needs
files no other consumer should ever get (its own `profile/README.md`,
its own top-level `CODE_OF_CONDUCT.md`/`CONTRIBUTING.md`/`SECURITY.md`).
Normal consumers must NOT receive `profile/README.md` — that would put
an org-landing-page profile inside every project repo.

**Proposed (not yet implemented):** add a `target_repo_role:` key to
`bos-managed-files.yaml` with values:

| Value              | Meaning                                                 | Receives                                                       |
| ------------------ | ------------------------------------------------------- | -------------------------------------------------------------- |
| `consumer` (default) | A normal project repo                                  | Today's services only — none of the org-default-only content   |
| `org-default-repo` | The `blackoutsecure/.github` org repo itself           | `community-health/*`, `github-meta/*`, `org-profile/README.md` |

The role gates which services in `SERVICE_FILES` / `SERVICE_INIT_FILES`
fire. Wiring lives in `sync.py` and is deliberately deferred until the
content-extraction migration above is also designed — they share
plumbing.

## Migration roadmap (proposed, not started)

A safe migration of a given file requires three pieces moving together:

1. **Extract** the Python string constant into a file under
   `managed-files/` matching the layout above.
2. **Re-point** `sync.py` to load the body via
   `pathlib.Path(__file__).parents[3] / "managed-files" / <path>` (or an
   equivalent helper) at module-import time, replacing the literal.
3. **Guard** the move with a drift check — until *all* constants are
   migrated, run a check that asserts every extracted file's contents
   match the live `_*` constant. This catches the failure mode where
   someone edits one and not the other during the migration window.

Recommended migration order (simplest first; lowest blast radius):

| Order | File(s)                       | Why first                                                |
| ----- | ----------------------------- | -------------------------------------------------------- |
| 0     | `community-health/*`,         | Already extracted — pure static markdown, no placeholders, |
|       | `github-meta/*`,              | single consumer (`.github` org repo) via `target_repo_role`. |
|       | `org-profile/README.md`       | Lowest blast radius of all — only one consumer.          |
| 1     | `licenses/*.txt`              | Static text, no placeholders for Apache-2.0; one-to-one  |
|       |                               | registry mapping; lowest reviewer ambiguity              |
| 2     | `notice.apache2.txt`,         | Static-ish templates with `{{KEY}}` placeholders only —  |
|       | `codeowners.txt`              | placeholder substitution already lives in sync.py        |
| 3     | `prettierrc.yaml`,            | Whole-file overwrites, single consumer service each      |
|       | `log-functions.sh`            |                                                          |
| 4     | `workflows/bos-launchpad-*`   | The launchpad kickers — bigger, but identical structure  |
|       |                               | so they batch well; existing kicker-examples drift check |
|       |                               | already verifies their content                           |
| 5     | `workflows/sync-*`,           | Init-if-missing starters — exercised less, write-once    |
|       | `workflows/lint.*`            | semantics make accidental regressions easier to spot     |

Each step is its own PR with its own drift-check survival. Do not batch.

### Status of step 0 (community-health / github-meta / org-profile)

The content is **on disk** (the new `community-health/`, `github-meta/`,
`org-profile/` directories you see today). What is NOT done:

- `sync.py` has no `target_repo_role` plumbing yet.
- No service registry entries point at these files.
- No kicker workflow exists to push them to `blackoutsecure/.github`.

This is intentional. The content moved first so it could be reviewed
in isolation. Wiring is the next PR — and lands together with the
first `_*` constant extraction so both share the same `_load_template()`
helper + drift-check.

## What does NOT belong here

- **Per-consumer overrides.** Consumers express choices in
  `bos-managed-files.yaml` (e.g. `license_type: mit`); the rendered
  output uses whichever variant from this folder. Don't add
  consumer-specific content here.
- **GitHub Actions workflows the hub itself runs.** Those live in
  [`../.github/workflows/`](../.github/workflows/) — they are not synced
  outward.
- **Example consumer files.** Those live in
  [`../examples/`](../examples/) — they are illustrative, not synced.
- **Linter configs.** Lint defaults for consumers ship from
  [`bos-marketplace-kit`'s `.github/actions/lint` composite](https://github.com/blackoutsecure/bos-marketplace-kit/tree/main/.github/actions/lint/configs)
  with inherit-by-default semantics. Don't duplicate them here.

## Reading order for newcomers

1. This README.
2. [`../.github/actions/sync-managed-files/action.yml`](../.github/actions/sync-managed-files/action.yml)
   — the composite that invokes sync.py.
3. [`../.github/actions/sync-managed-files/sync.py`](../.github/actions/sync-managed-files/sync.py)
   — search for `_LICENSE_APACHE2`, `_NOTICE_TEMPLATE`,
   `_BOS_LAUNCHPAD_RELEASE_YML` etc. to see the current source-of-truth
   constants this folder will eventually replace.
4. [`../scripts/sync-kicker-examples-from-sync.py`](../scripts/sync-kicker-examples-from-sync.py)
   — the drift-check pattern that should be extended to also guard
   `managed-files/` once migration starts.
