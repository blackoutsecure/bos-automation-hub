#!/bin/bash

# =============================================================
# Copyright (c) 2026 Blackout Secure
# https://blackoutsecure.app
# License: Apache-2.0  (see repository root LICENSE)
#
# Script:  reset-mac-app-store.sh
# Purpose: Resets the Mac App Store on macOS by closing the app,
#          killing the Apple "store" background agents, clearing
#          per-user caches / preferences / containers, and
#          refreshing the softwareupdate catalog. Useful when
#          the App Store hangs on "Loading", will not sign in,
#          gets stuck on a pending download, or refuses to apply
#          updates.
#
# Modes:
#   apply (default) - perform the reset
#   --check         - read-only audit. Exit 0 = all PASS, 2 = drift
#   --dry-run       - print every destructive command instead of
#                     running it (no processes killed, no files
#                     removed, no preferences deleted). Exit 0.
#
# Idempotency:
#   This is a destructive reset, not an installer -- there is no
#   "already done" state to skip. Every invocation re-runs all
#   steps. Re-running is safe (each `rm -rf` / `defaults delete`
#   / `killall` is best-effort and ignores missing targets).
#
# WARNING -- side effects on the invoking user account:
#   * The Mac App Store will be force-quit.
#   * `~/Library/Containers/com.apple.appstore{,agent}` is
#     removed. macOS will recreate these on next launch, but
#     the user will be SIGNED OUT of the App Store and any
#     in-flight downloads will be lost.
#   * `defaults delete com.apple.appstore{,agent}` and
#     `com.apple.storeagent` wipe per-user App Store prefs.
#   * `softwareupdate --clear-catalog` (root) discards the
#     cached software-update catalog. NOTE: Apple deprecated
#     catalog management in macOS Big Sur (11.0); on 11+ the
#     tool prints "Catalog management is no longer supported."
#     and is a no-op, so this script only invokes the legacy
#     flag on macOS 10.x and relies on `--list` to refresh
#     metadata on newer releases.
#
# Privilege model:
#   This script MUST be run as the logged-in console user
#   (not via `sudo bash ...`) because the cache, container,
#   and `defaults` operations are per-user. `sudo` is primed
#   once up-front (apply mode only) so the `softwareupdate`
#   step and the /var/log write do not re-prompt. `--check`
#   and `--dry-run` are pure read-only and never invoke sudo.
#
# macOS permissions:
#   Removing `~/Library/Containers/com.apple.appstore{,agent}`
#   is protected by macOS TCC (App Sandbox container privacy).
#   The terminal app invoking this script (Terminal.app,
#   iTerm2, Ghostty, Warp, the MDM script runner, ...) needs
#   Full Disk Access:
#     System Settings > Privacy & Security > Full Disk Access
#   Toggle the terminal on, then QUIT AND REOPEN it before
#   re-running. Without FDA the script reports a clear WARN
#   and continues -- the App Store still resets but signed-in
#   state may persist.
#
# Deployment:
#   MDM (Intune, Jamf, Kandji, Mosyle, Workspace ONE):
#     Deploy as a *user-context* script (not root-context) so
#     `$HOME`, `$TMPDIR`, and `defaults` resolve to the target
#     user. Activity is streamed to both the console and $log
#     (apply mode only). Exit codes:
#       0 = success (reset completed, or --dry-run completed,
#           or --check found no drift)
#       1 = failure (review log for details)
#       2 = drift detected (only emitted by --check)
#
#   Manual:
#     bash ./macos/application-management/mac-app-store/reset-mac-app-store.sh
# =============================================================

# Define variables

appname="Mac App Store"
log="/var/log/resetmacappstore.log"

# Argument parsing
mode="apply"   # apply | check | dry-run
for arg in "$@"; do
    case "$arg" in
        --check|--status) mode="check" ;;
        --dry-run|-n)     mode="dry-run" ;;
        -h|--help)
            cat <<EOF
Usage: $0 [--check | --dry-run]
  (no args)   Apply: reset the $appname for the current user
              and refresh the system softwareupdate catalog.
  --check     Read-only audit: report whether the environment is
              ready to run the reset, plus an informational
              snapshot of current caches/containers/processes.
              Exit 0 if compliant, 2 on drift.
  --dry-run   Print every destructive command without executing it.
EOF
            exit 0
            ;;
        *) echo "ERROR: unknown argument '$arg' (try --help)"; exit 1 ;;
    esac
done

