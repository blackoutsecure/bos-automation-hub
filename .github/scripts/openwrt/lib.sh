#!/usr/bin/env bash
# Shared helpers for the OpenWrt build & publish workflow.
#
# Conventions:
#   /tmp/spec.tsv         one package per row, separated by ASCII US (\x1f):
#                         name<US>ref<US>subs<US>repo. We use US instead of tab
#                         because bash's `read` folds consecutive whitespace IFS
#                         chars, which would collapse empty middle fields (e.g.
#                         empty `subs` would swallow `repo`). Helpers below
#                         (spec_each, all_pkg_names) hide the encoding.
#   /tmp/pkg-meta/<n>.env per-package metadata produced by stage-sources.sh
#   PUBLISH_ARCHES        space-separated arch labels
#   SITE_BASE, FEED_NAME  used by feed_dir() to compute per-arch paths

# Bash 5.2 patsub_replacement would treat `&` in ${var//pat/repl} as the matched
# pattern, corrupting `&gt;`/`&amp;` HTML entities. Force off everywhere.
shopt -u patsub_replacement 2>/dev/null || true

SPEC_SEP=$'\x1f'

log()         { printf '==> %s\n' "$*"; }
log_step()    { printf '    %s\n' "$*"; }
gh_notice()   { printf '::notice title=%s::%s\n' "$1" "$2"; }
gh_error()    { printf '::error::%s\n' "$*" >&2; }
gh_group()    { printf '::group::%s\n' "$*"; }
gh_endgroup() { printf '::endgroup::\n'; }

# req VAR1 VAR2 ... — fail loud if any env var is unset/empty.
req() { local v; for v; do [ -n "${!v:-}" ] || { gh_error "$v must be set"; exit 1; }; done; }

# Per-arch feed directory under the rendered site.
feed_dir() { printf '%s/%s/%s\n' "${SITE_BASE}" "$1" "${FEED_NAME}"; }

# Strip .git suffix and https://github.com/ prefix from a repo URL.
gh_slug() { local s="${1%.git}"; printf '%s\n' "${s#https://github.com/}"; }

# Iterate /tmp/spec.tsv calling fn name ref subs repo per row.
spec_each() {
  local fn="$1" name ref subs repo
  while IFS="${SPEC_SEP}" read -r name ref subs repo; do
    "$fn" "$name" "$ref" "$subs" "$repo"
  done < /tmp/spec.tsv
}

# Iterate EXTRA_PACKAGES tokens, calling `fn parent outs` per token.
#
# Each EXTRA_PACKAGES token has one of two shapes:
#   * `<name>`                — `<name>` is BOTH the source-tree directory to
#                               build AND the kconfig / .ipk output name (used
#                               for packages whose Makefile defines a single
#                               `Package/<name>` block, e.g. `wireguard-tools`).
#   * `<parent>:<o1>,<o2>,…`  — build the source-tree directory `<parent>`,
#                               but ship only the listed sub-packages. Use this
#                               when the desired output (e.g. `librtlsdr`) is a
#                               sub-package defined inside a differently-named
#                               source dir (`rtl-sdr/Makefile` has both
#                               `Package/rtl-sdr` and `Package/librtlsdr`).
#
# `outs` is a space-separated list (callers can word-split it). For the simple
# shape, `outs` equals `parent`.
extra_each() {
  local fn="$1" entry parent rest
  # shellcheck disable=SC2086
  for entry in ${EXTRA_PACKAGES:-}; do
    parent="${entry%%:*}"
    rest="${entry#*:}"
    if [ "${rest}" = "${entry}" ]; then
      "$fn" "${parent}" "${parent}"
    else
      "$fn" "${parent}" "${rest//,/ }"
    fi
  done
}

# Print every requested *output* package name (spec rows + their sub-packages
# + EXTRA_PACKAGES output names), one per line, deduplicated, preserving
# first-seen order. Use this for things that operate on .ipk filenames or
# kconfig nodes, where each sub-package is a distinct entity (configure-sdk's
# CONFIG_PACKAGE_*=m, materialize-feeds' filename prefix grep, etc.).
all_pkg_names() {
  {
    while IFS="${SPEC_SEP}" read -r pkg _ subs _; do
      printf '%s\n' "$pkg"
      [ -n "$subs" ] && tr ',' '\n' <<< "$subs"
    done < /tmp/spec.tsv
    extra_each _emit_outs
  } | awk 'NF && !seen[$0]++'
}
# shellcheck disable=SC2086
_emit_outs() { printf '%s\n' $2; }

# Print every name that should map to a `make package/<path>/compile` target:
# parent names from spec rows + EXTRA_PACKAGES parents, but NOT sub-packages
# (those don't have their own filesystem entry or build rule — they're produced
# as a side effect of building the parent). Use this in build-packages.sh.
all_build_targets() {
  {
    while IFS="${SPEC_SEP}" read -r pkg _ _ _; do printf '%s\n' "$pkg"; done < /tmp/spec.tsv
    extra_each _emit_parent
  } | awk 'NF && !seen[$0]++'
}
_emit_parent() { printf '%s\n' "$1"; }

