# Managed files

This directory contains canonical hub templates published with the repository.
Managed-file synchronization is provided by
[`bos-managed-file-sync-action`](https://github.com/blackoutsecure/bos-managed-file-sync-action).
Consumer repositories select its services in the `managed_file_sync` section
of `.github/bos-universal-config.json`. The global policy enables
organization-wide `shellcheck`, Security kicker, and Sync kicker defaults;
repository-specific kickers remain available as global service definitions
and must be selected by repositories that need them. The global policy also
sets `take_over_managed_files: true` so organization-owned blocks can replace
competing managed blocks.
This repository's own sync wrappers use `.github/bos-universal-config.json` as
the repo layer and pass the shared global sync policy inline through
`global_config_json`.
Settings may be authored as flat top-level keys or grouped under a named
section per service (`launchpad`, `marketplace`, `security`, plus a
`general` catch-all for anything else) — see the ["Config sections"](../README.md#config-sections)
table in the hub README for the full key mapping; this file doesn't repeat it.

## Active templates

Canonical workflow and dotfile templates live under [`workflows/`](workflows/)
and [`dotfiles/`](dotfiles/). These files remain the normal authoring and
review surface for hub-specific release content.

- [`bos-universal-launchpad-kicker.yml`](workflows/bos-universal-launchpad-kicker.yml)
  is the managed release/deploy caller. It reads `.github/bos-universal-config.json`
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
  event routing and ref resolution, then delegates to the promoted hub
  `bos-universal-sync.yml@dev`/`@main`. Repository maintenance never starts
  the delivery workflow.

These workflows are file-owned managed. Consumer repositories must not edit
them directly. A repository opts into the callers it needs, for example:

```json
{
  "managed_file_sync": {
    "services": [
      "shellcheck"
    ]
  }
}
```

Enable the published managed-file sync action alongside whichever other
managed callers the repository needs. GitHub still requires one event-trigger
workflow per repository; `.github/bos-universal-config.json` controls sync behavior
through `managed_file_sync`. The `editorconfig` service is provided by the
published action's default catalog; hub-specific templates remain under the
local `dotfiles/` source directory.

## Ownership modes

The sync engine supports three ownership modes:

- **Section:** replaces only content between managed markers.
- **Whole-file:** continuously overwrites the complete target file.
  Consumers install these thin managed callers, not the hub's stage-level
  reusable workflows. The callers invoke the promoted Universal and pre-merge
  gate entry points at `@main`; the hub keeps their internal stage composition
  centralized and independently testable.
- **Init-if-missing:** creates a starter file once and never overwrites it.

The published action's catalog is authoritative for generic services. Hub-only
workflow templates are maintained here and are not part of the public sync
catalog.

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

- the security and Marketplace kickers resolve which branch (`dev` or `main`)
  a run targets, then dispatch to same-named jobs whose `uses:` refs are pinned
  to `@dev` and `@main`; the sync kicker invokes the published action directly
  and does not depend on a hub runtime branch;
- the launchpad kicker only ever fires on `main` pushes, so it has no `dev`
  variant and always calls `@main`;
- `bos-universal-sync.yml` is the hub's callable-only reusable workflow for
  the published sync action; `bos-universal-sync-kicker.yml` owns all events;
- `release-hub.yml` cannot reference `@main` without breaking
  self-validation, so it uses local `./.github/...` references instead;
- the hub uses the published action for generic managed-file synchronization;
- runtime branch decisions inside actions use the caller repository's
  `github.event.repository.default_branch` where appropriate.

The hub promotion workflow publishes shared actions, this directory, core
documentation/license files, and workflows declaring `workflow_call`. Event-only
hub maintenance workflows remain on `dev`.