# Apply-mode-only: refuse to run as root. The per-user paths
# (~/Library/..., $TMPDIR, `defaults` domains) belong to the
# invoking user. If this script were launched via `sudo`, they
# would resolve to /var/root and silently miss the real target.
if [[ "$mode" == "apply" && "$(id -u)" -eq 0 ]]; then
    echo "ERROR: do not run $0 as root."
    echo "       Run it as the logged-in user; sudo will be prompted"
    echo "       once for the privileged softwareupdate step."
    exit 1
fi

# Apply-mode-only: prime sudo so the upcoming `sudo touch` (for
# /var/log) and the later `sudo softwareupdate` calls do not
# each re-prompt. Prepare the shared log file under /var/log
# with mode 0666 so tee can append from the unprivileged user
# shell. (--check and --dry-run skip this and log to console
# only -- they must remain side-effect-free.)
if [[ "$mode" == "apply" ]]; then
    if ! sudo -v; then
        echo "ERROR: sudo authentication failed -- cannot continue."
        exit 1
    fi
    sudo touch "$log" && sudo chmod 0666 "$log"
    # start logging (console + file)
    exec > >(tee -a "$log") 2>&1
fi

# Optional LOG_LEVEL gate: trace|debug|info|warn|error. Default 'info'
# keeps existing output unchanged. 'debug' adds log_debug lines; 'trace'
# additionally enables shell tracing (set -x). See ../README.md.
LOG_LEVEL="${LOG_LEVEL:-info}"
case "$LOG_LEVEL" in
    trace) _ll_cur=0 ;; debug) _ll_cur=1 ;; info) _ll_cur=2 ;;
    warn)  _ll_cur=3 ;; error) _ll_cur=4 ;;
    *) echo "ERROR: invalid LOG_LEVEL='$LOG_LEVEL' (trace|debug|info|warn|error)"; exit 1 ;;
esac
log_debug() { [[ $_ll_cur -le 1 ]] && echo "DEBUG: $*" || true; }
log_warn()  { [[ $_ll_cur -le 3 ]] && echo "WARN: $*"  || true; }
[[ "$LOG_LEVEL" == "trace" ]] && set -x

# Derive the per-user Darwin cache root from $TMPDIR. On macOS,
# $TMPDIR resolves to /var/folders/<aa>/<bbbb...>/T/ and the
# sibling 'C' directory holds per-user app caches.
darwin_cache_root="${TMPDIR%/}/../C"

# Begin Script Body

echo ""
echo "##############################################################"
echo "# $(date) | Starting reset of $appname"
echo "##############################################################"
echo ""

