#!/bin/bash

# =============================================================
# Copyright (c) 2026 Blackout Secure
# https://blackoutsecure.app
# License: Apache-2.0  (see repository root LICENSE)
#
# Script:  install-utm-vm-autostart.sh
# Purpose: Installs a small helper script and a system-wide
#          LaunchAgent that auto-starts UTM virtual machines
#          (via `utmctl start`) every time a user logs in.
#
# Prerequisite -- per-user "Open at Login" walkthrough:
#   The LaunchAgent installed here is SYSTEM-WIDE: it triggers
#   the helper at *every* user login on this Mac. But the helper
#   can only start VMs while UTM.app itself is running, and
#   UTM.app is a per-user GUI application -- macOS will not
#   launch it system-wide. You must therefore add UTM.app to
#   "Open at Login" for *every* user account whose VMs should
#   auto-start. Users without it will still trigger the
#   LaunchAgent at login, but the helper will wait
#   $UTM_AUTOSTART_BOOT_TIMEOUT seconds for UTM.app, fail to
#   find it, log an error to /var/log/bos-utm-vm-autostart.log,
#   and exit non-zero -- so the Login Item is effectively the
#   opt-in switch: no Login Item, no VM autostart for that user.
#
#   For each user that should have VMs auto-start, sign in as
#   that user and do ONE of the following:
#
#     Option A -- GUI (per end user):
#       1. System Settings -> General -> Login Items & Extensions.
#       2. Under "Open at Login", click the "+" button.
#       3. Navigate to /Applications/UTM.app and click "Open".
#       4. Verify "UTM" appears in the list (optionally tick "Hide").
#       5. Log out and back in to confirm UTM.app launches.
#
#     Option B -- AppleScript (scriptable, works in the user's
#                 own GUI session; useful in onboarding scripts):
#       osascript -e 'tell application "System Events" to make \
#         new login item at end with properties \
#         {path:"/Applications/UTM.app", hidden:false}'
#
#     Option C -- MDM-managed Login Item (Jamf, Kandji, Mosyle,
#                 Intune, Workspace ONE):
#       Push a per-user Login Items configuration profile
#       targeted at the user-scoped group whose VMs should
#       auto-start. Pair it with this installer (scoped at the
#       device level) so the two halves stay in sync.
#
# Modes:
#   apply (default) - install / reconcile helper + LaunchAgent
#   --check         - read-only audit. Exit 0 = all PASS, 2 = drift
#   --uninstall     - bootout the LaunchAgent for the active console
#                     user, then remove the helper + plist. Idempotent
#                     (succeeds even if nothing is installed). The
#                     shared log is retained for diagnostics.
#
# Idempotency:
#   Re-running with the same configuration is a no-op: the helper
#   script and plist are byte-compared, ownership and permissions
#   are reasserted, and the LaunchAgent is only re-bootstrapped
#   when content actually changed.
#
# Selection model:
#   The helper picks VMs to start using an explicit name list, a regex
#   matched against 'utmctl list', or both. The default mode 'auto'
#   accepts either or both inputs and falls back from list -> regex at
#   runtime; the strict modes 'list' / 'regex' force exactly one input
#   and error on conflicts.
#
#   UTM_AUTOSTART_MODE             Selection mode:
#                                    list   -- force list mode. Use
#                                              UTM_AUTOSTART_VMS (or
#                                              the baked-in 'defaultvms').
#                                              Errors if MATCH is also set.
#                                    regex  -- force regex mode. Use
#                                              UTM_AUTOSTART_MATCH.
#                                              Errors if MATCH is unset
#                                              or if VMS is also set.
#                                    auto   -- (default) accept either
#                                              or both inputs. At login
#                                              the helper:
#                                                1. tries the explicit
#                                                   list (only counting
#                                                   names that exist in
#                                                   'utmctl list'); if
#                                                   any match -> done.
#                                                2. otherwise falls
#                                                   back to the regex.
#                                                3. if both yield zero,
#                                                   logs 'no VMs found'
#                                                   and exits 0.
#                                              Setting both VMS and
#                                              MATCH is only allowed in
#                                              this mode.
#   UTM_AUTOSTART_VMS              Explicit list input: newline- or
#                                  comma-separated VM names, started
#                                  in the order given.
#   UTM_AUTOSTART_MATCH            Regex input: POSIX ERE tested
#                                  against the name column of
#                                  `utmctl list` at every login.
#                                    Examples:
#                                      ^prod-                       (all prod-* VMs)
#                                      \[autostart\]$              (suffix tag)
#   UTM_AUTOSTART_EXCLUDE          Optional ERE; regex names matching
#                                  this are skipped (applies whenever
#                                  the regex branch runs, including in
#                                  the auto-mode fallback).
#   (no selection input)           Auto mode falls back to the
#                                  `defaultvms` variable in the
#                                  variables block below. If that is
#                                  also empty (the default), the
#                                  installer still completes but the
#                                  helper will log 'no VMs found' at
#                                  every login until selection is set.
#
# Common runtime tuning (env vars, evaluated at install time):
#   UTM_AUTOSTART_DELAY_SECONDS    Delay between successive VM starts.
#                                  Default: 5
#   UTM_AUTOSTART_BOOT_TIMEOUT     Max seconds to wait for UTM.app
#                                  to be running before giving up.
#                                  Default: 60
#   UTM_AUTOSTART_WAIT_POLL_INTERVAL
#                                  Seconds between `pgrep` polls while
#                                  waiting for UTM.app. Lower = faster
#                                  detection (more CPU); higher = less
#                                  CPU on slow hardware. Default: 1
#   UTM_AUTOSTART_SKIP_RUNNING     Skip VMs whose `utmctl list` status
#                                  is not 'stopped' (true/false).
#                                  Default: true
#   UTM_AUTOSTART_USER_EXCLUDE     Comma- or newline-separated list of
#                                  macOS usernames to skip. If the user
#                                  triggering the LaunchAgent at login
#                                  matches one of these names, the
#                                  helper exits 0 immediately without
#                                  contacting utmctl. Use this when the
#                                  Mac is shared with accounts (guest,
#                                  kiosk, demo, service users, ...) for
#                                  which VMs should NOT auto-start.
#                                  Default: (empty -- no users excluded)
#
# Deployment:
#   MDM (Intune, Jamf, Kandji, Mosyle, Workspace ONE):
#     Activity is streamed to both the console and $log.
#     Exit codes:
#       0 = success (installed / already in desired state)
#       1 = failure (review log for details)
#       2 = drift detected (only emitted by --check)
#
#   Manual:
#     sudo bash ./macos/application-management/utm-vm-autostart/install-utm-vm-autostart.sh
# =============================================================

