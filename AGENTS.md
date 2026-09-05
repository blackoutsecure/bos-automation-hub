# AGENTS.md

This file provides guidance to agents when working with code in this repository.

## What this is

`bos-automation-hub` is the centre of the `blackoutsecure` organization. It holds every
reusable `workflow_call` workflow, every shared composite action, the canonical managed
files synced into every other repository, and the global configuration tier that sits
between each downstream tool's bundled defaults and each repository's own
`.github/bos-universal-config.json`. Nothing here is a product; everything here is
infrastructure that other repositories consume by reference.

The blast radius is the whole organization. A commit to `sync-files/` rewrites files in
every subscribed repository on the next sync run — `LICENSE`, `.editorconfig`,
`.gitignore`, `.shellcheckrc`, `.yamllint.yml`, `README.md` blocks, and the managed
`.github/workflows/bos-universal-gatekeeper-kicker.yml` front door. A change to a
`workflow_call` input or secret contract breaks every caller that passes it. A change to
`sync-files/config/*.json` changes audit and gate policy for every repository at once,
without any of those repositories changing a line. Treat this as production infrastructure
with dozens of live consumers.

The stack is deliberately thin: GitHub Actions YAML (27 workflows), composite actions in
bash plus embedded or sibling Python, and Python 3 stdlib-only scripts under `scripts/`.
There is no application runtime, package manifest, lockfile, build step, or server. The
only non-stdlib dependency is PyYAML, needed by `scripts/test_check_readme_header.sh` to
extract a step body out of a composite `action.yml`. Validation is by offline contract
test: `scripts/test_*.py` assert schema shape, input and secret forwarding, branch and ref
ownership, pin format, and documentation links, never live GitHub behaviour, so CI never
depends on the Releases API or a downstream repository being reachable.

Consumers reference the promoted `@main` runtime, not `dev`. As of this writing `main` is
behind `dev` in layout: it still carries `.github/actions/shared/` and
`bos-universal-launchpad.yml`, which is why cross-repo `uses:` lines in `dev` workflows
point at `.../.github/actions/shared/<name>@main` while the `dev` tree has those actions
flattened to `.github/actions/<name>/`. Resolve any `uses:` path against the branch it
targets, not against the working tree.

## Commands

```bash
cd /Volumes/devbox/repos/blackoutsecure/bos-automation-hub

# Offline contract tests. These four are exactly what lint.yml runs, in this order.
python3 scripts/test_universal_config_contract.py
python3 scripts/test_sync_action_pins.py
python3 scripts/test_release_validation.py
python3 scripts/test_gatekeeper_app_setup.py

# Not in lint.yml; run by hand after touching the README-header composite.
# Needs bash, python3 + PyYAML, and (optionally) shellcheck.
bash scripts/test_check_readme_header.sh

# First-party action pins.
python3 scripts/sync_action_pins.py --check          # report drift, exit 1 when stale
python3 scripts/sync_action_pins.py --check --json
python3 scripts/sync_action_pins.py --write          # rewrite pins in place

# OSI licence catalogue (SPDX json/licenses.json must be fetched first).
python3 scripts/build_osi_catalogue.py --spdx <path-to>/licenses.json --check
python3 scripts/build_osi_catalogue.py --spdx <path-to>/licenses.json
python3 scripts/build_osi_catalogue.py --derive-overlay

# Syntax / whitespace sanity, as documented in README.md.
python3 -m py_compile scripts/test_universal_config_contract.py
git diff --check

# Linters. lint.yml runs these through the bos-marketplace-kit `lint` composite;
# locally, invoke them directly against the same committed config files.
actionlint
yamllint -c .yamllint.yml .
markdownlint-cli2 "**/*.md"
shellcheck scripts/test_check_readme_header.sh
```

`brew install actionlint shellcheck` covers the two binaries, per
`.github/actions/README.md`.

## Validating changes

`.github/workflows/lint.yml` is the only always-on CI here. It fires on `push` to `main`
and `dev`, on `pull_request`, and on `workflow_dispatch`, scoped to a path list covering
`**/*.md`, `**/*.yml`, `**/*.yaml`, `**/*.sh`, `.shellcheckrc`,
`.github/bos-universal-config.json`, the `scripts/` files, `tools/gatekeeper-app-setup/**`,
`.github/actions/release-validation/**`, and
`sync-files/config/release-validation-global-config.json`. Three jobs run:

1. `universal config contract` — the four `python3 scripts/test_*.py` commands above.
2. `actionlint` — the kit lint composite with `run_actionlint: "true"` and
   `severity: "fail"`. This is the blocking lint job; its name is preserved so branch
   protection checks named `actionlint` keep matching.
3. `markdown + yaml + shell (advisory)` — the same composite with markdownlint, yamllint,
   and shellcheck at `severity: "warn"`. Findings annotate the job summary only.

`.github/workflows/bos-hub-gatekeeper-kicker.yml` additionally drives the hub's own
security, sync, and metadata maintenance on a six-hourly cron, on pushes touching
`.github/bos-universal-config.json`, `.github/actions/**`, `bos-universal-gatekeeper.yml`,
`repo-metadata-sync.yml`, or `README.md`, and on manual dispatch.

Locally, narrowest first: run the single `scripts/test_*.py` covering what you touched,
then `actionlint`, then the other three contract tests, then `yamllint`/`markdownlint` if
you edited YAML or Markdown, then `git diff --check`.

Be explicit about what these prove. `test_universal_config_contract.py` validates universal
config and gate input forwarding, managed-service output, branch and ref ownership,
semantic runtime promotion, and internal README links. `test_sync_action_pins.py` asserts
pin _shape_ — an immutable 40-character SHA plus a trailing `# vX.Y.Z` comment — and the
SemVer ranking. `test_release_validation.py` exercises the release-validation engine's
pass/fail behaviour without network access. `test_gatekeeper_app_setup.py` tests the
loopback helper's server module. None of them start a runner, call GitHub, or execute a
workflow. A change to a reusable workflow, a composite action's runtime behaviour, or a
managed sync payload must be exercised by a real caller — a dispatch of the hub's own
gatekeeper kicker, or a `hub_ref: dev` run from a consumer — before promotion to `main`.

## Architecture

```text
README.md                              Canonical prose: branch model, gatekeeper, secrets strategy
LICENSE                                Apache-2.0; also the source for the license_service payload
.github/bos-universal-config.json      The hub's OWN repo-tier config, plus the action_pins manifest
.github/CODEOWNERS                      Placeholder; no owners assigned yet
.github/dependabot.yml                  github-actions + pip, both inside managed-file-sync blocks
.github/workflows/                      27 workflows: reusable runtime + hub-only maintenance
.github/actions/                        32 composite actions (dev layout; `main` still nests under shared/)
.github/actions/README.md               Authoring rules for composite actions — read before editing one
sync-files/                             The managed-file payload pushed into every subscribed repo
sync-files/README.md                    Template ownership, ownership modes, branch policy
sync-files/workflows/                   The single managed kicker template
sync-files/config/                      12 global-tier config files, one per downstream tool
sync-files/community-health/            CODE_OF_CONDUCT, CONTRIBUTING, SECURITY, SUPPORT, FUNDING.yml
sync-files/github-meta/                 PR template and ISSUE_TEMPLATE/
sync-files/legal/                       LICENSE, osi-licenses.json, osi-overlay.json, osi_catalogue.py
sync-files/org-profile/README.md        profile/README.md for blackoutsecure/.github
scripts/                                Contract tests, pin bumper, catalogue builder, App launcher
scripts/marketplace-repo/               Declarative org ruleset template; no script applies it
tools/gatekeeper-app-setup/             Loopback-only GitHub App manifest helper (index.html + server.py)
.markdownlint.yaml .yamllint.yml .shellcheckrc .editorconfig .gitattributes  Lint/EOL config, all managed
```

### Branch model and promotion

`dev` is the default and development branch; all work lands there. `main` is the promoted
stable runtime consumers reference as `@main`, alongside `vX.Y.Z` and floating `vX` tags.
Never push to `main` and never move a tag by hand.

