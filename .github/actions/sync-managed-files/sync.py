#!/usr/bin/env python3
"""
Sync standardized "managed" sections — and whole files — into
consumer repositories.

Three registries are supported:

* ``SERVICE_BLOCKS`` (section mode) — each enabled service contributes
  one or more blocks fenced by
  ``>>> bos-automation-hub:<service> >>>`` /
  ``<<< bos-automation-hub:<service> <<<`` marker lines using the
  comment syntax of the target file. Used for multi-tenant dotfiles
  (``.gitignore``, ``.dockerignore``, ``.editorconfig``,
  ``.gitattributes``, ``.github/dependabot.yml``) where multiple
  services contribute distinct blocks to the same file and
  hand-authored content must coexist outside the markers. Files
  listed in ``SECTION_FILE_HEADERS`` are created with a top-level
  scaffold (e.g. ``version: 2\\nupdates:\\n`` for dependabot.yml)
  when they don't already exist; existing files are left alone.

* ``SERVICE_FILES`` (whole-file mode) — each enabled service may own
  one or more files outright. The hub overwrites the file with the
  canonical content (prefixed by a single-line ``Managed by…`` header
  comment) on every run. Used for shared scripts where the entire file
  body is authoritative (e.g. ``log-functions.sh``, ``.prettierrc.yaml``).
  No markers; no merging — a file may only be claimed by exactly one
  whole-file service.

* ``SERVICE_INIT_FILES`` (init-if-missing mode) — each enabled service
  may ship a starter template. The hub writes the file ONLY when it
  does not already exist; once present, the hub NEVER overwrites it.
  Used for caller workflows and CI templates the consumer is expected
  to customize after init (e.g. ``.github/workflows/lint.yml``).
  Multiple services MAY target the same path (e.g. per-language lint
  variants), but at most ONE may be enabled per repo, enforced at
  parse time.

Rules
-----
* If a service is in ``SERVICES``, ensure its blocks / files exist and
  match the canonical content. For init-if-missing services, write the
  file only if missing. Create files (and parent dirs) if missing.
* If a service is NOT in ``SERVICES``, do nothing for it — existing
  blocks AND existing whole-file / init-file targets are left untouched.
* For section mode, nothing outside the marker pair is ever read or
  written.
* A single file path may not be registered under more than one mode.
  Within SERVICE_FILES, multiple services MAY claim the same path
  (e.g. the two `bos_launchpad_*` kickers); within SERVICE_INIT_FILES
  the same is true (e.g. the three `gha_lint_*` variants). In both
  cases at most one of the co-targeting services may be enabled per
  repo — enforced at parse time by `parse_services()`.

Env (set by `action.yml`)
-------------------------
    SERVICES         newline / whitespace list of enabled service names
    DRY_RUN          'true' | 'false'
    FAIL_ON_DRIFT    'true' | 'false' (implies dry_run for writes)
    GITHUB_OUTPUT    workflow output file (provided by runner)
    GITHUB_WORKSPACE optional override of CWD (provided by runner)
"""

from __future__ import annotations

import difflib
import os
import re
import sys
from typing import Dict, List, Tuple

MARKER_NAMESPACE = "bos-automation-hub"
MARKER_NOTE = (
    "Managed by https://github.com/blackoutsecure/bos-automation-hub — "
    "do not edit between markers."
)


# --------------------------------------------------------------------------- #
# Canonical block content per (service, file)                                 #
# --------------------------------------------------------------------------- #
#
# Block content MUST end with a newline so the open/close markers each
# sit on their own line.  Block content is the body BETWEEN markers; the
# markers themselves are added by `make_block`.

_GITIGNORE_COMMON = """\
# OS noise
.DS_Store
Thumbs.db
._*

# Editor / IDE noise
.vscode/
.idea/
*.swp
*.swo
*~

# Local env files (NEVER commit credentials)
.env
.env.*
!.env.example
.envrc
.direnv/

# TLS / private keys
.secrets/
*.pem
*.key
*.crt
*.p12
*.pfx
secrets.*
"""

_GITIGNORE_PYTHON = """\
__pycache__/
*.py[cod]
*.egg-info/
.venv/
venv/
.pytest_cache/
.mypy_cache/
.ruff_cache/
"""

_GITIGNORE_NODE = """\
node_modules/
npm-debug.log*
yarn-debug.log*
yarn-error.log*
.pnpm-debug.log*
"""

# --------------------------------------------------------------------------- #

_DOCKERIGNORE_DOCKER = """\
# CI / automation metadata — not needed inside the Docker build context.
.git
.github/
.gitignore
.gitattributes
.dockerignore
.editorconfig

# Editor / IDE noise
.vscode/
.idea/
*.swp
*~

# OS noise
.DS_Store
Thumbs.db
._*

# Documentation / metadata (if your image legitimately needs one of
# these inside the runtime, add a `!README.md` style re-include AFTER
# this managed block — `.dockerignore` is last-match-wins, so a
# re-include placed BEFORE the block would be overridden by these
# excludes).
README.md
LICENSE
SECURITY.md
CHANGELOG.md

# Local env / secrets — NEVER ship inside an image.
.env
.env.*
.secrets/
*.pem
*.key
*.crt
*.p12
*.pfx
"""

# Must come AFTER any `docker` block in `.dockerignore` because re-includes
# (`!path`) are evaluated in order against preceding excludes.
_DOCKERIGNORE_BALENA = """\
# `balena.yml` is rendered by the bos-automation-hub workflow into the
# repo root before `balena push` runs; @balena/compose reads it from the
# build context, so it MUST NOT be excluded.
!balena.yml
"""

_DOCKERIGNORE_PYTHON = """\
__pycache__/
*.py[cod]
*.egg-info/
.venv/
venv/
.pytest_cache/
.mypy_cache/
.ruff_cache/
"""

_DOCKERIGNORE_NODE = """\
node_modules/
npm-debug.log*
yarn-debug.log*
yarn-error.log*
.pnpm-debug.log*
"""

# --------------------------------------------------------------------------- #

_EDITORCONFIG_COMMON = """\
root = true

[*]
charset = utf-8
end_of_line = lf
insert_final_newline = true
trim_trailing_whitespace = true
indent_style = space
indent_size = 2

[*.md]
trim_trailing_whitespace = false

[Makefile]
indent_style = tab
"""

# --------------------------------------------------------------------------- #

_GITATTRIBUTES_LF = """\
# Normalize all text files to LF on checkout and in the index.
* text=auto eol=lf

# Explicit binary types (defensive — git usually auto-detects).
*.png  binary
*.jpg  binary
*.jpeg binary
*.gif  binary
*.webp binary
*.ico  binary
*.pdf  binary
"""

# --------------------------------------------------------------------------- #
# Whole-file canonical content                                                #
# --------------------------------------------------------------------------- #
#
# These entries are written VERBATIM (preceded by a "managed by" header
# comment, prepended by `_make_whole_file`) to their target paths. The
# entire file body is owned by the hub — no markers, no merging.

# Note: this is a Python raw-string. The `\` escapes inside `log_pipe_cmd`'s
# awk template are intended to reach bash unmodified — keep them as-is.
_LOG_FUNCTIONS_SH = r"""#!/usr/bin/env bash
# shellcheck shell=bash
#
# Canonical shared logging library for s6-overlay init and svc scripts
# across the blackoutsecure container fleet.
#
# Sourced (not executed). Provides one consistent log line format:
#
#     <RFC3339 UTC> <tag>[<level>]: <message>
#
# Two API styles are supported (mix freely; pick whichever reads best
# at the call site):
#
#   1. Function-per-level:
#          SVC_NAME="svc-readsb"      # OR: LOG_TAG="svc-readsb"
#          . /usr/local/bin/log-functions.sh
#          log_info  "starting up"
#          log_warn  "degraded"
#          log_error "connection refused"
#          log_fatal "cannot continue"
#          log_debug "fyi"            # gated by LOG_LEVEL
#      warn/error/fatal route to stderr; debug/info to stdout.
#
#   2. Generic dispatcher:
#          LOG_TAG="svc-gh-runner"    # OR: SVC_NAME="svc-gh-runner"
#          . /usr/local/bin/log-functions.sh
#          log info  "starting up"
#          log warn  "degraded"
#          log error "connection refused"
#          log fatal "cannot continue"
#      All levels route to stdout (legacy docker-github-runner shape).
#      Callers that want stderr add `>&2` at the call site.
#
# Severity ordering (case-insensitive):
#     debug < info < warn < error < fatal
# Lines below ${LOG_LEVEL:-info} are dropped; `fatal` is always emitted.
#
# Tag resolution: SVC_NAME wins, then LOG_TAG, then "unknown" (with a
# one-shot warning on stderr) so a misconfigured caller is noisy but not
# fatal.
#
# Extras (readsb provenance):
#   log_kv KEY value             # pretty key/value (gated at info)
#   log_pipe_cmd [priority]      # awk pipe that prefixes each stdin
#                                # line with the syslog format. Usage:
#                                #   exec mybinary 2>&1 \
#                                #     | eval "$(log_pipe_cmd decoder)"

if [[ -z "${SVC_NAME:-}" && -z "${LOG_TAG:-}" ]]; then
    printf '%s log-functions.sh[warn]: neither SVC_NAME nor LOG_TAG set; using "unknown"\n' \
        "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" >&2
fi

_log_tag() {
    printf '%s' "${SVC_NAME:-${LOG_TAG:-unknown}}"
}

_log_severity() {
    case "${1,,}" in
        debug) printf '10' ;;
        info)  printf '20' ;;
        warn)  printf '30' ;;
        error) printf '40' ;;
        fatal) printf '50' ;;
        *)     printf '20' ;;
    esac
}

_log_should_emit() {
    # _log_should_emit <level> -> 0 if yes, 1 if no
    local level="${1,,}" cur min
    [[ "${level}" == "fatal" ]] && return 0
    cur=$(_log_severity "${level}")
    min=$(_log_severity "${LOG_LEVEL:-info}")
    [[ "${cur}" -ge "${min}" ]]
}

_log_emit() {
    # _log_emit <level> <fd> <msg ...>
    local level="$1" fd="$2"; shift 2
    printf '%s %s[%s]: %s\n' \
        "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" \
        "$(_log_tag)" \
        "${level}" \
        "$*" >&"${fd}"
}

# Generic dispatcher (docker-github-runner API). All levels to stdout to
# preserve legacy behavior; callers add `>&2` when they want stderr.
log() {
    local level="$1"; shift
    _log_should_emit "${level}" || return 0
    _log_emit "${level}" 1 "$*"
}

# Function-per-level (readsb / mlat-hub API). warn/error/fatal -> stderr.
log_debug() { _log_should_emit debug && _log_emit debug 1 "$@"; return 0; }
log_info()  { _log_should_emit info  && _log_emit info  1 "$@"; return 0; }
log_warn()  { _log_should_emit warn  && _log_emit warn  2 "$@"; return 0; }
log_error() { _log_should_emit error && _log_emit error 2 "$@"; return 0; }
log_fatal() { _log_emit fatal 2 "$@"; }

# Pretty key/value (readsb provenance). Gated at info.
log_kv() {
    _log_should_emit info || return 0
    local key="$1" value="$2"
    printf '%s %s[%s]: %-15s %s\n' \
        "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" \
        "$(_log_tag)" \
        "info" \
        "${key}:" \
        "${value}"
}

# Returns an awk pipeline string for prefixing each stdin line with the
# syslog format. fflush() avoids buffering so log lines surface in real
# time. Usage:
#     exec some-binary 2>&1 | eval "$(log_pipe_cmd decoder)"
log_pipe_cmd() {
    local priority="${1:-stdout}"
    local tag
    tag="$(_log_tag)"
    printf "awk '{ printf \"%%s %s[%s]: %%s\\\\n\", strftime(\"%%Y-%%m-%%dT%%H:%%M:%%SZ\", systime(), 1), \$0; fflush() }'" \
        "${tag}" "${priority}"
}
"""


# --------------------------------------------------------------------------- #
# Dependabot section content (per ecosystem)                                  #
# --------------------------------------------------------------------------- #
#
# Each ecosystem contributes ONE list item under the file-wide
# `updates:` key. The shared header (`version: 2\nupdates:\n`) is
# injected via SECTION_FILE_HEADERS for `.github/dependabot.yml` so a
# repo can enable any combination of `dependabot_actions`,
# `dependabot_npm`, `dependabot_pip` and produce a valid file. YAML
# accepts `#` comments inside a sequence, so the marker pair lives
# between items without breaking parsing.

# The `{{DEPENDABOT_TARGET_BRANCH_LINE}}` token (sits at end of the
# `directory: /` line) is rendered at sync time from
# `dependabot_target_branch:` in `bos-managed-files.yaml`. When that
# config key is empty (the default), the placeholder resolves to the
# empty string and the block stays one line shorter. When set (e.g.
# `dependabot_target_branch: dev` for Marketplace Action repos using
# the dev/main split), the placeholder expands to
# `\n    target-branch: <branch>` so Dependabot opens its PRs against
# the named branch instead of the default branch. Dependabot reads
# this config only from the default branch, so the dev/main split
# pattern relies on this override to keep PRs landing on `dev`
# (where the CI workflows live) even though the config itself is
# committed to `main` by the promote pipeline.

_DEPENDABOT_ACTIONS = """\
  - package-ecosystem: github-actions
    directory: /{{DEPENDABOT_TARGET_BRANCH_LINE}}
    schedule:
      interval: weekly
      day: monday
      time: "06:00"
      timezone: "Etc/UTC"
    open-pull-requests-limit: 5
    commit-message:
      prefix: "ci"
      include: scope
    labels:
      - dependencies
      - github-actions
    groups:
      docker-actions:
        patterns:
          - "docker/*"
      github-actions:
        patterns:
          - "actions/*"
"""

