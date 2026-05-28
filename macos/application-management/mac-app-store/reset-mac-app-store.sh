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
#     cached software-update catalog for the whole machine.
#
# Privilege model:
#   This script MUST be run as the logged-in console user
#   (not via `sudo bash ...`) because the cache, container,
#   and `defaults` operations are per-user. `sudo` is primed
#   once up-front (apply mode only) so the `softwareupdate`
#   step and the /var/log write do not re-prompt. `--check`
#   and `--dry-run` are pure read-only and never invoke sudo.
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
    for cmd in osascript killall defaults softwareupdate sudo; do
        if command -v "$cmd" >/dev/null 2>&1; then
            report PASS "tool $cmd" "$(command -v "$cmd")"
        else
            report FAIL "tool $cmd" "missing"
        fi
    done

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

echo "=== Resetting $appname (mode=$mode) ==="

echo "$(date) | Closing App Store"
run osascript -e 'tell application "App Store" to quit'

echo "$(date) | Killing App Store background agents"
run killall -9 appstoreagent
run killall -9 storeaccountd
run killall -9 storedownloadd
run killall -9 storeinstalld

echo "$(date) | Clearing App Store caches"
run rm -rf "$darwin_cache_root/com.apple.appstore"
run rm -rf "$darwin_cache_root/com.apple.appstoreagent"

echo "$(date) | Clearing App Store preferences"
run defaults delete com.apple.appstore
run defaults delete com.apple.appstoreagent
run defaults delete com.apple.storeagent

echo "$(date) | Clearing MAS receipts cache"
run rm -rf "$HOME/Library/Containers/com.apple.appstore"
run rm -rf "$HOME/Library/Containers/com.apple.appstoreagent"
run rm -rf "$HOME/Library/Caches/com.apple.appstore"
run rm -rf "$HOME/Library/Caches/com.apple.appstoreagent"

echo "$(date) | Resetting softwareupdate catalog"
run sudo softwareupdate --clear-catalog

echo "$(date) | Refreshing update metadata"
run sudo softwareupdate --list

echo ""
echo "$(date) | $appname reset complete (mode=$mode)"
if [[ "$mode" == "apply" ]]; then
    echo "$(date) | Please restart your Mac to finish recreating the App Store containers"
fi
exit 0
