#!/usr/bin/env bash
# Stage each package's source under sdk/package/extra/<n>/ and write per-package
# metadata to /tmp/pkg-meta/<n>.env. Repos are cloned into
# /tmp/feed-clones/<repo-slug>__<ref-slug>/ and reused across packages.
#
# Emitted env fields (consumed by render-site.sh, check-changes.sh, write-state.sh):
#   PKG_FEED_TYPE  gl-sdk4 | openwrt
#   PKG_PKGARCH    PKGARCH:= line, defaults to BUILT_ARCH
#   PKG_RUNTIME    first PROVIDES:= token, falls back to package name
#   PKG_CONF       conffile basename, falls back to package name
#   PKG_KEEP_CONF  1 if the package owns no conffile, else 0
#   PKG_COMMIT     upstream commit SHA the source was cloned at (for incremental rebuilds)

set -euo pipefail
# shellcheck source=.github/scripts/openwrt/lib.sh
. "$(dirname "$0")/lib.sh"
req BUILT_ARCH

mkdir -p package/extra /tmp/feed-clones /tmp/pkg-meta

while IFS="${SPEC_SEP}" read -r name ref _subs repo; do
  repo_slug="$(printf '%s' "${repo}" | tr -c 'A-Za-z0-9' '_' | sed -E 's/_+/_/g; s/^_|_$//g')"
  clone_dir="/tmp/feed-clones/${repo_slug}__${ref//\//_}"
  if [ ! -d "${clone_dir}/.git" ]; then
    log "clone ${repo}@${ref}"
    git clone --depth=1 --branch "${ref}" "${repo}" "${clone_dir}"
  fi
  commit="$(git -C "${clone_dir}" rev-parse HEAD)"

  # Match nested (utils/<n>/Makefile) and flat (<n>/Makefile, e.g. gl-feeds) layouts.
  PKG_SRC="$(find "${clone_dir}" -mindepth 2 -maxdepth 5 -type f \
    -name Makefile -path "*/${name}/Makefile" | head -n1)"
  if [ -z "${PKG_SRC}" ]; then
    gh_error "Could not find ${name}/Makefile in ${repo}@${ref}"
    find "${clone_dir}" -maxdepth 5 -name Makefile | head -n50 >&2
    exit 1
  fi

  DEST="package/extra/${name}"
  rm -rf "${DEST}"
  cp -r "$(dirname "${PKG_SRC}")" "${DEST}"

  pkgarch="$(mk_var PKGARCH "${PKG_SRC}")"
  provides="$(mk_provides "${PKG_SRC}")"
  conffile="$(mk_conffile "${PKG_SRC}")"

  feed_type=openwrt
  if [[ "${name}" == gl-sdk4-* ]] \
    || grep -qE '^[[:space:]]*CATEGORY[[:space:]]*[:+]?=[[:space:]]*gl-sdk4' "${PKG_SRC}"; then
    feed_type=gl-sdk4
  fi

  cat > "/tmp/pkg-meta/${name}.env" <<EOF
PKG_FEED_TYPE=${feed_type}
PKG_PKGARCH=${pkgarch:-${BUILT_ARCH}}
PKG_RUNTIME=${provides:-${name}}
PKG_CONF=${conffile:-${name}}
PKG_KEEP_CONF=$([ -z "${conffile}" ] && echo 1 || echo 0)
PKG_COMMIT=${commit}
EOF
  log_step "staged ${DEST}  type=${feed_type}  arch=${pkgarch:-${BUILT_ARCH}}  provides=${provides:-<none>}  conffile=${conffile:-<none>}  commit=${commit:0:12}"
done < /tmp/spec.tsv

./scripts/feeds update -a >/dev/null
./scripts/feeds install -a >/dev/null