# =============================================================
# CONFIGURATION
# =============================================================
# All operator-facing knobs live in this block. The script body
# below references only these variables (or values derived from
# them) -- there are no other hardcoded paths, timings, or names.
# Edit a default here for a permanent change, or override at
# install time via the matching UTM_AUTOSTART_* env var.

# ----- Identity & installed paths -----
appname="UTM VM Autostart"
label="app.blackoutsecure.utm-vm-autostart"
helperpath="/usr/local/bin/bos-utm-vm-autostart"
plistpath="/Library/LaunchAgents/${label}.plist"
# Single shared log for both the installer (running as root) and the
# per-user LaunchAgent helper (running in the login user's gui domain).
# Created 0666 by the installer so the user-context helper can append --
# see the `touch + chmod` block right before `exec > >(tee -a "$log")`.
log="/var/log/bos-utm-vm-autostart.log"

# ----- Ownership / permissions applied to installed files -----
fileowner="root:wheel"
helpermode="0755"
plistmode="0644"

# ----- Platform / UTM integration points -----
# These are the only literal paths and process names referenced by both
# the installer's --check audit and the rendered helper script. Edit
# here to support a non-standard UTM install location.
supportedos="Darwin"
utmappdir="/Applications/UTM.app"
utmappbinary="${utmappdir}/Contents/MacOS/utmctl"
utmprocess="UTM"
# Space-separated list of utmctl candidates, in lookup order.
utmctlpaths="/usr/local/bin/utmctl /opt/homebrew/bin/utmctl ${utmappbinary}"

# ----- Selection defaults (which mode / which VMs / which users) -----
# defaultmode: baked-in default for UTM_AUTOSTART_MODE. One of:
#   auto   -- (default) accept either UTM_AUTOSTART_VMS, UTM_AUTOSTART_MATCH,
#             or BOTH. At login the helper tries the explicit list first
#             (only counting names that exist in 'utmctl list') and falls
#             back to the regex if the list yielded nothing.
#   list   -- force list mode (UTM_AUTOSTART_VMS / defaultvms).
#             Errors if UTM_AUTOSTART_MATCH is also passed in.
#   regex  -- force regex mode (UTM_AUTOSTART_MATCH). Errors if
#             UTM_AUTOSTART_MATCH is unset, or UTM_AUTOSTART_VMS is
#             also passed in.
defaultmode="auto"
# defaultvms: optional newline-separated baked-in VM list. Leave empty
# to require callers to provide UTM_AUTOSTART_VMS or UTM_AUTOSTART_MATCH;
# set to, e.g., $'web-vm\napi-vm\ndb-vm' to embed a deployment-specific
# fallback list directly in the script.
defaultvms=""
# defaultuserexclude: optional newline-separated baked-in list of macOS
# usernames that should NEVER trigger the autostart helper. Leave empty
# to opt all users in (the historical behaviour). Set to, e.g.,
# $'guest\nkiosk\ndemo' to embed a deployment-specific denylist.
defaultuserexclude=""

# ----- Runtime tuning defaults (timing, skip behaviour, log level) -----
# All of these are overridable at install time via the matching
# UTM_AUTOSTART_* env var (see the resolved-tunables block below).
defaultdelay=5                 # seconds between successive VM starts
defaultboottimeout=60          # max seconds to wait for UTM.app
defaultwaitpollinterval=1      # seconds between pgrep polls in wait loop
defaultskiprunning="true"      # skip VMs not in 'stopped' state
defaultloglevel="info"         # trace|debug|info|warn|error

# ----- Resolved user tunables (env vars consumed at install time) -----
delaybetween="${UTM_AUTOSTART_DELAY_SECONDS:-${defaultdelay}}"
boottimeout="${UTM_AUTOSTART_BOOT_TIMEOUT:-${defaultboottimeout}}"
waitpollinterval="${UTM_AUTOSTART_WAIT_POLL_INTERVAL:-${defaultwaitpollinterval}}"
skiprunning_raw="${UTM_AUTOSTART_SKIP_RUNNING:-${defaultskiprunning}}"
userexcluderaw="${UTM_AUTOSTART_USER_EXCLUDE:-${defaultuserexclude}}"

# Normalise UTM_AUTOSTART_SKIP_RUNNING to 'true' / 'false'.
case "$(printf '%s' "$skiprunning_raw" | tr '[:upper:]' '[:lower:]')" in
    true|1|yes|y|on)   skiprunning="true" ;;
    false|0|no|n|off)  skiprunning="false" ;;
    *) echo "ERROR: UTM_AUTOSTART_SKIP_RUNNING must be true/false (got '$skiprunning_raw')"; exit 1 ;;
esac

# Normalise UTM_AUTOSTART_USER_EXCLUDE: accept comma- or newline-separated,
# trim whitespace, drop blanks. Validate against POSIX portable username
# charset so we don't bake a typo or shell metacharacter into the helper.
userexcludelist="$(printf '%s\n' "$userexcluderaw" | tr ',' '\n' \
    | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' \
    | grep -v '^$' || true)"
if [[ -n "$userexcludelist" ]]; then
    while IFS= read -r _u; do
        if ! [[ "$_u" =~ ^[a-zA-Z_][a-zA-Z0-9_.-]*$ ]]; then
            echo "ERROR: UTM_AUTOSTART_USER_EXCLUDE contains invalid username '$_u'"
            echo "       (allowed: letters, digits, dot, dash, underscore; must not start with a digit)"
            exit 1
        fi
    done <<< "$userexcludelist"
fi