GitHub Actions does not allow expressions in `uses:` references, so branch selection is
resolved by ownership rather than generated ref strings: managed consumer callers pin
`blackoutsecure/bos-automation-hub/...@main`; hub-only self-validation uses local
`./.github/actions/...` references; runtime branch decisions use
`github.event.repository.default_branch` when the _caller's_ branch is meant.
`bos-universal-security.yml` and `bos-universal-sync.yml` expose a `hub_ref` input
defaulting to `auto`, which follows a pull request or merge-group base branch, then the
current `dev` ref, and otherwise selects `main`; pass `hub_ref: dev` or `hub_ref: main`
only as a deliberate override.

`release-hub.yml` is the hub's own promotion path, manual dispatch only. It computes or
accepts a SemVer tag, runs release validation against the default-branch candidate, then
materialises `main`'s next tree in a worktree from a runtime allowlist: `.github/actions`,
`sync-files`, `LICENSE`, `README.md`, plus every workflow matched by
`grep -lE '^  workflow_call:' .github/workflows/*.yml`. Event-only maintenance workflows
and maintainer tooling stay on `dev` by construction, and the promote commit's parent is
the previous `main` HEAD so consumers see a fast-forward. Adding `workflow_call:` to a
hub-only maintenance workflow silently promotes it to the public runtime; that is why
`bos-hub-managed-sync-propagate.yml` dispatches `bos-org-kicker-fanout.yml` rather than
calling it.

### Reusable workflows

Universal gates, all `workflow_call`:

- `bos-universal-gatekeeper.yml` — the release/deploy/security/metadata orchestrator; composes upstream monitoring, Docker, Balena, GitHub Release, Cloudflare Pages, security scan, and metadata stages.
- `bos-universal-security.yml` — the single required pre-merge check (`security (dev|main) / Security summary`); jobs are `lint`, `node-lint`, `python-lint`, `shell-lint`, `dependency-review`, `code-scan`, `pinned-actions`, `readme-header`, `pr-title`, `summary`.
- `bos-universal-sync.yml` — thin wrapper around the published `bos-managed-file-sync-action`; named "Managed File Sync" because it only does one thing, filename kept for `uses:` stability.
- `bos-universal-action-test.yml` — reusable pytest matrix plus an optional live-upstream smoke test for Action repositories with a Python implementation.
- `bos-universal-marketplace.yml` — Marketplace manifest, branding, and rule validation via `bos-marketplace-kit`.
- `bos-universal-release-validation.yml` — final read-only release-readiness gate: reruns the candidate's tests/build at the exact publish ref and rejects uncommitted generated output.

Release and deploy stage modules, all `workflow_call`:

- `release.yml` — artifact-release composition (Docker to Balena to GitHub Release).
- `release-promote.yml` — allowlisted `dev` to `main` promotion for Marketplace Action repos.
- `github-release.yml` — the shared GitHub Release publisher used by artifact, Marketplace, and hub releases.
- `docker-build-push.yml` — multi-architecture Docker publication leaf.
- `balena-block-publish.yml` — Balena block publication, with optional `balena.yml` rendering.
- `balena-fleet-deploy.yml` — per-fleet deployment matrix, distinct from block publication.
- `deploy-cloudflare-pages.yml` — Cloudflare Pages build and deploy, including the site-generator compliance audit wiring.
- `security-scan.yml` — shared scanning stage used by trusted delivery and pre-merge validation.
- `repo-metadata-sync.yml` — About-box description/homepage/topics sync via `bos-repo-about-sync-action`.
- `monitor-upstream-release.yml` — upstream version discovery and tracker-state updates via `bos-upstream-watcher`.
- `marketplace-repo-guard.yml` — trusted-target enforcement for workflow-free Marketplace branches.
- `nginx-config-validate.yml` — standalone `nginx -t` validation for an in-repo config tree.

Hub-only orchestration and maintenance, never promoted to `main`:

