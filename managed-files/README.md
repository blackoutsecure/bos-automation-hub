# Managed files

This directory contains canonical files loaded by
[`sync-managed-files`](../.github/actions/sync-managed-files/). Consumer
repositories select services in their sync configuration; repository-specific
launchpad behavior belongs in `bos-launchpad-config.json`.

## Active templates

Canonical workflow and dotfile templates live under [`workflows/`](workflows/)
and [`dotfiles/`](dotfiles/). `sync.py` loads these files with inline fallbacks,
so the files in this directory are the normal authoring and review surface.

- [`bos-universal-launchpad-kicker.yml`](workflows/bos-universal-launchpad-kicker.yml)
  is the managed release/deploy caller. It reads `bos-launchpad-config.json`
  and calls the promoted hub runtime on `@main`.
- [`bos-universal-security-kicker.yml`](workflows/bos-universal-security-kicker.yml)
  is the managed PR and merge-queue caller for shared lint, dependency review,
  code scanning, and repository policy. Pin `security / Security summary` in
  branch protection.
- [`bos-universal-marketplace-kicker.yml`](workflows/bos-universal-marketplace-kicker.yml)
  is installed only in Marketplace Action repositories. One event-routed file
  owns Marketplace validation, trusted stable-branch guarding, name checks,
  and manual promotion/releases.
- [`bos-universal-sync-kicker.yml`](workflows/bos-universal-sync-kicker.yml)
  is the independent scheduled/manual managed-file caller. It contains only
  event routing and calls the config-aware sync backend, which reads the
  `sync_files` block. Repository maintenance never starts the delivery
  workflow.

These workflows are whole-file managed. Consumer repositories must not edit
them directly.

Enable `bos_universal_sync` alongside whichever other managed callers the
repository needs. GitHub still requires one event-trigger workflow per
repository; `bos-launchpad-config.json` controls sync behavior but cannot
itself trigger a reusable workflow.

## Ownership modes

The sync engine supports three ownership modes:

- **Section:** replaces only content between managed markers.
- **Whole-file:** continuously overwrites the complete target file.
  Consumers install these thin managed callers, not the hub's stage-level
  reusable workflows. The callers invoke the promoted Universal and pre-merge
  gate entry points at `@main`; the hub keeps their internal stage composition
  centralized and independently testable.
- **Init-if-missing:** creates a starter file once and never overwrites it.

The service registry in
[`sync.py`](../.github/actions/sync-managed-files/sync.py) is authoritative for
which files each service owns.

## Organization defaults

[`community-health/`](community-health/), [`github-meta/`](github-meta/), and
[`org-profile/`](org-profile/) are canonical sources for the dedicated
`blackoutsecure/.github` repository. Enable the whole-file `org_defaults`
service there and set this in `bos-managed-files.yaml`:

```yaml
target_repo_role: org-default-repo
```

The role check prevents these files, especially `profile/README.md`, from
being copied into normal product repositories. Product repositories use the
default `target_repo_role: consumer` and inherit community-health files and
templates from GitHub's organization repository.

The organization-default targets are the repository root files
`CODE_OF_CONDUCT.md`, `CONTRIBUTING.md`, `SECURITY.md`, and `SUPPORT.md`;
`.github/FUNDING.yml`; `.github/PULL_REQUEST_TEMPLATE.md`;
`.github/ISSUE_TEMPLATE/*`; and `profile/README.md`.

## Branch policy

`dev` is the hub development branch; `main` is the promoted stable runtime.
GitHub Actions does not allow expressions in `uses:` references, so branch
targeting is resolved ahead of time and encoded as static per-branch jobs:

- the security, Marketplace, and sync kickers each resolve which branch
  (`dev` or `main`) a run targets, then dispatch to a same-named job pair
  (e.g. `security-dev` / `security-main`) whose `uses:` refs are pinned to
  `@dev` and `@main` respectively — a `dev`-targeted run exercises the hub's
  current unreleased source, a `main`-targeted run exercises the promoted
  stable runtime;
- the launchpad kicker only ever fires on `main` pushes, so it has no `dev`
  variant and always calls `@main`;
- workflows that cannot reference `@main` without breaking self-validation
  (e.g. `sync-managed-files.yml`, `release-hub.yml`) use local
  `./.github/...` references instead;
- the hub otherwise dogfoods its own managed services like any consumer —
  e.g. `bos_universal_security` is enabled in the hub's own
  `bos-launchpad-config.json` and synced to
  `.github/workflows/bos-universal-security-kicker.yml`;
- runtime branch decisions inside actions use the caller repository's
  `github.event.repository.default_branch` where appropriate.

The hub promotion workflow publishes shared actions, this directory, core
documentation/license files, and workflows declaring `workflow_call`. Event-only
hub maintenance workflows remain on `dev`.