# OpenWrt SDK helpers (mkhash, usign) live at unpredictable paths under staging_dir/.
sdk_find_tool() {
  find "${2:-${GITHUB_WORKSPACE:-${PWD}}/sdk}" -type f -name "$1" -executable 2>/dev/null | head -n1
}

# Top-level Makefile var: PKGARCH, TITLE, etc.
mk_var() {
  awk -v key="$1" '
    $0 ~ "^[[:space:]]*"key"[[:space:]]*[:+]?=" {
      sub("^[[:space:]]*"key"[[:space:]]*[:+]?=[[:space:]]*", "")
      sub("[[:space:]]+$", ""); print; exit
    }' "$2"
}

# First word of any PROVIDES:= line.
mk_provides() {
  awk '/^[[:space:]]*PROVIDES[[:space:]]*[:+]?=/ {
    sub(/^[[:space:]]*PROVIDES[[:space:]]*[:+]?=[[:space:]]*/, "")
    n = split($0, a, /[[:space:]]+/)
    for (i = 1; i <= n; i++) if (a[i] != "") { print a[i]; exit }
  }' "$1"
}

# Basename of first /etc/config/* in any define Package/<n>/conffiles ... endef block.
mk_conffile() {
  awk '
    /^define[[:space:]]+Package\/[^\/[:space:]]+\/conffiles/ { in_b=1; next }
    in_b && /^endef/ { in_b=0; next }
    in_b && /\/etc\/config\// {
      sub(/^[[:space:]]+/,""); sub(/[[:space:]]+$/,"")
      n = split($0, a, "/"); print a[n]; exit
    }' "$1"
}

# Repack a .ipk under a new arch label: rewrite control.tar.gz Architecture line,
# leave data.tar.gz untouched. Modern OpenWrt .ipk is a gzip-tar of three members;
# legacy is an `ar` archive. Echoes detected fmt (targz|ar) on stdout.
repack_ipk() {
  local src="$1" dst="$2" arch="$3" stage fmt
  stage="$(mktemp -d)"
  cp "$src" "$stage/pkg.ipk"
  if   tar -tzf "$stage/pkg.ipk" >/dev/null 2>&1; then fmt="targz"
  elif ar  t    "$stage/pkg.ipk" >/dev/null 2>&1; then fmt="ar"
  else gh_error "Unknown .ipk container: $src"; rm -rf "$stage"; return 1
  fi
  ( cd "$stage" && case "$fmt" in
      targz) tar -xzf pkg.ipk ;;
      ar)    ar  x  pkg.ipk ;;
    esac && rm pkg.ipk )

  mkdir -p "$stage/control"
  tar -C "$stage/control" -xzf "$stage/control.tar.gz"
  sed -i 's/\r$//' "$stage/control/control"
  sed -i -E "s/^Architecture:[[:space:]]*.*/Architecture: ${arch}/" "$stage/control/control"
  grep -q "^Architecture: ${arch}\$" "$stage/control/control" || {
    gh_error "Failed to rewrite Architecture for $src -> $arch"
    rm -rf "$stage"; return 1
  }
  rm -f "$stage/control.tar.gz"
  tar --owner=0 --group=0 -C "$stage/control" -czf "$stage/control.tar.gz" .
  rm -rf "$stage/control"
  ( cd "$stage" && case "$fmt" in
      targz) tar --owner=0 --group=0 -czf pkg.ipk ./debian-binary ./control.tar.gz ./data.tar.gz ;;
      ar)    ar -rc pkg.ipk debian-binary data.tar.gz control.tar.gz ;;
    esac )
  mv "$stage/pkg.ipk" "$dst"
  rm -rf "$stage"
  printf '%s\n' "$fmt"
}

arch_hint() {
  case "$1" in
    aarch64_cortex-a53_neon-vfpv4) echo "GL.iNet ApNos firmware (AXT1800 / AX1800, 23.05-based)" ;;
    aarch64_cortex-a53)            echo "Mainline OpenWrt on Cortex-A53 (ipq60xx / ipq807x)" ;;
    arm_cortex-a7)                 echo "GL.iNet legacy QSDK 4.x firmware (32-bit)" ;;
    *)                             echo "$1" ;;
  esac
}

type_label() {
  case "$1" in gl-sdk4) echo "GL.iNet 4.x" ;; openwrt) echo "OpenWrt" ;; *) echo "$1" ;; esac
}