# =============================================================
# --check (read-only status) mode
# Exit 0 = all PASS, 2 = drift detected.
#
# "Compliance" here means: the environment is ready to run the
# reset in apply mode. The presence of App Store caches and
# containers is NORMAL (not drift) -- those entries are reported
# as informational PASS lines so the operator can see what
# `apply` would actually touch.
# =============================================================
if [[ "$mode" == "check" ]]; then
    echo "=== --check (read-only) ==="
    pass=0; fail=0
    report() {
        local verdict="$1" name="$2" detail="$3"
        printf "  [%-4s] %-32s %s\n" "$verdict" "$name" "$detail"
        case "$verdict" in PASS) ((pass++));; FAIL) ((fail++));; esac
    }

    # OS must be macOS
    os="$(uname -s 2>/dev/null || echo unknown)"
    if [[ "$os" == "Darwin" ]]; then
        report PASS "operating system" "Darwin (macOS)"
    else
        report FAIL "operating system" "got '$os', want Darwin"
    fi

    # Must NOT be running as root (apply mode would refuse)
    if [[ "$(id -u)" -ne 0 ]]; then
        report PASS "invoking user" "$USER (uid $(id -u))"
    else
        report FAIL "invoking user" "running as root -- apply mode would refuse"
    fi

    # $HOME must exist and be writable so the rm -rf targets resolve
    if [[ -n "$HOME" && -d "$HOME" && -w "$HOME" ]]; then
        report PASS "\$HOME writable" "$HOME"
    else
        report FAIL "\$HOME writable" "HOME='${HOME:-unset}' not writable"
    fi

    # $TMPDIR must be set so the Darwin cache root resolves correctly
    if [[ -n "$TMPDIR" && -d "$darwin_cache_root" ]]; then
        report PASS "darwin cache root" "$darwin_cache_root"
    else
        report FAIL "darwin cache root" "TMPDIR='${TMPDIR:-unset}', resolved='$darwin_cache_root'"
    fi

    # Required tools for the reset
    for cmd in osascript killall defaults softwareupdate sudo sw_vers; do
        if command -v "$cmd" >/dev/null 2>&1; then
            report PASS "tool $cmd" "$(command -v "$cmd")"
        else
            report FAIL "tool $cmd" "missing"
        fi
    done

    # macOS major version: informational. `softwareupdate --clear-catalog`
    # was deprecated in macOS Big Sur (11.0); apply mode will skip it
    # on 11+ and run it on 10.x. Not drift either way -- just surface
    # which code path apply will take.
    macos_major_check="$(sw_vers -productVersion 2>/dev/null | cut -d. -f1)"
    if [[ -n "$macos_major_check" && "$macos_major_check" =~ ^[0-9]+$ ]]; then
        if (( macos_major_check < 11 )); then
            report PASS "macOS major" "$macos_major_check (apply will run --clear-catalog)"
        else
            report PASS "macOS major" "$macos_major_check (apply will skip --clear-catalog; deprecated)"
        fi
    else
        report PASS "macOS major" "unknown (sw_vers returned no value)"
    fi

    # Full Disk Access preflight. ~/Library/Containers/com.apple.appstore
    # is protected by macOS TCC (App Sandbox container privacy). Without
    # Full Disk Access the rm -rf in apply mode fails with "Operation
    # not permitted", the container survives, and the reset is incomplete
    # (the user remains signed in). Probe by attempting to list the
    # container -- if it exists and we cannot read it, FDA is missing.
    # If the container is absent we cannot preflight from this path,
    # but apply has nothing to remove there anyway.
    #
    # Counted as FAIL (drift, exit 2) when FDA is required but absent,
    # because the reset will not be complete without it.
    fda_target="$HOME/Library/Containers/com.apple.appstore"
    if [[ ! -e "$fda_target" ]]; then
        report PASS "full disk access" "cannot preflight (container absent; nothing to remove)"
    elif ls "$fda_target" >/dev/null 2>&1; then
        report PASS "full disk access" "granted (can read $fda_target)"
    else
        report FAIL "full disk access" "DENIED for $fda_target -- grant terminal Full Disk Access (System Settings > Privacy & Security > Full Disk Access), then QUIT AND REOPEN the terminal"
    fi

    # Informational snapshot: what would apply mode actually touch?
    # These never count as drift -- App Store leaving caches behind
    # is normal. Reported as PASS so the operator sees the inventory.
    if pgrep -x "App Store" >/dev/null 2>&1; then
        report PASS "App Store process" "running (apply would quit it)"
    else
        report PASS "App Store process" "not running"
    fi
    for agent in appstoreagent storeaccountd storedownloadd storeinstalld; do
        if pgrep -x "$agent" >/dev/null 2>&1; then
            report PASS "agent $agent" "running (apply would kill -9)"
        else
            report PASS "agent $agent" "not running"
        fi
    done
    for cache in \
        "$darwin_cache_root/com.apple.appstore" \
        "$darwin_cache_root/com.apple.appstoreagent" \
        "$HOME/Library/Containers/com.apple.appstore" \
        "$HOME/Library/Containers/com.apple.appstoreagent" \
        "$HOME/Library/Caches/com.apple.appstore" \
        "$HOME/Library/Caches/com.apple.appstoreagent"; do
        if [[ -e "$cache" ]]; then
            report PASS "path $(basename "$cache")" "present at $cache (apply would rm -rf)"
        else
            report PASS "path $(basename "$cache")" "absent at $cache"
        fi
    done

    echo ""
    echo "Summary: $pass PASS / $fail FAIL"
    if [[ "$fail" -gt 0 ]]; then
        echo "DRIFT DETECTED. Re-run without --check to reconcile."
        exit 2
    fi
    echo "Environment is ready. Re-run without --check to perform the reset."
    exit 0
fi

# =============================================================
# apply / dry-run shared body
# =============================================================

# Dispatch helper: in apply mode runs the command, in dry-run mode
# only prints it. Quoting is preserved by `printf %q`.
run() {
    if [[ "$mode" == "dry-run" ]]; then
        printf 'DRY-RUN:'
        printf ' %q' "$@"
        printf '\n'
    else
        "$@"
    fi
}

# killall_quiet: `killall -9 <proc>` exits non-zero and prints
# "No matching processes belonging to you were found" when the
# target agent is not running. That is the expected state most
# of the time, so suppress the noise and treat absence as success.
killall_quiet() {
    local proc="$1"
    if [[ "$mode" == "dry-run" ]]; then
        printf 'DRY-RUN: killall -9 %q\n' "$proc"
        return 0
    fi
    killall -9 "$proc" 2>/dev/null || true
    log_debug "killall -9 $proc (best-effort)"
}

