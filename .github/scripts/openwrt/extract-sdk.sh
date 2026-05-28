#!/usr/bin/env bash
# Extract sdk.tar into ./sdk (strip top-level dir). Upstream switches between
# .tar.xz and .tar.zst across releases without warning.

set -euo pipefail
# shellcheck source=.github/scripts/openwrt/lib.sh
. "$(dirname "$0")/lib.sh"

mkdir -p sdk
if file sdk.tar | grep -qi 'Zstandard'; then
  tar --use-compress-program=unzstd -xf sdk.tar -C sdk --strip-components=1
else
  tar -xf sdk.tar -C sdk --strip-components=1
fi
log "Extracted SDK into ./sdk"
