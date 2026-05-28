#!/usr/bin/env bash
# Render site-src/index.html into $SITE/index.html, copy _headers, write a
# build summary to $GITHUB_STEP_SUMMARY, and emit ::notice lines. Per-package
# metadata is sourced from /tmp/pkg-meta/<n>.env so the docs stay in sync with
# each upstream Makefile (no hand-maintained per-package table). Fails loud on
# any leftover __TOKEN__.

set -euo pipefail
# shellcheck source=.github/scripts/openwrt/lib.sh
. "$(dirname "$0")/lib.sh"
req FEED_NAME FEED_RELEASE_LABEL PUBLIC_FEED_BASE SDK_URL_BASE GITHUB_WORKSPACE

SITE="${GITHUB_WORKSPACE}/site"
SRC="${GITHUB_WORKSPACE}/site-src"
REL="/${FEED_RELEASE_LABEL}/packages"

# Copy hand-maintained `_headers` if the consumer ships one. When the
# OpenWrt deploy workflow has `cloudflare_generate_headers: true` the
# consumer omits this file and the hub's `cf-pages-headers-generate`
# composite action writes `site/_headers` itself after this script
# runs. Both paths are mutually exclusive: the generator refuses to
# overwrite an existing `_headers` unless `replace_existing: true`.
if [ -f "${SRC}/_headers" ]; then
  install -m 0644 "${SRC}/_headers" "${SITE}/_headers"
fi

mapfile -t ARCHES < <(find "${SITE}${REL}" -mindepth 1 -maxdepth 1 -type d -printf '%f\n' | sort)

# ── Per-arch install snippets + arch-table rows ────────────────
FEED_INSTALL_SNIPPETS=""
ARCH_TABLE_ROWS=""
for a in "${ARCHES[@]}"; do
  url="${PUBLIC_FEED_BASE}${REL}/${a}/${FEED_NAME}"
  FEED_INSTALL_SNIPPETS+="$(cat <<EOF
  <h3>$(arch_hint "$a")</h3>
  <pre><code>echo 'src/gz ${FEED_NAME} ${url}' &gt;&gt; /etc/opkg/customfeeds.conf
opkg update</code></pre>
  <details class="upstream-feeds">
    <summary>Why this is the only line you need to add (and what comes from your stock feeds)</summary>
$(upstream_feeds_html "$a")
  </details>
EOF
)"$'\n'
  ARCH_TABLE_ROWS+="      <tr><td><code>${a}</code></td><td><a href=\"${REL}/${a}/${FEED_NAME}/Packages\">Packages</a></td><td><a href=\"${REL}/${a}/${FEED_NAME}/Packages.gz\">Packages.gz</a></td></tr>"$'\n'
done

# ── Per-package tab inputs / labels / panels / CSS ─────────────
PACKAGE_TAB_INPUTS=""; PACKAGE_TAB_LABELS=""
PACKAGE_PANELS="";    PACKAGE_TAB_CSS=""
declare -A type_count
first=1

