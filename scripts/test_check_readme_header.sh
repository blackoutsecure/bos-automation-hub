#!/usr/bin/env bash
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# test_check_readme_header.sh — fixture-driven test for the
# `.github/actions/shared/check-readme-header` composite action.
#
# Extracts the embedded shell from the composite, then exercises pass +
# fail paths for every profile (marketplace, docker, generic) plus the
# brand-color and badge-order checks.
#
# Run from the repo root:
#   bash scripts/test_check_readme_header.sh
#
# Requires: bash, python3 + PyYAML (for action.yml step extraction),
# ShellCheck (optional; lints the embedded script when present).
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
set -euo pipefail

repo_root="$(cd "$(dirname "$0")/.." && pwd)"
action_yml="${repo_root}/.github/actions/shared/check-readme-header/action.yml"
work="${TMPDIR:-/tmp}/bos-readme-header-test.$$"
trap 'rm -rf "${work}"' EXIT
mkdir -p "${work}"

# ----------------------------------------------------------------------
# Extract the composite's `run:` script into a standalone file so we
# can lint + execute it without rendering a full workflow.
# ----------------------------------------------------------------------
script="${work}/script.sh"
python3 - "${action_yml}" "${script}" <<'PY'
import sys, yaml
src, dst = sys.argv[1], sys.argv[2]
with open(src) as f:
    data = yaml.safe_load(f)
for st in data["runs"]["steps"]:
    if "run" in st:
        with open(dst, "w") as out:
            out.write(st["run"])
        break
PY

if command -v shellcheck >/dev/null 2>&1; then
  shellcheck --shell=bash "${script}"
  echo "[ok] shellcheck clean"
fi

# ----------------------------------------------------------------------
# Fixture runner. Each fixture sets up a sandbox dir, drops marker
# files (action.yml / Dockerfile / neither) so `auto` profile detection
# resolves correctly, then runs the script and asserts the exit code.
# ----------------------------------------------------------------------
fail=0
run_case() {
  local name="$1" exp_exit="$2"
  local dir="${work}/${name}"
  mkdir -p "${dir}"
  ( cd "${dir}" && eval "$3" )
  local actual
  set +e
  ( cd "${dir}" && env README_PATH=README.md PROFILE_INPUT=auto \
                       EXPECTED_ORG=blackoutsecure FAIL_ON_WARNING=false \
                       bash "${script}" ) >"${dir}/out" 2>"${dir}/err"
  actual=$?
  set -e
  if [[ "${actual}" -eq "${exp_exit}" ]]; then
    printf '[ok] %-22s exit=%d\n' "${name}" "${actual}"
  else
    printf '[FAIL] %-22s expected=%d actual=%d\n' "${name}" "${exp_exit}" "${actual}"
    echo "----- stderr -----"; cat "${dir}/err"
    fail=1
  fi
}

# Pass: marketplace profile with the live bos-marketplace-kit README,
# if present (skipped on CI when sibling checkouts aren't available).
if [[ -f "${repo_root}/../bos-marketplace-kit/README.md" ]]; then
  run_case mp-pass-live 0 '
    touch action.yml
    cp '"${repo_root}"'/../bos-marketplace-kit/README.md README.md
  '
fi
if [[ -f "${repo_root}/../docker-readsb/README.md" ]]; then
  run_case dk-pass-live 0 '
    touch Dockerfile
    cp '"${repo_root}"'/../docker-readsb/README.md README.md
  '
fi

# Pass: marketplace profile, synthetic canonical header.
run_case mp-pass-synth 0 '
  touch action.yml
  cat >README.md <<EOF
# Blackout Secure Test Kit

**Copyright © 2025-2026 Blackout Secure | Apache License 2.0**

