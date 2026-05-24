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

_DEPENDABOT_ACTIONS = """\
  - package-ecosystem: github-actions
    directory: /
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
    directory: /
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
    directory: /
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
                after = _make_init_file(svc, body)
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
