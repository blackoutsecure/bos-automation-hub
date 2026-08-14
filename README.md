# Blackout Secure Automation Hub

[![Made by BlackoutSecure](https://img.shields.io/badge/made%20by-BlackoutSecure-1f1f1f)](https://github.com/blackoutsecure)

Central reusable GitHub Actions workflows, shared composite actions, and
managed repository files for Blackout Secure projects.

## Branch model

- `dev` is the development and default branch.
- `main` is the promoted stable runtime consumed through `@main`.
- version tags (`vX.Y.Z` and floating `vX`) point at promoted runtime commits.

GitHub Actions does not allow expressions in `uses:` references. Branch
selection is therefore handled by ownership rather than generated ref strings:

- managed consumer callers use `blackoutsecure/bos-automation-hub/...@main`;
- hub-only validation uses local `./.github/actions/...` references;
- runtime branch decisions use `github.event.repository.default_branch` where
  the caller repository's branch is intended.

The reusable security and managed-file-sync workflows expose `hub_ref`, which
defaults to `auto`. Auto follows a pull request or merge-group base branch,
then the current `dev` ref, and otherwise selects `main`. The managed dev/main
callers pass an explicit `hub_ref` override so each static `uses:` job consumes
the matching hub branch; other callers can pass `hub_ref: dev` or
`hub_ref: main` when they need a deliberate override.

[`release-hub.yml`](.github/workflows/release-hub.yml) promotes shared actions,
managed templates, `LICENSE`, this README, and every workflow declaring
`workflow_call`. Event-only maintenance workflows stay on `dev` automatically.

## Universal launchpad

[`bos-universal-launchpad.yml`](.github/workflows/bos-universal-launchpad.yml)
is the release and deployment orchestrator. Its managed consumer caller is
[`bos-universal-launchpad-kicker.yml`](sync-files/workflows/bos-universal-launchpad-kicker.yml).

The launchpad can compose:

- upstream release monitoring;
- multi-architecture Docker publishing and Docker Scout scanning;
- Balena block and fleet publishing;
- GitHub Releases;
- Cloudflare Pages deployment and generated site metadata;
- security scanning;
- repository metadata updates.

Consumer behavior is data-driven through `.github/bos-universal-config.json`; managed
workflow files are not edited in consumer repositories.

## Universal security

[`bos-universal-security.yml`](.github/workflows/bos-universal-security.yml) is
the reusable universal PR, protected-branch push, and merge-queue security/policy workflow. Its
managed caller is
[`bos-universal-security-kicker.yml`](sync-files/workflows/bos-universal-security-kicker.yml).

The required check is `security (dev) / Security summary` or
`security (main) / Security summary`, depending on which branch a run
targets. Gates are grouped by concern (see the comments in the workflow
file), though they stay one `workflow_call` surface and one required check
by design — splitting into separate workflows would force every consumer to
re-pin branch protection whenever a gate moved between groups:

- **Code quality:** workflow, Markdown, YAML, and Shell lint; optional Node
  checks (ESLint and Prettier); optional Python checks (Ruff and pytest);
  optional Shell checks (ShellCheck and Bats);
- **Security:** dependency review, code scanning (secret scan, SAST, GHAS
  posture audit), and pinned-action enforcement;
- **Compliance:** README-header and PR-title checks.

The hub itself runs
[`bos-universal-security.yml`](.github/workflows/bos-universal-security.yml)
directly. Use **Actions → Blackout Secure universal security (reusable) → Run
workflow** on `dev` for a manual scan; it loads the current `security` section
from `.github/bos-universal-config.json`, just like the sync backend. The managed
[`bos-universal-security-kicker.yml`](sync-files/workflows/bos-universal-security-kicker.yml)
is retained for consumer repositories, but the hub does not install a local
kicker for this workflow.

Marketplace-specific validation is intentionally excluded. Marketplace Action
repositories add the managed
[`bos-universal-marketplace-kicker.yml`](sync-files/workflows/bos-universal-marketplace-kicker.yml),
which owns Marketplace validation, stable-branch guarding, promotion, and
opt-in post-release repository metadata synchronization.

Code-scan policy layers the same way sync policy does. Org-wide defaults live in
[sync-files/config/code-scanning-kit-global-config.json](sync-files/config/code-scanning-kit-global-config.json),
a hub-authored file the code-scan job checks out alongside the caller repo and
passes via `global_config_path`. A repository can layer its own overrides with
a `code_scanning` block in its own `.github/bos-universal-config.json`, which
`bos-code-scanning-kit` receives explicitly as its repository-tier config.

## Managed file sync

[`bos-universal-sync.yml`](.github/workflows/bos-universal-sync.yml) is a thin
wrapper around the published
[`bos-managed-file-sync-action`](https://github.com/blackoutsecure/bos-managed-file-sync-action).
Unlike the other reusable entry points, this workflow only ever does one
thing (managed-file sync), so its display name and run name read "Blackout
Secure managed file sync" rather than "universal" — the `bos-universal-sync*`
filenames are unchanged to keep existing `uses:` references stable. It is
callable only through the managed
[`bos-universal-sync-kicker.yml`](sync-files/workflows/bos-universal-sync-kicker.yml).
That kicker owns the schedule, config-change, and manual events. Its
consumer front door resolves the target hub branch and delegates to
`bos-universal-sync.yml`, the same pattern used by the launchpad, security, and
Marketplace kickers. The reusable workflow never self-triggers and never
traverses the release, security, or Marketplace workflows. Sync defaults live in
[sync-files/config/managed-file-sync-global-config.json](sync-files/config/managed-file-sync-global-config.json),
a hub-authored file. `bos-universal-sync.yml` checks out this hub alongside
the consumer repo and passes `global_config_path` at the checked-out copy, so
the policy stays a real, editable JSON file instead of an inline blob.

## Universal action test

[`bos-universal-action-test.yml`](.github/workflows/bos-universal-action-test.yml)
is a reusable pytest matrix plus an optional live-upstream smoke test for
Actions repositories with a Python implementation. Its managed caller is
[`bos-universal-action-test-kicker.yml`](sync-files/workflows/bos-universal-action-test-kicker.yml).

It complements `bos-universal-security.yml`'s single-OS/Python `python-lint`
job (Ruff + pytest, part of the PR security gate) rather than replacing it:
use this workflow when a repo needs broader Python/OS matrix coverage and/or
validation against a live upstream target, driven by an `action_test` block
in `.github/bos-universal-config.json`:

```json
{
  "action_test": {
    "python_versions": ["3.10", "3.11", "3.12"],
    "os_matrix": ["ubuntu-latest", "macos-latest", "windows-latest"],
    "python_packages": ["pytest>=8.0", "ruff>=0.6", "PyYAML>=6.0"],
    "pytest_args": "-q",
    "enable_smoke_test": true,
    "smoke_trigger": "push-dev",
    "smoke_test_config": { "source": "npm", "package_name": "@actions/core" }
  }
}
```

The smoke-test job checks out the calling repo, invokes it as an action
(`uses: ./`) with the configured `source` and `package_name` inputs, and
asserts its declared `version` output is non-empty; it requires an
`action.yml` at the repo root. `smoke_trigger` defaults to
`push-dev` so live-upstream calls don't run on untrusted PR heads.

## Workflow boundaries

Gate and release workflows intentionally remain separate because they run at
different trust and permission boundaries:

| Layer | Trigger and authority | Responsibility |
| --- | --- | --- |
| `bos-universal-security.yml` (universal security) | Pull request / merge queue; read-mostly | Lint, tests, dependency review, code scanning, and policy checks before merge. |
| `bos-universal-marketplace-kicker.yml` | Marketplace PR, trusted-target PR, or manual release/metadata operation | Validate Actions, guard the workflow-free stable branch, promote releases, and refresh the repository About box. |
| `bos-universal-launchpad.yml` | Push, schedule, or manual caller; publish permissions | Monitor upstreams, run the release-blocking security scan, and coordinate delivery. |
| `bos-universal-sync-kicker.yml` | Config push, schedule, or manual dispatch; repository contents write | Resolve the target hub branch and invoke the reusable sync workflow. |
| `release.yml` (artifact release) | Called by Universal or another trusted workflow | Publish Docker, Balena, and GitHub Release artifacts for an already-approved version. |
| `release-promote.yml` (Marketplace promotion) | Operator-driven Marketplace caller | Promote an allowlisted source tree to the workflow-free stable branch and release it. |
| `release-hub.yml` (hub runtime release) | Hub-only manual workflow | Promote this hub's reusable runtime from the default branch to `main` and tag it. |

These release orchestrators should not be merged. Artifact release does not
mutate branches; Marketplace promotion deliberately removes disallowed files;
hub promotion must publish reusable workflows that Marketplace promotion
forbids. Their common publication stage is already consolidated in
[`github-release.yml`](.github/workflows/github-release.yml), while runner
validation, release-tag resolution, and release-context logic live in shared
actions. Hub and Marketplace promotion both use
[`resolve-release-tag`](.github/actions/shared/resolve-release-tag/action.yml),
with distinct first-release defaults (`v0.0.1` for the hub and `v0.1.0` for a
Marketplace action).

There is intentionally no standalone `bos-universal-release-kicker.yml`.
Release entry points are already owned by the workflow that has enough context
and authority to start them: Universal Launchpad calls
[`release.yml`](.github/workflows/release.yml) for artifact publication,
Marketplace repos use
[`bos-universal-marketplace-kicker.yml`](sync-files/workflows/bos-universal-marketplace-kicker.yml)
for operator-driven promotion, and this hub uses
[`release-hub.yml`](.github/workflows/release-hub.yml) for its own runtime
promotion. A generic release kicker would either duplicate those front doors
or need enough branching logic to blur their trust boundaries.

### Dev to production

For this hub, the production path is a manual dispatch of
[`release-hub.yml`](.github/workflows/release-hub.yml) from `dev`. It computes
or accepts a SemVer tag, builds the runtime allowlist, promotes that allowlist
to `main`, pushes the tag, publishes the GitHub Release, and optionally calls
[`repo-metadata-sync.yml`](.github/workflows/repo-metadata-sync.yml) against
the released tag. Consumers then use the promoted runtime from `@main` (or a
version tag).

For a Marketplace Action consumer, the production path is a manual
`operation: release` dispatch of the managed
[`bos-universal-marketplace-kicker.yml`](sync-files/workflows/bos-universal-marketplace-kicker.yml)
from the source branch. It validates trusted configuration, calls
[`release-promote.yml`](.github/workflows/release-promote.yml) to promote the
allowlist to the stable branch, publishes the GitHub Release, and optionally
calls [`repo-metadata-sync.yml`](.github/workflows/repo-metadata-sync.yml)
against the promoted tag. `operation: metadata` refreshes the same fields from
the configured stable branch without cutting another release.

For a product repository using Universal Launchpad, the launchpad owns the
artifact path: it calls [`release.yml`](.github/workflows/release.yml), which
publishes the configured Docker, Balena, and GitHub Release artifacts for an
already-approved version. It does not promote a `dev` branch to `main`.

There is deliberately no cross-workflow dependency requiring a Marketplace
release to wait for `release-hub.yml`. The hub release publishes this hub's
runtime; Marketplace promotion publishes a consumer Action's curated source
tree. Making one wait on the other would couple independent repositories,
create an unnecessary release deadlock, and would not prove that the
consumer's own validation passed. The Marketplace kicker already validates the
consumer before its release job; use protected environments or required
checks when an additional human approval gate is needed.

The Universal Launchpad retains a release-blocking scan. Scheduled and manual
releases need a fresh assessment even when no PR triggered the universal
security kicker. This is defense in depth at a different trust boundary, not a
second consumer security workflow.

The Marketplace kicker combines three event-scoped jobs in one managed file.
Its `pull_request_target` guard reads trusted default-branch configuration and
never executes PR-head code; its release job runs only by manual dispatch with
`contents: write`. Product-specific integration tests remain local.

## Consumer configuration

Create `.github/bos-universal-config.json` in the repository. A minimal
configuration can enable only the required stages:

```json
{
  "stages": {
    "docker": true,
    "balena": false,
    "github_release": true,
    "cloudflare_pages": false
  },
  "upstream": {
    "repo": "owner/project",
    "source": "github_release"
  },
  "docker": {
    "image_name": "project"
  },
  "gate": {
    "enable_node_lint": false,
    "enable_python_lint": false,
    "enable_shell_lint": true
  },
  "marketplace": {
    "enabled": false,
    "target_branch": "main",
    "allowlist_paths": ["action.yml", "README.md", "LICENSE"],
    "repo_metadata": {
      "enable": false,
      "homepage": "",
      "generate_topics": false
    }
  },
  "managed_file_sync": {
    "services": ["editorconfig"]
  }
}
```

The shared
[`universal-config`](.github/actions/shared/universal-config/action.yml) action
validates and normalizes this file. Missing optional objects fall back to the
reusable workflow defaults. Marketplace `allowlist_paths`, `blocked_paths`,
`required_paths`, and `extra_sync_paths` accept JSON arrays of non-empty
strings. The normalizer converts arrays to the newline-delimited workflow API
used by the Marketplace guard and promotion workflows.

### Config sections

Launchpad, Marketplace, security, and action-test workflows read this file
through the shared
[`universal-config`](.github/actions/shared/universal-config/action.yml)
action. Managed-file synchronization is the exception: the published
`bos-managed-file-sync-action` reads `managed_file_sync` directly. Settings can be authored as
flat top-level keys (as shown above, and required for anyone who already has
a config) or grouped under a named section per service; both layouts, and
any mix of the two, normalize identically. A flat key always wins over its
section-nested equivalent when both are present.

| Section (optional) | Flat top-level key(s) it groups | Consumed by |
| --- | --- | --- |
| `organization` | `organization` (already the flat key name) | every hub workflow, for runner topology and report policy |
| `security` | `gate` | `bos-universal-security.yml` |
| `managed_file_sync` | `managed_file_sync` | `bos-universal-sync.yml` and `bos-managed-file-sync-action` |
| `launchpad` | `upstream`, `stages`, `docker`, `scout`, `balena`, `companion_docker`, `release`, `platforms`, `security_scan`, `repo_metadata`, `cloudflare`, `triggers` | `bos-universal-launchpad.yml` |
| `marketplace` | `marketplace` (already the flat key name) | `bos-universal-marketplace.yml` |
| `general` | any key not owned by the shared workflow sections above (e.g. `action_test`) | whichever workflow reads that key |

Unlike the other sections, `general` hoists every key it contains rather than
a fixed allowlist — it's the landing spot for a new standalone service's
config (like `bos-universal-action-test.yml`'s `action_test` block) before it
earns its own named section.

### Organization section

`organization` is the one section that is not owned by a single workflow. It
carries cross-cutting policy — runner topology, per-workflow overrides, and
report behavior — so runner labels and timeouts are data instead of a literal
repeated in every job:

```json
{
  "organization": {
    "runners": {
      "default": "ubuntu-latest",
      "x64": "ubuntu-latest",
      "arm64": "ubuntu-24.04-arm"
    },
    "reporting": {
      "enable_job_summary": true,
      "enable_annotations": true,
      "title_prefix": "Blackout Secure",
      "fail_on": "fail"
    },
    "defaults": {
      "timeout_minutes": 30
    }
  }
}
```

Config consumers can override per-workflow timeouts by adding entries under `workflows`; any unspecified workflows inherit the `defaults.timeout_minutes`. For example:

```json
{
  "organization": {
    "workflows": {
      "security": { "timeout_minutes": 20 },
      "sync": { "runs_on": ["self-hosted", "Linux"] }
    }
  }
}
```

| Key | Default | Meaning |
| --- | --- | --- |
| `runners.default` | `ubuntu-latest` | Runner used by any workflow with no override. |
| `runners.x64` / `runners.arm64` | `runners.default` | Architecture-specific labels for multi-arch build jobs. |
| `reporting.enable_job_summary` | `true` | `false` suppresses the `$GITHUB_STEP_SUMMARY` report. |
| `reporting.enable_annotations` | `true` | `false` suppresses `::error::` / `::warning::` annotations. |
| `reporting.title_prefix` | `Blackout Secure` | Prefix applied to generated report titles. |
| `reporting.fail_on` | `fail` | `fail`, `warn`, or `never` — the severity tier that makes a report step exit non-zero. |
| `defaults.timeout_minutes` | `30` | Fallback job timeout. |
| `workflows.<name>.runs_on` | `runners.default` | Per-workflow runner override. |
| `workflows.<name>.timeout_minutes` | `defaults.timeout_minutes` | Per-workflow timeout override. |

Recognized workflow names are `security`, `sync`, `launchpad`, `marketplace`,
`action_test`, and `release`. Every one is always present in the normalized
`organization` output, so a workflow can read
`fromJSON(needs.resolve-config.outputs.org).workflows.<name>.runs_on`
unconditionally. A runner value may be a bare label or an array of labels;
both normalize to a value that `runs-on:` accepts directly, with no
`startsWith` guard in the workflow.

Two jobs deliberately keep a literal runner: each workflow's `resolve-config`
job (it bootstraps the runner topology) and the security workflow's `summary`
job (it is the required status check, so it must still publish a report when
config resolution itself failed).

### Run reporting

Every workflow reports through one shared surface,
[`job-report`](.github/actions/shared/job-report/action.yml), which renders the
same audit layout used by `bos-code-scanning-kit` and
`bos-managed-file-sync-action`: a verdict, a severity-count table, recommended
actions, a `Configuration used` disclosure, and grouped findings tables — plus
matching workflow annotations.

Findings are pure data, so a workflow only builds a JSON array and the report
surface stays identical everywhere:

```json
[
  {
    "id": "SG021",
    "severity": "fail",
    "control": "Code scanning + posture audit",
    "evidence": "job result: failure",
    "remediation": "Review the SARIF findings on the Security tab.",
    "group": "security"
  }
]
```

| Severity | Report label | Meaning |
| --- | --- | --- |
| `pass` | Pass | Control satisfied. |
| `warn` | Warning | Advisory drift; review recommended but not blocking. |
| `fail` | High | Required control failed. |
| `skip` | Not Assessed | Not evaluated; no verdict can be inferred. |

A skipped gate reports as `Not Assessed` rather than a pass, so a report never
implies coverage the run did not actually provide. The action's `outcome`
output (`success`, `warn`, `failure`) reflects severity only and does not
change with `fail_on`, so a caller can gate on the verdict independently of
whether the report step itself exited non-zero.

For example, the sample above can equivalently be written grouped:

```json
{
  "launchpad": {
    "stages": { "docker": true, "balena": false, "github_release": true },
    "upstream": { "repo": "owner/project", "source": "github_release" },
    "docker": { "image_name": "project" }
  },
  "security": {
    "enable_node_lint": false,
    "enable_python_lint": false,
    "enable_shell_lint": true
  },
  "marketplace": {
    "enabled": false,
    "target_branch": "main",
    "allowlist_paths": ["action.yml", "README.md", "LICENSE"]
  },
  "managed_file_sync": {
    "services": ["prettier"]
  },
  "general": {
    "action_test": { "python_versions": ["3.11", "3.12"] }
  }
}
```

## Managed files

[`bos-universal-sync.yml`](.github/workflows/bos-universal-sync.yml) is a thin
event and commit wrapper around the published
[`bos-managed-file-sync-action`](https://github.com/blackoutsecure/bos-managed-file-sync-action).
The published action reads the `managed_file_sync` block from
[`bos-universal-config.json`](.github/bos-universal-config.json), resolves its catalog,
and reconciles the working tree. Canonical hub templates live under
[`sync-files/`](sync-files/); this repository no longer contains a local
sync engine or service registry. The global hub policy enables the
organization-wide `shellcheck`, Security kicker, and Sync kicker defaults;
repository-specific kicker definitions are available globally but must be
selected by each repository that needs them. It also sets
`take_over_managed_files: true`, allowing organization-owned managed blocks to
replace competing managed blocks from another namespace.

Service ownership modes:

- **Section:** preserves user content outside managed markers.
- **File:** continuously replaces a file with its canonical template.
- **Init-if-missing:** creates a starter once and leaves later edits alone.

### Supported sync services

The published action's default catalog currently includes `baseline`,
`codeowners`, `common`, `dependabot_actions`, `editorconfig`, `lf_line_endings`,
`license`, `markdownlint`, `notice_apache2`, `prettier`, and `shellcheck`.
Repos can extend or override the catalog with `service_definitions` or a
separate catalog file; the published action validates service conflicts before
writing anything.

See [`sync-files/README.md`](sync-files/README.md) for template ownership
and branch policy.

### Minimum sync workflow policy

GitHub requires an event-trigger workflow in each repository; a configuration
file cannot schedule a cross-repository reusable workflow by itself. Enable
the published managed-file sync action wherever managed files should be
maintained. It runs independently from release, security, and Marketplace
workflows.

- delivery repositories can use the published action independently of
  `bos_launchpad`;
- repositories without delivery use the same managed-file sync wrapper;
- this hub uses the managed Sync kicker as the event front door, which calls
  the reusable [`bos-universal-sync.yml`](.github/workflows/bos-universal-sync.yml)
  workflow.

Removing every consumer workflow would require a separate organization-wide
GitHub App or PAT-backed controller with write access to all repositories. That
larger trust boundary is intentionally not part of managed-file sync.

## Marketplace Action enrollment

All repositories should enable `bos_universal_security`. Marketplace Action
repositories additionally enable `bos_universal_marketplace`. Keep
product-specific test workflows local. Remove generic lint, Marketplace CI,
guard, and release wrappers only after the required managed kickers have been
installed from the hub's canonical templates or from an organization catalog;
the public sync action's default catalog does not include these hub-specific
workflow files.

For `blackoutsecure/bos-upstream-watcher`, retain `.github/workflows/test.yml`
because it owns the 3-OS by 3-Python matrix and live npm smoke test. Remove
`.github/workflows/lint.yml`, `.github/workflows/marketplace-ci.yml`,
`.github/workflows/marketplace-guard.yml`, and `.github/workflows/release.yml`.
Use this consumer configuration:

```json
{
  "gate": {
    "enable_lint": true,
    "enable_python_lint": true,
    "python_version": "3.12",
    "enable_shell_lint": false
  },
  "marketplace": {
    "enabled": true,
    "target_branch": "main",
    "allowlist_paths": ["action.yml", "src", "README.md", "LICENSE", "NOTICE"],
    "blocked_paths": [
      ".github/workflows/",
      ".editorconfig",
      ".gitattributes",
      ".gitignore",
      ".markdownlint.yaml",
      "pyproject.toml",
      "test/"
    ],
    "required_paths": [
      ".github/dependabot.yml",
      "action.yml",
      "src",
      "LICENSE",
      "NOTICE",
      "README.md"
    ],
    "include_dependabot_config": true,
    "include_github_metadata": false
  },
  "managed_file_sync": {
    "services": [
      "common",
      "lf_line_endings",
      "dependabot_actions",
      "editorconfig",
      "shellcheck"
    ]
  }
}
```

Marketplace validation loads the hub's strict
[`marketplace-kit-global-config.json`](sync-files/config/marketplace-kit-global-config.json)
policy and then the repository's `marketplace_kit` block. The global policy
inherits organization community-health files and defers GHAS and Security
DevOps posture rules to `bos-code-scanning-kit`; set a per-repository
`marketplace_kit` field only to override a specific policy.

For `blackoutsecure/bos-code-scanning-kit`, merge this policy into its existing
`marketplace` object to replace the repository-local post-release workflow:

```json
{
  "marketplace": {
    "repo_metadata": {
      "enable": true,
      "homepage": "https://github.com/marketplace/actions/blackout-secure-code-scanning-kit",
      "generate_topics": true,
      "topics_fallback": "github-actions code-scanning security sarif posture-audit gitleaks actionlint shellcheck composite-action devsecops github-advanced-security"
    }
  }
}
```

The source branch defaults to `github.event.repository.default_branch`; only
the stable Marketplace target remains explicitly `main`. GitHub Actions does
not support expressions in reusable-workflow `uses:` refs, so managed callers
continue to consume promoted hub runtime at `@main`.

Marketplace metadata synchronization is opt-in through
`marketplace.repo_metadata.enable`. Real writes prefer `REPO_ADMIN_PAT` and
fall back to `RELEASE_PAT`; the selected token needs `Administration: write`
and `Metadata: read` on the consumer repository. With neither secret, the
metadata stage succeeds as a documented skip so an already-published release
is not retroactively failed. Dispatch `operation: metadata` with `dry_run:
true` to preview README-derived values using the scoped `GITHUB_TOKEN` without
granting repository-administration authority.

## Workflow API

Consumer repositories normally need only the managed
[`bos-universal-launchpad-kicker.yml`](sync-files/workflows/bos-universal-launchpad-kicker.yml),
[`bos-universal-security-kicker.yml`](sync-files/workflows/bos-universal-security-kicker.yml),
[`bos-universal-marketplace-kicker.yml`](sync-files/workflows/bos-universal-marketplace-kicker.yml),
and [`bos-universal-sync-kicker.yml`](sync-files/workflows/bos-universal-sync-kicker.yml)
callers. They read `.github/bos-universal-config.json` and invoke independent hub
entry points:

| Entry point | Purpose |
| --- | --- |
| [`bos-universal-launchpad.yml`](.github/workflows/bos-universal-launchpad.yml) | Coordinate trusted release, deployment, security, and metadata stages. |
| [`bos-universal-security.yml`](.github/workflows/bos-universal-security.yml) | Aggregate read-mostly PR and merge-queue validation into one required check. |
| [`bos-universal-sync.yml`](.github/workflows/bos-universal-sync.yml) | Reconcile managed files without invoking delivery or policy workflows. |

The following reusable workflows are stage modules, not additional files that
consumer repositories must install. Universal and the specialized promotion
workflows call them from the promoted `@main` runtime. They remain separate
because reusable jobs provide job-level permissions, outputs, matrices,
concurrency, and focused validation; inlining them would reduce file count but
would not reduce Actions jobs or runner usage.

| Workflow | Purpose |
| --- | --- |
| [`monitor-upstream-release.yml`](.github/workflows/monitor-upstream-release.yml) | Universal stage for upstream version discovery and tracking-state updates. |
| [`release.yml`](.github/workflows/release.yml) | Artifact-release composition stage; also supports direct tag-driven releases without upstream monitoring. |
| [`docker-build-push.yml`](.github/workflows/docker-build-push.yml) | Release leaf for multi-architecture Docker publication. |
| [`balena-block-publish.yml`](.github/workflows/balena-block-publish.yml) | Release leaf for Balena block publication. |
| [`github-release.yml`](.github/workflows/github-release.yml) | Shared publisher used by artifact, Marketplace, and hub releases. |
| [`deploy-cloudflare-pages.yml`](.github/workflows/deploy-cloudflare-pages.yml) | Universal stage for Cloudflare Pages build and deployment. |
| [`security-scan.yml`](.github/workflows/security-scan.yml) | Shared scanning stage used by trusted delivery and pre-merge validation. |
| [`repo-metadata-sync.yml`](.github/workflows/repo-metadata-sync.yml) | Shared About-box synchronization stage used by hub, Launchpad, and Marketplace publication. |
| [`bos-universal-sync.yml`](.github/workflows/bos-universal-sync.yml) | Thin event and commit wrapper around the published managed-file sync action. |

Specialized reusable entry points remain separate when their event or mutation
contract does not belong in Universal:

| Specialized workflow | Boundary |
| --- | --- |
| [`bos-universal-marketplace.yml`](.github/workflows/bos-universal-marketplace.yml) | Marketplace validation nested by the Marketplace kicker. |
| [`marketplace-repo-guard.yml`](.github/workflows/marketplace-repo-guard.yml) | Trusted-target enforcement for workflow-free Marketplace branches. |
| [`release-promote.yml`](.github/workflows/release-promote.yml) | Allowlisted Marketplace branch promotion. |
| [`balena-fleet-deploy.yml`](.github/workflows/balena-fleet-deploy.yml) | Per-fleet deployment matrix, distinct from block publication. |
| [`nginx-config-validate.yml`](.github/workflows/nginx-config-validate.yml) | Standalone Nginx configuration validation. |

## Shared actions

Reusable implementation components live under
[`.github/actions/`](.github/actions/) and include release-context and release-tag
resolution, Docker tag, build-argument, and manifest handling, Docker Scout
scanning, Balena rendering and publishing, Cloudflare project/zone helpers,
config normalization, and safe commit/push behavior.

Workflows should reuse these composites when behavior crosses more than one
workflow. Workflow-specific orchestration remains in the owning workflow.

### Balena deployment boundary

Balena publication is consolidated in
[`balena-publish`](.github/actions/shared/balena-publish/action.yml), while the
two reusable workflows retain separate caller contracts:

- [`balena-block-publish.yml`](.github/workflows/balena-block-publish.yml)
  resolves block versions and optionally renders or commits `balena.yml`;
- [`balena-fleet-deploy.yml`](.github/workflows/balena-fleet-deploy.yml)
  validates a target set and deploys it as a per-fleet matrix.

These workflows should not be merged into a mode-driven input surface. Their
shared operation is one `balena push`; their versioning, mutation, outputs, and
concurrency contracts are different.

The official
[`balena-io/deploy-to-balena-action@v2.3.1`](https://github.com/balena-io/deploy-to-balena-action/releases/tag/v2.3.1)
supports release reuse, layer caching, custom sources and Dockerfiles, registry
secrets, custom environments, draft/final release handling, and release
outputs. It remains a Docker action containing the x64 standalone CLI, so it
cannot run reliably inside containerized self-hosted runners whose workspace
path is not visible to the host Docker daemon. The shared composite invokes the
same supported `balena push` path without a nested container, installs the
native x64 or ARM64 CLI, and tracks the official action's CLI pin (`v24.1.4`).

## Required variables and secrets

Common organization or repository variables:

- `DEFAULT_RUNNER`: runner label or JSON label array;
- `RUNNER_X64`, `RUNNER_ARM64`: optional architecture-specific runners;
- `DOCKERHUB_NAMESPACE`, `BALENA_NAMESPACE`: publishing namespaces.

Common secrets are stage-dependent:

- Docker: `DOCKERHUB_USERNAME`, `DOCKERHUB_TOKEN`;
- Balena: `BALENA_API_TOKEN`;
- private upstreams: `UPSTREAM_TOKEN`;
- Cloudflare: `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`, optionally
  `CLOUDFLARE_ZONE_ID` and `CLOUDFLARE_PAGES_ADMIN_TOKEN`;
- administration/scanning: `REPO_ADMIN_PAT`, `SCANNING_PAT`;
- workflow-file propagation: `WORKFLOW_SYNC_PAT`;
- hub promotion: `RELEASE_PAT` when protected-branch bypass is required.

### Elevated posture scanning (`SCANNING_PAT`)

The code-scanning kit's posture probes (secret-scanning enablement, Dependabot
alerts enablement, push-protection visibility, branch-protection drift) need
Administration/security read access that the default `GITHUB_TOKEN` does not
have; without it those probes report indeterminate rather than `pass`/`warn`/
`fail`.

1. Create a PAT for the repositories to audit.
2. Preferred: a fine-grained PAT scoped only to the selected repositories.
3. Grant read access for repository metadata plus the security and
   administration surfaces the posture checks need (secret scanning alerts,
   Dependabot alerts, administration).
4. Fallback: a classic PAT with the `repo` scope.
5. Store the token as an Actions secret named `SCANNING_PAT` at the
   organization or repository level.
6. Re-run the workflow and confirm the previously indeterminate rows now show
   `pass`, `warn`, or `fail`.

No consumer wiring is required beyond creating the secret:

- [`bos-universal-security.yml`](.github/workflows/bos-universal-security.yml)'s
  `code-scan` job always passes
  `github_token: ${{ secrets.SCANNING_PAT || secrets.GITHUB_TOKEN }}`, and both
  managed kickers already forward `secrets.SCANNING_PAT` unconditionally — the
  PAT is used automatically the moment the secret exists, with no config
  change needed.
- [`bos-universal-launchpad.yml`](.github/workflows/bos-universal-launchpad.yml)'s
  security-scan stage additionally requires
  `launchpad.security_scan.use_advanced_pat: true` (or the flat
  `security_scan.use_advanced_pat` equivalent) in
  [`bos-universal-config.json`](.github/bos-universal-config.json) — this hub ships
  that flag enabled by default. It is a documented no-op when `SCANNING_PAT`
  is absent (the kit transparently falls back to `GITHUB_TOKEN`), so enabling
  it ahead of provisioning the secret is safe.

### Workflow-file propagation (`WORKFLOW_SYNC_PAT`)

`GITHUB_TOKEN` can never push changes to `.github/workflows/**` — this is a
hard GitHub platform restriction, not something a workflow's `permissions:`
block can grant. Without a PAT, `bos-universal-sync.yml` skips the five
managed kicker workflow files
(`bos-universal-action-test-kicker.yml`, `bos-universal-launchpad-kicker.yml`,
`bos-universal-marketplace-kicker.yml`, `bos-universal-security-kicker.yml`,
`bos-universal-sync-kicker.yml`) and syncs every other managed file normally.

1. Create a fine-grained PAT scoped to the repositories that install the
   kicker workflows, with the **Workflows** repository permission set to
   **Read and write** (a classic PAT with the `workflow` scope also works).
2. Store it as an Actions secret named `WORKFLOW_SYNC_PAT` at the
   organization or repository level.
3. No further wiring is required: the sync kicker already forwards
   `secrets.WORKFLOW_SYNC_PAT` unconditionally, and the reusable workflow
   starts propagating the kicker files as soon as the secret exists.

## Development and validation

Run the repository contract before promotion:

```bash
python3 scripts/test_universal_config_contract.py
python3 -m py_compile scripts/test_universal_config_contract.py
git diff --check
```

The contract verifies universal config and gate input forwarding, managed-service
output, branch/ref ownership, semantic runtime promotion, and internal README
links. [`lint.yml`](.github/workflows/lint.yml) runs it in CI.

## License

[Apache License 2.0](LICENSE)