- `bos-hub-gatekeeper-kicker.yml` — the hub's own kicker (`schedule`, `push`, `workflow_dispatch`).
- `bos-org-kicker-fanout.yml` — dispatches a universal kicker across every participating org repository; `seed_missing` opens a PR installing the kicker where none exists.
- `bos-hub-managed-sync-propagate.yml` — on `push` to `sync-files/**`, dispatches the fan-out so managed-file changes reach consumers immediately.
- `release-hub.yml` — hub runtime promotion, manual dispatch only.
- `sync-action-pins.yml` — daily; resolves the newest tag for each `action_pins` entry, rewrites stale SHAs, opens a PR.
- `osi-license-catalogue-refresh.yml` — monthly; rebuilds `sync-files/legal/osi-licenses.json` from SPDX and opens a PR.
- `gatewall-smoke-test.yml` — non-mutating verification of each Gatewall permission subset.
- `openwrt-readsb-wiedehopf-bump.yml` — proposes upstream `openwrt/packages` bumps from a bot-owned fork; schedule currently disabled.
- `lint.yml` — the CI gate described above.

### Shared composite actions

Config and routing: `universal-config` (reads and normalizes `.github/bos-universal-config.json`), `resolve-hub-ref` (dev/main routing for managed kickers), `preflight-runner-config` (validate `vars.DEFAULT_RUNNER`/`RUNNER_X64`/`RUNNER_ARM64` before downstream `runs-on:` evaluates).

Release: `resolve-release-tag`, `resolve-release-context`, `resolve-upstream-version`, `resolve-latest-action-ref`, `render-release-notes`, `release-validation`, `commit-and-push`.

Docker and Balena: `resolve-docker-image-tags`, `compose-docker-build-args`, `docker-multiarch-manifest`, `docker-scout-scan`, `docker-scout-enable-repo`, `sync-dockerhub-description`, `balena-publish`, `render-balena-yml`.

Cloudflare and site: `cloudflare-project-exists`, `cloudflare-resolve-id`, `cloudflare-zone-purge`, `cloudflare-pages-compose-command`, `cf-pages-headers-generate`, `cf-pages-redirects-generate`, `stage-deploy-dir`.

Policy and reporting: `check-pinned-actions`, `check-readme-header`, `job-report` (the shared Markdown/HTML/JSON audit surface), `ai-remediation-pr`, `nginx-config-validate`, `automation-app-token`.

`.github/actions/README.md` states the authoring rules: inputs go through `env:` and never
into a `run:` body as `${{ … }}`; every bash `run:` starts with `set -euo pipefail`;
validation helpers stay inlined per action; Python over roughly 20 lines moves to a sibling
`.py` invoked as `python3 "${GITHUB_ACTION_PATH}/script.py"`; third-party actions are
SHA-pinned with a trailing version comment; `persist-credentials: false` on every
`actions/checkout` that does not push.

### Managed file sync payload

`sync-files/` is the payload, not a reference copy. The published
`bos-managed-file-sync-action` reads the `managed_file_sync` block from each consumer's
`.github/bos-universal-config.json`, layers the hub's
`sync-files/config/managed-file-sync-global-config.json` on top of its own bundled
defaults, and reconciles that repository's working tree against these files. Editing a file
here rewrites that file in every subscribed repository on the next sync run.

Three ownership modes: `file` continuously overwrites the whole target; `block` replaces
only the region between managed markers, preserving surrounding content byte for byte;
init-if-missing creates a starter once and never overwrites it. The delimiter contract for
`block` mode is two marker lines in the target file's comment syntax:

```text
# >>> managed-file-sync:<service> >>>
...canonical content...
# <<< managed-file-sync:<service> <<<
```

Those exact strings are how every downstream block is located. Changing the format orphans
every existing block in every repository.

