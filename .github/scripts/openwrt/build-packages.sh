#!/usr/bin/env bash
# Resolve each requested package name to its sdk/package/<path> and build the
# explicit `<path>/compile` targets — avoids `make world` which would also
# rebuild every package the SDK feeds installed. Dump per-package logs on
# failure so the GitHub UI shows the real error.
#
# Spec packages listed in /tmp/pkg-skip.list (produced by check-changes.sh)
# are dropped from the build target set — their .ipk files are pulled from the
# previously-published feed by materialize-feeds.sh. EXTRA_PACKAGES always
# build (they aren't tracked by upstream commit and ship from the SDK feeds).
#
# Build targets vs output names — only iterate `all_build_targets()` here,
# NOT `all_pkg_names()`. Sub-packages (e.g. `viewadsb-wiedehopf` declared
# inside `readsb-wiedehopf/Makefile`) are output names — they get their own
# .ipk and their own kconfig node, but they don't have an own filesystem
# entry and OpenWrt's toplevel.mk has no `package/<sub-name>/compile` rule
# for them. Building the parent produces every sub-package's .ipk as a side
# effect of the same `make package/<parent>/compile` invocation. Iterating
# subs here used to fail with `make: *** No rule to make target`.
#
# Path-resolution gotchas the find has to cope with for parent names:
#   * Spec packages → real directories at package/extra/<n>/ (cp -r in
#     stage-sources.sh).
#   * Base-tree packages (e.g. wireguard-tools) → real directories under
#     package/network/, package/utils/, ... that ship inside the SDK tarball.
#   * Feed packages (e.g. librtlsdr, libzstd) → symlinks created by
#     `./scripts/feeds install -a` at package/feeds/<feed>/<n> pointing into
#     feeds/<feed>/<cat>/<n>. Plain `find -type d` would silently skip these
#     (a symlink-to-dir is -type l, not -type d) — use
#     `\( -type d -o -xtype d \)` so symlinks whose target is a directory
#     also match.
#
# Without the symlink fix, an incremental run that skips every spec package
# would also silently skip every EXTRA_PACKAGE that comes from a non-base
# feed, leaving the published index with broken `Depends:` chains (the
# readsb-wiedehopf `cannot find dependency librtlsdr / libzstd` regression).
# An EXTRA_PACKAGES sub-package (e.g. `coreutils-stat`) won't resolve here
# either — the user must list the parent (`coreutils`) instead.

set -euo pipefail
# shellcheck source=.github/scripts/openwrt/lib.sh
. "$(dirname "$0")/lib.sh"
req EXTRA_PACKAGES

declare -A skip_set=()
if [ -s /tmp/pkg-skip.list ]; then
  while IFS= read -r s; do [ -n "$s" ] && skip_set["$s"]=1; done < /tmp/pkg-skip.list
fi

mapfile -t names < <(all_build_targets)

targets=()
for n in "${names[@]}"; do
  if [ -n "${skip_set[$n]:-}" ]; then
    log_step "skip compile ${n} (carried over from previous publish)"
    continue
  fi
  # `-xtype d` evaluates the type after dereferencing a symlink, so feed
  # symlinks created by `feeds install -a` match the same way real dirs do.
  path="$(find package -mindepth 2 -maxdepth 6 \( -type d -o -xtype d \) -name "$n" 2>/dev/null | grep -v '/files$' | head -n1)" || true
  if [ -n "$path" ]; then
    targets+=( "$path/compile" )
  else
    gh_error "Cannot resolve '${n}' to a build target."
    gh_error "  Searched package/**/${n} for a directory or symlink-to-directory under sdk/."
    gh_error "  If '${n}' is a sub-package (defined as 'define Package/${n}' inside"
    gh_error "  some other package's Makefile), list the PARENT package name here"
    gh_error "  instead — building the parent produces every sub-package .ipk as"
    gh_error "  a side effect. Sub-packages should only appear in PACKAGES_SPEC's"
    gh_error "  3rd '|'-separated field (subs), never as a standalone spec row or"
    gh_error "  in EXTRA_PACKAGES."
    exit 1
  fi
done

if [ "${#targets[@]}" -eq 0 ]; then
  log "No build targets — every requested package was skipped or already up to date."
  exit 0
fi

log "Build targets: ${targets[*]}"
if ! make -j"$(nproc)" "${targets[@]}" V=s BUILD_LOG=1; then
  gh_group "Build logs"
  find logs -type f -name '*.txt' -print -exec sh -c 'echo "--- $1 ---"; cat "$1"' _ {} \;
  gh_endgroup
  exit 1
fi