_DEPENDABOT_NPM = """\
  - package-ecosystem: npm
    directory: /{{DEPENDABOT_TARGET_BRANCH_LINE}}
    schedule:
      interval: weekly
      day: monday
      time: "06:00"
      timezone: "Etc/UTC"
    open-pull-requests-limit: 5
    commit-message:
      prefix: "chore"
      include: scope
    labels:
      - dependencies
      - npm
    groups:
      dev-dependencies:
        dependency-type: development
      prod-dependencies:
        dependency-type: production
        update-types:
          - minor
          - patch
"""

_DEPENDABOT_PIP = """\
  - package-ecosystem: pip
    directory: /{{DEPENDABOT_TARGET_BRANCH_LINE}}
    schedule:
      interval: weekly
      day: monday
      time: "06:00"
      timezone: "Etc/UTC"
    open-pull-requests-limit: 5
    commit-message:
      prefix: "chore"
      include: scope
    labels:
      - dependencies
      - python
"""


# --------------------------------------------------------------------------- #
# Prettier — whole-file (YAML so we can carry a "managed by" header)          #
# --------------------------------------------------------------------------- #
#
# Uses `.prettierrc.yaml` (not `.prettierrc.json`) because JSON has no
# comment syntax and the whole-file mode injects a "Managed by …"
# header. Prettier resolves `.prettierrc.yaml` natively.

_PRETTIERRC_YAML = """\
semi: true
singleQuote: true
trailingComma: all
printWidth: 100
tabWidth: 2
useTabs: false
arrowParens: always
bracketSpacing: true
endOfLine: lf
"""


# --------------------------------------------------------------------------- #
# Init-if-missing whole files — starter templates                             #
# --------------------------------------------------------------------------- #
#
# Written ONCE on first sync if the target path does not exist. The hub
# NEVER touches the file after that (no drift detection, no overwrite),
# so each repo is free to customize after init. The header injected by
# `_make_init_file()` says so explicitly.
#
# Naming: `gha_*` for `.github/workflows/*.yml` files. Per-language
# lint variants (`gha_lint_node`, `gha_lint_python`, `gha_lint_shell`)
# all target the same `.github/workflows/lint.yml`, so at most ONE may
# be enabled per repo (enforced at parse time by `parse_services()`).

_GHA_SYNC_COMMIT_YML = """\
# Calls the bos-automation-hub `sync-managed-files.yml` reusable in
# `commit` mode on a weekly schedule. Edit the `services:` list below
# to control which canonical blocks / files the hub maintains in this
# repo. Disabling a service leaves any existing content untouched.
#
# Known services:
#   common            common .gitignore + .editorconfig sections
#   docker            Docker-related .dockerignore section
#   balena            balena.yml re-include for .dockerignore
#   node              Node.js .gitignore + .dockerignore sections
#   python            Python .gitignore + .dockerignore sections
#   lf_line_endings   .gitattributes LF normalization block
#   dependabot_actions  github-actions ecosystem in .github/dependabot.yml
#   dependabot_npm      npm ecosystem in .github/dependabot.yml
#   dependabot_pip      pip ecosystem in .github/dependabot.yml
#   prettier          full .prettierrc.yaml (overwritten on every run)
#   logger            full root/usr/local/bin/log-functions.sh

name: Sync managed files

on:
  schedule:
    - cron: '17 14 * * 1'   # Monday 14:17 UTC
  workflow_dispatch:
  push:
    branches: [main]
    paths:
      - '.github/workflows/sync-managed-files.yml'

permissions:
  contents: read

concurrency:
  group: sync-managed-files-${{ github.ref }}
  cancel-in-progress: false

jobs:
  sync:
    permissions:
      contents: write
    uses: blackoutsecure/bos-automation-hub/.github/workflows/sync-managed-files.yml@main
    with:
      services: |
        common
        lf_line_endings
        # Uncomment what this repo needs:
        # node
        # python
        # docker
        # balena
        # dependabot_actions
        # dependabot_npm
        # dependabot_pip
        # prettier
        # logger
"""

_GHA_SYNC_DRIFT_CHECK_YML = """\
# PR-time drift check. Runs the bos-automation-hub
# `sync-managed-files.yml` reusable in `check` mode and fails the job
# (and therefore the PR) if any managed block / file would be modified.
#
# The `services:` list below MUST mirror the list in
# `.github/workflows/sync-managed-files.yml`. Keep them in sync
# manually or treat this file as authoritative and have the commit
# workflow read from it (out of scope for the starter template).

name: Sync drift check

on:
  pull_request:
    branches: [main]
    paths:
      - '.gitignore'
      - '.dockerignore'
      - '.editorconfig'
      - '.gitattributes'
      - '.github/dependabot.yml'
      - '.prettierrc.yaml'
      - 'root/usr/local/bin/log-functions.sh'
      - '.github/workflows/sync-drift-check.yml'
  workflow_dispatch:

permissions:
  contents: read

concurrency:
  group: sync-drift-check-${{ github.ref }}
  cancel-in-progress: true

jobs:
  drift-check:
    uses: blackoutsecure/bos-automation-hub/.github/workflows/sync-managed-files.yml@main
    with:
      mode: check
      services: |
        common
        lf_line_endings
        # Mirror the list in `.github/workflows/sync-managed-files.yml`.
"""

_GHA_LINT_NODE_YML = """\
# Lint for a Node-based GitHub Action repo. Runs:
#   - actionlint (workflow + composite action YAML)
#   - eslint    (npm run lint, if defined)
#   - prettier  (npm run format -- --check, if defined)
#
# This is a starter template — the hub writes it only when missing and
# will never overwrite it. Adjust scripts/Node version to taste.

name: Lint

on:
  push:
    branches: [main]
    paths:
      - '**/*.js'
      - '**/*.json'
      - '**/*.md'
      - '**/*.yml'
      - '**/*.yaml'
      - 'package.json'
      - 'package-lock.json'
      - '.eslintrc*'
      - 'eslint.config.*'
      - '.prettierrc*'
      - '.github/workflows/lint.yml'
  pull_request:
    paths:
      - '**/*.js'
      - '**/*.json'
      - '**/*.md'
      - '**/*.yml'
      - '**/*.yaml'
      - 'package.json'
      - 'package-lock.json'
      - '.eslintrc*'
      - 'eslint.config.*'
      - '.prettierrc*'
      - '.github/workflows/lint.yml'
  workflow_dispatch:

permissions:
  contents: read

concurrency:
  group: lint-${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: true

jobs:
  actionlint:
    name: actionlint
    runs-on: ubuntu-latest
    timeout-minutes: 5
    steps:
      - uses: actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd # v6.0.2
        with:
          persist-credentials: false
      - name: Run actionlint
        uses: raven-actions/actionlint@205b530c5d9fa8f44ae9ed59f341a0db994aa6f8 # v2.1.2
        with:
          matcher: true
          fail-on-error: true
          shellcheck: true

  node-lint:
    name: eslint + prettier
    runs-on: ubuntu-latest
    timeout-minutes: 10
    steps:
      - uses: actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd # v6.0.2
        with:
          persist-credentials: false
      - uses: actions/setup-node@a0853c24544627f65ddf259abe73b1d18a591444 # v6.0.0
        with:
          node-version: 20
          cache: npm
      - run: npm ci
      - name: ESLint
        run: |
          if npm run | grep -qE '^  lint$'; then
            npm run lint -- --max-warnings=0 || npm run lint
          else
            echo "::notice::no 'lint' script in package.json — skipping eslint"
          fi
      - name: Prettier
        run: |
          if npm run | grep -qE '^  format$'; then
            npx prettier --check "**/*.{js,json,md,yml,yaml}" \\
              --ignore-path .gitignore || \\
            echo "::warning::prettier reported drift; run 'npm run format' locally to fix"
          else
            echo "::notice::no 'format' script in package.json — skipping prettier"
          fi
"""

_GHA_LINT_PYTHON_YML = """\
# Lint for a Python-based GitHub Action repo. Runs:
#   - actionlint (workflow + composite action YAML)
#   - ruff      (linter; reads pyproject.toml [tool.ruff])
#   - pytest    (unit tests under test/)
#
# This is a starter template — the hub writes it only when missing and
# will never overwrite it. Adjust Python version / extra steps to taste.

name: Lint

on:
  push:
    branches: [main]
    paths:
      - '**/*.py'
      - 'pyproject.toml'
      - 'requirements*.txt'
      - '**/*.yml'
      - '**/*.yaml'
      - '.github/workflows/lint.yml'
  pull_request:
    paths:
      - '**/*.py'
      - 'pyproject.toml'
      - 'requirements*.txt'
      - '**/*.yml'
      - '**/*.yaml'
      - '.github/workflows/lint.yml'
  workflow_dispatch:

permissions:
  contents: read

concurrency:
  group: lint-${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: true

jobs:
  actionlint:
    name: actionlint
    runs-on: ubuntu-latest
    timeout-minutes: 5
    steps:
      - uses: actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd # v6.0.2
        with:
          persist-credentials: false
      - name: Run actionlint
        uses: raven-actions/actionlint@205b530c5d9fa8f44ae9ed59f341a0db994aa6f8 # v2.1.2
        with:
          matcher: true
          fail-on-error: true
          shellcheck: true

  python-lint:
    name: ruff + pytest
    runs-on: ubuntu-latest
    timeout-minutes: 10
    steps:
      - uses: actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd # v6.0.2
        with:
          persist-credentials: false
      - uses: actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065 # v6.0.0
        with:
          python-version: '3.11'
          cache: pip
      - name: Install dev deps
        run: |
          python -m pip install --upgrade pip
          if [ -f requirements-dev.txt ]; then
            pip install -r requirements-dev.txt
          else
            pip install ruff pytest
          fi
      - name: Ruff
        run: ruff check .
      - name: Pytest
        run: |
          if [ -d test ] || [ -d tests ]; then
            pytest -q
          else
            echo "::notice::no test/ or tests/ directory — skipping pytest"
          fi
"""

_GHA_LINT_SHELL_YML = """\
# Lint for a shell/bash-based GitHub Action repo. Runs:
#   - actionlint (workflow + composite action YAML, with shellcheck)
#   - shellcheck (top-level *.sh files outside .github/)
#   - bats       (any test/**/*.bats files, if present)
#
# This is a starter template — the hub writes it only when missing and
# will never overwrite it. Adjust paths to taste.

name: Lint

on:
  push:
    branches: [main]
    paths:
      - '**/*.sh'
      - '**/*.bats'
      - '**/*.yml'
      - '**/*.yaml'
      - 'action.yml'
      - '.github/workflows/lint.yml'
  pull_request:
    paths:
      - '**/*.sh'
      - '**/*.bats'
      - '**/*.yml'
      - '**/*.yaml'
      - 'action.yml'
      - '.github/workflows/lint.yml'
  workflow_dispatch:

permissions:
  contents: read

concurrency:
  group: lint-${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: true

jobs:
  actionlint:
    name: actionlint
    runs-on: ubuntu-latest
    timeout-minutes: 5
    steps:
      - uses: actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd # v6.0.2
        with:
          persist-credentials: false
      - name: Run actionlint
        uses: raven-actions/actionlint@205b530c5d9fa8f44ae9ed59f341a0db994aa6f8 # v2.1.2
        with:
          matcher: true
          fail-on-error: true
          shellcheck: true

  shellcheck:
    name: shellcheck + bats
    runs-on: ubuntu-latest
    timeout-minutes: 5
    steps:
      - uses: actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd # v6.0.2
        with:
          persist-credentials: false
      - name: Install shellcheck + bats
        run: |
          sudo apt-get update
          sudo apt-get install -y shellcheck bats
      - name: ShellCheck
        run: |
          # Lint every *.sh outside .github/ (raven-actions/actionlint
          # already covers workflow run-blocks via its own shellcheck).
          mapfile -t files < <(find . -path ./.github -prune -o -type f -name '*.sh' -print)
          if [ "${#files[@]}" -eq 0 ]; then
            echo "::notice::no *.sh files outside .github/ — skipping shellcheck"
            exit 0
          fi
          shellcheck "${files[@]}"
      - name: Bats
        run: |
          if find test tests -type f -name '*.bats' 2>/dev/null | grep -q .; then
            bats $(find test tests -type f -name '*.bats' 2>/dev/null)
          else
            echo "::notice::no *.bats files under test/ or tests/ — skipping bats"
          fi
"""


# --------------------------------------------------------------------------- #
# bos-launchpad kicker workflows                                              #
# --------------------------------------------------------------------------- #
#
# Two whole-file kicker workflows that target the SAME path
# (`.github/workflows/bos-launchpad.yml`) and are MUTUALLY EXCLUSIVE
# per consumer repo (enforced at parse time, same pattern as
# `gha_lint_*`).
#
# Both kickers delegate to the org's `bos-launchpad.yml` reusable
# meta-workflow and read per-repo customization from a consumer-owned
# `.bos-launchpad.yaml` data file at the repo root. The kicker pipes
# that YAML through `yq -o=json` to a job output and the downstream
# `release:` / `cloudflare:` job consumes it via
# `fromJson(needs.parse-config.outputs.cfg).<path>` for each launchpad
# input. The data file is NOT hub-managed — consumers own it.
#
# Why two kickers instead of one mega-kicker:
#   * GHA `on:` triggers are static and cannot come from a data file.
#     One kicker = one trigger set; the cron-driven container release
#     and the push-driven static site need distinct trigger shapes.
#   * `secrets:` forwarding is static. Each kicker forwards only the
#     secrets its flow actually needs (DOCKERHUB_* + BALENA_* for
#     release, CLOUDFLARE_* for cf-pages).
#   * Concurrency group keys differ so a release on `main` doesn't
#     queue behind a static-site push and vice versa.
#
# The `runner` (blackoutmode/runner) repo is INTENTIONALLY out of scope:
# it has a hand-authored preflight job that has to gate the launchpad
# call. Hand-author its caller; don't enable either kicker service
# for that repo.
#
# Schema for `.bos-launchpad.yaml` is documented in the hub README
# under "bos_launchpad_release / bos_launchpad_cf_pages services".