[![Marketplace](https://img.shields.io/badge/GitHub%20Marketplace-blue?logo=github)](https://github.com/marketplace/actions/test)
[![GitHub release](https://img.shields.io/github/v/release/blackoutsecure/test?sort=semver)](https://github.com/blackoutsecure/test/releases)
[![License](https://img.shields.io/badge/license-Apache%202.0-blue)](LICENSE)
[![Made by BlackoutSecure](https://img.shields.io/badge/made%20by-BlackoutSecure-1f1f1f)](https://github.com/blackoutsecure)
EOF
'

# Pass: docker profile, synthetic canonical header.
run_case dk-pass-synth 0 '
  touch Dockerfile
  cat >README.md <<EOF
<p align="center"><img src="logo.png" alt="x logo" width="200"></p>

# blackoutsecure/x

[![GitHub Stars](https://img.shields.io/github/stars/blackoutsecure/docker-x?style=flat-square&color=E7931D&logo=github)](https://github.com/blackoutsecure/docker-x/stargazers)
[![Docker Pulls](https://img.shields.io/docker/pulls/blackoutsecure/x?style=flat-square&color=E7931D&logo=docker&logoColor=FFFFFF)](https://hub.docker.com/r/blackoutsecure/x)
[![GitHub Release](https://img.shields.io/github/release/blackoutsecure/docker-x.svg?style=flat-square&color=E7931D&logo=github&logoColor=FFFFFF)](https://github.com/blackoutsecure/docker-x/releases)
[![Blackout Secure Launchpad](https://img.shields.io/github/actions/workflow/status/blackoutsecure/docker-x/bos-universal-launchpad-kicker.yml?style=flat-square&label=blackout%20secure%20launchpad&color=E7931D)](https://github.com/blackoutsecure/docker-x/actions/workflows/bos-universal-launchpad-kicker.yml)
[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg?style=flat-square)](https://www.gnu.org/licenses/gpl-3.0)
[![Made by BlackoutSecure](https://img.shields.io/badge/made%20by-BlackoutSecure-1f1f1f?style=flat-square)](https://github.com/blackoutsecure)
EOF
'

# Pass: generic profile with only the made-by badge.
run_case gen-pass 0 '
  cat >README.md <<EOF
# Some Site

Description.

[![Made by BlackoutSecure](https://img.shields.io/badge/made%20by-BlackoutSecure-1f1f1f)](https://github.com/blackoutsecure)
EOF
'

# Fail: marketplace profile, License badge missing.
run_case mp-fail-license 1 '
  touch action.yml
  cat >README.md <<EOF
# Blackout Secure Broken Kit

**Copyright © 2025-2026 Blackout Secure | Apache License 2.0**

[![Marketplace](https://img.shields.io/badge/GitHub%20Marketplace-blue?logo=github)](https://github.com/marketplace/actions/broken)
[![GitHub release](https://img.shields.io/github/v/release/blackoutsecure/broken?sort=semver)](https://github.com/blackoutsecure/broken/releases)
[![Made by BlackoutSecure](https://img.shields.io/badge/made%20by-BlackoutSecure-1f1f1f)](https://github.com/blackoutsecure)
EOF
'

# Fail: docker profile, Launchpad/Balena badge missing.
run_case dk-fail-launchpad 1 '
  touch Dockerfile
  cat >README.md <<EOF
<p align="center"><img src="logo.png" alt="x logo" width="200"></p>

# blackoutsecure/x

[![GitHub Stars](https://img.shields.io/github/stars/blackoutsecure/docker-x?style=flat-square&color=E7931D&logo=github)](https://github.com/blackoutsecure/docker-x/stargazers)
[![Docker Pulls](https://img.shields.io/docker/pulls/blackoutsecure/x?style=flat-square&color=E7931D&logo=docker&logoColor=FFFFFF)](https://hub.docker.com/r/blackoutsecure/x)
[![GitHub Release](https://img.shields.io/github/release/blackoutsecure/docker-x.svg?style=flat-square&color=E7931D&logo=github&logoColor=FFFFFF)](https://github.com/blackoutsecure/docker-x/releases)
[![Made by BlackoutSecure](https://img.shields.io/badge/made%20by-BlackoutSecure-1f1f1f?style=flat-square)](https://github.com/blackoutsecure)
EOF
'

# Fail: docker profile, Stars badge uses wrong brand color.
run_case dk-fail-color 1 '
  touch Dockerfile
  cat >README.md <<EOF
<p align="center"><img src="logo.png" alt="x logo" width="200"></p>

# blackoutsecure/x

[![GitHub Stars](https://img.shields.io/github/stars/blackoutsecure/docker-x?style=flat-square&color=blue&logo=github)](https://github.com/blackoutsecure/docker-x/stargazers)
[![Docker Pulls](https://img.shields.io/docker/pulls/blackoutsecure/x?style=flat-square&color=E7931D&logo=docker&logoColor=FFFFFF)](https://hub.docker.com/r/blackoutsecure/x)
[![GitHub Release](https://img.shields.io/github/release/blackoutsecure/docker-x.svg?style=flat-square&color=E7931D&logo=github&logoColor=FFFFFF)](https://github.com/blackoutsecure/docker-x/releases)
[![Blackout Secure Launchpad](https://img.shields.io/github/actions/workflow/status/blackoutsecure/docker-x/bos-universal-launchpad-kicker.yml?style=flat-square&label=blackout%20secure%20launchpad&color=E7931D)](https://github.com/blackoutsecure/docker-x/actions/workflows/bos-universal-launchpad-kicker.yml)
[![Made by BlackoutSecure](https://img.shields.io/badge/made%20by-BlackoutSecure-1f1f1f?style=flat-square)](https://github.com/blackoutsecure)
EOF
'

# Fail: generic profile missing required made-by badge.
run_case gen-fail-madeby 1 '
  cat >README.md <<EOF
# My Repo

Some text but no required badge.
EOF
'

# Warning-only: marketplace badge order swapped (Marketplace + Release).
# fail_on_warning=false so this passes; we just confirm exit 0.
run_case mp-warn-order 0 '
  touch action.yml
  cat >README.md <<EOF
# Blackout Secure Test Kit

**Copyright © 2025-2026 Blackout Secure | Apache License 2.0**

[![GitHub release](https://img.shields.io/github/v/release/blackoutsecure/test?sort=semver)](https://github.com/blackoutsecure/test/releases)
[![Marketplace](https://img.shields.io/badge/GitHub%20Marketplace-blue?logo=github)](https://github.com/marketplace/actions/test)
[![License](https://img.shields.io/badge/license-Apache%202.0-blue)](LICENSE)
[![Made by BlackoutSecure](https://img.shields.io/badge/made%20by-BlackoutSecure-1f1f1f)](https://github.com/blackoutsecure)
EOF
'

echo ""
if [[ "${fail}" -eq 0 ]]; then
  echo "ALL FIXTURES PASSED"
else
  echo "ONE OR MORE FIXTURES FAILED"
  exit 1
fi
