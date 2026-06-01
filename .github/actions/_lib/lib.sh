# shellcheck shell=bash
# Shared bash helpers for the bos-marketplace-kit composite actions.
#
# Sourced via:
#     . "${GITHUB_ACTION_PATH}/../_lib/lib.sh"
#
# Conventions:
#   * Every helper logs to stderr and exits 1 on hard failure.
#   * `die` accepts an optional `ERR_TITLE` env var for the GH error
#     annotation title; defaults to "Marketplace".
#   * `parse_pathlist` populates a caller-named array via nameref
#     (bash 4.3+; GH runners are bash 5).
#
# Actions that need their own `record`, `skipped`, or `validate_req`
# define them locally AFTER sourcing this file (the kit's `check`
# action does so because its rule machinery differs).

# ---------------------------------------------------------------------------
# die / validation
# ---------------------------------------------------------------------------

# die "<message>"  — emit GH-annotated error and exit 1.
die() {
  printf '::error title=%s::%s\n' "${ERR_TITLE:-Marketplace}" "$*" >&2
  exit 1
}

# validate_bool <var-name> — assert that the value is exactly "true" or
# "false". Reads via indirection so callers can pass the variable name.
validate_bool() {
  local name="$1"
  local val="${!name-}"
  case "${val}" in
    true|false) : ;;
    *) die "input ${name}=${val:-<empty>} must be 'true' or 'false'" ;;
  esac
}

# require_var <var-name> — assert that the named variable is non-empty.
# (Named `require_var` instead of `validate_req` to avoid colliding
# with action-local `validate_req` helpers that take different
# argument shapes.)
require_var() {
  local name="$1"
  local val="${!name-}"
  [ -n "${val}" ] || die "required input ${name} is empty"
}

# ---------------------------------------------------------------------------
# Path-list parsing
# ---------------------------------------------------------------------------

# parse_pathlist <out-array-name> <raw-multiline-string> <label> [glob_ok]
#
# Splits the raw multi-line input on newlines and whitespace, strips
# `#`-comments, validates each token against:
#   * no absolute paths (must be repo-relative)
#   * no '..' segments
#   * no embedded newline/CR (defence-in-depth: input arrives via env)
#   * no glob metacharacters, UNLESS glob_ok=yes (guard's blocked_paths
#     accepts git pathspecs, which can contain '*')
# Populates the caller's array via nameref.
#
# Usage:
#     local -a ALLOW=()
#     parse_pathlist ALLOW "${RAW_INPUT}" "allowlist"
parse_pathlist() {
  local -n _out="$1"
  local raw="$2"
  local label="$3"
  local glob_ok="${4:-no}"

  _out=()
  local line token
  while IFS= read -r line || [ -n "${line}" ]; do
    # Strip everything after the first unquoted '#' — keep it simple.
    line="${line%%#*}"
    for token in ${line}; do
      [ -z "${token}" ] && continue
      case "${token}" in
        /*)
          die "${label} path must be repo-relative, got absolute: '${token}'"
          ;;
        *..*)
          die "${label} path must not contain '..': '${token}'"
          ;;
        *$'\n'*|*$'\r'*)
          die "${label} path contains newline/CR: '${token}'"
          ;;
      esac
      if [ "${glob_ok}" != "yes" ]; then
        case "${token}" in
          *'*'*|*'?'*|*'['*)
            die "${label} path must not contain glob characters: '${token}'"
            ;;
        esac
      fi
      _out+=("${token%/}")
    done
  done <<< "${raw}"
}