_BOS_LAUNCHPAD_RELEASE_YML = """\
# Blackout Secure Launchpad — release kicker (hub-managed).
#
# Calls `bos-launchpad.yml` in blackoutsecure/bos-automation-hub. Reads
# per-repo customization from `.bos-launchpad.yaml` at the repo root.
#
# CUSTOMIZE via `.bos-launchpad.yaml` — NOT this file. The hub
# overwrites this workflow in place on every sync; hand-edits are
# lost. Schema docs: https://github.com/blackoutsecure/bos-automation-hub
#
# Required vars   (names overridable via `.bos-launchpad.yaml`):
#   DOCKERHUB_NAMESPACE, BALENA_NAMESPACE
# Required secrets:
#   DOCKERHUB_USERNAME, DOCKERHUB_TOKEN, BALENA_API_TOKEN
#   UPSTREAM_TOKEN (optional — only for private upstream repos)
name: Blackout Secure Launchpad

on:
  schedule:
    - cron: '17 */6 * * *'   # stagger off :00 to dodge org cron pile-ups
  push:
    branches: [main]
    # Source paths that should trigger the workflow on commit. Whether
    # push events actually FORCE a rebuild is controlled by
    # `triggers.force_on_push` in `.bos-launchpad.yaml` (default
    # `false`: a push that doesn't move upstream is a no-op release).
    paths:
      - 'Dockerfile'
      - '.dockerignore'
      - 'root/**'
      - 'build/**'
      - 'scripts/**'
      - '.github/upstream/**'
      - '.bos-launchpad.yaml'
      - '.github/workflows/bos-launchpad.yml'
  workflow_dispatch:
    inputs:
      force_run:
        description: 'Force: run pipeline even if upstream unchanged'
        type: boolean
        default: false

# No top-level `concurrency:` — the hub workflow owns serialization.
# Declaring it on both sides triggers a GHA self-deadlock.
permissions:
  contents: read

jobs:
  parse-config:
    name: Parse .bos-launchpad.yaml
    runs-on: ubuntu-latest
    timeout-minutes: 2
    outputs:
      cfg: ${{ steps.read.outputs.cfg }}
    steps:
      - name: Checkout
        uses: actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd # v6.0.2
        with:
          persist-credentials: false
      - name: Convert .bos-launchpad.yaml to JSON
        id: read
        shell: bash
        run: |
          set -euo pipefail
          if [[ ! -f .bos-launchpad.yaml ]]; then
            echo "::error file=.bos-launchpad.yaml::Required config file not found. See https://github.com/blackoutsecure/bos-automation-hub for the schema."
            exit 1
          fi
          # yq is preinstalled on ubuntu-latest GitHub-hosted runners.
          # `-I=0` emits compact (single-line) JSON to keep the GHA
          # output payload small and avoid heredoc edge cases.
          if ! cfg="$(yq -o=json -I=0 '.' .bos-launchpad.yaml)"; then
            echo "::error file=.bos-launchpad.yaml::YAML parse error (see preceding yq output)."
            exit 1
          fi
          # Round-trip through python's JSON parser as a sanity check —
          # catches any yq quirk that produces non-conformant JSON.
          if ! echo "$cfg" | python3 -c 'import json,sys; json.loads(sys.stdin.read())' >/dev/null; then
            echo "::error file=.bos-launchpad.yaml::Resulting JSON did not parse — yq emitted invalid output."
            exit 1
          fi
          # Heredoc framing tolerates literal newlines inside string
          # values even though we asked yq for compact JSON.
          {
            echo "cfg<<__BOS_EOF__"
            echo "$cfg"
            echo "__BOS_EOF__"
          } >> "$GITHUB_OUTPUT"

  release:
    name: Release
    needs: parse-config
    permissions:
      contents:        write   # monitor tracking-file commit + GitHub Release publish
      actions:         write   # nested monitor (`gh workflow run`)
      pull-requests:   write   # nested Docker Scout PR annotations
      security-events: write   # nested Docker Scout SARIF upload
    uses: blackoutsecure/bos-automation-hub/.github/workflows/bos-launchpad.yml@main
    with:
      # ----- Monitor stage -----
      upstream_repo:     ${{ fromJson(needs.parse-config.outputs.cfg).upstream.repo || '' }}
      source:            ${{ fromJson(needs.parse-config.outputs.cfg).upstream.source || 'github_release' }}
      upstream_branch:   ${{ fromJson(needs.parse-config.outputs.cfg).upstream.branch || '' }}
      version_file_path: ${{ fromJson(needs.parse-config.outputs.cfg).upstream.version_file_path || 'version' }}
      version_regex:     ${{ fromJson(needs.parse-config.outputs.cfg).upstream.version_regex || '' }}
      image_ref:         ${{ fromJson(needs.parse-config.outputs.cfg).upstream.image_ref || '' }}
      package_name:      ${{ fromJson(needs.parse-config.outputs.cfg).upstream.package_name || '' }}
      version_url:       ${{ fromJson(needs.parse-config.outputs.cfg).upstream.version_url || '' }}
      tag_pattern:       ${{ fromJson(needs.parse-config.outputs.cfg).upstream.tag_pattern || '' }}
      track_file:        ${{ fromJson(needs.parse-config.outputs.cfg).upstream.track_file || '.github/upstream/tracked-release.json' }}
      # Force gating: schedule NEVER forces; push forces only when the
      # data file opts in via `triggers.force_on_push: true`; dispatch
      # honours the operator checkbox.
      force_run: ${{ (github.event_name == 'push' && fromJson(needs.parse-config.outputs.cfg).triggers.force_on_push == true) || (github.event_name == 'workflow_dispatch' && inputs.force_run) }}

      # ----- Stage toggles -----
      docker:           ${{ fromJson(needs.parse-config.outputs.cfg).stages.docker == true }}
      balena:           ${{ fromJson(needs.parse-config.outputs.cfg).stages.balena == true }}
      github_release:   ${{ fromJson(needs.parse-config.outputs.cfg).stages.github_release == true }}
      companion_docker: ${{ fromJson(needs.parse-config.outputs.cfg).stages.companion_docker == true }}

      # ----- Docker stage -----
      image_name:                ${{ fromJson(needs.parse-config.outputs.cfg).docker.image_name || '' }}
      # `vars[<expr>]` does dynamic key lookup against the `vars`
      # context. Default `DOCKERHUB_NAMESPACE` matches the historic
      # caller convention; override via `docker.namespace_var`.
      dockerhub_namespace:       ${{ vars[fromJson(needs.parse-config.outputs.cfg).docker.namespace_var || 'DOCKERHUB_NAMESPACE'] }}
      docker_extra_tags:         ${{ fromJson(needs.parse-config.outputs.cfg).docker.extra_tags || '' }}
      docker_short_description:  ${{ fromJson(needs.parse-config.outputs.cfg).docker.short_description || '' }}
      # `!= false` semantics: missing key → null → null != false → true.
      # Match the launchpad's own default (true) for these flags.
      docker_latest:             ${{ fromJson(needs.parse-config.outputs.cfg).docker.latest != false }}
      docker_multi_arch:         ${{ fromJson(needs.parse-config.outputs.cfg).docker.multi_arch != false }}
      docker_update_description: ${{ fromJson(needs.parse-config.outputs.cfg).docker.update_description != false }}
      docker_force: ${{ (github.event_name == 'push' && fromJson(needs.parse-config.outputs.cfg).triggers.force_on_push == true) || (github.event_name == 'workflow_dispatch' && inputs.force_run) }}

      # ----- Docker Scout -----
      docker_enable_scout:             ${{ fromJson(needs.parse-config.outputs.cfg).scout.enable != false }}
      docker_scout_command:            ${{ fromJson(needs.parse-config.outputs.cfg).scout.command || 'cves' }}
      docker_scout_severities:         ${{ fromJson(needs.parse-config.outputs.cfg).scout.severities || 'critical,high' }}
      docker_scout_only_fixed:         ${{ fromJson(needs.parse-config.outputs.cfg).scout.only_fixed == true }}
      docker_scout_ignore_base:        ${{ fromJson(needs.parse-config.outputs.cfg).scout.ignore_base == true }}
      docker_scout_organization:       ${{ fromJson(needs.parse-config.outputs.cfg).scout.organization || '' }}
      docker_scout_record_environment: ${{ fromJson(needs.parse-config.outputs.cfg).scout.record_environment || '' }}
      docker_scout_sarif_upload:       ${{ fromJson(needs.parse-config.outputs.cfg).scout.sarif_upload != false }}
      docker_scout_exit_code:          ${{ fromJson(needs.parse-config.outputs.cfg).scout.exit_code == true }}
      docker_scout_enable_repo:        ${{ fromJson(needs.parse-config.outputs.cfg).scout.enable_repo != false }}

      # ----- Balena stage -----
      block_name:                 ${{ fromJson(needs.parse-config.outputs.cfg).balena.block_name || '' }}
      balena_namespace:           ${{ vars[fromJson(needs.parse-config.outputs.cfg).balena.namespace_var || 'BALENA_NAMESPACE'] }}
      balena_sync_yml:            ${{ fromJson(needs.parse-config.outputs.cfg).balena.sync_yml != false }}
      balena_draft:               ${{ fromJson(needs.parse-config.outputs.cfg).balena.draft == true }}
      balena_force: ${{ (github.event_name == 'push' && fromJson(needs.parse-config.outputs.cfg).triggers.force_on_push == true) || (github.event_name == 'workflow_dispatch' && inputs.force_run) }}
      balena_generate_yml:        ${{ fromJson(needs.parse-config.outputs.cfg).balena.generate_yml == true }}
      balena_type:                ${{ fromJson(needs.parse-config.outputs.cfg).balena.type || 'sw.block' }}
      balena_repository_url:      ${{ fromJson(needs.parse-config.outputs.cfg).balena.repository_url || '' }}
      balena_logo_url:            ${{ fromJson(needs.parse-config.outputs.cfg).balena.logo_url || '' }}
      balena_default_device_type: ${{ fromJson(needs.parse-config.outputs.cfg).balena.default_device_type || '' }}
      # `|-` chomps the trailing newline that a plain `|` block would
      # add at each forwarding hop, so multi-line caller values stay
      # byte-stable through the kicker → launchpad → release.yml chain.
      balena_description: |-
        ${{ fromJson(needs.parse-config.outputs.cfg).balena.description || '' }}
      balena_post_provisioning: |-
        ${{ fromJson(needs.parse-config.outputs.cfg).balena.post_provisioning || '' }}
      balena_supported_device_types: |-
        ${{ fromJson(needs.parse-config.outputs.cfg).balena.supported_device_types || '' }}

      # ----- Companion Docker stage -----
      companion_image_name:               ${{ fromJson(needs.parse-config.outputs.cfg).companion_docker.image_name || '' }}
      companion_build_target:             ${{ fromJson(needs.parse-config.outputs.cfg).companion_docker.build_target || '' }}
      companion_docker_short_description: ${{ fromJson(needs.parse-config.outputs.cfg).companion_docker.short_description || '' }}

      # ----- GitHub Release stage -----
      release_template_path:  ${{ fromJson(needs.parse-config.outputs.cfg).release.template_path || '' }}
      release_extra_context:  ${{ fromJson(needs.parse-config.outputs.cfg).release.extra_context || '' }}
      generate_release_notes: ${{ fromJson(needs.parse-config.outputs.cfg).release.generate_notes != false }}
      release_files:          ${{ fromJson(needs.parse-config.outputs.cfg).release.files || '' }}
      release_draft:          ${{ fromJson(needs.parse-config.outputs.cfg).release.draft == true }}

      # ----- Shared -----
      platforms: ${{ fromJson(needs.parse-config.outputs.cfg).platforms || 'linux/amd64,linux/arm64' }}
    secrets:
      DOCKERHUB_USERNAME: ${{ secrets.DOCKERHUB_USERNAME }}
      DOCKERHUB_TOKEN:    ${{ secrets.DOCKERHUB_TOKEN }}
      BALENA_API_TOKEN:   ${{ secrets.BALENA_API_TOKEN }}
      UPSTREAM_TOKEN:     ${{ secrets.UPSTREAM_TOKEN }}
"""