`file_patches` lets a small deviation from an inherited service skip redefining that
service. The global policy uses it against the published `common` service's `.gitignore`:
it removes `.vscode/*` and `!.vscode/extensions.json`, then appends `.vscode/`, private-key
and cert patterns (`*.pem`, `*.key`, `*.crt`, `*.p12`, `*.pfx`, `secrets.*`, `.secrets/`,
with `!SECRETS.md`), and local tool logs. The global policy also sets
`take_over_managed_files: true` (org blocks may replace a competing namespace's block),
`cleanup_duplicate_lines: true`, and `managed_files_path: sync-files`, with a default
service list of `shellcheck`, `yamllint`, `coverage_artifacts`, `license_service`,
`security_readme_pointer`, `bos_universal_gatekeeper_kicker`.

Hub-defined services and the paths they write downstream:

| Service                           | Mode  | Writes                                                                                                                                                                                                               |
| --------------------------------- | ----- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `license_service`                 | file  | `LICENSE` from `legal/LICENSE` (Apache-2.0); enabled by default                                                                                                                                                      |
| `proprietary_license_service`     | file  | `LICENSE` from `legal/LICENSE-PROPRIETARY`; opt-in, and requires `disabled_services: ["license_service"]` because both claim `LICENSE` in `file` mode                                                                |
| `license_catalogue_service`       | file  | `src/osi-licenses.json` and `src/osi_catalogue.py` — a versioned pair that must move together                                                                                                                        |
| `bos_universal_gatekeeper_kicker` | file  | `.github/workflows/bos-universal-gatekeeper-kicker.yml` from `workflows/`                                                                                                                                            |
| `security_readme_pointer`         | block | a `## Security & secrets` block in `README.md` pointing at the hub's secrets section                                                                                                                                 |
| `python_ecosystem`                | block | Python ignore rules in `.gitignore`                                                                                                                                                                                  |
| `org_defaults`                    | file  | `CODE_OF_CONDUCT.md`, `CONTRIBUTING.md`, `SECURITY.md`, `SUPPORT.md`, `.github/FUNDING.yml`, `.github/PULL_REQUEST_TEMPLATE.md`, `.github/ISSUE_TEMPLATE/*`, `profile/README.md` — for `blackoutsecure/.github` only |

Generic dotfile services (`baseline`, `codeowners`, `common`, `dependabot_actions`,
`editorconfig`, `lf_line_endings`, `markdownlint`, `notice_apache2`, `prettier`,
`shellcheck`, `yamllint`, `coverage_artifacts`) come from the published action's own
bundled catalog, not from this tree. A repository opts out of a hub service with
`managed_file_sync.disabled_services`.

Two services own `LICENSE` in `file` mode, and `check_conflicts` rejects two non-`block`
services claiming one path, so they are mutually exclusive by construction. `license_service`
is the Apache-2.0 default for anything published as a reusable Action, package, or library;
`proprietary_license_service` is the opt-in standard for internal and commercial repositories
and must be paired with `disabled_services: ["license_service"]`. A Marketplace listing can
never use the proprietary service — `bos-marketplace-kit` enforces
`allowed_licenses: ["Apache-2.0"]` with `require_license_audit: fail` — and a repository that
redistributes a copyleft dependency cannot use it either, because its license is inherited
rather than chosen.

`org_defaults` is gated on `target_repo_role: org-default-repo` so `profile/README.md` and
the community-health files never land in a product repository; product repos inherit those
from GitHub's organization defaults.

`bos_universal_gatekeeper_kicker` triggers on `push` to both `dev` and `main` and carries no
`on.push.paths` filter. `on:` is parsed before any job runs and cannot evaluate expressions,
so one `file`-mode template cannot express a path list that fits every repo shape. The
kicker's `changed-paths` job decides relevance instead, reading `triggers.push_paths` from
the consumer's own `.github/bos-universal-config.json` (accepted at top level or under
`launchpad`) and matching it against the push diff. It runs before `resolve-target-ref`, and
therefore before `sync-check-*` holds `contents: write`, so an irrelevant push never reaches
a job that can commit. An absent, empty, or unreadable list means "run on every push"; the
kicker and the config itself are always in scope; and a branch creation or unreachable
force-push base fails open. `scripts/test_universal_config_contract.py` pins all of this.

### Global configuration

The precedence chain is: the downstream tool's bundled marketplace defaults, then this
hub's global tier from `sync-files/config/`, then the consumer's own
`.github/bos-universal-config.json`, then any explicit workflow input. Hub workflows deliver
the global tier by sparse-checking-out this repository alongside the caller and passing a
`global_config_path` at the checked-out copy, so the policy stays a real reviewable file.