# Per-arch HTML fragment with an explanatory note about the firmware's
# stock /etc/opkg/distfeeds.conf, plus (for the mainline OpenWrt case) a
# recovery snippet for the rare case where distfeeds.conf is missing.
# Consumed by render-site.sh as the body of a collapsed <details> block
# next to the main install snippet.
#
# Important: on a stock install of either supported firmware, the user
# only adds ONE src/gz line to customfeeds.conf (ours) and `opkg install`
# of any spec package resolves end-to-end, because /etc/opkg/distfeeds.conf
# already covers the rest. This block exists to MAKE THAT FACT EXPLICIT
# (so users don't second-guess and start pasting random feeds), not to
# suggest extra setup is normally needed.
#
# opkg matches packages by literal Architecture: string and each upstream
# feed only publishes one label, so any extra feed pasted in must match
# the router's arch_priority. The upstream OpenWrt downloads.openwrt.org
# packages feed labels its aarch64 packages plainly `aarch64_cortex-a53`
# and is therefore safe on mainline OpenWrt but NOT on GL.iNet ApNos
# (`aarch64_cortex-a53_neon-vfpv4`), where opkg would silently reject
# every entry with "no valid architecture, ignoring".
#
# Reads SDK_URL_BASE for the OpenWrt release version (e.g. 23.05.6).
upstream_feeds_html() {
  local arch="$1" rel
  rel="$(printf '%s\n' "${SDK_URL_BASE:-}" | sed -nE 's|.*/releases/([^/]+)/.*|\1|p')"
  case "${arch}" in
    aarch64_cortex-a53)
      cat <<HTML
    <p>Stock mainline OpenWrt preconfigures its package feeds in <code>/etc/opkg/distfeeds.conf</code> (<code>openwrt_base</code>, <code>openwrt_packages</code>, <code>openwrt_luci</code>, <code>openwrt_routing</code>, <code>openwrt_telephony</code>). Common deps (<code>libncurses6</code>, <code>zlib</code>, <code>jsonfilter</code>, <code>ca-bundle</code>, <code>libstdcpp6</code>, etc.) come from there. You don't need to add anything else for <code>opkg install</code> of any spec package above to resolve end-to-end \u2014 verify with <code>cat /etc/opkg/distfeeds.conf</code>.</p>
    <p><strong>Recovery snippet (rare).</strong> If your <code>distfeeds.conf</code> is empty or you've intentionally disabled it, paste this to re-add the upstream OpenWrt ${rel:-23.05.x} feeds. Architecture labels match (<code>${arch}</code>) so opkg accepts them:</p>
    <pre><code>cat &gt;&gt; /etc/opkg/customfeeds.conf &lt;&lt;'OPENWRT_UPSTREAM'
src/gz openwrt_base       https://downloads.openwrt.org/releases/${rel:-23.05.6}/packages/${arch}/base
src/gz openwrt_packages   https://downloads.openwrt.org/releases/${rel:-23.05.6}/packages/${arch}/packages
src/gz openwrt_luci       https://downloads.openwrt.org/releases/${rel:-23.05.6}/packages/${arch}/luci
src/gz openwrt_routing    https://downloads.openwrt.org/releases/${rel:-23.05.6}/packages/${arch}/routing
src/gz openwrt_telephony  https://downloads.openwrt.org/releases/${rel:-23.05.6}/packages/${arch}/telephony
OPENWRT_UPSTREAM
opkg update</code></pre>
HTML
      ;;
    aarch64_cortex-a53_neon-vfpv4)
      cat <<HTML
    <p>Stock GL.iNet ApNos firmware (AXT1800 / AX1800) preconfigures its package feeds in <code>/etc/opkg/distfeeds.conf</code>: <code>glinet_core</code> (kernel modules) and <code>opnwrt_packages</code> (userspace). The deps the spec packages need that GL.iNet <em>doesn't</em> ship (<code>librtlsdr</code>, <code>libzstd</code>) are bundled into this feed; everything else (<code>libncurses6</code>, <code>zlib</code>, <code>jsonfilter</code>, <code>ca-bundle</code>, <code>libstdcpp6</code>, <code>bash</code>, <code>jq</code>, <code>curl</code>, <code>coreutils-stat</code>, <code>wireguard-tools</code>, etc.) comes from there. You don't need to add anything else \u2014 verify with <code>cat /etc/opkg/distfeeds.conf</code>.</p>
    <p><strong>Do not paste the upstream OpenWrt 23.05 packages feed on this arch.</strong> Upstream labels its <code>aarch64</code> packages plainly <code>aarch64_cortex-a53</code>, but ApNos's <code>arch_priority</code> accepts only the literal string <code>${arch}</code>. opkg would fetch the index and silently drop every entry with <em>no valid architecture, ignoring</em>.</p>
HTML
      ;;
    *)
      cat <<HTML
    <p>No supplementary feed note recorded for <code>${arch}</code>. Refer to your distribution's package-feed documentation; on most stock installs <code>/etc/opkg/distfeeds.conf</code> already covers everything this feed depends on.</p>
HTML
      ;;
  esac
}