_BOS_LAUNCHPAD_CF_PAGES_YML = """\
# Blackout Secure Launchpad — Cloudflare Pages kicker (hub-managed).
#
# Calls `bos-launchpad.yml` in blackoutsecure/bos-automation-hub. Reads
# per-repo customization from `.bos-launchpad.yaml` at the repo root.
#
# CUSTOMIZE via `.bos-launchpad.yaml` — NOT this file. The hub
# overwrites this workflow in place on every sync; hand-edits are
# lost. Schema docs: https://github.com/blackoutsecure/bos-automation-hub
#
# Required secrets:
#   CLOUDFLARE_API_TOKEN — Account → Cloudflare Pages → Edit, plus
#     Zone → Cache Purge → Purge and Zone → Zone → Read when
#     `cloudflare.purge_cache: true` (the default). Zone:Read also
#     lets the purge step auto-resolve the zone ID from the site URL.
#   CLOUDFLARE_ACCOUNT_ID — optional; auto-resolved via `GET /accounts`
#   CLOUDFLARE_ZONE_ID    — optional; auto-resolved from site URL
name: Blackout Secure Launchpad

on:
  push:
    branches: [main]
  workflow_dispatch:
    inputs:
      force_run:
        description: 'Force: deploy even when the auto gate would skip'
        type: boolean
        default: false

# No top-level `concurrency:` — the hub workflow owns per-project
# Cloudflare Pages serialization; both sides would deadlock.
permissions:
  contents: read

jobs:
  parse-config:
    name: Parse .bos-launchpad.yaml
    runs-on: ubuntu-latest
    timeout-minutes: 2
    outputs:
      cfg: ${{ steps.read.outputs.cfg }}
    steps:
      - name: Checkout
        uses: actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd # v6.0.2
        with:
          persist-credentials: false
      - name: Convert .bos-launchpad.yaml to JSON
        id: read
        shell: bash
        run: |
          set -euo pipefail
          if [[ ! -f .bos-launchpad.yaml ]]; then
            echo "::error file=.bos-launchpad.yaml::Required config file not found. See https://github.com/blackoutsecure/bos-automation-hub for the schema."
            exit 1
          fi
          if ! cfg="$(yq -o=json -I=0 '.' .bos-launchpad.yaml)"; then
            echo "::error file=.bos-launchpad.yaml::YAML parse error (see preceding yq output)."
            exit 1
          fi
          if ! echo "$cfg" | python3 -c 'import json,sys; json.loads(sys.stdin.read())' >/dev/null; then
            echo "::error file=.bos-launchpad.yaml::Resulting JSON did not parse — yq emitted invalid output."
            exit 1
          fi
          {
            echo "cfg<<__BOS_EOF__"
            echo "$cfg"
            echo "__BOS_EOF__"
          } >> "$GITHUB_OUTPUT"

  launchpad:
    name: Cloudflare Pages
    needs: parse-config
    permissions:
      # GHA validates nested reusable-workflow permissions STATICALLY
      # at workflow-call time, so the full superset declared by leaf
      # jobs in the launchpad (monitor, release, github-release,
      # cloudflare-pages) must be granted here even though only the
      # cloudflare-pages stage runs at runtime.
      contents:        write
      actions:         write
      pull-requests:   write
      security-events: write
    uses: blackoutsecure/bos-automation-hub/.github/workflows/bos-launchpad.yml@main
    with:
      # ----- Cloudflare Pages stage -----
      cloudflare_pages:                     ${{ fromJson(needs.parse-config.outputs.cfg).stages.cloudflare_pages != false }}
      cloudflare_project_name:              ${{ fromJson(needs.parse-config.outputs.cfg).cloudflare.project_name || '' }}
      cloudflare_deployment_environment:    ${{ fromJson(needs.parse-config.outputs.cfg).cloudflare.deployment_environment || '' }}
      cloudflare_site_url:                  ${{ fromJson(needs.parse-config.outputs.cfg).cloudflare.site_url || '' }}
      cloudflare_public_dir:                ${{ fromJson(needs.parse-config.outputs.cfg).cloudflare.public_dir || '.' }}
      cloudflare_deploy_dir:                ${{ fromJson(needs.parse-config.outputs.cfg).cloudflare.deploy_dir || './dist' }}
      cloudflare_clean_deploy_dir:          ${{ fromJson(needs.parse-config.outputs.cfg).cloudflare.clean_deploy_dir != false }}
      # `|-` chomps the trailing newline so multi-line caller values
      # (one path per line) stay byte-stable through the kicker →
      # launchpad → deploy-cloudflare-pages chain.
      cloudflare_copy_files: |-
        ${{ fromJson(needs.parse-config.outputs.cfg).cloudflare.copy_files || '' }}
      cloudflare_copy_dirs: |-
        ${{ fromJson(needs.parse-config.outputs.cfg).cloudflare.copy_dirs || '' }}
      cloudflare_prebuild_command:          ${{ fromJson(needs.parse-config.outputs.cfg).cloudflare.prebuild_command || '' }}
      cloudflare_working_directory:         ${{ fromJson(needs.parse-config.outputs.cfg).cloudflare.working_directory || '' }}
      cloudflare_branch:                    ${{ fromJson(needs.parse-config.outputs.cfg).cloudflare.branch || '' }}
      cloudflare_commit_message:            ${{ fromJson(needs.parse-config.outputs.cfg).cloudflare.commit_message || '' }}
      cloudflare_wrangler_version:          ${{ fromJson(needs.parse-config.outputs.cfg).cloudflare.wrangler_version || '' }}
      cloudflare_extra_wrangler_args:       ${{ fromJson(needs.parse-config.outputs.cfg).cloudflare.extra_wrangler_args || '' }}
      # Force gating: manual dispatch + `force_run` checkbox forces a
      # deploy past the hub's "only on default-branch pushes" default.
      cloudflare_deploy:                    ${{ github.event_name == 'workflow_dispatch' && inputs.force_run && 'true' || '' }}
      cloudflare_runs_on:                   ${{ fromJson(needs.parse-config.outputs.cfg).cloudflare.runs_on || '' }}
      cloudflare_checkout_fetch_depth:      ${{ fromJson(needs.parse-config.outputs.cfg).cloudflare.checkout_fetch_depth || 0 }}
      cloudflare_purge_cache:               ${{ fromJson(needs.parse-config.outputs.cfg).cloudflare.purge_cache != false }}

      # ----- Generators -----
      cloudflare_generate_sitemap:          ${{ fromJson(needs.parse-config.outputs.cfg).cloudflare.generate_sitemap == true }}
      cloudflare_generate_robots:           ${{ fromJson(needs.parse-config.outputs.cfg).cloudflare.generate_robots == true }}
      cloudflare_generate_security_txt:     ${{ fromJson(needs.parse-config.outputs.cfg).cloudflare.generate_security_txt == true }}
      cloudflare_security_contact:          ${{ fromJson(needs.parse-config.outputs.cfg).cloudflare.security_contact || '' }}
      cloudflare_generate_manifest:         ${{ fromJson(needs.parse-config.outputs.cfg).cloudflare.generate_manifest == true }}
      cloudflare_manifest_name:             ${{ fromJson(needs.parse-config.outputs.cfg).cloudflare.manifest_name || '' }}
      cloudflare_manifest_short_name:       ${{ fromJson(needs.parse-config.outputs.cfg).cloudflare.manifest_short_name || '' }}
      cloudflare_manifest_description:      ${{ fromJson(needs.parse-config.outputs.cfg).cloudflare.manifest_description || '' }}
      cloudflare_manifest_orientation:      ${{ fromJson(needs.parse-config.outputs.cfg).cloudflare.manifest_orientation || '' }}
      cloudflare_manifest_theme_color:      ${{ fromJson(needs.parse-config.outputs.cfg).cloudflare.manifest_theme_color || '' }}
      cloudflare_manifest_background_color: ${{ fromJson(needs.parse-config.outputs.cfg).cloudflare.manifest_background_color || '' }}
      cloudflare_manifest_lang:             ${{ fromJson(needs.parse-config.outputs.cfg).cloudflare.manifest_lang || '' }}
      cloudflare_manifest_dir:              ${{ fromJson(needs.parse-config.outputs.cfg).cloudflare.manifest_dir || '' }}
      cloudflare_manifest_categories:       ${{ fromJson(needs.parse-config.outputs.cfg).cloudflare.manifest_categories || '' }}
      cloudflare_manifest_icons_dir:        ${{ fromJson(needs.parse-config.outputs.cfg).cloudflare.manifest_icons_dir || '' }}
    secrets:
      CLOUDFLARE_API_TOKEN:  ${{ secrets.CLOUDFLARE_API_TOKEN }}
      CLOUDFLARE_ACCOUNT_ID: ${{ secrets.CLOUDFLARE_ACCOUNT_ID }}
      CLOUDFLARE_ZONE_ID:    ${{ secrets.CLOUDFLARE_ZONE_ID }}
"""


# --------------------------------------------------------------------------- #
# Templated whole-file canonical content (LICENSE / NOTICE / CODEOWNERS)      #
# --------------------------------------------------------------------------- #
#
# These three services are init-if-missing (the hub writes them ONLY when
# the target file is absent and NEVER overwrites a hand-edited version).
# They support `{{KEY}}` placeholder substitution from per-repo
# `bos-managed-files.yaml` config (see `_load_managed_config` below).
#
# Supported placeholders:
#
#   * `{{COPYRIGHT_HOLDER}}`     — from config; default "Blackout Secure".
#   * `{{COPYRIGHT_YEAR_RANGE}}` — auto-computed: `YYYY` when
#     `copyright_year_start` is current year (or unset), `YYYY-YYYY`
#     otherwise.
#   * `{{MAINTAINERS_TEAM}}`     — from config; default
#     "@blackoutsecure/maintainers".
#   * `{{REPO_NAME}}`            — repo name from `GITHUB_REPOSITORY`
#     (after the slash) or the workspace root basename.
#   * `{{REPO_OWNER}}`           — org from `GITHUB_REPOSITORY` (before
#     the slash) or `"blackoutsecure"`.
#
# LICENSE is intentionally verbatim Apache 2.0 with NO placeholders — the
# Apache convention is that the LICENSE file is a verbatim copy and any
# copyright / project identification belongs in NOTICE.
#
# LICENSE and NOTICE skip the "Initialized by hub" header injection so
# automatic license-detection tools (GitHub linguist, FOSSA, etc.) can
# still match the canonical SHA. CODEOWNERS uses `#` comments natively
# so the header is kept (helpful provenance for reviewers).

# Embedded verbatim from `LICENSE` at the hub repo root.
# DO NOT EDIT IN PLACE — re-run `scripts/sync-license-from-disk.py` if the
# canonical text ever needs to be refreshed (e.g., new section numbering
# from apache.org). The text below MUST byte-match `apache.org/licenses/
# LICENSE-2.0.txt` so license-detection tools recognize it.
_LICENSE_APACHE2 = """\
                                 Apache License
                           Version 2.0, January 2004
                        http://www.apache.org/licenses/

   TERMS AND CONDITIONS FOR USE, REPRODUCTION, AND DISTRIBUTION

   1. Definitions.

      "License" shall mean the terms and conditions for use, reproduction,
      and distribution as defined by Sections 1 through 9 of this document.

      "Licensor" shall mean the copyright owner or entity authorized by
      the copyright owner that is granting the License.

      "Legal Entity" shall mean the union of the acting entity and all
      other entities that control, are controlled by, or are under common
      control with that entity. For the purposes of this definition,
      "control" means (i) the power, direct or indirect, to cause the
      direction or management of such entity, whether by contract or
      otherwise, or (ii) ownership of fifty percent (50%) or more of the
      outstanding shares, or (iii) beneficial ownership of such entity.

      "You" (or "Your") shall mean an individual or Legal Entity
      exercising permissions granted by this License.

      "Source" form shall mean the preferred form for making modifications,
      including but not limited to software source code, documentation
      source, and configuration files.

      "Object" form shall mean any form resulting from mechanical
      transformation or translation of a Source form, including but
      not limited to compiled object code, generated documentation,
      and conversions to other media types.

      "Work" shall mean the work of authorship, whether in Source or
      Object form, made available under the License, as indicated by a
      copyright notice that is included in or attached to the work
      (an example is provided in the Appendix below).

      "Derivative Works" shall mean any work, whether in Source or Object
      form, that is based on (or derived from) the Work and for which the
      editorial revisions, annotations, elaborations, or other modifications
      represent, as a whole, an original work of authorship. For the purposes
      of this License, Derivative Works shall not include works that remain
      separable from, or merely link (or bind by name) to the interfaces of,
      the Work and Derivative Works thereof.

      "Contribution" shall mean any work of authorship, including
      the original version of the Work and any modifications or additions
      to that Work or Derivative Works thereof, that is intentionally
      submitted to Licensor for inclusion in the Work by the copyright owner
      or by an individual or Legal Entity authorized to submit on behalf of
      the copyright owner. For the purposes of this definition, "submitted"
      means any form of electronic, verbal, or written communication sent
      to the Licensor or its representatives, including but not limited to
      communication on electronic mailing lists, source code control systems,
      and issue tracking systems that are managed by, or on behalf of, the
      Licensor for the purpose of discussing and improving the Work, but
      excluding communication that is conspicuously marked or otherwise
      designated in writing by the copyright owner as "Not a Contribution."

      "Contributor" shall mean Licensor and any individual or Legal Entity
      on behalf of whom a Contribution has been received by Licensor and
      subsequently incorporated within the Work.

   2. Grant of Copyright License. Subject to the terms and conditions of
      this License, each Contributor hereby grants to You a perpetual,
      worldwide, non-exclusive, no-charge, royalty-free, irrevocable
      copyright license to reproduce, prepare Derivative Works of,
      publicly display, publicly perform, sublicense, and distribute the
      Work and such Derivative Works in Source or Object form.

   3. Grant of Patent License. Subject to the terms and conditions of
      this License, each Contributor hereby grants to You a perpetual,
      worldwide, non-exclusive, no-charge, royalty-free, irrevocable
      (except as stated in this section) patent license to make, have made,
      use, offer to sell, sell, import, and otherwise transfer the Work,
      where such license applies only to those patent claims licensable
      by such Contributor that are necessarily infringed by their
      Contribution(s) alone or by combination of their Contribution(s)
      with the Work to which such Contribution(s) was submitted. If You
      institute patent litigation against any entity (including a
      cross-claim or counterclaim in a lawsuit) alleging that the Work
      or a Contribution incorporated within the Work constitutes direct
      or contributory patent infringement, then any patent licenses
      granted to You under this License for that Work shall terminate
      as of the date such litigation is filed.

   4. Redistribution. You may reproduce and distribute copies of the
      Work or Derivative Works thereof in any medium, with or without
      modifications, and in Source or Object form, provided that You
      meet the following conditions:

      (a) You must give any other recipients of the Work or
          Derivative Works a copy of this License; and

      (b) You must cause any modified files to carry prominent notices
          stating that You changed the files; and

      (c) You must retain, in the Source form of any Derivative Works
          that You distribute, all copyright, patent, trademark, and
          attribution notices from the Source form of the Work,
          excluding those notices that do not pertain to any part of
          the Derivative Works; and

      (d) If the Work includes a "NOTICE" text file as part of its
          distribution, then any Derivative Works that You distribute must
          include a readable copy of the attribution notices contained
          within such NOTICE file, excluding those notices that do not
          pertain to any part of the Derivative Works, in at least one
          of the following places: within a NOTICE text file distributed
          as part of the Derivative Works; within the Source form or
          documentation, if provided along with the Derivative Works; or,
          within a display generated by the Derivative Works, if and
          wherever such third-party notices normally appear. The contents
          of the NOTICE file are for informational purposes only and
          do not modify the License. You may add Your own attribution
          notices within Derivative Works that You distribute, alongside
          or as an addendum to the NOTICE text from the Work, provided
          that such additional attribution notices cannot be construed
          as modifying the License.

      You may add Your own copyright statement to Your modifications and
      may provide additional or different license terms and conditions
      for use, reproduction, or distribution of Your modifications, or
      for any such Derivative Works as a whole, provided Your use,
      reproduction, and distribution of the Work otherwise complies with
      the conditions stated in this License.

   5. Submission of Contributions. Unless You explicitly state otherwise,
      any Contribution intentionally submitted for inclusion in the Work
      by You to the Licensor shall be under the terms and conditions of
      this License, without any additional terms or conditions.
      Notwithstanding the above, nothing herein shall supersede or modify
      the terms of any separate license agreement you may have executed
      with Licensor regarding such Contributions.

   6. Trademarks. This License does not grant permission to use the trade
      names, trademarks, service marks, or product names of the Licensor,
      except as required for reasonable and customary use in describing the
      origin of the Work and reproducing the content of the NOTICE file.

   7. Disclaimer of Warranty. Unless required by applicable law or
      agreed to in writing, Licensor provides the Work (and each
      Contributor provides its Contributions) on an "AS IS" BASIS,
      WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
      implied, including, without limitation, any warranties or conditions
      of TITLE, NON-INFRINGEMENT, MERCHANTABILITY, or FITNESS FOR A
      PARTICULAR PURPOSE. You are solely responsible for determining the
      appropriateness of using or redistributing the Work and assume any
      risks associated with Your exercise of permissions under this License.

   8. Limitation of Liability. In no event and under no legal theory,
      whether in tort (including negligence), contract, or otherwise,
      unless required by applicable law (such as deliberate and grossly
      negligent acts) or agreed to in writing, shall any Contributor be
      liable to You for damages, including any direct, indirect, special,
      incidental, or consequential damages of any character arising as a
      result of this License or out of the use or inability to use the
      Work (including but not limited to damages for loss of goodwill,
      work stoppage, computer failure or malfunction, or any and all
      other commercial damages or losses), even if such Contributor
      has been advised of the possibility of such damages.

   9. Accepting Warranty or Additional Liability. While redistributing
      the Work or Derivative Works thereof, You may choose to offer,
      and charge a fee for, acceptance of support, warranty, indemnity,
      or other liability obligations and/or rights consistent with this
      License. However, in accepting such obligations, You may act only
      on Your own behalf and on Your sole responsibility, not on behalf
      of any other Contributor, and only if You agree to indemnify,
      defend, and hold each Contributor harmless for any liability
      incurred by, or claims asserted against, such Contributor by reason
      of your accepting any such warranty or additional liability.

   END OF TERMS AND CONDITIONS

   APPENDIX: How to apply the Apache License to your work.

      To apply the Apache License to your work, attach the following
      boilerplate notice, with the fields enclosed by brackets "[]"
      replaced with your own identifying information. (Don't include
      the brackets!)  The text should be enclosed in the appropriate
      comment syntax for the file format. We also recommend that a
      file or class name and description of purpose be included on the
      same "printed page" as the copyright notice for easier
      identification within third-party archives.

   Copyright [yyyy] [name of copyright owner]

   Licensed under the Apache License, Version 2.0 (the "License");
   you may not use this file except in compliance with the License.
   You may obtain a copy of the License at

       http://www.apache.org/licenses/LICENSE-2.0

   Unless required by applicable law or agreed to in writing, software
   distributed under the License is distributed on an "AS IS" BASIS,
   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
   See the License for the specific language governing permissions and
   limitations under the License.
"""