# Argument parsing (hoisted above mode resolution so --help works even
# when no VMs have been configured).
mode="apply"   # apply | check | uninstall
for arg in "$@"; do
    case "$arg" in
        --check|--status) mode="check" ;;
        --uninstall|--remove) mode="uninstall" ;;
        -h|--help)
            printf '%s\n' "Usage: $0 [--check|--uninstall]
  (no args)    Apply: install helper + LaunchAgent, reconcile state
  --check      Read-only audit: report whether prerequisites and the
               helper + LaunchAgent are present and current.
               Exit 0 if compliant, 2 on drift.
  --uninstall  Remove the installed helper script and LaunchAgent and
               unload any running instance from the active console
               user's GUI domain. Idempotent: succeeds even when
               nothing is installed. Runtime + install logs are left
               in place as a diagnostic audit trail.

Environment variables (apply + check):
  UTM_AUTOSTART_MODE            Force selection mode: 'list' | 'regex' |
                                'auto' (default: ${defaultmode}). 'auto'
                                infers from which selection env var is
                                set; 'list' / 'regex' fail fast if the
                                wrong input combination is provided.
  UTM_AUTOSTART_VMS             List-mode input: VM names, newline- or
                                comma-separated, started in order.
  UTM_AUTOSTART_MATCH           Regex-mode input: POSIX ERE matched
                                against 'utmctl list' at every login.
                                Picks up new/renamed VMs without
                                re-install.
  UTM_AUTOSTART_EXCLUDE         Optional ERE; matching names are skipped
                                (only relevant in regex mode)
Common:
  UTM_AUTOSTART_DELAY_SECONDS   delay between VM starts (default: ${defaultdelay})
  UTM_AUTOSTART_BOOT_TIMEOUT    max wait for UTM.app (default: ${defaultboottimeout})
  UTM_AUTOSTART_WAIT_POLL_INTERVAL
                                seconds between pgrep polls while waiting
                                for UTM.app (default: ${defaultwaitpollinterval})
  UTM_AUTOSTART_SKIP_RUNNING    skip VMs not in 'stopped' state
                                (true/false, default: ${defaultskiprunning})
  UTM_AUTOSTART_USER_EXCLUDE    comma/newline list of macOS usernames
                                that should NOT trigger autostart at
                                login. Helper exits 0 immediately for
                                excluded users without waiting for
                                UTM.app. Default: (empty -- all users)"
            exit 0
            ;;
        *) echo "ERROR: unknown argument '$arg' (try --help)"; exit 1 ;;
    esac
done

# Normalise UTM_AUTOSTART_MODE -> 'auto' | 'list' | 'regex'. Validated
# strictly: no synonyms, so a typo errors fast at install time rather
# than silently picking the wrong selection branch.
automode_raw="${UTM_AUTOSTART_MODE:-${defaultmode}}"
case "$(printf '%s' "$automode_raw" | tr '[:upper:]' '[:lower:]')" in
    auto)  automode="auto"  ;;
    list)  automode="list"  ;;
    regex) automode="regex" ;;
    *) echo "ERROR: UTM_AUTOSTART_MODE must be one of: auto, list, regex (got '$automode_raw')"; exit 1 ;;
esac

# Mode resolution.
#
# automode='list'  -> use UTM_AUTOSTART_VMS (or defaultvms). Conflicts
#                     with UTM_AUTOSTART_MATCH being set.
# automode='regex' -> use UTM_AUTOSTART_MATCH. Conflicts with
#                     UTM_AUTOSTART_VMS being set; MATCH must be non-empty.
# automode='auto'  -> bake EVERYTHING that's configured into the helper.
#                     At login the helper tries the explicit list first
#                     (only counting names that exist in 'utmctl list'
#                     output) and falls back to the regex if the list
#                     yielded nothing. If both yield nothing, the helper
#                     logs "no VMs found" and exits 0. This is the only
#                     mode where setting both VMS and MATCH is allowed.
autostart_mode=""
match_pattern=""
exclude_pattern=""
vmsraw=""

if [[ "$automode" == "list" ]]; then
    if [[ -n "${UTM_AUTOSTART_MATCH:-}" ]]; then
        echo "ERROR: UTM_AUTOSTART_MODE=list conflicts with UTM_AUTOSTART_MATCH being set."
        echo "       Unset UTM_AUTOSTART_MATCH, or set UTM_AUTOSTART_MODE=auto to enable list-then-regex fallback."
        exit 1
    fi
    autostart_mode="list"
    vmsraw="${UTM_AUTOSTART_VMS:-$defaultvms}"
elif [[ "$automode" == "regex" ]]; then
    if [[ -n "${UTM_AUTOSTART_VMS:-}" ]]; then
        echo "ERROR: UTM_AUTOSTART_MODE=regex conflicts with UTM_AUTOSTART_VMS being set."
        echo "       Unset UTM_AUTOSTART_VMS, or set UTM_AUTOSTART_MODE=auto to enable list-then-regex fallback."
        exit 1
    fi
    if [[ -z "${UTM_AUTOSTART_MATCH:-}" ]]; then
        echo "ERROR: UTM_AUTOSTART_MODE=regex requires UTM_AUTOSTART_MATCH to be set."
        exit 1
    fi
    autostart_mode="regex"
    match_pattern="$UTM_AUTOSTART_MATCH"
    exclude_pattern="${UTM_AUTOSTART_EXCLUDE:-}"
else
    # automode == "auto" -- bake whatever's configured. Both VMS and
    # MATCH may be set simultaneously; the helper decides at runtime.
    autostart_mode="auto"
    vmsraw="${UTM_AUTOSTART_VMS:-$defaultvms}"
    match_pattern="${UTM_AUTOSTART_MATCH:-}"
    exclude_pattern="${UTM_AUTOSTART_EXCLUDE:-}"
    if [[ -z "$vmsraw" && -z "$match_pattern" && "$mode" != "uninstall" ]]; then
        # No selection of any kind. Still proceed so the helper +
        # LaunchAgent get installed; the helper will log "no VMs
        # found" at every login until selection is provided.
        printf '%s\n' >&2 "WARNING: no VMs configured.

Proceeding with install so the helper and LaunchAgent are in place,
but no VMs will be started at login until selection is provided.

To enable autostart, re-run this installer with one (or both) of:

  UTM_AUTOSTART_VMS='vm-one,vm-two'    (explicit name list)
  UTM_AUTOSTART_MATCH='^prod-'         (POSIX ERE matched against 'utmctl list')

