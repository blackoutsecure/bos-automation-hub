#!/usr/bin/env bash
# Per-package incremental build decision.
#
# For each row in /tmp/spec.tsv, compare the just-cloned upstream commit
# (PKG_COMMIT in /tmp/pkg-meta/<name>.env) against the commit recorded in the
# previously-published state.json at ${PUBLIC_FEED_BASE}/${FEED_RELEASE_LABEL}/state.json.
#
# Outputs:
#   /tmp/pkg-build.list      one name per line: spec packages we will rebuild
#   /tmp/pkg-skip.list       one name per line: spec packages we will carry over
#                            verbatim from the previous publish
#   /tmp/build-fingerprint   sha256 over (SDK_URL|BUILT_ARCH|PUBLISH_ARCHES|EXTRA_PACKAGES);
#                            consumed by write-state.sh so the next run can detect
#                            a non-source change that invalidates carry-over.
#
# Forces a full rebuild (every spec package on /tmp/pkg-build.list) when:
#   * SKIP_UNCHANGED_PACKAGES is anything other than the literal string 'true'
#   * No previous state.json is reachable (HTTP fetch failed / 404)
#   * state.json has an unexpected schema_version
#   * The build fingerprint changed (SDK URL, BUILT_ARCH, PUBLISH_ARCHES, or
#     EXTRA_PACKAGES differ from last run) — because carry-over .ipk files were
#     built against the previous toolchain / arch labels.
#
# EXTRA_PACKAGES are never on either list: they aren't tracked by upstream
# commit, they come from the SDK feeds and rebuild on every run.

set -euo pipefail
# shellcheck source=.github/scripts/openwrt/lib.sh
. "$(dirname "$0")/lib.sh"
req PUBLIC_FEED_BASE FEED_RELEASE_LABEL SDK_URL BUILT_ARCH PUBLISH_ARCHES EXTRA_PACKAGES

: > /tmp/pkg-build.list
: > /tmp/pkg-skip.list

fingerprint="$(printf '%s|%s|%s|%s' \
  "${SDK_URL}" "${BUILT_ARCH}" "${PUBLISH_ARCHES}" "${EXTRA_PACKAGES}" \
  | sha256sum | awk '{print $1}')"
printf '%s\n' "${fingerprint}" > /tmp/build-fingerprint

mark_all_build() {
  local reason="$1" name
  log "${reason} — every spec package will be rebuilt."
  : > /tmp/pkg-skip.list
  while IFS="${SPEC_SEP}" read -r name _ _ _; do
    printf '%s\n' "${name}" >> /tmp/pkg-build.list
  done < /tmp/spec.tsv
}

if [ "${SKIP_UNCHANGED_PACKAGES:-true}" != "true" ]; then
  mark_all_build "skip_unchanged_packages=false"
  exit 0
fi

state_url="${PUBLIC_FEED_BASE}/${FEED_RELEASE_LABEL}/state.json"
state_file="$(mktemp)"
trap 'rm -f "${state_file}"' EXIT

if ! curl -fsSL --max-time 30 "${state_url}" -o "${state_file}"; then
  mark_all_build "no previous state.json at ${state_url}"
  exit 0
fi

# A truncated / non-JSON response (e.g. HTML 404 page served with 200) would
# crash later jq calls — validate before reading.
if ! jq -e . "${state_file}" >/dev/null 2>&1; then
  mark_all_build "previous state.json is not valid JSON"
  exit 0
fi

prev_schema="$(jq -r '.schema_version // 0' "${state_file}")"
if [ "${prev_schema}" != "1" ]; then
  mark_all_build "previous state.json schema_version=${prev_schema} (expected 1)"
  exit 0
fi

prev_fingerprint="$(jq -r '.build_fingerprint // ""' "${state_file}")"
if [ "${prev_fingerprint}" != "${fingerprint}" ]; then
  mark_all_build "build fingerprint changed (SDK / arches / extra_packages)"
  exit 0
fi

# Per-package decision.
gh_group "Per-package change detection"
while IFS="${SPEC_SEP}" read -r name _ _ _; do
  meta="/tmp/pkg-meta/${name}.env"
  [ -f "${meta}" ] || { gh_error "missing ${meta} for '${name}'"; exit 1; }
  # shellcheck disable=SC1090
  ( . "${meta}"; printf '%s\n' "${PKG_COMMIT:-}" ) > /tmp/.pkg-commit
  cur_commit="$(cat /tmp/.pkg-commit)"; rm -f /tmp/.pkg-commit
  prev_commit="$(jq -r --arg n "${name}" '.packages[$n].commit // ""' "${state_file}")"

  if [ -n "${prev_commit}" ] && [ "${cur_commit}" = "${prev_commit}" ]; then
    printf '%s\n' "${name}" >> /tmp/pkg-skip.list
    log_step "skip   ${name}  (commit ${cur_commit:0:12} unchanged)"
  else
    printf '%s\n' "${name}" >> /tmp/pkg-build.list
    if [ -z "${prev_commit}" ]; then
      log_step "build  ${name}  (new package, commit ${cur_commit:0:12})"
    else
      log_step "build  ${name}  (commit ${prev_commit:0:12} -> ${cur_commit:0:12})"
    fi
  fi
done < /tmp/spec.tsv
gh_endgroup

n_build="$(wc -l < /tmp/pkg-build.list | tr -d ' ')"
n_skip="$(wc -l < /tmp/pkg-skip.list  | tr -d ' ')"
gh_notice "Incremental build" "rebuild=${n_build}  skip=${n_skip}"