| File                                        | Configures                                                                                                                         |
| ------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------- |
| `managed-file-sync-global-config.json`      | `bos-managed-file-sync-action` — services, `file_patches`, hub `service_definitions`                                               |
| `code-scanning-kit-global-config.json`      | `bos-code-scanning-kit` — PS/LD/LF severities, licence allow/deny, AI summary off                                                  |
| `marketplace-kit-global-config.json`        | `bos-marketplace-kit` — `profile: strict`, `defer_to_code_scanning_kit`, sponsorship at `warn`, `allowed_licenses: ["Apache-2.0"]` |
| `marketplace-kicker-global-config.json`     | Marketplace release labelling, source-release freshness, `repo_metadata` defaults                                                  |
| `release-validation-global-config.json`     | `bos-universal-release-validation.yml` and the `release-validation` action                                                         |
| `action-test-global-config.json`            | `bos-universal-action-test.yml` matrix and timeouts                                                                                |
| `upstream-watcher-global-config.json`       | `bos-upstream-watcher` — tracker path, prerelease policy, AI defaults                                                              |
| `sitemap-generator-global-config.json`      | `bos-sitemap-generator` audit (`SM###`)                                                                                            |
| `securitytxt-generator-global-config.json`  | `bos-securitytxt-generator` audit (`ST###`, RFC 9116)                                                                              |
| `robotstxt-generator-global-config.json`    | `bos-robotstxt-generator` audit (`RB###`, RFC 9309)                                                                                |
| `humanstxt-generator-global-config.json`    | `bos-humanstxt-generator` audit (`HM###`)                                                                                          |
| `web-manifest-generator-global-config.json` | `bos-web-application-manifest-generator` audit (`WM###`)                                                                           |

All five site-generator files set `audit.fail_on: never`, so a failing control is fully
reported without blocking a deploy until a repository opts up, and all set
`remediation.enable_ai_findings_summary: false` to match the code-scanning kit.

The hub's own `.github/bos-universal-config.json` also carries the `action_pins` manifest —
`channel`, `scan_globs` (`.github/workflows/*.yml`, `.github/actions/**/action.yml`,
`sync-files/workflows/*.yml`), and the list of first-party repositories whose pins
`sync-action-pins.yml` keeps current.

### Scripts and tools

- `scripts/test_universal_config_contract.py` — offline; hub runtime, managed caller, branch, and documentation contracts (the largest test, ~1140 lines).
- `scripts/test_sync_action_pins.py` — offline; pin-resolver and rewriter unit tests, asserting SHA-plus-comment shape and SemVer ranking.
- `scripts/test_release_validation.py` — offline; drives `.github/actions/release-validation/validate.py` pass/fail behaviour.
- `scripts/test_gatekeeper_app_setup.py` — offline; loads `tools/gatekeeper-app-setup/server.py` by path and mocks its externals.
- `scripts/test_check_readme_header.sh` — fixture-driven test for the `check-readme-header` composite; extracts the embedded shell from `action.yml`, so it needs PyYAML. Not wired into `lint.yml`.
- `scripts/sync_action_pins.py` — `--check` / `--write` / `--json`; imports the shared resolver from `.github/actions/resolve-latest-action-ref/resolve.py` so ranking has one definition.
- `scripts/build_osi_catalogue.py` — merges the SPDX license list with the hand-curated `sync-files/legal/osi-overlay.json` into `osi-licenses.json`; fails the build on overlay drift rather than silently dropping an entry.
- `scripts/start-gatekeeper-app-setup.ps1` — PowerShell launcher for the loopback App setup helper; `-Profile gatekeeper|gatewall`, `-Port`, `-NoBrowser`, `-Repository`, `-RunId`.
- `scripts/marketplace-repo/main-protection-ruleset.json` — declarative org ruleset template for Marketplace repos; nothing in this repository applies it.
- `tools/gatekeeper-app-setup/` — `index.html` plus a stdlib `server.py` bound to `127.0.0.1`. Uses GitHub's App manifest flow and the authenticated `gh` CLI to create the App, exchange the one-time code in the browser, set the org App ID variable, and pipe the private key straight to the org secret without writing it to disk.

## Secrets pipelining

Credentials are split so the highest-privilege token is used for as little as possible, and
a purpose-scoped GitHub App is preferred over a PAT wherever the credential only talks to
the GitHub API — App installation tokens are minted per run, expire in about an hour, and
nobody rotates them.