In auto mode (default), setting both makes the helper try the list
first and fall back to the regex if no list names actually exist on
this Mac at login. Edit the 'defaultvms' variable at the top of this
script to bake in a deployment-specific fallback list. Try --help for
the full list of supported environment variables."
    fi
fi

# Normalise the explicit name list (only consulted when autostart_mode == "list").
vmlist="$(printf '%s\n' "$vmsraw" | tr ',' '\n' \
    | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' \
    | grep -v '^$' || true)"

# Validate numeric env input.
if ! [[ "$delaybetween" =~ ^[0-9]+$ ]]; then
    echo "ERROR: UTM_AUTOSTART_DELAY_SECONDS must be a non-negative integer (got '$delaybetween')"
    exit 1
fi
if ! [[ "$boottimeout" =~ ^[0-9]+$ ]]; then
    echo "ERROR: UTM_AUTOSTART_BOOT_TIMEOUT must be a non-negative integer (got '$boottimeout')"
    exit 1
fi
if ! [[ "$waitpollinterval" =~ ^[0-9]+$ ]] || (( waitpollinterval < 1 )); then
    echo "ERROR: UTM_AUTOSTART_WAIT_POLL_INTERVAL must be a positive integer (got '$waitpollinterval')"
    exit 1
fi

# start logging (console + file)
#
# The same $log path is shared with the per-user LaunchAgent helper, so
# ensure it exists and is appendable by non-root callers before tee'ing
# into it. Without 0666 here the helper (running as the logged-in user)
# would fail to append on first run after a fresh install.
#
# Gated on being root because:
#   - apply / uninstall already require root and will hit their own
#     root assertion shortly after this block, so writing to $log is
#     guaranteed to succeed here.
#   - --check (read-only) is usable as a regular user; in that case we
#     skip the prep + tee and stay on stdout-only to keep output clean.
if [[ "$(id -u)" -eq 0 ]]; then
    mkdir -p "$(dirname "$log")"
    touch "$log"
    chown "$fileowner" "$log"
    chmod 0666 "$log"
    exec > >(tee -a "$log") 2>&1
fi

# Optional LOG_LEVEL gate: trace|debug|info|warn|error. Default 'info'
# keeps existing output unchanged. 'debug' adds log_debug lines; 'trace'
# additionally enables shell tracing (set -x). See ../README.md.
LOG_LEVEL="${LOG_LEVEL:-${defaultloglevel}}"
case "$LOG_LEVEL" in
    trace) _ll_cur=0 ;; debug) _ll_cur=1 ;; info) _ll_cur=2 ;;
    warn)  _ll_cur=3 ;; error) _ll_cur=4 ;;
    *) echo "ERROR: invalid LOG_LEVEL='$LOG_LEVEL' (trace|debug|info|warn|error)"; exit 1 ;;
esac
log_debug() { [[ $_ll_cur -le 1 ]] && echo "DEBUG: $*" || true; }
log_warn()  { [[ $_ll_cur -le 3 ]] && echo "WARN: $*"  || true; }
[[ "$LOG_LEVEL" == "trace" ]] && set -x

# Begin Script Body

banner_verb="install"
[[ "$mode" == "uninstall" ]] && banner_verb="uninstall"

echo ""
echo "##############################################################"
echo "# $(date) | Starting $banner_verb of $appname"
echo "##############################################################"
echo ""

if [[ "$autostart_mode" == "list" && -z "$vmlist" && "$mode" != "uninstall" ]]; then
    log_warn "no VM names provided; helper will be a no-op at login. Re-run with UTM_AUTOSTART_VMS (newline- or comma-separated) or UTM_AUTOSTART_MATCH to enable autostart."
fi

# ----- build the helper script content -----
# Each name / pattern is single-quote-escaped (embedded ' -> '\'').
escape_sq() { printf "%s" "$1" | sed "s/'/'\\\\''/g"; }

# Render the EXPLICIT_VMS array body. Populated whenever $vmlist
# is non-empty -- that is, list mode (always) and auto mode (when
# UTM_AUTOSTART_VMS or defaultvms is set). Regex-only configurations
# leave the array empty so the helper's auto-fallback knows to skip
# straight to the regex branch.
explicit_block=""
if [[ -n "$vmlist" ]]; then
    while IFS= read -r vm; do
        [[ -z "$vm" ]] && continue
        explicit_block+="    '$(escape_sq "$vm")'"$'\n'
    done <<< "$vmlist"
fi

match_escaped="$(escape_sq "$match_pattern")"
exclude_escaped="$(escape_sq "$exclude_pattern")"

# Render the USER_EXCLUDE bash array body (one quoted username per
# line). Empty when no users are excluded -- the helper then skips the
# check entirely.
user_exclude_block=""
if [[ -n "$userexcludelist" ]]; then
    while IFS= read -r _u; do
        [[ -z "$_u" ]] && continue
        user_exclude_block+="    '$(escape_sq "$_u")'"$'\n'
    done <<< "$userexcludelist"
fi

# Multi-line double-quoted string: ${install-time vars} expand here,
# while $runtime references are kept literal via \$ escaping (same
# semantics as an unquoted heredoc, just without the cat <<EOF).
helper_content="#!/bin/bash
# =============================================================
# Copyright (c) 2026 Blackout Secure
# https://blackoutsecure.app
# License: Apache-2.0  (see repository root LICENSE)
#
# Generated by install-utm-vm-autostart.sh. Edit the installer
# and re-run; do not edit this file directly.
#
# Starts UTM virtual machines via utmctl once the UTM.app process
# is running. Selection is one of:
#   list  -- start a fixed set of VM names baked in at install time.
#   regex -- match a POSIX ERE against 'utmctl list' at every login.
#   auto  -- try the explicit list first (only counting names that
#            actually exist in 'utmctl list'); if it yields nothing,
#            fall back to the regex; if both yield nothing, log
#            'no VMs found' and exit 0.
#
# SCOPE -- runtime only.
#   This helper does NOT inspect, verify, or repair its own
#   install state. It assumes the installer (re-run
#   install-utm-vm-autostart.sh as root) has placed it at the
#   correct path with the correct permissions. At login time
#   the helper does only the runtime work needed to start the
#   configured VMs:
#     1. Honour USER_EXCLUDE (skip on excluded user accounts)
#     2. Locate utmctl
#     3. Wait for UTM.app to be running (per-user GUI app)
#     4. Resolve which VMs to start (list -> regex fallback in auto)
#     5. Log a 'Detected N VM(s) ...' summary line for debugging
#     6. For each: skip if already running, else 'utmctl start'
#   Anything beyond that (file integrity, ownership, plist
#   shape, etc.) belongs to the installer's pre-flight audit,
#   not here. Keep this script lean.
#
# Usage: bos-utm-vm-autostart [--dry-run|--help]
#   --dry-run   Resolve the start list and print what would be
#               started, without invoking 'utmctl start'.
# =============================================================

