#!/usr/bin/env bash
# Discover the SDK tarball name from upstream sha256sums (the filename embeds
# a long compiler-version suffix that changes between point releases) and emit
# `url=` and `file=` step outputs.

set -euo pipefail
# shellcheck source=.github/scripts/openwrt/lib.sh
. "$(dirname "$0")/lib.sh"
req SDK_URL_BASE GITHUB_OUTPUT

SDK_FILE=$(curl -fsSL "${SDK_URL_BASE}/sha256sums" \
  | awk '{print $2}' | sed 's/^\*//' \
  | grep -E '^openwrt-sdk-.*\.Linux-x86_64\.tar\.(xz|zst)$' | head -n1)
[ -n "${SDK_FILE}" ] || { gh_error "Could not find SDK in ${SDK_URL_BASE}"; exit 1; }

{
  echo "url=${SDK_URL_BASE}/${SDK_FILE}"
  echo "file=${SDK_FILE}"
} >> "${GITHUB_OUTPUT}"
log "Resolved SDK: ${SDK_FILE}"
