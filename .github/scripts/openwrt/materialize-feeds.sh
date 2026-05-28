#!/usr/bin/env bash
# Materialize one feed directory per PUBLISH_ARCH from the single SDK build.
# For each .ipk we want to publish:
#   * _all.ipk             → copy verbatim into every arch dir
#   * ARCH == BUILT_ARCH   → copy verbatim
#   * otherwise            → repack with the new Architecture: label
#
# Spec packages on /tmp/pkg-skip.list (produced by check-changes.sh) weren't
# rebuilt — for each such package (and every sub-package it declares) we
# download the matching .ipk filenames from the previously-published per-arch
# Packages index and drop them into the new arch dir so the published feed
# stays complete. EXTRA_PACKAGES are always rebuilt and never carried over.
#
# Writes the site root path to $GITHUB_OUTPUT as `site_base=`.

set -euo pipefail
# shellcheck source=.github/scripts/openwrt/lib.sh
. "$(dirname "$0")/lib.sh"
req BUILT_ARCH EXTRA_PACKAGES PUBLISH_ARCHES FEED_NAME FEED_RELEASE_LABEL GITHUB_WORKSPACE GITHUB_OUTPUT
# PUBLIC_FEED_BASE is only required when there's something to carry over.
if [ -s /tmp/pkg-skip.list ]; then req PUBLIC_FEED_BASE; fi

SITE_BASE="${GITHUB_WORKSPACE}/site/${FEED_RELEASE_LABEL}/packages"

# Names we will carry over (spec package + its declared subs).
declare -A carry_set=()
if [ -s /tmp/pkg-skip.list ]; then
  while IFS= read -r skipped; do
    [ -n "${skipped}" ] || continue
    while IFS="${SPEC_SEP}" read -r name _ subs _; do
      [ "${name}" = "${skipped}" ] || continue
      carry_set["${name}"]=1
      for s in ${subs//,/ }; do [ -n "$s" ] && carry_set["$s"]=1; done
    done < /tmp/spec.tsv
  done < /tmp/pkg-skip.list
fi

# Build the regex of name prefixes we want to publish from this build's output.
# Skipped names are excluded so we don't accidentally pick up a stale leftover
# from a previous SDK invocation in the same workspace.
fresh_names="$(
  all_pkg_names | while IFS= read -r n; do
    [ -n "${carry_set[$n]:-}" ] || printf '%s\n' "$n"
  done
)"
prefixes="$(printf '%s' "${fresh_names}" | paste -sd'|' -)"

SRC_IPKS=()
if [ -n "${prefixes}" ]; then
  mapfile -t SRC_IPKS < <(
    find bin/packages -type f \( -name "*_${BUILT_ARCH}.ipk" -o -name "*_all.ipk" \) \
      | grep -E "/(${prefixes})[_-][^/]*\.ipk\$"
  )
fi
if [ "${#SRC_IPKS[@]}" -eq 0 ] && [ "${#carry_set[@]}" -eq 0 ]; then
  gh_error "No built .ipk files found and nothing to carry over"
  find bin/packages -type f -name '*.ipk' | head -50 >&2
  exit 1
fi
log "Built artifacts (${#SRC_IPKS[@]}):"
printf '  %s\n' "${SRC_IPKS[@]}"

for ARCH in ${PUBLISH_ARCHES}; do
  OUT="$(feed_dir "${ARCH}")"
  mkdir -p "${OUT}"
  log "staging ${ARCH} -> ${OUT}"
  for src in "${SRC_IPKS[@]}"; do
    base="$(basename "${src}")"
    if [[ "${base}" == *_all.ipk ]] || [ "${ARCH}" = "${BUILT_ARCH}" ]; then
      cp "${src}" "${OUT}/${base}"
    else
      new="${base%_"${BUILT_ARCH}".ipk}_${ARCH}.ipk"
      fmt="$(repack_ipk "${src}" "${OUT}/${new}" "${ARCH}")"
      log_step "${base} -> ${new} (${fmt})"
    fi
  done

  # Carry over skipped packages from the previously-published feed.
  if [ "${#carry_set[@]}" -gt 0 ]; then
    BASE_URL="${PUBLIC_FEED_BASE}/${FEED_RELEASE_LABEL}/packages/${ARCH}/${FEED_NAME}"
    PKG_INDEX="$(mktemp)"
    if ! curl -fsSL --max-time 30 "${BASE_URL}/Packages" -o "${PKG_INDEX}"; then
      gh_error "Cannot fetch ${BASE_URL}/Packages — needed to carry over skipped packages."
      gh_error "Re-run with skip_unchanged_packages=false to do a full rebuild."
      rm -f "${PKG_INDEX}"
      exit 1
    fi
    for n in "${!carry_set[@]}"; do
      mapfile -t fnames < <(awk -v want="$n" '
        /^$/        { pkg="" }
        /^Package: /  { pkg=$2 }
        /^Filename: / { if (pkg==want) print $2 }
      ' "${PKG_INDEX}")
      if [ "${#fnames[@]}" -eq 0 ]; then
        gh_error "Skipped package '${n}' has no Filename: entry in ${BASE_URL}/Packages"
        gh_error "Re-run with skip_unchanged_packages=false to do a full rebuild."
        rm -f "${PKG_INDEX}"
        exit 1
      fi
      for fn in "${fnames[@]}"; do
        out_name="$(basename "${fn}")"
        log_step "carry-over ${ARCH}: ${out_name}"
        if ! curl -fsSL --max-time 60 "${BASE_URL}/${fn}" -o "${OUT}/${out_name}"; then
          gh_error "Failed to download ${BASE_URL}/${fn}"
          gh_error "Re-run with skip_unchanged_packages=false to do a full rebuild."
          rm -f "${PKG_INDEX}"
          exit 1
        fi
      done
    done
    rm -f "${PKG_INDEX}"
  fi
done

echo "site_base=${SITE_BASE}" >> "${GITHUB_OUTPUT}"
