#!/usr/bin/env bash
# Sign each per-arch Packages index with usign, producing Packages.sig.
# Runs only when the caller's `if:` guard sees USIGN_SECRET_KEY is non-empty.

set -euo pipefail
# shellcheck source=.github/scripts/openwrt/lib.sh
. "$(dirname "$0")/lib.sh"
req SITE_BASE PUBLISH_ARCHES FEED_NAME USIGN_SECRET_KEY

USIGN="$(sdk_find_tool usign)"
[ -n "${USIGN}" ] || { gh_error "usign not found in SDK"; exit 1; }

umask 077
keyfile="$(mktemp)"
trap 'rm -f "${keyfile}"' EXIT
printf '%s' "${USIGN_SECRET_KEY}" > "${keyfile}"

for ARCH in ${PUBLISH_ARCHES}; do
  ( cd "$(feed_dir "${ARCH}")" && "${USIGN}" -S -m Packages -s "${keyfile}" -x Packages.sig )
done
