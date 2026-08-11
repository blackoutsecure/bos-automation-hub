# Blackout Secure Automation Hub

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

[`release-hub.yml`](.github/workflows/release-hub.yml) promotes shared actions,
managed templates, `LICENSE`, this README, and every workflow declaring
`workflow_call`. Event-only maintenance workflows stay on `dev` automatically.

## Universal launchpad

[`bos-universal-launchpad.yml`](.github/workflows/bos-universal-launchpad.yml)
is the release and deployment orchestrator. Its managed consumer caller is
[`bos-universal-launchpad-kicker.yml`](managed-files/workflows/bos-universal-launchpad-kicker.yml).

The launchpad can compose:

- upstream release monitoring;
- multi-architecture Docker publishing and Docker Scout scanning;
- Balena block and fleet publishing;
- GitHub Releases;
- Cloudflare Pages deployment and generated site metadata;
- security scanning;
- repository metadata updates.

Consumer behavior is data-driven through `bos-launchpad-config.json`; managed
workflow files are not edited in consumer repositories.

## Universal security

[`bos-universal-security.yml`](.github/workflows/bos-universal-security.yml) is
the reusable universal PR and merge-queue security/policy workflow. Its
managed caller is
[`bos-universal-security-kicker.yml`](managed-files/workflows/bos-universal-security-kicker.yml).

The required check is `security (dev) / Security summary` or
`security (main) / Security summary`, depending on which branch a run
targets. It aggregates:

- workflow, Markdown, YAML, and Shell lint;
- optional Node checks (ESLint and Prettier);
- optional Python checks (Ruff and pytest);
- optional Shell checks (ShellCheck and Bats);
- dependency review and code scanning;
- pinned-action enforcement;
- README-header and PR-title checks.

Marketplace-specific validation is intentionally excluded. Marketplace Action
repositories add the managed
[`bos-universal-marketplace-kicker.yml`](managed-files/workflows/bos-universal-marketplace-kicker.yml),
which owns Marketplace validation, stable-branch guarding, and promotion.

## Universal sync

[`sync-managed-files.yml`](.github/workflows/sync-managed-files.yml) is the
single config-aware managed-file backend. It also handles this hub's local
schedule, config-change, and manual events. Its consumer front door is
[`bos-universal-sync-kicker.yml`](managed-files/workflows/bos-universal-sync-kicker.yml).
It runs independently on config changes, schedule, or manual dispatch and
never traverses the release, security, or Marketplace workflows.

## Workflow boundaries

Gate and release workflows intentionally remain separate because they run at
different trust and permission boundaries:

| Layer | Trigger and authority | Responsibility |
| --- | --- | --- |
| `bos-universal-security.yml` (universal security) | Pull request / merge queue; read-mostly | Lint, tests, dependency review, code scanning, and policy checks before merge. |
| `bos-universal-marketplace-kicker.yml` | Marketplace PR, trusted-target PR, or manual release | Validate Actions, guard the workflow-free stable branch, and promote releases. |
| `bos-universal-launchpad.yml` | Push, schedule, or manual caller; publish permissions | Monitor upstreams, run the release-blocking security scan, and coordinate delivery. |
| `bos-universal-sync-kicker.yml` | Config push, schedule, or manual dispatch; repository contents write | Reconcile only the managed files selected by `sync_files.services`. |
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

The Universal Launchpad retains a release-blocking scan. Scheduled and manual
releases need a fresh assessment even when no PR triggered the universal
security kicker. This is defense in depth at a different trust boundary, not a
second consumer security workflow.

The Marketplace kicker combines three event-scoped jobs in one managed file.
Its `pull_request_target` guard reads trusted default-branch configuration and
never executes PR-head code; its release job runs only by manual dispatch with
`contents: write`. Product-specific integration tests remain local.

## Consumer configuration

