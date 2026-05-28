#!/usr/bin/env bash
# Normalize PACKAGES_SPEC into /tmp/spec.tsv. We read the textarea via jq from
# $GITHUB_EVENT_PATH because `${{ inputs.X }}` interpolated into a YAML scalar
# collapses real newlines. Both triggers now use the same single-line `;;`
# joiner format (see PACKAGES_SPEC_FALLBACK). Real newlines are also accepted —
# either separator works for ad-hoc edits in the textarea.

set -euo pipefail
# shellcheck source=.github/scripts/openwrt/lib.sh
. "$(dirname "$0")/lib.sh"
req FEED_REPO PACKAGES_SPEC_FALLBACK

spec=""
if [ "${GITHUB_EVENT_NAME:-}" = "workflow_dispatch" ] && [ -r "${GITHUB_EVENT_PATH:-/dev/null}" ]; then
  spec="$(jq -r '.inputs.packages_spec // ""' "${GITHUB_EVENT_PATH}")"
fi
[ -n "${spec}" ] || spec="${PACKAGES_SPEC_FALLBACK}"
spec="${spec//;;/$'\n'}"

: > /tmp/spec.tsv
lineno=0
# Read with one extra var (`extra`) to detect rows with too many `|` fields,
# which happen when the upstream multi-line input arrived collapsed to a
# single line (every `|` past the 4th would otherwise silently get folded
# into `repo`, producing nonsense like `pkgB|refB|...|https://...` as a URL).
while IFS='|' read -r name ref subs repo extra; do
  lineno=$((lineno+1))
  name="${name//[$' \t\r\n']/}"; ref="${ref//[$' \t\r\n']/}"
  subs="${subs//[$' \t\r\n']/}"; repo="${repo//[$' \t\r\n']/}"
  extra="${extra//[$' \t\r\n']/}"
  [ -z "${name}" ] || [ "${name#\#}" != "${name}" ] && continue
  [ -n "${ref}" ] || { gh_error "line ${lineno}: missing ref for '${name}'"; exit 1; }
  if [ -n "${extra}" ]; then
    gh_error "line ${lineno}: too many '|' fields for package '${name}'."
    gh_error "  Got: name='${name}' ref='${ref}' subs='${subs}' repo='${repo}' extra='${extra}'"
    gh_error "  Expected exactly 4 '|'-separated fields per package: name|ref|subs|repo"
    gh_error "  Separate multiple packages with ';;' or a real newline (do NOT join them with '|')."
    exit 1
  fi
  case "${repo}" in
    ''|http://*|https://*|git://*|ssh://*|git@*)
      ;;
    *)
      gh_error "line ${lineno}: repo URL for '${name}' is not a recognised git URL: '${repo}'"
      gh_error "  Expected http(s)://, git://, ssh:// or git@host:path/repo.git form, or an empty 4th field to inherit FEED_REPO."
      exit 1
      ;;
  esac
  printf '%s%s%s%s%s%s%s\n' \
    "${name}" "${SPEC_SEP}" "${ref}" "${SPEC_SEP}" "${subs}" "${SPEC_SEP}" "${repo:-${FEED_REPO}}" >> /tmp/spec.tsv
done <<< "${spec}"

[ -s /tmp/spec.tsv ] || { gh_error "PACKAGES_SPEC produced zero packages"; exit 1; }

gh_group "Resolved spec (/tmp/spec.tsv)"
# Render the SPEC_SEP-delimited TSV as aligned columns when `column(1)` is
# available, otherwise fall back to tab-separated. The previous form was
# `tr ... | column -ts ... 2>/dev/null || tr ...` which:
#   1. Triggered a spurious `tr: write error: Broken pipe` when `column`
#      was missing (the `|| tr ...` fired before `tr` finished flushing).
#   2. Hid real `column` failures behind the silent `2>/dev/null`.
# Probe once, then run a single non-piped command.
if command -v column >/dev/null 2>&1; then
  tr "${SPEC_SEP}" '\t' < /tmp/spec.tsv | column -ts $'\t'
else
  tr "${SPEC_SEP}" '\t' < /tmp/spec.tsv
fi
gh_endgroup
