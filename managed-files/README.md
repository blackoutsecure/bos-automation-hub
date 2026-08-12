# Managed files

This directory contains canonical files loaded by
[`sync-managed-files`](../.github/actions/sync-managed-files/). Consumer
repositories select services in their sync configuration; repository-specific
universal behavior belongs in `bos-universal-config.json`.
Settings may be authored as flat top-level keys or grouped under a named
section per service (`launchpad`, `marketplace`, `security`, `sync`, plus a
`general` catch-all for anything else) — see the ["Config sections"](../README.md#config-sections)
table in the hub README for the full key mapping; this file doesn't repeat it.

## Active templates

Canonical workflow and dotfile templates live under [`workflows/`](workflows/)
and [`dotfiles/`](dotfiles/). `sync.py` loads these files with inline fallbacks,
so the files in this directory are the normal authoring and review surface.

- [`bos-universal-launchpad-kicker.yml`](workflows/bos-universal-launchpad-kicker.yml)
  is the managed release/deploy caller. It reads `bos-universal-config.json`
  and calls the promoted hub runtime on `@main`.
- [`bos-universal-security-kicker.yml`](workflows/bos-universal-security-kicker.yml)
  is the managed PR and merge-queue caller for shared lint, dependency review,
  code scanning, and repository policy. Pin `security (dev) / Security summary`
  or `security (main) / Security summary` in branch protection, depending on
  the branch.
- [`bos-universal-marketplace-kicker.yml`](workflows/bos-universal-marketplace-kicker.yml)
  is installed only in Marketplace Action repositories. One event-routed file
  owns Marketplace validation, trusted stable-branch guarding, name checks,
  manual promotion/releases, and opt-in post-release or manual repository
  metadata refreshes.
- [`bos-universal-sync-kicker.yml`](workflows/bos-universal-sync-kicker.yml)
  is the independent scheduled/manual managed-file caller. It contains only
  event routing and calls the config-aware sync backend, which reads the
  `sync_files` block. Repository maintenance never starts the delivery
  workflow.

These workflows are whole-file managed. Consumer repositories must not edit
them directly.

Enable `bos_universal_sync` alongside whichever other managed callers the
repository needs. GitHub still requires one event-trigger workflow per
repository; `bos-universal-config.json` controls sync behavior and is the
only repo-level config used by the sync service. The convenience alias
`dotfiles` is supported in `sync.services` and expands to the standard
managed-dotfile bundle defined in the sync registry.

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
service there and set this in the JSON config:

```json
{
  "general": {
    "target_repo_role": "org-default-repo"
  }
}
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
- `bos-universal-sync.yml` is both the hub's own event trigger and the
  `workflow_call` backend in one file, so it never needs to call itself;
- `release-hub.yml` cannot reference `@main` without breaking
  self-validation, so it uses local `./.github/...` references instead;
- the hub otherwise dogfoods its own managed services like any consumer —
  `bos_universal_config`, `bos_universal_security`, and
  `bos_universal_sync` are enabled in the hub's own
  `bos-universal-config.json`; the latter two maintain the security and sync
  kicker workflows while the config service provisions the canonical config
  when needed;
- runtime branch decisions inside actions use the caller repository's
  `github.event.repository.default_branch` where appropriate.

The hub promotion workflow publishes shared actions, this directory, core
documentation/license files, and workflows declaring `workflow_call`. Event-only
hub maintenance workflows remain on `dev`.