Create `bos-launchpad-config.json` at the consumer repository root. A minimal
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
    "allowlist_paths": ["action.yml", "README.md", "LICENSE"]
  },
  "sync_files": {
    "services": [
      "common",
      "lf_line_endings",
      "dependabot_actions",
      "bos_launchpad",
      "bos_universal_security",
      "bos_universal_marketplace"
    ],
    "mode": "commit"
  }
}
```

The shared
[`launchpad-config`](.github/actions/shared/launchpad-config/action.yml) action
validates and normalizes this file. Missing optional objects fall back to the
reusable workflow defaults. Marketplace `allowlist_paths`, `blocked_paths`,
`required_paths`, and `extra_sync_paths` accept JSON arrays of non-empty
strings. Legacy newline-delimited strings remain supported; the normalizer
converts arrays to the newline-delimited workflow API used by the Marketplace
guard and promotion workflows.

## Managed files

[`sync-managed-files.yml`](.github/workflows/sync-managed-files.yml) exposes
one reusable orchestration backend. It resolves explicit caller inputs when
provided and otherwise reads the `sync_files` block from
[`bos-launchpad-config.json`](bos-launchpad-config.json). The
[`sync-managed-files`](.github/actions/sync-managed-files/action.yml) composite
action remains the file-mutation engine. Canonical on-disk templates live
under [`managed-files/`](managed-files/); the service registry in
[`sync.py`](.github/actions/sync-managed-files/sync.py) is authoritative.

Service ownership modes:

- **Section:** preserves user content outside managed markers.
- **Whole-file:** continuously enforces a canonical file.
- **Init-if-missing:** creates a starter once and leaves later edits alone.

Primary services include:

- repository policy: `common`, `docker`, `balena`, `node`, `python`,
  `lf_line_endings`;
- dependency automation: `dependabot_actions`, `dependabot_npm`,
  `dependabot_pip`;
- canonical files: `logger`, `shellcheckrc`, `markdownlint`, `prettier`,
  `wranglerignore`, `humans`;
- universal callers: `bos_launchpad`, `bos_universal_security`,
  `bos_universal_marketplace`, `bos_universal_sync`;
- initialization: `bos_launchpad_config`, `gha_sync_drift_check`, `license`,
  `notice_apache2`, `codeowners`;
- organization repository only: `org_defaults`, gated by
  `target_repo_role: org-default-repo` in `bos-managed-files.yaml`.

See [`managed-files/README.md`](managed-files/README.md) for template ownership
and branch policy.

### Minimum sync workflow policy

GitHub requires an event-trigger workflow in each repository; a configuration
file cannot schedule a cross-repository reusable workflow by itself. Enable
`bos_universal_sync` wherever managed files should be maintained. It calls the
lightweight sync reusable directly and runs independently from release,
security, and Marketplace workflows.

- delivery repositories can enable both `bos_launchpad` and
  `bos_universal_sync` without duplicate sync execution;
- repositories without delivery still use the same `bos_universal_sync`
  caller;
- this hub runs the same
  [`sync-managed-files.yml`](.github/workflows/sync-managed-files.yml) backend
  directly from its own events, so there is no second orchestration workflow.

Removing every consumer workflow would require a separate organization-wide
GitHub App or PAT-backed controller with write access to all repositories. That
larger trust boundary is intentionally not part of managed-file sync.

## Marketplace Action enrollment

All repositories should enable `bos_universal_security`. Marketplace Action
repositories additionally enable `bos_universal_marketplace`. Keep
product-specific test workflows local; remove generic
lint, Marketplace CI, guard, and release wrappers once sync creates the managed
kickers.

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
      "requirements-dev.txt",
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
  "sync_files": {
    "services": [
      "common",
      "lf_line_endings",
      "python",
      "dependabot_actions",
      "dependabot_pip",
      "bos_launchpad_config",
      "bos_universal_security",
      "bos_universal_marketplace",
      "bos_universal_sync"
    ],
    "mode": "commit"
  }
}
```

The source branch defaults to `github.event.repository.default_branch`; only
the stable Marketplace target remains explicitly `main`. GitHub Actions does
not support expressions in reusable-workflow `uses:` refs, so managed callers
continue to consume promoted hub runtime at `@main`.

## Workflow API

Consumer repositories normally need only the managed
[`bos-universal-launchpad-kicker.yml`](managed-files/workflows/bos-universal-launchpad-kicker.yml)
[`bos-universal-security-kicker.yml`](managed-files/workflows/bos-universal-security-kicker.yml),
and [`bos-universal-sync-kicker.yml`](managed-files/workflows/bos-universal-sync-kicker.yml)
callers. They read `bos-launchpad-config.json` and invoke independent hub
entry points:

| Entry point | Purpose |
| --- | --- |
| [`bos-universal-launchpad.yml`](.github/workflows/bos-universal-launchpad.yml) | Coordinate trusted release, deployment, security, and metadata stages. |
| [`bos-universal-security.yml`](.github/workflows/bos-universal-security.yml) | Aggregate read-mostly PR and merge-queue validation into one required check. |
| [`sync-managed-files.yml`](.github/workflows/sync-managed-files.yml) | Reconcile managed files without invoking delivery or policy workflows. |

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
| [`sync-managed-files.yml`](.github/workflows/sync-managed-files.yml) | Shared managed-file engine called directly by the dedicated universal sync kicker. |

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
- hub promotion: `RELEASE_PAT` when protected-branch bypass is required.

## Development and validation

Run the repository contract before promotion:

```bash
python3 scripts/test_launchpad_contract.py
python3 -m py_compile \
  .github/actions/sync-managed-files/sync.py \
  scripts/test_launchpad_contract.py
git diff --check
```

The contract verifies launchpad and gate input forwarding, managed-service
output, branch/ref ownership, semantic runtime promotion, and internal README
links. [`lint.yml`](.github/workflows/lint.yml) runs it in CI.

## License

[Apache License 2.0](LICENSE)
