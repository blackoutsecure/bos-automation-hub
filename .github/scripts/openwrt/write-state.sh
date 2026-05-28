#!/usr/bin/env bash
# Write site/${FEED_RELEASE_LABEL}/state.json describing what's currently
# published. Consumed by the next run's check-changes.sh to decide whether
# each spec package needs a rebuild or can be carried over verbatim.
#
# Schema (version 1):
#   {
#     "schema_version":   1,
#     "generated_at":     "2026-05-08T12:34:56Z",
#     "build_fingerprint": "sha256(SDK_URL|BUILT_ARCH|PUBLISH_ARCHES|EXTRA_PACKAGES)",
#     "sdk_url":          "...",
#     "built_arch":       "...",
#     "publish_arches":   "...",
#     "extra_packages":   "...",
#     "packages": {
#       "<name>": {
#         "ref":    "<git ref>",
#         "repo":   "<upstream URL>",
#         "subs":   "<comma-separated sub-packages>",
#         "commit": "<upstream commit SHA the .ipk was built from>",
#         "carried_over": false
#       },
#       ...
#     }
#   }
#
# `commit` always reflects the upstream commit of the .ipk that's actually live
# in the published feed for this run — for skipped packages that's the same as
# last run (because we skipped precisely because it didn't change), for built
# packages it's the just-cloned commit. Both come from PKG_COMMIT in
# /tmp/pkg-meta/<n>.env, which check-changes.sh already used as the comparison
# basis.

set -euo pipefail
# shellcheck source=.github/scripts/openwrt/lib.sh
. "$(dirname "$0")/lib.sh"
req SDK_URL BUILT_ARCH PUBLISH_ARCHES EXTRA_PACKAGES FEED_RELEASE_LABEL GITHUB_WORKSPACE

SITE="${GITHUB_WORKSPACE}/site"
OUT="${SITE}/${FEED_RELEASE_LABEL}/state.json"
mkdir -p "$(dirname "${OUT}")"

if [ -s /tmp/build-fingerprint ]; then
  fingerprint="$(cat /tmp/build-fingerprint)"
else
  fingerprint="$(printf '%s|%s|%s|%s' \
    "${SDK_URL}" "${BUILT_ARCH}" "${PUBLISH_ARCHES}" "${EXTRA_PACKAGES}" \
    | sha256sum | awk '{print $1}')"
fi

declare -A skip_set=()
if [ -s /tmp/pkg-skip.list ]; then
  while IFS= read -r s; do [ -n "$s" ] && skip_set["$s"]=1; done < /tmp/pkg-skip.list
fi

# Build the .packages object incrementally with jq.
pkgs_json='{}'
while IFS="${SPEC_SEP}" read -r name ref subs repo; do
  meta="/tmp/pkg-meta/${name}.env"
  [ -f "${meta}" ] || { gh_error "missing ${meta} for '${name}'"; exit 1; }
  # shellcheck disable=SC1090
  commit="$( . "${meta}"; printf '%s' "${PKG_COMMIT:-}" )"
  carried=false
  [ -n "${skip_set[$name]:-}" ] && carried=true
  pkgs_json="$(jq \
    --arg n "${name}" \
    --arg ref "${ref}" \
    --arg subs "${subs}" \
    --arg repo "${repo}" \
    --arg commit "${commit}" \
    --argjson carried "${carried}" \
    '. + {($n): {ref: $ref, repo: $repo, subs: $subs, commit: $commit, carried_over: $carried}}' \
    <<< "${pkgs_json}")"
done < /tmp/spec.tsv

jq -n \
  --arg generated_at  "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  --arg fingerprint   "${fingerprint}" \
  --arg sdk_url       "${SDK_URL}" \
  --arg built_arch    "${BUILT_ARCH}" \
  --arg publish_arches "${PUBLISH_ARCHES}" \
  --arg extra_packages "${EXTRA_PACKAGES}" \
  --argjson packages  "${pkgs_json}" \
  '{
    schema_version: 1,
    generated_at:   $generated_at,
    build_fingerprint: $fingerprint,
    sdk_url:        $sdk_url,
    built_arch:     $built_arch,
    publish_arches: $publish_arches,
    extra_packages: $extra_packages,
    packages:       $packages
  }' > "${OUT}"

log "Wrote ${OUT}"