# MIT License (SPDX: MIT). The 2-paragraph permissive license that
# dominates the npm ecosystem. Year + copyright holder appear IN the
# license text (unlike Apache, where they live in NOTICE).
_LICENSE_MIT = """\
MIT License

Copyright (c) {{COPYRIGHT_YEAR_RANGE}} {{COPYRIGHT_HOLDER}}

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""


# BSD 3-Clause License (SPDX: BSD-3-Clause). The "Modified BSD License"
# — adds the no-endorsement clause to the original 2-clause BSD. Common
# in academic / networking projects. Note the comma after the year, per
# canonical SPDX template.
_LICENSE_BSD_3_CLAUSE = """\
BSD 3-Clause License

Copyright (c) {{COPYRIGHT_YEAR_RANGE}}, {{COPYRIGHT_HOLDER}}
All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

1. Redistributions of source code must retain the above copyright notice, this
   list of conditions and the following disclaimer.

2. Redistributions in binary form must reproduce the above copyright notice,
   this list of conditions and the following disclaimer in the documentation
   and/or other materials provided with the distribution.

3. Neither the name of the copyright holder nor the names of its
   contributors may be used to endorse or promote products derived from
   this software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
"""


# ISC License (SPDX: ISC). Functionally equivalent to MIT but shorter;
# used by npm itself and other Node tooling. Distinct SPDX identifier
# matters for downstream license-policy enforcement (some corporate
# license allowlists list MIT but not ISC, or vice versa).
_LICENSE_ISC = """\
ISC License

Copyright (c) {{COPYRIGHT_YEAR_RANGE}} {{COPYRIGHT_HOLDER}}

Permission to use, copy, modify, and/or distribute this software for any
purpose with or without fee is hereby granted, provided that the above
copyright notice and this permission notice appear in all copies.

THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES WITH
REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF MERCHANTABILITY
AND FITNESS. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR ANY SPECIAL, DIRECT,
INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES WHATSOEVER RESULTING FROM
LOSS OF USE, DATA OR PROFITS, WHETHER IN AN ACTION OF CONTRACT, NEGLIGENCE OR
OTHER TORTIOUS ACTION, ARISING OUT OF OR IN CONNECTION WITH THE USE OR
PERFORMANCE OF THIS SOFTWARE.
"""


# SPDX-keyed registry. The `license` service (init-if-missing,
# templated) reads `license_type` from `bos-managed-files.yaml` and
# resolves the rendered body from here at sync time. Keys MUST match
# the lowercased SPDX short identifier so consumers can copy-paste
# from spdx.org/licenses. Apache 2.0 has no placeholders by design
# (the Apache convention puts copyright in NOTICE); MIT/BSD/ISC carry
# `{{COPYRIGHT_YEAR_RANGE}}` and `{{COPYRIGHT_HOLDER}}` inline.
_LICENSE_REGISTRY: Dict[str, str] = {
    "apache-2.0": _LICENSE_APACHE2,
    "mit": _LICENSE_MIT,
    "bsd-3-clause": _LICENSE_BSD_3_CLAUSE,
    "isc": _LICENSE_ISC,
}


def _resolve_license_text(license_type: str) -> str:
    """Look up canonical text for `license_type` in `_LICENSE_REGISTRY`.
    Caller is responsible for validating `license_type` against
    `_LICENSE_REGISTRY.keys()` BEFORE calling — this function will
    raise KeyError otherwise (defensive; should never happen because
    the config loader validates eagerly)."""
    return _LICENSE_REGISTRY[license_type]


# --------------------------------------------------------------------------- #
# DEFERRED: managed-mode for NOTICE and CODEOWNERS                            #
# --------------------------------------------------------------------------- #
#
# The current LICENSE / NOTICE / CODEOWNERS services are all
# init-if-missing — the hub writes them ONCE on first sync and never
# overwrites afterwards. Two of these have legitimate dynamic-update
# use cases that aren't yet supported:
#
#   * NOTICE year-refresh: every Jan 1, `{COPYRIGHT_YEAR_RANGE}` should
#     auto-bump from e.g. "2024-2025" to "2024-2026" without manual
#     edits. Cleanest implementation: add a new `notice_apache2_managed`
#     service in SERVICE_FILES (whole-file overwrite mode) sharing the
#     same NOTICE path. Cross-registry path mutex needs an exemption
#     (see `_ALLOWED_CROSS_MODE_VARIANTS` design — TBD).
#
#   * CODEOWNERS team-rename propagation: when `maintainers_team` in
#     `bos-managed-files.yaml` changes, the catch-all rule should
#     update without clobbering per-path overrides the consumer has
#     added below. Cleanest implementation: SECTION mode (markers
#     around just the catch-all line), reusing the existing
#     `apply_block()` mechanism. Whole-file overwrite is too blunt
#     here — it would destroy per-path overrides.
#
# LICENSE-managed-mode is intentionally NOT planned: changing a
# distributed work's LICENSE is a deliberate legal act, not something
# CI should automate. The init-if-missing semantics correctly model
# "hub provides the initial LICENSE, human owns subsequent changes."
# A `license_type` change in config does NOT rewrite an existing
# LICENSE file — consumers must delete-and-resync to switch licenses,
# which forces a deliberate review.


# Apache 2.0 NOTICE template. Substitutions are applied per-repo by
# `_render_placeholders()`. The file format follows the convention from
# https://www.apache.org/foundation/license-faq.html#Required-Notice
# (project name, copyright line, then any required attributions).
_NOTICE_TEMPLATE = """\
{{REPO_NAME}}
Copyright {{COPYRIGHT_YEAR_RANGE}} {{COPYRIGHT_HOLDER}}