Gatekeeper — dispatcher authorization only. `vars.GATEKEEPER_APP_ID` plus
`secrets.GATEKEEPER_APP_PRIVATE_KEY` mint a token scoped to organization
`Members: Read-only` for the org-role and team lookups. `secrets.GATEKEEPER_AUTHZ_PAT` is
the fallback for those lookups and is the _only_ credential that can resolve enterprise
ownership, because an App installation token cannot read `enterprise.ownerInfo`; that
lookup needs `admin:enterprise` held by an enterprise owner. With neither credential the
gate denies the dispatch, since the default `GITHUB_TOKEN` cannot resolve org role, team
membership, or enterprise ownership. Never add repository write permissions to this App.

Gatewall — repository automation. `vars.GATEWALL_APP_ID` plus
`secrets.GATEWALL_APP_PRIVATE_KEY`, with each job minting a token attenuated to just the
permissions that operation needs (Actions, Administration, Contents, Pull requests,
Workflows: write; secret-scanning and Dependabot alerts: read; Security events: write).
`secrets.WORKFLOW_SYNC_PAT` is the migration fallback and exists because `GITHUB_TOKEN` can
_never_ push to `.github/workflows/**` — a hard GitHub platform restriction no
`permissions:` block can grant. Without one of those two, `bos-universal-sync.yml` skips the
managed kicker workflow files and syncs everything else normally, and
`sync-action-pins.yml` reports drift and exits cleanly. `ORG_KICK_PAT`, `DISPATCH_TOKEN`,
`SCANNING_PAT`, `REPO_ADMIN_PAT`, and `RELEASE_PAT` are temporary compatibility fallbacks;
delete each once its App profile is verified.

Per-target release credentials stay with their provider and are never folded into a GitHub
credential: `DOCKERHUB_USERNAME` and `DOCKERHUB_TOKEN`; `BALENA_API_TOKEN`;
`CLOUDFLARE_API_TOKEN` with `CLOUDFLARE_ACCOUNT_ID` and `CLOUDFLARE_ZONE_ID` as non-secret
variables, plus optional `CLOUDFLARE_PAGES_ADMIN_TOKEN`. GitHub Apps cannot authenticate to
external providers.

Store secrets at the narrowest tier that avoids duplicate rotation work: repository secret
for single-repo values, environment secret with required reviewers for anything that can
push/publish/deploy, organization secret scoped to selected repositories for a shared
credential. App IDs are variables, not secrets. Split credentials rather than widening the
scope of an existing one — that is why seeding in `bos-org-kicker-fanout.yml` reuses the
workflow-sync credential instead of granting the dispatch credential write access to code.

## Conventions

Every `uses:` is a 40-character commit SHA with a trailing version comment; the
`pinned-actions` gate, `PS012`, Marketplace `SC002`, and CodeQL `actions/unpinned-tag` all
depend on it. Workflows declare `permissions: contents: read` (or `permissions: {}`) at the
top and grant per-job, never workflow-wide write. Job names are human-readable and stable
because branch protection matches on them. Authorization always resolves
`github.triggering_actor` before `github.actor`: on a re-run those differ, so authorizing
`actor` would let anyone with write access replay a privileged dispatch under someone
else's identity.

```yaml
- name: Mint authorization token
  id: authz_token
  if: github.event_name == 'workflow_dispatch' && vars.GATEKEEPER_APP_ID != ''
  uses: actions/create-github-app-token@bcd2ba49218906704ab6c1aa796996da409d3eb1 # v3.2.0
  with:
    app-id: ${{ vars.GATEKEEPER_APP_ID }}
    private-key: ${{ secrets.GATEKEEPER_APP_PRIVATE_KEY }}
    owner: ${{ github.repository_owner }}

- name: Check dispatcher authorization
  uses: blackoutsecure/bos-workflow-gatekeeper@eae1fdd8ab731e72bf890fdb7c1504415d12d88c # v1.0.2
  with:
    actor: ${{ github.triggering_actor || github.actor }}
    fail_closed: true
    token: ${{ steps.authz_token.outputs.token || secrets.GATEKEEPER_AUTHZ_PAT }}
```