# defaults_delete: `defaults delete <domain>` prints a noisy
# multi-line error when the per-user defaults domain is absent
# ("Domain (com.apple.foo) not found. Defaults have not been
# changed."). Treat absence as success and only surface real
# failures. Never fatal -- the reset continues.
defaults_delete() {
    local domain="$1"
    if [[ "$mode" == "dry-run" ]]; then
        printf 'DRY-RUN: defaults delete %q\n' "$domain"
        return 0
    fi
    local out rc
    out="$(defaults delete "$domain" 2>&1)"
    rc=$?
    if (( rc == 0 )); then
        log_debug "defaults delete $domain ok"
        return 0
    fi
    if grep -qiE 'domain .* not found|does not exist' <<<"$out"; then
        log_debug "defaults: $domain absent (nothing to delete)"
        return 0
    fi
    log_warn "defaults delete $domain failed: $out"
    return 0
}

# rm_protected: rm -rf a path that may be protected by macOS
# TCC (App Sandbox container privacy on ~/Library/Containers/*).
# Distinguishes three cases:
#   * target already absent -> silent success
#   * "Operation not permitted" -> clear Full Disk Access guidance
#   * any other failure       -> WARN and continue
# Never fatal -- the reset proceeds even if a container survives,
# because the rest of the steps still meaningfully reset state.
rm_protected() {
    local target="$1"
    if [[ "$mode" == "dry-run" ]]; then
        printf 'DRY-RUN: rm -rf %q\n' "$target"
        return 0
    fi
    if [[ ! -e "$target" ]]; then
        log_debug "rm: $target absent"
        return 0
    fi
    local err rc
    err="$(rm -rf "$target" 2>&1)"
    rc=$?
    if (( rc == 0 )) && [[ ! -e "$target" ]]; then
        return 0
    fi
    if grep -q 'Operation not permitted' <<<"$err"; then
        cat >&2 <<EOF
WARN: cannot remove $target -- macOS denied access (TCC).
      Grant your terminal app Full Disk Access and re-run:
        System Settings > Privacy & Security > Full Disk Access
      Add your terminal (Terminal, iTerm, Ghostty, Warp, ...) to
      the list, toggle it ON, then QUIT AND REOPEN the terminal
      before re-running this script.
EOF
        return 0
    fi
    log_warn "rm -rf $target failed: $err"
    return 0
}

echo "=== Resetting $appname (mode=$mode) ==="

echo "$(date) | Closing App Store"
run osascript -e 'tell application "App Store" to quit'

echo "$(date) | Killing App Store background agents"
killall_quiet appstoreagent
killall_quiet storeaccountd
killall_quiet storedownloadd
killall_quiet storeinstalld

echo "$(date) | Clearing App Store caches"
rm_protected "$darwin_cache_root/com.apple.appstore"
rm_protected "$darwin_cache_root/com.apple.appstoreagent"

echo "$(date) | Clearing App Store preferences"
defaults_delete com.apple.appstore
defaults_delete com.apple.appstoreagent
defaults_delete com.apple.storeagent

echo "$(date) | Clearing MAS receipts cache"
rm_protected "$HOME/Library/Containers/com.apple.appstore"
rm_protected "$HOME/Library/Containers/com.apple.appstoreagent"
rm_protected "$HOME/Library/Caches/com.apple.appstore"
rm_protected "$HOME/Library/Caches/com.apple.appstoreagent"

echo "$(date) | Resetting softwareupdate catalog"
# `softwareupdate --clear-catalog` was deprecated in macOS Big Sur
# (11.0). On 11+ it prints "Catalog management is no longer
# supported." and is a no-op, so skip the call and rely on the
# `--list` refresh below to re-pull metadata. Only invoke the
# legacy flag on macOS 10.x.
macos_major="$(sw_vers -productVersion 2>/dev/null | cut -d. -f1)"
if [[ -n "$macos_major" && "$macos_major" =~ ^[0-9]+$ && "$macos_major" -lt 11 ]]; then
    run sudo softwareupdate --clear-catalog
else
    echo "$(date) | Skipping --clear-catalog (deprecated on macOS ${macos_major:-?}; not supported)"
fi

echo "$(date) | Refreshing update metadata"
run sudo softwareupdate --list

echo ""
echo "$(date) | $appname reset complete (mode=$mode)"
if [[ "$mode" == "apply" ]]; then
    echo "$(date) | Please restart your Mac to finish recreating the App Store containers"
fi
exit 0
