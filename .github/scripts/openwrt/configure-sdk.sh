#!/usr/bin/env bash
# Build the SDK .config: enable the target, mark every requested package as =m,
# defconfig, then validate that each requested package landed and that the
# resolved arch matches BUILT_ARCH.

set -euo pipefail
# shellcheck source=.github/scripts/openwrt/lib.sh
. "$(dirname "$0")/lib.sh"
req TARGET SUBTARGET BUILT_ARCH EXTRA_PACKAGES

mapfile -t names < <(all_pkg_names)

{
  cat <<EOF
CONFIG_TARGET_${TARGET}=y
CONFIG_TARGET_${TARGET}_${SUBTARGET}=y
CONFIG_ALL_KMODS=n
CONFIG_ALL_NONSHARED=n
CONFIG_AUTOREMOVE=n
CONFIG_SIGNED_PACKAGES=y
EOF
  for n in "${names[@]}"; do echo "CONFIG_PACKAGE_${n}=m"; done
} >> .config
make defconfig

# Validate spec parent packages + EXTRA_PACKAGES output names landed in
# .config. Spec sub-packages are implicit and trusted; if a sub fails to land,
# the build itself will fail with a concrete kconfig error.
required() {
  while IFS="${SPEC_SEP}" read -r n _ _ _; do echo "$n"; done < /tmp/spec.tsv
  extra_each _emit_outs
}
while read -r p; do
  grep -qE "^CONFIG_PACKAGE_${p}=(m|y)$" .config || {
    gh_error "Package '${p}' could not be selected (check dependencies / feed availability)"
    exit 1
  }
done < <(required)

ACTUAL="$(awk -F'"' '/^CONFIG_TARGET_ARCH_PACKAGES=/ {print $2}' .config)"
[ "${ACTUAL}" = "${BUILT_ARCH}" ] || { gh_error "SDK arch '${ACTUAL}' != BUILT_ARCH '${BUILT_ARCH}'"; exit 1; }
log "SDK arch=${ACTUAL} ok"