`workflow_call` surfaces declare every `inputs:` entry with a `description`, `required`,
`type`, and a `default` that means "inherit from the config cascade" — usually `""` or `0`.
Optional `secrets:` are declared with `required: false` and a description explaining what
degrades when absent, so a missing credential is a documented skip rather than a failure.
Reusable workflows expose `outputs:` bound to a named job output. Runner topology and
timeouts come from the normalized `organization` config
(`fromJSON(needs.resolve-config.outputs.org).workflows.<name>.runs_on`), not from literals;
the two deliberate exceptions are each workflow's `resolve-config` job and the security
workflow's `summary` job, which must still report when config resolution itself failed.
Long block comments at the head of a workflow are load-bearing design history — do not
strip them.

Python in `scripts/` is stdlib-only, opens with `from __future__ import annotations`, uses
full type hints and `pathlib`, returns an exit code from `main(argv) -> int` rather than
scattering `sys.exit` through helpers, and keeps one definition of shared logic by
importing a composite's `.py` by path instead of copying it. Comments explain why a
non-obvious choice exists rather than restating the code.

## Boundaries

### Always

- Run the four `python3 scripts/test_*.py` contract tests and `actionlint` before finishing.
- Pin every `uses:` to a commit SHA with a trailing `# vX.Y.Z` comment.
- Declare least-privilege `permissions:` at the job level; keep the workflow default read-only.
- Pass inputs into bash through `env:`, never as `${{ … }}` inside a `run:` body.
- Start every bash `run:` with `set -euo pipefail`, and set `persist-credentials: false` on any `actions/checkout` that does not push.
- Resolve a cross-repo `uses:` path against the branch it targets (`@main` is the promoted runtime, which can differ in layout from `dev`).
- Keep `scripts/` stdlib-only and its tests offline.
- Update `README.md` when behaviour it documents changes; `test_universal_config_contract.py` validates its internal links.

### Ask first

- Any change to a `workflow_call` input or secret contract — renaming, removing, re-defaulting, or changing the meaning of one is breaking for every caller in the organization.
- Any change to `sync-files/` content or to a `service_definitions` entry, including `file_patches`: it rewrites files in every subscribed repository on the next sync.
- Any change to the managed-file-sync delimiter contract (`# >>> managed-file-sync:<service> >>>` / `# <<< managed-file-sync:<service> <<<`), the namespace grammar, or `take_over_managed_files`.
- Any change to release or promotion behaviour: the `release-hub.yml` runtime allowlist, `release-promote.yml` allowlist semantics, tag handling, or the `@main` promotion contract. Adding `workflow_call:` to a hub-only maintenance workflow silently promotes it.
- Widening any token scope, adding a new secret or App permission, or collapsing Gatekeeper and Gatewall into one credential.
- Changing a severity or `fail_on` value in `sync-files/config/*.json`, which changes policy for every consumer at once.
- Editing `sync-files/legal/osi-licenses.json` or `osi_catalogue.py` by hand; they are generated as a versioned pair by `scripts/build_osi_catalogue.py`.
- Changing the required check name `security (dev|main) / Security summary`, or the `actionlint` job name, both of which are pinned in downstream branch protection.

### Never

- Never commit secrets, private keys, PATs, App private keys, or real credentials to workflows, configs, templates, tests, or fixtures.
- Never use an unpinned or tag-only `uses:` reference. The only permitted exception is a literal `owner/repository@latest` for an entry explicitly marked `"ref_mode": "latest"` and passed through the pinned-actions gate's `latest_repositories` input.
- Never push directly to `main` or move a version tag by hand; promotion runs through `release-hub.yml`.
- Never weaken the universal security gate to make a downstream build pass — do not lower a severity, disable a job, remove a gate from `bos-universal-security.yml`, or set `fail_on: never` to get green.
- Never add a backend server, application runtime, package manifest, or third-party runtime dependency to this repository. The loopback `tools/gatekeeper-app-setup/server.py` is a local operator helper, not a service, and must stay bound to `127.0.0.1`.
- Never interpolate untrusted input (`inputs.*`, `github.event.*`) into a `run:` body, and never grant write permission to an authorization or preflight job.
- Never let a skipped gate report as a pass; a skip is `Not Assessed`.
- Never remove or bypass the `authorize` job, or authorize `github.actor` in place of `github.triggering_actor`.