LOGFILE="${log}"
BOOT_TIMEOUT=${boottimeout}
WAIT_POLL_INTERVAL=${waitpollinterval}
DELAY=${delaybetween}
SKIP_RUNNING=\"${skiprunning}\"
AUTOSTART_MODE=\"${autostart_mode}\"
MATCH='${match_escaped}'
EXCLUDE='${exclude_escaped}'
EXPLICIT_VMS=(
${explicit_block})
USER_EXCLUDE=(
${user_exclude_block})

DRY_RUN=\"false\"
if [[ \"\${1:-}\" == \"--dry-run\" ]]; then
    DRY_RUN=\"true\"
elif [[ \"\${1:-}\" == \"-h\" || \"\${1:-}\" == \"--help\" ]]; then
    echo \"Usage: bos-utm-vm-autostart [--dry-run|--help]\"
    echo \"  Starts UTM VMs as configured at install time.\"
    echo \"  --dry-run   Print the start list without invoking 'utmctl start'.\"
    exit 0
elif [[ -n \"\${1:-}\" ]]; then
    echo \"ERROR: unknown argument '\$1' (try --help)\" >&2
    exit 1
fi

log() {
    if [[ \"\$DRY_RUN\" == \"true\" ]]; then
        printf '%s | %s\n' \"\$(date)\" \"\$*\" | tee -a \"\$LOGFILE\"
    else
        printf '%s | %s\n' \"\$(date)\" \"\$*\" >> \"\$LOGFILE\"
    fi
}

# Early opt-out: skip the autostart run entirely if the user that
# triggered the LaunchAgent at login is on the install-time exclude
# list. Exits 0 so launchd records a clean run -- this is an
# intentional no-op, not a failure.
me=\"\$(id -un 2>/dev/null || echo unknown)\"
if [[ \${#USER_EXCLUDE[@]} -gt 0 ]]; then
    for excluded in \"\${USER_EXCLUDE[@]}\"; do
        if [[ \"\$me\" == \"\$excluded\" ]]; then
            log \"Skipping autostart: current user '\$me' is in USER_EXCLUDE\"
            exit 0
        fi
    done
fi

# Resolve utmctl: prefer a symlink/copy in PATH, else the app bundle.
utmctl=\"\"
for candidate in ${utmctlpaths}; do
    if [[ -x \"\$candidate\" ]]; then
        utmctl=\"\$candidate\"
        break
    fi
done
if [[ -z \"\$utmctl\" ]]; then
    log \"ERROR: utmctl not found; is UTM.app installed?\"
    exit 1
fi

# Wait for UTM.app to be running. utmctl needs the UTM daemon, which
# is the running UTM.app process. UTM.app itself is expected to launch
# at login via its own Login Item.
waited=0
while ! pgrep -x ${utmprocess} >/dev/null 2>&1; do
    if (( waited >= BOOT_TIMEOUT )); then
        log \"ERROR: UTM.app not running after \${BOOT_TIMEOUT}s; aborting.\"
        exit 1
    fi
    sleep \"\$WAIT_POLL_INTERVAL\"
    waited=\$((waited + WAIT_POLL_INTERVAL))
done
log \"UTM.app detected after \${waited}s; using \$utmctl\"

# Parse 'utmctl list' to TSV: status<TAB>name (preserves spaces in names).
# Header row is skipped. Output is cached once per run.
list_vms() {
    \"\$utmctl\" list 2>/dev/null | awk '
        NR == 1 { next }
        NF >= 3 {
            status = \$2
            if (match(\$0, /^[^[:space:]]+[[:space:]]+[^[:space:]]+[[:space:]]+/)) {
                name = substr(\$0, RLENGTH + 1)
                sub(/[[:space:]]+\$/, \"\", name)
                if (length(name) > 0) printf \"%s\\t%s\\n\", status, name
            }
        }
    '
}
list_cache=\"\$(list_vms)\"

status_of() {
    local want=\"\$1\" status name
    while IFS=\$'\t' read -r status name; do
        if [[ \"\$name\" == \"\$want\" ]]; then printf '%s' \"\$status\"; return; fi
    done <<< \"\$list_cache\"
    printf 'unknown'
}

# --- VM resolution helpers ---
#
# populate_wanted_from_list_strict:
#   Take EXPLICIT_VMS verbatim. Used in forced 'list' mode -- preserves
#   the original semantic that unknown VM names get attempted (and
#   utmctl reports the error per-VM) rather than silently dropped.
#
# populate_wanted_from_list_detect:
#   Take EXPLICIT_VMS but only keep names that currently exist in
#   'utmctl list' output. Used in 'auto' mode so we can tell whether
#   the list resolved to anything and decide whether to fall back to
#   the regex.
#
# populate_wanted_from_regex:
#   Walk the cached list sorted by name; apply MATCH / EXCLUDE bash
#   EREs. Used in forced 'regex' mode AND as the 'auto' fallback.
populate_wanted_from_list_strict() {
    wanted=()
    for vm in \"\${EXPLICIT_VMS[@]}\"; do
        wanted+=(\"\$vm\")
    done
}

populate_wanted_from_list_detect() {
    wanted=()
    local vm s
    for vm in \"\${EXPLICIT_VMS[@]}\"; do
        s=\"\$(status_of \"\$vm\")\"
        if [[ \"\$s\" != \"unknown\" ]]; then
            wanted+=(\"\$vm\")
        else
            log \"  auto/list: '\$vm' not present in 'utmctl list' (skipped)\"
        fi
    done
}

populate_wanted_from_regex() {
    wanted=()
    local status name
    while IFS=\$'\t' read -r status name; do
        [[ -z \"\$name\" ]] && continue
        if [[ -n \"\$MATCH\"   && ! \"\$name\" =~ \$MATCH   ]]; then continue; fi
        if [[ -n \"\$EXCLUDE\" &&   \"\$name\" =~ \$EXCLUDE ]]; then continue; fi
        wanted+=(\"\$name\")
    done < <(printf '%s\n' \"\$list_cache\" | sort -t \$'\t' -k2,2)
}

# Format \${wanted[@]} as 'a, b, c' for log output.
join_names() {
    local sep=\"\" out=\"\" n
    for n in \"\${wanted[@]}\"; do
        out+=\"\${sep}\${n}\"
        sep=\", \"
    done
    printf '%s' \"\$out\"
}

# Build the wanted list. In 'auto' the helper tries the explicit list
# first (only counting names that actually exist in 'utmctl list'),
# then falls back to the regex; if both yield nothing, 'wanted' stays
# empty and the helper logs 'no VMs found' and exits 0.
log \"==== UTM VM autostart run begin (mode=\$AUTOSTART_MODE, dry_run=\$DRY_RUN) ====\"

wanted=()
detected_method=\"\$AUTOSTART_MODE\"

case \"\$AUTOSTART_MODE\" in
    list)
        populate_wanted_from_list_strict
        ;;
    regex)
        populate_wanted_from_regex
        ;;
    auto)
        if (( \${#EXPLICIT_VMS[@]} > 0 )); then
            log \"Auto mode: trying explicit list first (\${#EXPLICIT_VMS[@]} candidate name(s) baked in)\"
            populate_wanted_from_list_detect
            if (( \${#wanted[@]} > 0 )); then
                detected_method=\"list\"
                log \"Auto mode: explicit list matched \${#wanted[@]} existing VM(s).\"
            else
                log \"Auto mode: none of the explicit names exist in 'utmctl list'; falling back to regex.\"
            fi
        else
            log \"Auto mode: no explicit names baked in; skipping straight to regex.\"
        fi
        if (( \${#wanted[@]} == 0 )) && [[ -n \"\$MATCH\" ]]; then
            log \"Auto mode: trying regex MATCH='\$MATCH' EXCLUDE='\$EXCLUDE'\"
            populate_wanted_from_regex
            if (( \${#wanted[@]} > 0 )); then
                detected_method=\"regex\"
                log \"Auto mode: regex matched \${#wanted[@]} VM(s).\"
            fi
        fi
        if (( \${#wanted[@]} == 0 )); then
            detected_method=\"none\"
        fi
        ;;
esac

# Always log the detected wanted list for debugging visibility -- even
# in forced list/regex modes you get a single summary line showing
# what was resolved before any 'utmctl start' fires.
if (( \${#wanted[@]} > 0 )); then
    log \"Detected \${#wanted[@]} VM(s) for autostart (via \$detected_method): \$(join_names)\"
else
    case \"\$AUTOSTART_MODE\" in
        auto)
            log \"No VMs specified/found via either method (explicit list candidates=\${#EXPLICIT_VMS[@]}, regex MATCH='\$MATCH' EXCLUDE='\$EXCLUDE'). Nothing to autostart.\"
            ;;
        list)
            log \"List mode: EXPLICIT_VMS is empty. Nothing to autostart.\"
            ;;
        regex)
            log \"Regex mode: no VMs matched MATCH='\$MATCH' EXCLUDE='\$EXCLUDE'. Nothing to autostart.\"
            ;;
    esac
    log \"==== UTM VM autostart run end ====\"
    exit 0
fi

first=1
for vm in \"\${wanted[@]}\"; do
    if [[ \$first -eq 0 ]]; then sleep \"\$DELAY\"; fi
    first=0

    status=\"\$(status_of \"\$vm\")\"
    if [[ \"\$SKIP_RUNNING\" == \"true\" && \"\$status\" != \"stopped\" && \"\$status\" != \"unknown\" ]]; then
        log \"Skipping (status=\$status): \$vm\"
        continue
    fi

    if [[ \"\$DRY_RUN\" == \"true\" ]]; then
        log \"DRY-RUN would start (status=\$status): \$vm\"
        continue
    fi

    log \"Starting VM (status=\$status): \$vm\"
    if \"\$utmctl\" start \"\$vm\" >> \"\$LOGFILE\" 2>&1; then
        log \"Start command issued: \$vm\"
    else
        log \"ERROR: failed to start VM: \$vm\"
    fi
done

log \"==== UTM VM autostart run end ====\"
exit 0"

# ----- build the LaunchAgent plist content -----
plist_content="<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<!DOCTYPE plist PUBLIC \"-//Apple//DTD PLIST 1.0//EN\" \"http://www.apple.com/DTDs/PropertyList-1.0.dtd\">
<plist version=\"1.0\">
<dict>
    <key>Label</key>
    <string>${label}</string>

    <key>ProgramArguments</key>
    <array>
        <string>${helperpath}</string>
    </array>

    <key>RunAtLoad</key>
    <true/>

    <key>StandardOutPath</key>
    <string>${log}</string>

    <key>StandardErrorPath</key>
    <string>${log}</string>
</dict>
</plist>"

# =============================================================
# Pre-flight audit (shared by --check and apply)
#
# Inspects every install target location and compares against the
# rendered desired state (helper_content / plist_content built
# above). Reports PASS / FAIL per check and sets two globals the
# caller reads to decide what to do next:
#
#   audit_pass -- count of PASS lines
#   audit_fail -- count of FAIL lines
#
# Pure reporter: never writes, never mutates, never exits. Both
# --check (read-only mode) and apply (uses it as a pre-flight
# install-location check so you can see what already exists and
# what will be replaced *before* anything is written) call this.
# =============================================================
run_preflight_audit() {
    local context="${1:-audit}"
    audit_pass=0
    audit_fail=0

    case "$context" in
        check)
            echo "=== --check (read-only) ==="
            ;;
        pre-flight)
            echo "=== Pre-flight: inspecting install locations ==="
            ;;
        *)
            echo "=== Audit ==="
            ;;
    esac
    # Mode line: 'auto' is reported with a hint about its fallback
    # semantics so logs are self-documenting.
    case "$autostart_mode" in
        auto)
            if [[ -n "$vmlist" && -n "$match_pattern" ]]; then
                echo "Mode: auto  (try explicit list first; fall back to regex if no list names exist)"
            elif [[ -n "$vmlist" ]]; then
                echo "Mode: auto  (explicit list only; no regex fallback configured)"
            elif [[ -n "$match_pattern" ]]; then
                echo "Mode: auto  (regex only; no explicit list configured)"
            else
                echo "Mode: auto  (NO selection configured -- helper will log 'no VMs found' at every login)"
            fi
            ;;
        *)
            echo "Mode: $autostart_mode"
            ;;
    esac
    # List candidates (printed for list mode and for auto when populated)
    if [[ "$autostart_mode" == "list" || ( "$autostart_mode" == "auto" && -n "$vmlist" ) ]]; then
        echo "Explicit VMs:"
        while IFS= read -r vm; do
            [[ -z "$vm" ]] && continue
            echo "    - $vm"
        done <<< "$vmlist"
    fi
    # Regex (printed for regex mode and for auto when populated)
    if [[ "$autostart_mode" == "regex" || ( "$autostart_mode" == "auto" && -n "$match_pattern" ) ]]; then
        echo "Include regex: $match_pattern"
        [[ -n "$exclude_pattern" ]] && echo "Exclude regex: $exclude_pattern"
    fi
    echo "Skip running: $skiprunning  |  Delay: ${delaybetween}s  |  Boot timeout: ${boottimeout}s  |  Poll: ${waitpollinterval}s"
    echo ""

    _report() {
        local verdict="$1" name="$2" detail="$3"
        printf "  [%-4s] %-32s %s\n" "$verdict" "$name" "$detail"
        case "$verdict" in PASS) ((audit_pass++));; FAIL) ((audit_fail++));; esac
    }

    # OS must be macOS
    local os
    os="$(uname -s 2>/dev/null || echo unknown)"
    if [[ "$os" == "$supportedos" ]]; then
        _report PASS "operating system" "$os (macOS)"
    else
        _report FAIL "operating system" "got '$os', want $supportedos"
    fi

    # UTM.app installed
    if [[ -d "$utmappdir" ]]; then
        _report PASS "UTM.app installed" "$utmappdir"
    else
        _report FAIL "UTM.app installed" "$utmappdir not present"
    fi

    # utmctl reachable somewhere
    local utmctl_found="" candidate
    for candidate in $utmctlpaths; do
        if [[ -x "$candidate" ]]; then utmctl_found="$candidate"; break; fi
    done
    if [[ -n "$utmctl_found" ]]; then
        _report PASS "utmctl reachable" "$utmctl_found"
    else
        _report FAIL "utmctl reachable" "not found in any of: $utmctlpaths"
    fi

    # Helper script present + content matches the rendered desired state.
    local owner
    if [[ -f "$helperpath" ]]; then
        if diff -q <(printf '%s\n' "$helper_content") "$helperpath" >/dev/null 2>&1; then
            _report PASS "helper script content" "$helperpath up to date"
        else
            _report FAIL "helper script content" "$helperpath differs from desired (will replace)"
        fi
        if [[ -x "$helperpath" ]]; then
            _report PASS "helper script executable" "yes"
        else
            _report FAIL "helper script executable" "missing +x (will fix)"
        fi
        owner="$(stat -f '%Su:%Sg' "$helperpath" 2>/dev/null || echo unknown)"
        if [[ "$owner" == "$fileowner" ]]; then
            _report PASS "helper script ownership" "$owner"
        else
            _report FAIL "helper script ownership" "got $owner, want $fileowner (will fix)"
        fi
    else
        _report FAIL "helper script present" "$helperpath missing (will install)"
    fi

    # Plist present + content matches.
    if [[ -f "$plistpath" ]]; then
        if diff -q <(printf '%s\n' "$plist_content") "$plistpath" >/dev/null 2>&1; then
            _report PASS "launch agent plist" "$plistpath up to date"
        else
            _report FAIL "launch agent plist" "$plistpath differs from desired (will replace)"
        fi
        owner="$(stat -f '%Su:%Sg' "$plistpath" 2>/dev/null || echo unknown)"
        if [[ "$owner" == "$fileowner" ]]; then
            _report PASS "plist ownership" "$owner"
        else
            _report FAIL "plist ownership" "got $owner, want $fileowner (will fix)"
        fi
    else
        _report FAIL "launch agent plist" "$plistpath missing (will install)"
    fi

    echo ""
    echo "Summary: $audit_pass PASS / $audit_fail FAIL"
}

# =============================================================
# --check (read-only status) mode
# Exit 0 = all PASS, 2 = drift detected.
# =============================================================
if [[ "$mode" == "check" ]]; then
    run_preflight_audit check
    if [[ "$audit_fail" -gt 0 ]]; then
        echo "DRIFT DETECTED. Re-run without --check to reconcile."
        exit 2
    fi
    echo "All applicable settings already configured."
    exit 0
fi

# =============================================================
# uninstall mode
# Removes the helper script + LaunchAgent and unloads any running
# instance for the active console user. Idempotent: succeeds even
# when nothing is installed. Runtime + install logs are left in
# place as a diagnostic audit trail.
# =============================================================
if [[ "$mode" == "uninstall" ]]; then
    if [[ "$(id -u)" -ne 0 ]]; then
        echo "$(date) | ERROR: must be run as root (try: sudo)"
        exit 1
    fi

    removed=0
    skipped=0

    # Bootout the LaunchAgent from the active console user's GUI
    # domain, if any. Best-effort: ignore "not loaded" errors so this
    # is safe to re-run after the agent has already been removed.
    consoleuser="$(stat -f%Su /dev/console 2>/dev/null || true)"
    if [[ -n "$consoleuser" && "$consoleuser" != "root" && "$consoleuser" != "loginwindow" ]]; then
        uid="$(id -u "$consoleuser" 2>/dev/null || true)"
        if [[ -n "$uid" ]]; then
            echo "$(date) | Unloading LaunchAgent from console user '$consoleuser' (uid $uid)"
            launchctl bootout "gui/${uid}/${label}" 2>/dev/null || true
        fi
    else
        echo "$(date) | No console user session detected; nothing to bootout."
    fi

    if [[ -f "$plistpath" ]]; then
        echo "$(date) | Removing LaunchAgent plist: $plistpath"
        if rm -f "$plistpath"; then
            removed=$((removed + 1))
        else
            echo "$(date) | ERROR: failed to remove $plistpath"
            exit 1
        fi
    else
        echo "$(date) | LaunchAgent plist not present: $plistpath"
        skipped=$((skipped + 1))
    fi

    if [[ -f "$helperpath" ]]; then
        echo "$(date) | Removing helper script: $helperpath"
        if rm -f "$helperpath"; then
            removed=$((removed + 1))
        else
            echo "$(date) | ERROR: failed to remove $helperpath"
            exit 1
        fi
    else
        echo "$(date) | Helper script not present: $helperpath"
        skipped=$((skipped + 1))
    fi

    echo "$(date) | $appname uninstall complete (removed=$removed, skipped=$skipped)"
    echo "$(date) | Log retained:  $log"
    echo "$(date) |   (delete manually if you no longer need it)"
    exit 0
fi

# =============================================================
# apply mode
# =============================================================

# Must be root to write to /usr/local/bin and /Library/LaunchAgents.
if [[ "$(id -u)" -ne 0 ]]; then
    echo "$(date) | ERROR: must be run as root (try: sudo)"
    exit 1
fi

# ----- pre-flight: inspect install target locations -----
# Every apply run begins with a full audit so we can see what
# already exists at the helper / plist install paths and decide
# exactly which files need to be (re)written. Each file write
# below is independently diff-guarded as a defense-in-depth
# backstop, so this audit is informational -- callers see *up
# front* what will and won't change before any disk write occurs.
run_preflight_audit pre-flight
echo ""

# UTM.app being missing won't block install (the LaunchAgent
# will simply fail at the next user login until UTM.app is
# present), but make the warning loud so it isn't lost in
# the audit table.
if [[ ! -d "$utmappdir" ]]; then
    log_warn "$utmappdir is not installed; the LaunchAgent will fail at login until UTM.app is present."
fi

if [[ "$audit_fail" -eq 0 ]]; then
    echo "$(date) | Pre-flight: all install locations in desired state; re-asserting permissions only (idempotent)."
else
    echo "$(date) | Pre-flight: $audit_fail check(s) need reconciliation; replacing/installing only what differs..."
fi
echo ""

# ----- write helper script -----
helper_changed=0
if [[ -f "$helperpath" ]] && diff -q <(printf '%s\n' "$helper_content") "$helperpath" >/dev/null 2>&1; then
    echo "$(date) | Helper script already up to date: $helperpath"
else
    echo "$(date) | Writing helper script: $helperpath"
    mkdir -p "$(dirname "$helperpath")"
    tmp="$(mktemp)"
    printf '%s\n' "$helper_content" > "$tmp"
    if ! mv "$tmp" "$helperpath"; then
        echo "$(date) | Failed to write helper script"
        rm -f "$tmp"
        exit 1
    fi
    helper_changed=1
fi
chown "$fileowner" "$helperpath"
chmod "$helpermode" "$helperpath"

# ----- write LaunchAgent plist -----
plist_changed=0
if [[ -f "$plistpath" ]] && diff -q <(printf '%s\n' "$plist_content") "$plistpath" >/dev/null 2>&1; then
    echo "$(date) | LaunchAgent plist already up to date: $plistpath"
else
    echo "$(date) | Writing LaunchAgent plist: $plistpath"
    mkdir -p "$(dirname "$plistpath")"
    tmp="$(mktemp)"
    printf '%s\n' "$plist_content" > "$tmp"
    if ! mv "$tmp" "$plistpath"; then
        echo "$(date) | Failed to write LaunchAgent plist"
        rm -f "$tmp"
        exit 1
    fi
    plist_changed=1
fi
chown "$fileowner" "$plistpath"
chmod "$plistmode" "$plistpath"

# Validate plist syntax (best effort).
if command -v plutil >/dev/null 2>&1; then
    if ! plutil -lint "$plistpath" >/dev/null; then
        echo "$(date) | ERROR: plutil rejected $plistpath"
        exit 1
    fi
fi

# ----- (re)load the LaunchAgent for the active console user, if any -----
# A system LaunchAgent under /Library/LaunchAgents/ is loaded
# automatically at the next login. If a user is already logged in at
# the console, try a best-effort bootstrap so changes take effect
# without forcing a logout.
#
# Reminder: this loads the LaunchAgent for the active console user,
# but it does NOT add UTM.app to that user's "Open at Login" list.
# That is a per-user prerequisite that must be done once for each
# account whose VMs should auto-start -- see the "per-user 'Open at
# Login' walkthrough" in the header of this script.
consoleuser="$(stat -f%Su /dev/console 2>/dev/null || true)"
if [[ -n "$consoleuser" && "$consoleuser" != "root" && "$consoleuser" != "loginwindow" ]]; then
    uid="$(id -u "$consoleuser" 2>/dev/null || true)"
    if [[ -n "$uid" ]]; then
        if (( helper_changed || plist_changed )); then
            echo "$(date) | Reloading LaunchAgent for console user '$consoleuser' (uid $uid)"
            # Best-effort bootout; ignore "not loaded" errors.
            launchctl bootout "gui/${uid}/${label}" 2>/dev/null || true
            if ! launchctl bootstrap "gui/${uid}" "$plistpath"; then
                log_warn "launchctl bootstrap failed; LaunchAgent will load at next login."
            fi
        else
            echo "$(date) | No content changes; leaving existing LaunchAgent state alone."
        fi
    fi
else
    echo "$(date) | No console user session detected; LaunchAgent will load at next login."
fi

echo "$(date) | $appname installed"
echo "$(date) | Helper script: $helperpath"
echo "$(date) | LaunchAgent:   $plistpath"
echo "$(date) | Log:           $log"
echo "$(date) | NEXT STEP (per user): add ${utmappdir} to 'Open at Login'"
echo "$(date) |   System Settings -> General -> Login Items & Extensions -> +"
echo "$(date) |   (do this once per macOS account whose VMs should auto-start)"
exit 0
