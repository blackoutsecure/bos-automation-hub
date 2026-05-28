#!/usr/bin/env bash
# Generate the opkg Packages / Packages.gz index in each per-arch feed dir.
# ipkg-make-index.sh shells out to bare `mkhash`, so put the SDK's binary on
# PATH (and export $MKHASH for code paths that honour it).

set -euo pipefail
# shellcheck source=.github/scripts/openwrt/lib.sh
. "$(dirname "$0")/lib.sh"
req SITE_BASE PUBLISH_ARCHES FEED_NAME GITHUB_WORKSPACE

IDX="${GITHUB_WORKSPACE}/sdk/scripts/ipkg-make-index.sh"
MKHASH_BIN="$(sdk_find_tool mkhash)"
[ -n "${MKHASH_BIN}" ] || { gh_error "mkhash not found in SDK"; exit 1; }
PATH="$(dirname "${MKHASH_BIN}"):${PATH}"
export PATH MKHASH="${MKHASH_BIN}"

for ARCH in ${PUBLISH_ARCHES}; do
  DIR="$(feed_dir "${ARCH}")"
  log "indexing ${DIR}"
  ( cd "${DIR}" && "${IDX}" . > Packages && gzip -9nc Packages > Packages.gz )
done