while IFS="${SPEC_SEP}" read -r name ref subs repo; do
  # shellcheck disable=SC1090
  . "/tmp/pkg-meta/${name}.env"
  type_count[${PKG_FEED_TYPE}]=$(( ${type_count[${PKG_FEED_TYPE}]:-0} + 1 ))

  remove_pkgs="${name}"
  for s in ${subs//,/ }; do [ -n "$s" ] && remove_pkgs+=" $s"; done

  repo_html="${repo%.git}"
  repo_slug="$(gh_slug "${repo}")"
  source_url="${repo_html}/tree/${ref}"
  type_lbl="$(type_label "${PKG_FEED_TYPE}")"
  arch_lbl="${PKG_PKGARCH}"
  [ "${arch_lbl}" = "all" ] && arch_lbl="all (every arch)"

  pkg_docs=""
  [ -f "${SRC}/pkg-${name}.html" ] && pkg_docs="$(cat "${SRC}/pkg-${name}.html")"

  if [ "${PKG_KEEP_CONF}" = "1" ]; then
    uninstall_html=$(cat <<EOF
    <p>Removes the package. Its <code>prerm</code> hook tears down all live state (sysctl drop-ins, firewall rules, hotplug overrides, runtime state). The package owns no conffile of its own — any UCI files it reads are firmware-owned and are left untouched:</p>
    <pre><code>opkg remove ${remove_pkgs}</code></pre>
EOF
)
  else
    uninstall_html=$(cat <<EOF
    <p>Removes the package(s). The <code>prerm</code> stops and disables the service; the <code>postrm</code> cleans up <code>/var/run/${PKG_RUNTIME}</code>:</p>
    <pre><code>opkg remove ${remove_pkgs}</code></pre>
    <p>Optional — also discard the conffile (opkg preserves your edits on remove and saves the package default as <code>*-opkg</code> if it differs):</p>
    <pre><code>rm -f /etc/config/${PKG_CONF} /etc/config/${PKG_CONF}-opkg</code></pre>
EOF
)
  fi

  PACKAGE_PANELS+="$(cat <<EOF
  <section class="pkg-panel pkg-panel-${name}" id="pkg-${name}-panel" aria-labelledby="tab-label-${name}">
    <h2 class="pkg-title">${name} <span class="pill pill-type pill-type-${PKG_FEED_TYPE}">${type_lbl}</span><span class="pill pill-arch">${arch_lbl}</span><span class="pill">from <a href="${source_url}"><code>${repo_slug}</code> @ <code>${ref}</code></a></span></h2>
    <h3 id="install-${name}">Install</h3>
    <p>After adding the feed above, install with:</p>
    <pre><code>opkg install ${name}</code></pre>
    ${pkg_docs}
    <h3 id="uninstall-${name}">Uninstall</h3>
${uninstall_html}
  </section>
EOF
)"$'\n'

  checked=""; [ "${first}" -eq 1 ] && { checked=" checked"; first=0; }
  # Active-tab fill colour follows the feed-type accent for visual consistency.
  case "${PKG_FEED_TYPE}" in gl-sdk4) accent='--accent-2' ;; *) accent='--accent' ;; esac

  PACKAGE_TAB_INPUTS+="  <input type=\"radio\" id=\"tab-${name}\" name=\"pkg-tab\" class=\"pkg-tab-radio\"${checked}>"$'\n'
  PACKAGE_TAB_LABELS+="    <label for=\"tab-${name}\" id=\"tab-label-${name}\" class=\"pkg-tab-label pkg-tab-label-${PKG_FEED_TYPE}\">${name}</label>"$'\n'
  PACKAGE_TAB_CSS+="  #tab-${name}:checked ~ .pkg-tab-panels .pkg-panel-${name} { display: block; }"$'\n'
  PACKAGE_TAB_CSS+="  #tab-${name}:checked ~ .pkg-tab-labels label[for=\"tab-${name}\"] { background: var(${accent}); color: #0b0d10; border-color: var(${accent}); }"$'\n'
done < /tmp/spec.tsv

[ -n "${PACKAGE_PANELS}" ] || { gh_error "PACKAGES_SPEC produced zero panels"; exit 1; }

# ── Token substitution ─────────────────────────────────────────
tpl="$(cat "${SRC}/index.html")"
tpl="${tpl//__PACKAGE_TAB_CSS__/${PACKAGE_TAB_CSS}}"
tpl="${tpl//__PACKAGE_TAB_INPUTS__/${PACKAGE_TAB_INPUTS}}"
tpl="${tpl//__PACKAGE_TAB_LABELS__/${PACKAGE_TAB_LABELS}}"
tpl="${tpl//__PACKAGE_PANELS__/${PACKAGE_PANELS}}"
tpl="${tpl//__FEED_INSTALL_SNIPPETS__/${FEED_INSTALL_SNIPPETS}}"
tpl="${tpl//__ARCH_TABLE_ROWS__/${ARCH_TABLE_ROWS}}"
tpl="${tpl//__FEED_RELEASE_LABEL__/${FEED_RELEASE_LABEL}}"
tpl="${tpl//__FEED_NAME__/${FEED_NAME}}"
tpl="${tpl//__FEED_REPO_SLUG__/$(gh_slug "${FEED_REPO:-}")}"
tpl="${tpl//__PUBLIC_FEED_BASE__/${PUBLIC_FEED_BASE}}"

if leftover="$(printf '%s' "$tpl" | grep -oE '__[A-Z_]+__' | sort -u)" && [ -n "${leftover}" ]; then
  gh_error "Unsubstituted tokens in site-src/index.html:"
  while IFS= read -r t; do printf '  %s\n' "$t" >&2; done <<< "${leftover}"
  exit 1
fi
printf '%s' "$tpl" > "${SITE}/index.html"

# ── GitHub Actions build summary + notices ─────────────────────
{
  echo "## Feed published"
  echo
  echo "**Base:** \`${PUBLIC_FEED_BASE}${REL}/<arch>/${FEED_NAME}\`"
  echo
  echo "**Arches:** ${ARCHES[*]}"
  echo
  echo "| Package | Type | PKGARCH | Source |"
  echo "| --- | --- | --- | --- |"
  while IFS="${SPEC_SEP}" read -r name ref _ repo; do
    # shellcheck disable=SC1090
    . "/tmp/pkg-meta/${name}.env"
    echo "| \`${name}\` | ${PKG_FEED_TYPE} | \`${PKG_PKGARCH}\` | \`$(gh_slug "${repo}")@${ref}\` |"
  done < /tmp/spec.tsv
} >> "${GITHUB_STEP_SUMMARY:-/dev/null}"

gh_notice "Feed published" "${PUBLIC_FEED_BASE}${REL}/<arch>/${FEED_NAME}"
gh_notice "Arches"         "${ARCHES[*]}"
for t in "${!type_count[@]}"; do gh_notice "Packages (${t})" "${type_count[$t]}"; done