This product is part of the Blackout Secure open-source platform
(https://github.com/{{REPO_OWNER}}).

Licensed under the Apache License, Version 2.0. See LICENSE for the
full terms.
"""


# Default CODEOWNERS — single catch-all rule routing all PRs to the
# maintainers team. Consumers are encouraged to add per-path rules
# BELOW the catch-all (CODEOWNERS uses last-match-wins semantics).
# Init-if-missing — once the file exists, the hub leaves it alone.
_CODEOWNERS_TEMPLATE = """\
# CODEOWNERS — default reviewer routing.
#
# See https://docs.github.com/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/about-code-owners
# for the full pattern syntax. CODEOWNERS does NOT inherit from the
# org `.github` repo; each repo curates its own.
#
# Pattern matching is last-match-wins per path, so add per-path
# overrides BELOW this default catch-all (e.g. `*.md @docs-team`).

*  {{MAINTAINERS_TEAM}}
"""


# --------------------------------------------------------------------------- #
# Per-repo managed-files config (bos-managed-files.yaml)                      #
# --------------------------------------------------------------------------- #
#
# Tiny flat-YAML reader for per-repo placeholder values consumed by the
# `license`, `license_apache2`, `notice_apache2`, and `codeowners`
# services. Pure
# stdlib — sync.py intentionally has no third-party deps.
#
# Schema (all keys optional; defaults applied for missing keys):
#
#     copyright_holder: Blackout Secure
#     copyright_year_start: 2024
#     maintainers_team: "@blackoutsecure/maintainers"
#     license_type: apache-2.0
#     dependabot_target_branch: dev   # optional; for dev/main split repos
#
# `dependabot_target_branch` adds a `target-branch:` knob to EVERY
# enabled `dependabot_*` ecosystem block, so Dependabot PRs land on
# the named branch instead of the default branch. Use this on
# Marketplace Action repos that follow the dev/main split (CI
# workflows live on `dev`, Marketplace artifact lives on `main`).
# Leave empty (or omit the key) for the standard same-branch flow.
#
# Comments (`#` whole-line and inline-after-value) are stripped. Values
# may be unquoted, double-quoted, or single-quoted. Nesting, lists, and
# multi-line scalars are NOT supported — the file is intentionally tiny.
#
# The config file is `bos-managed-files.yaml` at the repo root —
# deliberately NOT a dotfile so it shows up in `ls`, file pickers, and
# code reviews without needing `ls -a`.

MANAGED_FILES_CONFIG_FILENAME = "bos-managed-files.yaml"

_DEFAULT_MANAGED_CONFIG: Dict[str, str] = {
    "copyright_holder": "Blackout Secure",
    "copyright_year_start": "",
    "maintainers_team": "@blackoutsecure/maintainers",
    # SPDX short identifier (lowercased). Used by the `license` service
    # to pick which canonical text from `_LICENSE_REGISTRY` to render.
    # Must be one of the keys in `_LICENSE_REGISTRY`. The deprecated
    # alias service `license_apache2` IGNORES this and always emits
    # Apache 2.0 — useful when a repo wants to keep its existing
    # license while the org default shifts.
    "license_type": "apache-2.0",
    # Optional `target-branch:` override applied to every enabled
    # `dependabot_*` ecosystem block. Leave empty for the default
    # same-branch flow (Dependabot opens PRs against the repo's
    # default branch). Set to a branch name (e.g. "dev") for repos
    # using the dev/main split publishing pattern where the
    # workflow files live on `dev` and `main` is a curated artifact.
    "dependabot_target_branch": "",
}

_KNOWN_CONFIG_KEYS = frozenset(_DEFAULT_MANAGED_CONFIG.keys())

_KNOWN_PLACEHOLDERS = frozenset({
    "COPYRIGHT_HOLDER",
    "COPYRIGHT_YEAR_RANGE",
    "MAINTAINERS_TEAM",
    "REPO_NAME",
    "REPO_OWNER",
    # Resolves to either "" (no override) or "\n    target-branch:
    # <name>" — the leading newline + 4-space indent are part of the
    # substitution because the placeholder sits at the END of the
    # `directory: /` line inside the `_DEPENDABOT_*` block bodies.
    # See `_DEFAULT_MANAGED_CONFIG["dependabot_target_branch"]` for
    # the user-facing knob.
    "DEPENDABOT_TARGET_BRANCH_LINE",
})

_PLACEHOLDER_RE = re.compile(r"\{\{([A-Z_][A-Z0-9_]*)\}\}")

# Git branch names allowed for `dependabot_target_branch`. Permissive
# but rejects whitespace, control chars, leading dash, and other
# garbage that would crash Dependabot at parse time. Full Git
# branch-name validation is intentionally out of scope — anything
# pathological that slips through here will fail loudly on the
# Dependabot side, not silently.
_DEPENDABOT_BRANCH_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/\-]{0,99}$")

# Flat-YAML line: `key: value` or `key: "value"` or `key: 'value'`.
# Anchored to allow leading whitespace (tolerated even though flat YAML
# shouldn't have indentation).
_FLAT_YAML_LINE_RE = re.compile(
    r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*:\s*"
    r"(?:\"([^\"]*)\"|'([^']*)'|([^#\n]*?))\s*(?:#.*)?$"
)


def _parse_flat_yaml(text: str) -> Dict[str, str]:
    """Parse a tiny subset of YAML: flat key/value pairs, optional quoting,
    `#` comments, blank lines. Unknown keys are rejected with `die()` so
    typos fail fast rather than silently fall through to defaults.

    Returns a dict of {key: value}. Caller merges with defaults.
    """
    result: Dict[str, str] = {}
    for lineno, raw in enumerate(text.splitlines(), start=1):
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        m = _FLAT_YAML_LINE_RE.match(raw)
        if not m:
            die(
                f"{MANAGED_FILES_CONFIG_FILENAME}:{lineno}: cannot parse line: "
                f"{raw!r}. Expected `key: value` or `key: \"value\"`."
            )
        key = m.group(1)
        # First non-None capture group of (double, single, bare) is the value.
        value = m.group(2) if m.group(2) is not None else (
            m.group(3) if m.group(3) is not None else (m.group(4) or "")
        )
        value = value.strip()
        if key not in _KNOWN_CONFIG_KEYS:
            die(
                f"{MANAGED_FILES_CONFIG_FILENAME}:{lineno}: unknown key "
                f"'{key}'. Known: {', '.join(sorted(_KNOWN_CONFIG_KEYS))}."
            )
        result[key] = value
    return result


def _load_managed_config(root: str) -> Dict[str, str]:
    """Read `bos-managed-files.yaml` if present at `root`; return the
    parsed config merged over the defaults. Missing file is fine —
    defaults are used."""
    config_path = os.path.join(root, MANAGED_FILES_CONFIG_FILENAME)
    merged = dict(_DEFAULT_MANAGED_CONFIG)
    if not os.path.exists(config_path):
        return merged
    with open(config_path, "r", encoding="utf-8") as fh:
        parsed = _parse_flat_yaml(fh.read())
    merged.update(parsed)

    # Validate license_type eagerly so an unknown SPDX ID fails fast
    # (with a helpful list of supported types) rather than crashing
    # deep inside the init-files loop with a KeyError.
    license_type = merged.get("license_type", "").strip().lower()
    if license_type and license_type not in _LICENSE_REGISTRY:
        die(
            f"{MANAGED_FILES_CONFIG_FILENAME}: 'license_type' "
            f"({license_type!r}) is not a supported SPDX identifier. "
            f"Supported: {', '.join(sorted(_LICENSE_REGISTRY))}."
        )
    merged["license_type"] = license_type or _DEFAULT_MANAGED_CONFIG["license_type"]

    # Validate dependabot_target_branch eagerly. Empty is fine (means
    # "use Dependabot's default = the repo's default branch"). Any
    # non-empty value MUST match the permissive Git branch-name
    # regex — otherwise the resulting `.github/dependabot.yml` would
    # be syntactically valid YAML but reject by Dependabot's own
    # branch resolver.
    dep_branch = merged.get("dependabot_target_branch", "").strip()
    if dep_branch and not _DEPENDABOT_BRANCH_NAME_RE.match(dep_branch):
        die(
            f"{MANAGED_FILES_CONFIG_FILENAME}: "
            f"'dependabot_target_branch' ({dep_branch!r}) is not a "
            f"valid Git branch name. Allowed: starts with an "
            f"alphanumeric, then alphanumerics / `.` / `_` / `/` / "
            f"`-` (max 100 chars). Common values: 'dev', 'main', "
            f"'develop'."
        )
    merged["dependabot_target_branch"] = dep_branch

    return merged


def _resolve_repo_full_name(root: str) -> str:
    """Best-effort `owner/repo` resolution. Prefers `GITHUB_REPOSITORY`
    (set by every GHA runner). Falls back to the workspace basename
    prefixed with the canonical org so local smoke tests still produce
    sane output."""
    env = os.environ.get("GITHUB_REPOSITORY", "").strip()
    if "/" in env:
        return env
    basename = os.path.basename(os.path.abspath(root)) or "repo"
    return f"blackoutsecure/{basename}"


def _resolve_placeholders(
    config: Dict[str, str], repo_full_name: str
) -> Dict[str, str]:
    """Build the final substitution dict from config + env.

    Year range: `copyright_year_start` empty or equal to current year
    yields just `YYYY`; otherwise `YYYY-YYYY` (start-current).
    """
    import datetime

    current_year = datetime.datetime.now(datetime.timezone.utc).year
    start_raw = (config.get("copyright_year_start") or "").strip()
    if start_raw:
        try:
            start_year = int(start_raw)
        except ValueError:
            die(
                f"{MANAGED_FILES_CONFIG_FILENAME}: 'copyright_year_start' "
                f"must be an integer year (got: {start_raw!r})."
            )
        if start_year < 1970 or start_year > current_year:
            die(
                f"{MANAGED_FILES_CONFIG_FILENAME}: 'copyright_year_start' "
                f"({start_year}) must be between 1970 and {current_year}."
            )
        year_range = (
            f"{start_year}" if start_year == current_year
            else f"{start_year}-{current_year}"
        )
    else:
        year_range = f"{current_year}"

    owner, _, repo = repo_full_name.partition("/")

    # Render `dependabot_target_branch` into a ready-to-splice line
    # fragment. Empty config value → empty substitution (block stays
    # unchanged). Non-empty → leading `\n` + 4-space indent so the
    # token sitting at the end of `directory: /` opens a new YAML
    # line at the correct indent for the surrounding list item.
    dep_branch = (config.get("dependabot_target_branch") or "").strip()
    dep_branch_line = (
        f"\n    target-branch: {dep_branch}" if dep_branch else ""
    )

    return {
        "COPYRIGHT_HOLDER": config["copyright_holder"],
        "COPYRIGHT_YEAR_RANGE": year_range,
        "MAINTAINERS_TEAM": config["maintainers_team"],
        "REPO_NAME": repo or "repo",
        "REPO_OWNER": owner or "blackoutsecure",
        "DEPENDABOT_TARGET_BRANCH_LINE": dep_branch_line,
    }


def _render_placeholders(body: str, subs: Dict[str, str]) -> str:
    """Substitute `{{KEY}}` markers in `body` using `subs`. Unknown
    placeholders (not in `_KNOWN_PLACEHOLDERS`) are left untouched — we
    don't want to silently rewrite content that happens to look like a
    marker. Known placeholders without a value also pass through (caller
    is responsible for ensuring `subs` covers every known key)."""
    def _sub(match: "re.Match[str]") -> str:
        key = match.group(1)
        if key in _KNOWN_PLACEHOLDERS and key in subs:
            return subs[key]
        return match.group(0)
    return _PLACEHOLDER_RE.sub(_sub, body)


# Init-if-missing services whose canonical body is committed VERBATIM
# (no `# Initialized by ...` header injection). LICENSE and NOTICE files
# MUST stay byte-identical to the canonical form so license-detection
# tools (GitHub linguist, FOSSA, etc.) can recognize the file by SHA /
# fuzzy hash. Any service NOT in this set goes through the standard
# `_make_init_file()` header injection. Both `license` (multi-type) and
# `license_apache2` (alias) are listed — they share the LICENSE path
# and the no-header requirement is per-PATH-content, not per-service.
_NO_HEADER_INIT_SERVICES = frozenset({
    "license",
    "license_apache2",
    "notice_apache2",
})

# Init-if-missing services whose body contains `{{KEY}}` placeholders
# that must be rendered at sync time (not registry time, since values
# vary per-repo). Pre-flight: every such service's canonical body MUST
# only reference placeholder names in `_KNOWN_PLACEHOLDERS`; an unknown
# `{{KEY}}` would silently land in the consumer's file.
#
# `license` is templated because MIT/BSD-3-Clause/ISC carry
# `{{COPYRIGHT_YEAR_RANGE}}` and `{{COPYRIGHT_HOLDER}}` INSIDE the
# license text (only Apache 2.0 puts those in NOTICE instead, but
# `license` covers all four types via dynamic resolution — see
# `_DYNAMIC_LICENSE_INIT_SERVICES`).
_TEMPLATED_INIT_SERVICES = frozenset({
    "license",
    "notice_apache2",
    "codeowners",
})

# Init-if-missing services whose body is RESOLVED at sync time from
# `_LICENSE_REGISTRY` based on `license_type` in `bos-managed-files.yaml`.
# The body registered in `SERVICE_INIT_FILES` for these services is
# a fallback placeholder; the init-loop swaps it for the right canonical
# text BEFORE placeholder rendering. The `license_apache2` alias is
# intentionally NOT here — it always emits Apache 2.0 regardless of
# config, preserving backward compatibility for early adopters.
_DYNAMIC_LICENSE_INIT_SERVICES = frozenset({"license"})

# Section-mode services whose body contains `{{KEY}}` placeholders
# that must be rendered at sync time (not registry time, since values
# vary per-repo). Currently only the `dependabot_*` services carry a
# placeholder — the `{{DEPENDABOT_TARGET_BRANCH_LINE}}` token that
# expands to either empty (default) or `\n    target-branch: <name>`
# when the consumer's `bos-managed-files.yaml` sets the
# `dependabot_target_branch` knob.
#
# Same registry-time validation as `_TEMPLATED_INIT_SERVICES`: every
# `{{...}}` reference inside a templated section body MUST be in
# `_KNOWN_PLACEHOLDERS`; an unknown placeholder would silently slip
# into the consumer's `.github/dependabot.yml`.
_TEMPLATED_SECTION_SERVICES = frozenset({
    "dependabot_actions",
    "dependabot_npm",
    "dependabot_pip",
})


# --------------------------------------------------------------------------- #
# Service registry                                                            #
# --------------------------------------------------------------------------- #
#
# Three registries — see module docstring for the section vs whole-file
# vs init-if-missing distinction.
#
# `SERVICE_BLOCKS`     — per-service: ordered dict of {file_path:
#   block_body}. Section mode. When a service contributes to multiple
#   files, each file is processed independently. Existing blocks
#   (matched by marker pair) are replaced in place; missing blocks are
#   appended. Content OUTSIDE the markers is never read or written.
#
# `SERVICE_FILES`      — per-service: ordered dict of {file_path:
#   full_body}. Whole-file mode. The hub overwrites the file outright
#   on every run. Cross-mode uniqueness per path is enforced (a path
#   cannot appear in both whole-file AND section/init), but MULTIPLE
#   whole-file services MAY target the same path (e.g. the two
#   `bos_launchpad_*` kickers both target
#   `.github/workflows/bos-launchpad.yml`) — at most one may be
#   enabled per repo, enforced at parse time by `parse_services()`.
#
# `SERVICE_INIT_FILES` — per-service: ordered dict of {file_path:
#   full_body}. Init-if-missing mode. The hub writes the file ONLY if
#   it does not already exist. On subsequent runs (file present) the
#   hub does nothing. Use for starter templates the consumer is
#   expected to customize (e.g. CI workflows). Cross-mode uniqueness
#   per path is enforced, but MULTIPLE init services MAY target the
#   same path — at most one may be enabled per repo, enforced at parse
#   time by `parse_services()`.

SERVICE_BLOCKS: Dict[str, Dict[str, str]] = {
    "common": {
        ".gitignore": _GITIGNORE_COMMON,
        ".editorconfig": _EDITORCONFIG_COMMON,
    },
    "docker": {
        ".dockerignore": _DOCKERIGNORE_DOCKER,
    },
    "balena": {
        ".dockerignore": _DOCKERIGNORE_BALENA,
    },
    "python": {
        ".gitignore": _GITIGNORE_PYTHON,
        ".dockerignore": _DOCKERIGNORE_PYTHON,
    },
    "node": {
        ".gitignore": _GITIGNORE_NODE,
        ".dockerignore": _DOCKERIGNORE_NODE,
    },
    "lf_line_endings": {
        ".gitattributes": _GITATTRIBUTES_LF,
    },
    "dependabot_actions": {
        ".github/dependabot.yml": _DEPENDABOT_ACTIONS,
    },
    "dependabot_npm": {
        ".github/dependabot.yml": _DEPENDABOT_NPM,
    },
    "dependabot_pip": {
        ".github/dependabot.yml": _DEPENDABOT_PIP,
    },
}

# For fresh-file creation in section mode: any file listed here is
# created with the given header BEFORE any service blocks are appended.
# Existing files (header may differ or be missing entirely) are NOT
# touched — `apply_block()` only ever rewrites content inside its own
# marker pair.
#
# `.github/dependabot.yml` requires a top-level `version: 2\nupdates:\n`
# scaffold or the YAML is invalid. Each `dependabot_*` service then
# contributes ONE list item under `updates:` as a markered block.
SECTION_FILE_HEADERS: Dict[str, str] = {
    ".github/dependabot.yml": "version: 2\nupdates:\n",
}

SERVICE_FILES: Dict[str, Dict[str, str]] = {
    "logger": {
        "root/usr/local/bin/log-functions.sh": _LOG_FUNCTIONS_SH,
    },
    "prettier": {
        ".prettierrc.yaml": _PRETTIERRC_YAML,
    },
    "bos_launchpad_release": {
        ".github/workflows/bos-launchpad.yml": _BOS_LAUNCHPAD_RELEASE_YML,
    },
    "bos_launchpad_cf_pages": {
        ".github/workflows/bos-launchpad.yml": _BOS_LAUNCHPAD_CF_PAGES_YML,
    },
}

SERVICE_INIT_FILES: Dict[str, Dict[str, str]] = {
    "gha_sync_commit": {
        ".github/workflows/sync-managed-files.yml": _GHA_SYNC_COMMIT_YML,
    },
    "gha_sync_drift_check": {
        ".github/workflows/sync-drift-check.yml": _GHA_SYNC_DRIFT_CHECK_YML,
    },
    "gha_lint_node": {
        ".github/workflows/lint.yml": _GHA_LINT_NODE_YML,
    },
    "gha_lint_python": {
        ".github/workflows/lint.yml": _GHA_LINT_PYTHON_YML,
    },
    "gha_lint_shell": {
        ".github/workflows/lint.yml": _GHA_LINT_SHELL_YML,
    },
    # Templated whole-file content (init-if-missing). Values for
    # `{{KEY}}` placeholders come from `bos-managed-files.yaml` at
    # the consumer repo root (see `_load_managed_config`).
    #
    # `license` resolves its body DYNAMICALLY at sync time by looking
    # up `license_type` from config in `_LICENSE_REGISTRY`. The
    # registered body below is just a fallback placeholder — the
    # real text is swapped in by the init-loop. See
    # `_DYNAMIC_LICENSE_INIT_SERVICES`.
    #
    # `license_apache2` is a deprecated back-compat alias that ALWAYS
    # emits Apache 2.0, ignoring `license_type` in config. Existing
    # consumers using `license_apache2` continue to work unchanged;
    # new consumers should use `license` + set `license_type` in
    # `bos-managed-files.yaml`. Both target `LICENSE` and are
    # mutually exclusive per repo (enforced at parse time).
    "license": {
        "LICENSE": _LICENSE_APACHE2,
    },
    "license_apache2": {
        "LICENSE": _LICENSE_APACHE2,
    },
    "notice_apache2": {
        "NOTICE": _NOTICE_TEMPLATE,
    },
    "codeowners": {
        ".github/CODEOWNERS": _CODEOWNERS_TEMPLATE,
    },
}

KNOWN_SERVICES = (
    list(SERVICE_BLOCKS.keys())
    + list(SERVICE_FILES.keys())
    + list(SERVICE_INIT_FILES.keys())
)

# Cross-registry path conflicts: a single file path may only be claimed
# by ONE registry mode. Within SERVICE_FILES, MULTIPLE services MAY
# target the same path (e.g. `bos_launchpad_release` /
# `bos_launchpad_cf_pages` both write
# `.github/workflows/bos-launchpad.yml`) — at most one may be enabled
# per repo, enforced at parse time by `parse_services()`. Within
# SERVICE_INIT_FILES, MULTIPLE services may also target the same path
# (e.g. `gha_lint_node` / `gha_lint_python` / `gha_lint_shell` all
# write `.github/workflows/lint.yml`) with the same mutex semantics.
# All checks run at import so a registry typo fails CI immediately
# rather than at runtime.
_whole_file_owners: Dict[str, List[str]] = {}
for _svc, _files in SERVICE_FILES.items():
    for _path in _files:
        _whole_file_owners.setdefault(_path, []).append(_svc)

_section_paths = {
    _path for _blocks in SERVICE_BLOCKS.values() for _path in _blocks
}
_init_paths = {
    _path for _files in SERVICE_INIT_FILES.values() for _path in _files
}

for _path, _svcs in _whole_file_owners.items():
    if _path in _section_paths:
        raise RuntimeError(
            f"sync.py registry conflict: file '{_path}' is registered as "
            f"both a SECTION target and a WHOLE-FILE target "
            f"(SERVICE_FILES{_svcs!r}) — these modes are mutually "
            f"exclusive per path."
        )
    if _path in _init_paths:
        raise RuntimeError(
            f"sync.py registry conflict: file '{_path}' is registered as "
            f"both a WHOLE-FILE target (SERVICE_FILES{_svcs!r}) and an "
            f"INIT-IF-MISSING target — these modes are mutually exclusive "
            f"per path."
        )
for _path in _section_paths & _init_paths:
    raise RuntimeError(
        f"sync.py registry conflict: file '{_path}' is registered as both "
        f"a SECTION target and an INIT-IF-MISSING target — these modes "
        f"are mutually exclusive per path."
    )

# Templated init services must reference only known placeholders. An
# unknown `{{X}}` would be silently passed through by `_render_placeholders`
# and land in the consumer's committed file, so catch typos at import.
for _svc in _TEMPLATED_INIT_SERVICES:
    if _svc not in SERVICE_INIT_FILES:
        raise RuntimeError(
            f"sync.py registry conflict: '{_svc}' is listed in "
            f"_TEMPLATED_INIT_SERVICES but missing from SERVICE_INIT_FILES."
        )
    for _path, _body in SERVICE_INIT_FILES[_svc].items():
        for _ph_match in _PLACEHOLDER_RE.finditer(_body):
            _ph = _ph_match.group(1)
            if _ph not in _KNOWN_PLACEHOLDERS:
                raise RuntimeError(
                    f"sync.py: service '{_svc}' body for '{_path}' "
                    f"references unknown placeholder '{{{{{_ph}}}}}'. "
                    f"Known: {sorted(_KNOWN_PLACEHOLDERS)}."
                )

# Dynamic-license services pick their body from `_LICENSE_REGISTRY` at
# sync time, so we also need to validate the placeholders inside EVERY
# registered license text — not just the fallback body registered in
# SERVICE_INIT_FILES. Apache 2.0 has no placeholders; MIT/BSD/ISC do.
for _svc in _DYNAMIC_LICENSE_INIT_SERVICES:
    if _svc not in _TEMPLATED_INIT_SERVICES:
        raise RuntimeError(
            f"sync.py: dynamic-license service '{_svc}' must also be "
            f"in _TEMPLATED_INIT_SERVICES (license texts carry "
            f"placeholders)."
        )
    for _lic_type, _lic_body in _LICENSE_REGISTRY.items():
        for _ph_match in _PLACEHOLDER_RE.finditer(_lic_body):
            _ph = _ph_match.group(1)
            if _ph not in _KNOWN_PLACEHOLDERS:
                raise RuntimeError(
                    f"sync.py: _LICENSE_REGISTRY[{_lic_type!r}] "
                    f"references unknown placeholder '{{{{{_ph}}}}}'. "
                    f"Known: {sorted(_KNOWN_PLACEHOLDERS)}."
                )

# Templated section services must reference only known placeholders.
# Mirror of the `_TEMPLATED_INIT_SERVICES` check above. An unknown
# `{{X}}` reference inside a `_DEPENDABOT_*` body would be silently
# passed through by `_render_placeholders` and land verbatim in the
# consumer's `.github/dependabot.yml`, breaking the YAML.
for _svc in _TEMPLATED_SECTION_SERVICES:
    if _svc not in SERVICE_BLOCKS:
        raise RuntimeError(
            f"sync.py registry conflict: '{_svc}' is listed in "
            f"_TEMPLATED_SECTION_SERVICES but missing from SERVICE_BLOCKS."
        )
    for _path, _body in SERVICE_BLOCKS[_svc].items():
        for _ph_match in _PLACEHOLDER_RE.finditer(_body):
            _ph = _ph_match.group(1)
            if _ph not in _KNOWN_PLACEHOLDERS:
                raise RuntimeError(
                    f"sync.py: service '{_svc}' body for '{_path}' "
                    f"references unknown placeholder '{{{{{_ph}}}}}'. "
                    f"Known: {sorted(_KNOWN_PLACEHOLDERS)}."
                )

# Tidy module scope. The list of names mirrors every loop-variable
# introduced above; if you add another sanity check, append its names
# here to keep `dir()` clean.
del (
    _whole_file_owners,
    _section_paths,
    _init_paths,
    _svc,
    _svcs,
    _files,
    _path,
    _body,
    _ph,
    _ph_match,
    _lic_type,
    _lic_body,
)


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #


def die(msg: str) -> "NoReturn":  # type: ignore[name-defined]
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def parse_services(raw: str) -> List[str]:
    """Parse the SERVICES input. Newline OR whitespace separated. Strip
    blank lines and `#` comment lines (whole-line only, not inline).
    Preserve order; dedupe.

    Additionally enforces that at most ONE whole-file service AND at
    most ONE init-if-missing service may target a given path per repo.
    The registry-level check at import time only catches the case
    where two services accidentally target the same path AT THE PYTHON
    LEVEL — for both SERVICE_FILES (e.g. `bos_launchpad_release` vs
    `bos_launchpad_cf_pages`) and SERVICE_INIT_FILES (e.g.
    `gha_lint_*` variants) we deliberately allow that (so each flavor
    can ship its own canonical body) and instead enforce the mutual
    exclusion at parse time against the caller's `services:` list.
    """
    seen = set()
    result: List[str] = []
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        for tok in stripped.split():
            if not tok:
                continue
            if tok not in KNOWN_SERVICES:
                die(
                    f"unknown service '{tok}'. "
                    f"Known: {', '.join(KNOWN_SERVICES)}"
                )
            if tok in seen:
                continue
            seen.add(tok)
            result.append(tok)
    if not result:
        die("input 'services' resolved to zero entries")

    # Reject ≥2 enabled whole-file services that target the same path
    # (e.g. `bos_launchpad_release` + `bos_launchpad_cf_pages` both
    # writing `.github/workflows/bos-launchpad.yml`). Reported with
    # both service names AND the contested path so the caller can fix
    # their list.
    whole_path_owner: Dict[str, str] = {}
    for svc in result:
        if svc not in SERVICE_FILES:
            continue
        for path in SERVICE_FILES[svc]:
            if path in whole_path_owner:
                die(
                    f"services '{whole_path_owner[path]}' and '{svc}' both "
                    f"target whole-file path '{path}'. Enable at most "
                    f"one of them per repo."
                )
            whole_path_owner[path] = svc

    # Reject ≥2 enabled init-if-missing services that target the same
    # path (e.g. `gha_lint_node` + `gha_lint_python` both writing
    # `.github/workflows/lint.yml`). Reported with both service names
    # AND the contested path so the caller can fix their list.
    init_path_owner: Dict[str, str] = {}
    for svc in result:
        if svc not in SERVICE_INIT_FILES:
            continue
        for path in SERVICE_INIT_FILES[svc]:
            if path in init_path_owner:
                die(
                    f"services '{init_path_owner[path]}' and '{svc}' both "
                    f"target init-if-missing path '{path}'. Enable at "
                    f"most one of them per repo."
                )
            init_path_owner[path] = svc

    return result


def make_block(service: str, body: str) -> str:
    """Wrap `body` (already ending with `\\n`) in the marker lines."""
    if not body.endswith("\n"):
        body = body + "\n"
    open_marker = f"# >>> {MARKER_NAMESPACE}:{service} >>>"
    close_marker = f"# <<< {MARKER_NAMESPACE}:{service} <<<"
    return f"{open_marker}\n# {MARKER_NOTE}\n{body}{close_marker}\n"


# Header injected at the top of every whole-file managed asset. Uses
# `#` comments which is correct for all current whole-file targets
# (shell scripts). If we add a non-`#`-comment target in the future
# (e.g. an XML or JSON file), upgrade this to a per-file-extension
# comment style table rather than hard-coding `#` here.
_WHOLE_FILE_HEADER_TEMPLATE = (
    "# Managed by https://github.com/blackoutsecure/bos-automation-hub —\n"
    "# do not edit. To modify, update the `{service}` service in\n"
    "# .github/actions/sync-managed-files/sync.py.\n"
    "#\n"
)


def _make_whole_file(service: str, body: str) -> str:
    """Return ``body`` with a 'Managed by …' header injected after the
    shebang (if present, line 1) so editors and `head` immediately
    reveal the file is hub-managed. Ensures the result ends with
    exactly one trailing newline."""
    if not body.endswith("\n"):
        body = body + "\n"
    header = _WHOLE_FILE_HEADER_TEMPLATE.format(service=service)
    lines = body.splitlines(keepends=True)
    if lines and lines[0].startswith("#!"):
        return lines[0] + header + "".join(lines[1:])
    return header + body


# Header injected at the top of every init-if-missing asset on FIRST
# write. Distinct wording from `_WHOLE_FILE_HEADER_TEMPLATE` so an
# editor inspecting `head -4` of the file can tell the two modes apart.
# Uses `#` comments — see the equivalent comment on
# `_WHOLE_FILE_HEADER_TEMPLATE` for the future-proofing note.
_INIT_FILE_HEADER_TEMPLATE = (
    "# Initialized by https://github.com/blackoutsecure/bos-automation-hub —\n"
    "# starter template, SAFE to customize. The hub writes this file ONLY\n"
    "# when missing and will NEVER overwrite a hand-edited version.\n"
    "# Source: .github/actions/sync-managed-files/sync.py (`{service}` service).\n"
    "#\n"
)


def _make_init_file(service: str, body: str) -> str:
    """Return ``body`` with an 'Initialized by …' header injected after
    the shebang (if present). Same shebang rule as `_make_whole_file`;
    the header just carries different wording so consumers know the
    file is safe to edit."""
    if not body.endswith("\n"):
        body = body + "\n"
    header = _INIT_FILE_HEADER_TEMPLATE.format(service=service)
    lines = body.splitlines(keepends=True)
    if lines and lines[0].startswith("#!"):
        return lines[0] + header + "".join(lines[1:])
    return header + body


def block_pattern(service: str) -> "re.Pattern[str]":
    """Compile a regex matching the entire managed block for `service`,
    including the marker lines and any trailing newline. Tolerates
    whitespace variation in marker lines so legacy hand-edits still
    match."""
    svc_re = re.escape(service)
    ns_re = re.escape(MARKER_NAMESPACE)
    return re.compile(
        rf"^#\s*>>>\s*{ns_re}:{svc_re}\s*>>>.*?\n"
        rf"(?:.*?\n)*?"
        rf"^#\s*<<<\s*{ns_re}:{svc_re}\s*<<<.*?\n",
        re.MULTILINE,
    )


def apply_block(content: str, service: str, body: str) -> str:
    """Return updated file content with `service`'s managed block
    inserted or replaced. Existing block (if any) is replaced in place;
    otherwise the block is appended to the end of the file."""
    new_block = make_block(service, body)
    pattern = block_pattern(service)
    if pattern.search(content):
        return pattern.sub(_escape_replacement(new_block), content, count=1)
    # Append.  Ensure file ends with exactly one newline before the new block,
    # and that the new block is preceded by a blank-line separator if the
    # file is non-empty.
    if content and not content.endswith("\n"):
        content = content + "\n"
    if content:
        return content + "\n" + new_block
    return new_block


def _escape_replacement(s: str) -> str:
    """`re.sub` interprets backslashes in the replacement string. Escape
    them so canonical content is inserted byte-for-byte."""
    return s.replace("\\", "\\\\")


class FileChange:
    """Pending change for a single file. Hand-rolled (not a dataclass)
    so this module loads cleanly under Python 3.9 when imported via
    `importlib.util.spec_from_file_location` for local self-tests; the
    `dataclasses` interaction with PEP 563 string annotations breaks
    there. GitHub runners use Python 3.10+ where either form works."""

    __slots__ = ("path", "before", "after")

    def __init__(self, path: str, before: str, after: str) -> None:
        self.path = path
        self.before = before
        self.after = after

    @property
    def changed(self) -> bool:
        return self.before != self.after

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, FileChange):
            return NotImplemented
        return (
            self.path == other.path
            and self.before == other.before
            and self.after == other.after
        )

    def __repr__(self) -> str:
        return f"FileChange(path={self.path!r}, changed={self.changed})"


def _emit_composite_license_notice(root: str, services: List[str]) -> None:
    """Warn (via `::notice::`) when a consumer enables `license` /
    `license_apache2` on a repo whose existing `LICENSE` looks like
    a composite or forked work (multiple distinct copyright holders,
    separator lines between sections, or significantly larger than
    canonical SPDX text).

    The hub does NOT modify existing LICENSE files — init-if-missing
    semantics short-circuit before any write. This function is purely
    a heads-up: maintainers who don't realize their LICENSE has
    appended upstream/sub-licenses could destroy that content by
    doing `git rm LICENSE && <resync>` to switch `license_type`.

    Real-world examples in the Blackout Secure org:
      * docker-readsb     — GPL-3.0+ Docker packaging + dump1090 BSD
      * docker-dump978    — MIT Docker packaging + FlightAware BSD-2
      * docker-graphs1090 — MIT Docker packaging + upstream MIT
      * docker-tar1090    — GPL-2.0+ Docker packaging + upstream

    None of those repos currently enable the `license` service, but
    this guard ensures they get a loud notice if anyone ever does.
    """
    if "license" not in services and "license_apache2" not in services:
        return
    license_path = os.path.join(root, "LICENSE")
    if not os.path.exists(license_path):
        return  # init-if-missing path; no existing file to protect
    try:
        with open(license_path, "r", encoding="utf-8") as fh:
            existing = fh.read()
    except OSError:
        return  # unreadable; let the normal flow handle it

    size = len(existing)
    copyrights = re.findall(
        r"^Copyright\s+\([cC]\)\s+.+$", existing, re.MULTILINE
    )
    distinct_copyrights = {c.strip() for c in copyrights}
    has_separator = bool(re.search(r"^-{20,}$", existing, re.MULTILINE))

    signals = []
    if len(distinct_copyrights) >= 2:
        signals.append(
            f"{len(distinct_copyrights)} distinct Copyright lines"
        )
    if has_separator:
        signals.append("section-separator lines")
    # Canonical Apache 2.0 is the largest text in `_LICENSE_REGISTRY`
    # at ~11357 bytes. Anything notably larger is almost certainly
    # composite (appended upstream / sub-licenses).
    if size > 13000:
        signals.append(f"large file ({size} bytes)")

    if not signals:
        return  # looks like a single-license file; stay silent

    sys.stderr.write(
        f"::notice file=LICENSE::Existing LICENSE looks like a "
        f"composite/forked license ({'; '.join(signals)}). The hub "
        f"will NOT modify it (init-if-missing contract). If you "
        f"intend to switch license_type, do NOT just `git rm LICENSE` "
        f"and re-sync — the appended sub-licenses will not be "
        f"restored automatically. Preserve them manually first.\n"
    )


def sync_files(
    services: List[str], root: str
) -> Tuple[List[FileChange], List[FileChange]]:
    """Compute proposed changes. Returns (all_changes, drift_only).

    Handles section-mode (SERVICE_BLOCKS), whole-file (SERVICE_FILES),
    and init-if-missing (SERVICE_INIT_FILES) services. Output groups
    deterministically (sections, then whole-files, then init-files)
    so diff order is stable. The registry-level cross-mode conflict
    check at import time ensures no path can appear in more than one
    bucket here.

    Init-if-missing semantics: if the target file already exists, the
    service contributes NO change (before == after). If missing, the
    file is created with the rendered body. The hub never overwrites
    a file once present — that's the whole point of the mode.
    """
    # ------- Per-repo config -------
    # Load `bos-managed-files.yaml` ONCE per sync run if ANY enabled
    # service is templated (section OR init-if-missing). Values are
    # the same across all services in a run, and a missing config
    # file is cheap (one stat + dict copy).
    #
    # Section-templated services (`_TEMPLATED_SECTION_SERVICES`) need
    # the substitutions before `apply_block()` runs below;
    # init-templated services (`_TEMPLATED_INIT_SERVICES`) need them
    # later in the init-if-missing loop. Sharing the config load
    # keeps the two paths in lockstep — there is no scenario where
    # section and init would see different `bos-managed-files.yaml`
    # state within a single run.
    _needs_config = any(
        svc in _TEMPLATED_INIT_SERVICES
        or svc in _TEMPLATED_SECTION_SERVICES
        for svc in services
    )
    if _needs_config:
        _managed_config = _load_managed_config(root)
        _placeholder_subs = _resolve_placeholders(
            _managed_config, _resolve_repo_full_name(root)
        )
        # NOTICE files are an Apache 2.0 §4 distribution requirement —
        # they don't apply under MIT/BSD/ISC and would mislead downstream
        # consumers about the project's licensing terms. Fail loud rather
        # than silently produce a NOTICE under a non-Apache license.
        # The `license_apache2` alias is treated as implicit
        # `license_type: apache-2.0` (which it always is). When `license`
        # is enabled with a non-Apache `license_type`, `notice_apache2`
        # must NOT be in the services list.
        if "notice_apache2" in services and "license_apache2" not in services:
            _lic_type = _managed_config.get("license_type", "apache-2.0")
            if _lic_type != "apache-2.0":
                die(
                    f"service 'notice_apache2' requires "
                    f"license_type='apache-2.0' (got: {_lic_type!r}). "
                    f"NOTICE files are an Apache 2.0 §4 distribution "
                    f"requirement and don't apply under {_lic_type}. "
                    f"Either change 'license_type' in "
                    f"{MANAGED_FILES_CONFIG_FILENAME} to 'apache-2.0', "
                    f"or remove 'notice_apache2' from the services list."
                )
    else:
        _managed_config = dict(_DEFAULT_MANAGED_CONFIG)
        _placeholder_subs = {}

    # ------- Section mode -------
    # Group by file so we apply all enabled services for a file in one pass
    # and write only once. Preserve service input order so the order of
    # newly-appended blocks is predictable.
    file_to_services: Dict[str, List[str]] = {}
    for svc in services:
        if svc not in SERVICE_BLOCKS:
            continue
        for path in SERVICE_BLOCKS[svc].keys():
            file_to_services.setdefault(path, []).append(svc)

    all_changes: List[FileChange] = []
    for rel_path, svcs in file_to_services.items():
        abs_path = os.path.join(root, rel_path)
        if os.path.exists(abs_path):
            with open(abs_path, "r", encoding="utf-8") as fh:
                before = fh.read()
        else:
            before = ""
        # For files that need a top-level scaffold (e.g.
        # `.github/dependabot.yml` → `version: 2\nupdates:\n`), inject
        # the header when creating the file from scratch. Existing
        # files are left alone — header may differ or be missing, but
        # `apply_block()` only ever rewrites content inside its own
        # marker pair, so we never disturb hand-authored prefixes.
        if not before and rel_path in SECTION_FILE_HEADERS:
            after = SECTION_FILE_HEADERS[rel_path]
        else:
            after = before
        for svc in svcs:
            body = SERVICE_BLOCKS[svc][rel_path]
            # Templated section services (currently the `dependabot_*`
            # trio) carry `{{KEY}}` placeholders rendered from
            # `bos-managed-files.yaml`. Pass-through is safe for
            # non-templated services — `_render_placeholders` is a
            # no-op when the body has no `{{KEY}}` tokens — but we
            # gate on the explicit set anyway to keep the contract
            # documented: ONLY services listed in
            # `_TEMPLATED_SECTION_SERVICES` may carry placeholders,
            # and the import-time validation enforces that every
            # `{{KEY}}` they reference is in `_KNOWN_PLACEHOLDERS`.
            if svc in _TEMPLATED_SECTION_SERVICES:
                body = _render_placeholders(body, _placeholder_subs)
            after = apply_block(after, svc, body)
        all_changes.append(FileChange(path=rel_path, before=before, after=after))

    # ------- Whole-file mode -------
    for svc in services:
        if svc not in SERVICE_FILES:
            continue
        for rel_path, body in SERVICE_FILES[svc].items():
            abs_path = os.path.join(root, rel_path)
            if os.path.exists(abs_path):
                with open(abs_path, "r", encoding="utf-8") as fh:
                    before = fh.read()
            else:
                before = ""
            after = _make_whole_file(svc, body)
            all_changes.append(
                FileChange(path=rel_path, before=before, after=after)
            )

    # ------- Init-if-missing mode -------
    # If the file exists, before == after (no change). If missing,
    # before is "" and after is the rendered init body, so the diff
    # shows the new file. `apply_writes()` (the caller below) writes
    # only `drift` entries, so existing files are never touched.
    #
    # Templated services (`_TEMPLATED_INIT_SERVICES`) get their
    # `{{KEY}}` placeholders rendered from `bos-managed-files.yaml`
    # (or defaults). License/NOTICE (`_NO_HEADER_INIT_SERVICES`) skip
    # the "Initialized by ..." header injection so license-detection
    # tools still match the canonical text.
    #
    # Config has already been loaded at the top of `sync_files()` so
    # both section and init modes see the same `_managed_config` /
    # `_placeholder_subs`.

    # Composite-LICENSE heads-up. Runs unconditionally (cheap), only
    # emits when `license`/`license_apache2` is enabled AND existing
    # LICENSE shows composite markers. Behavior is NOT changed — the
    # init-if-missing loop below still short-circuits on existence.
    _emit_composite_license_notice(root, services)

    for svc in services:
        if svc not in SERVICE_INIT_FILES:
            continue
        for rel_path, body in SERVICE_INIT_FILES[svc].items():
            abs_path = os.path.join(root, rel_path)
            if os.path.exists(abs_path):
                with open(abs_path, "r", encoding="utf-8") as fh:
                    before = fh.read()
                # File exists → hub does nothing. Emit a no-op
                # FileChange so the diff output and the
                # `changed_files` list both reflect that the service
                # was considered. `change.changed` is False so it
                # won't be written.
                after = before
            else:
                before = ""
                # Dynamic license-text resolution: for `license`
                # service, swap the registered placeholder body for
                # the right canonical text from `_LICENSE_REGISTRY`
                # BEFORE placeholder rendering. Other services use
                # their registered body as-is.
                resolved_body = body
                if svc in _DYNAMIC_LICENSE_INIT_SERVICES:
                    resolved_body = _resolve_license_text(
                        _managed_config["license_type"]
                    )
                rendered = (
                    _render_placeholders(resolved_body, _placeholder_subs)
                    if svc in _TEMPLATED_INIT_SERVICES
                    else resolved_body
                )
                if svc in _NO_HEADER_INIT_SERVICES:
                    # Verbatim — no "Initialized by hub" header so
                    # license-detection tools can still match the
                    # canonical SHA.
                    after = rendered if rendered.endswith("\n") else rendered + "\n"
                else:
                    after = _make_init_file(svc, rendered)
            all_changes.append(
                FileChange(path=rel_path, before=before, after=after)
            )

    drift = [c for c in all_changes if c.changed]
    return all_changes, drift


def render_diff(change: FileChange) -> str:
    label = change.path if change.before else f"{change.path} (new file)"
    diff = difflib.unified_diff(
        change.before.splitlines(keepends=True),
        change.after.splitlines(keepends=True),
        fromfile=f"a/{label}",
        tofile=f"b/{change.path}",
        n=2,
    )
    return "".join(diff)


def write_outputs(pairs: List[Tuple[str, str]]) -> None:
    out = os.environ.get("GITHUB_OUTPUT")
    if not out:
        return  # tests don't set this
    with open(out, "a", encoding="utf-8") as fh:
        for k, v in pairs:
            if "\n" in v:
                # Multi-line via heredoc.
                fh.write(f"{k}<<__EOF__\n{v}\n__EOF__\n")
            else:
                fh.write(f"{k}={v}\n")


# --------------------------------------------------------------------------- #
# Main                                                                        #
# --------------------------------------------------------------------------- #


def main() -> int:
    raw_services = os.environ.get("SERVICES", "")
    dry_run = os.environ.get("DRY_RUN", "false").lower() == "true"
    fail_on_drift = os.environ.get("FAIL_ON_DRIFT", "false").lower() == "true"

    services = parse_services(raw_services)
    root = os.environ.get("GITHUB_WORKSPACE") or os.getcwd()

    # `fail_on_drift` implies we never write — we're only checking.
    if fail_on_drift:
        dry_run = True

    _, drift = sync_files(services, root)

    print(f"Enabled services: {', '.join(services)}")
    print(f"Root: {root}")

    if not drift:
        print("All managed sections and files are up to date.")
        write_outputs([("changed", "false"), ("changed_files", "")])
        return 0

    print(f"\n{len(drift)} file(s) would change:")
    for change in drift:
        print(f"  - {change.path}")
        diff_text = render_diff(change)
        # Indent diff for readability in job logs.
        for line in diff_text.splitlines():
            print(f"    {line}")

    if not dry_run:
        for change in drift:
            abs_path = os.path.join(root, change.path)
            os.makedirs(os.path.dirname(abs_path) or ".", exist_ok=True)
            with open(abs_path, "w", encoding="utf-8") as fh:
                fh.write(change.after)
        print(f"\nWrote {len(drift)} file(s).")

    changed_files = "\n".join(c.path for c in drift)
    write_outputs([("changed", "true"), ("changed_files", changed_files)])

    if fail_on_drift:
        print(
            "\nfail_on_drift=true: exiting non-zero because the managed "
            "content drifted from the canonical sections / files.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
