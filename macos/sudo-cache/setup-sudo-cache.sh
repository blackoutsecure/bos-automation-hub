#!/bin/bash

# =============================================================
# Copyright (c) 2026 Blackout Secure
# https://blackoutsecure.app
# License: Apache-2.0  (see repository root LICENSE)
#
# Script:  setup-sudo-cache.sh
# Purpose: Installs a sudoers drop-in that extends the lifetime
#          of the `sudo` credential cache (`timestamp_timeout`).
#
#          Useful for any workflow that runs multiple `sudo`
#          commands back-to-back -- in particular the BOS
#          automation hub installers (utm-vm-autostart, homebrew,
#          plex-media-server, ...) plus interactive sessions
#          with coding agents that cannot supply passwords.
#
#          With a longer cache, you only need to run `sudo -v`
#          once at the start of a session and all subsequent
#          `sudo` invocations inside the window inherit the
#          cached credential -- no extra prompts.
#
# Modes:
#   apply (default) - install / reconcile the sudoers drop-in
#   --check         - read-only audit. Exit 0 = compliant, 2 = drift
#   --uninstall     - remove the drop-in. Idempotent.
#
# Environment variables (evaluated at install time):
#   BOS_SUDO_CACHE_MINUTES         Value of `timestamp_timeout` in
#                                  minutes. macOS sudo accepts
#                                  fractional values too, but for
#                                  predictability this installer
#                                  restricts to non-negative integers
#                                  plus the special sentinel `-1`
#                                  (meaning "cache never expires until
#                                  the user logs out -- USE WITH CARE").
#                                  Default: 60
#
# Idempotency:
#   Re-running with the same value is a no-op: the drop-in is
#   byte-compared, owner / mode are unconditionally re-asserted
#   (sudo silently *ignores* drop-ins with wrong perms, so we
#   never trust the existing ones), and the file is only
#   rewritten when its content actually differs.
#
# Safety:
#   * Every write goes through `visudo -cf` first. A malformed
#     sudoers entry can lock you out of sudo entirely; this
#     installer refuses to install anything visudo rejects.
#   * The drop-in lives in /etc/sudoers.d/ and is read by sudo
#     only if owned root:wheel with mode 0440. We set both
#     unconditionally as defense in depth.
#
# Deployment:
#   MDM (Intune, Jamf, Kandji, Mosyle, Workspace ONE):
#     Activity is streamed to both the console and the log.
#     Exit codes:
#       0 = success (installed / already in desired state)
#       1 = failure (review log for details)
#       2 = drift detected (only emitted by --check)
#
#   Manual:
#     sudo bash ./macos/sudo-cache/setup-sudo-cache.sh
#     sudo BOS_SUDO_CACHE_MINUTES=120 \
#          bash ./macos/sudo-cache/setup-sudo-cache.sh
# =============================================================

# =============================================================
# CONFIGURATION
# =============================================================
appname="BOS Sudo Cache"
sudoersfile="/etc/sudoers.d/bos-sudo-cache-timeout"
log="/var/log/bos-sudo-cache.log"

# sudoers.d files MUST be exactly these. Sudo will silently
# refuse to read drop-ins with any other ownership or mode.
fileowner="root:wheel"
filemode="0440"

# Default timeout value (in minutes) if BOS_SUDO_CACHE_MINUTES
# is not set. Sized to comfortably cover a single agent-driven
# install session without re-prompting, while still expiring
# within a typical lunch break.
defaultminutes=60
defaultloglevel="info"

# Resolved env vars.
minutes_raw="${BOS_SUDO_CACHE_MINUTES:-${defaultminutes}}"

# Argument parsing.
mode="apply"   # apply | check | uninstall
for arg in "$@"; do
    case "$arg" in
        --check|--status) mode="check" ;;
        --uninstall|--remove) mode="uninstall" ;;
        -h|--help)
            printf '%s\n' "Usage: $0 [--check|--uninstall]
  (no args)    Apply: install/reconcile the sudoers drop-in.
  --check      Read-only audit: report whether the drop-in is
               present, content-correct, and has the ownership /
               mode that sudo requires. Exit 0 = compliant, 2 = drift.
  --uninstall  Remove the drop-in. Idempotent: succeeds even when
               nothing is installed. Log file is left in place.

Environment variables:
  BOS_SUDO_CACHE_MINUTES   timestamp_timeout in minutes
                           (default: ${defaultminutes}; '0' = always
                           prompt; '-1' = never expire until logout
                           -- USE WITH CARE)
  LOG_LEVEL                trace|debug|info|warn|error
                           (default: ${defaultloglevel})

Tips:
  Refresh / prime your cache without re-installing anything:
    sudo -v

  Verify the cache is currently active (no password prompt expected):
    sudo -n true && echo cached || echo expired"
            exit 0
            ;;
        *) echo "ERROR: unknown argument '$arg' (try --help)"; exit 1 ;;
    esac
done

# Validate BOS_SUDO_CACHE_MINUTES. Accept any non-negative integer
# plus the special value '-1' (sudo's "never expire" sentinel).
if [[ "$minutes_raw" != "-1" ]] && ! [[ "$minutes_raw" =~ ^[0-9]+$ ]]; then
    echo "ERROR: BOS_SUDO_CACHE_MINUTES must be a non-negative integer or '-1' (got '$minutes_raw')"
    exit 1
fi
minutes="$minutes_raw"

# Start logging (console + file). Same pattern as other macOS
# installers in this repo. Gated on root for the same reasons --
# --check runs as a regular user and stays stdout-only.
if [[ "$(id -u)" -eq 0 ]]; then
    mkdir -p "$(dirname "$log")"
    touch "$log"
    chown "$fileowner" "$log"
    # Other BOS installers use 0666 so user-context callers can
    # append too; this script has no per-user runtime component,
    # so 0644 (root-write, world-read) is sufficient.
    chmod 0644 "$log"
    exec > >(tee -a "$log") 2>&1
fi

# LOG_LEVEL gate. Matches macos/README.md conventions.
LOG_LEVEL="${LOG_LEVEL:-${defaultloglevel}}"
case "$LOG_LEVEL" in
    trace) _ll_cur=0 ;; debug) _ll_cur=1 ;; info) _ll_cur=2 ;;
    warn)  _ll_cur=3 ;; error) _ll_cur=4 ;;
    *) echo "ERROR: invalid LOG_LEVEL='$LOG_LEVEL' (trace|debug|info|warn|error)"; exit 1 ;;
esac
log_debug() { [[ $_ll_cur -le 1 ]] && echo "DEBUG: $*" || true; }
log_info()  { [[ $_ll_cur -le 2 ]] && printf '%s | %s\n' "$(date)" "$*" || true; }
log_warn()  { [[ $_ll_cur -le 3 ]] && echo "WARN: $*"  || true; }
log_error() { printf '%s | ERROR: %s\n' "$(date)" "$*" >&2; }
[[ "$LOG_LEVEL" == "trace" ]] && set -x

banner_verb="install"
[[ "$mode" == "uninstall" ]] && banner_verb="uninstall"

echo ""
echo "##############################################################"
echo "# $(date) | Starting $banner_verb of $appname"
echo "##############################################################"
echo ""

# ----- build the desired sudoers content -----
# Single Defaults line plus a header that explains where it
# came from. Header lines are comments (#) and don't affect
# the visudo check.
desired_content="# Installed by macos/sudo-cache/setup-sudo-cache.sh (BOS automation hub)
# Do not edit by hand; re-run the installer with BOS_SUDO_CACHE_MINUTES=<n>
# to change the value, or with --uninstall to remove.
#
# Extends the lifetime of the sudo credential cache (timestamp_timeout)
# so multiple back-to-back sudo invocations in the same session only
# need to authenticate once. See sudoers(5) for details.
Defaults timestamp_timeout=${minutes}"

# =============================================================
# Pre-flight audit (shared by --check and apply)
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
        post-flight)
            echo "=== Post-install verification: re-inspecting install locations ==="
            ;;
        *)
            echo "=== Audit ==="
            ;;
    esac
    echo "Desired timestamp_timeout: ${minutes} minute(s)"
    if [[ "$minutes" == "-1" ]]; then
        log_warn "BOS_SUDO_CACHE_MINUTES=-1 means the cache NEVER EXPIRES until logout. Make sure your screen lock policy is solid."
    elif [[ "$minutes" == "0" ]]; then
        log_warn "BOS_SUDO_CACHE_MINUTES=0 means sudo ALWAYS prompts (cache disabled). Probably not what you want for an automation helper."
    fi
    echo ""

    _report() {
        local verdict="$1" name="$2" detail="$3"
        printf "  [%-4s] %-32s %s\n" "$verdict" "$name" "$detail"
        case "$verdict" in PASS) ((audit_pass++));; FAIL) ((audit_fail++));; esac
    }

    # OS must be macOS
    local os
    os="$(uname -s 2>/dev/null || echo unknown)"
    if [[ "$os" == "Darwin" ]]; then
        _report PASS "operating system" "$os (macOS)"
    else
        _report FAIL "operating system" "got '$os', want Darwin (macOS)"
    fi

    # /etc/sudoers.d directory present (should always be, but verify)
    if [[ -d "$(dirname "$sudoersfile")" ]]; then
        _report PASS "sudoers.d directory" "$(dirname "$sudoersfile")"
    else
        _report FAIL "sudoers.d directory" "$(dirname "$sudoersfile") missing"
    fi

    # Drop-in present + content + owner + mode
    if [[ -f "$sudoersfile" ]]; then
        if diff -q <(printf '%s\n' "$desired_content") "$sudoersfile" >/dev/null 2>&1; then
            _report PASS "drop-in content" "$sudoersfile up to date"
        else
            _report FAIL "drop-in content" "$sudoersfile differs from desired (will replace)"
        fi
        local owner mode
        owner="$(stat -f '%Su:%Sg' "$sudoersfile" 2>/dev/null || echo unknown)"
        mode="$(stat -f '%Lp' "$sudoersfile" 2>/dev/null || echo unknown)"
        if [[ "$owner" == "$fileowner" ]]; then
            _report PASS "drop-in ownership" "$owner"
        else
            _report FAIL "drop-in ownership" "got $owner, want $fileowner (will fix; sudo ignores wrong-owner drop-ins)"
        fi
        # filemode is 0440 -> stat reports '440'
        if [[ "$mode" == "440" ]]; then
            _report PASS "drop-in mode" "0$mode"
        else
            _report FAIL "drop-in mode" "got 0$mode, want $filemode (will fix; sudo ignores wrong-mode drop-ins)"
        fi
    else
        _report FAIL "drop-in present" "$sudoersfile missing (will install)"
    fi

    # Whole-stack sudoers syntax check: validates /etc/sudoers PLUS
    # every drop-in (including ours) as the live sudo binary will see
    # them. Catches the case where our drop-in is syntactically valid
    # in isolation (which 'visudo -cf <file>' already verified pre-
    # install) but conflicts with something else on the system.
    # 'visudo -c' requires root to read /etc/sudoers, so skip silently
    # when running as a regular user (e.g. unprivileged --check).
    if [[ "$(id -u)" -eq 0 ]] && command -v visudo >/dev/null 2>&1; then
        local visudo_out
        if visudo_out="$(visudo -c 2>&1)"; then
            _report PASS "sudoers stack syntax" "visudo -c: ${visudo_out:-OK}"
        else
            _report FAIL "sudoers stack syntax" "visudo -c rejected the live stack: ${visudo_out}"
        fi
    else
        log_debug "Skipping whole-stack 'visudo -c' check (needs root)"
    fi

    echo ""
    echo "Summary: $audit_pass PASS / $audit_fail FAIL"
}

# =============================================================
# --check (read-only status) mode
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
# =============================================================
if [[ "$mode" == "uninstall" ]]; then
    if [[ "$(id -u)" -ne 0 ]]; then
        log_error "must be run as root (try: sudo)"
        exit 1
    fi
    if [[ -f "$sudoersfile" ]]; then
        log_info "Removing sudoers drop-in: $sudoersfile"
        if rm -f "$sudoersfile"; then
            log_info "$appname uninstall complete"
        else
            log_error "failed to remove $sudoersfile"
            exit 1
        fi
    else
        log_info "Sudoers drop-in not present: $sudoersfile (nothing to do)"
    fi
    log_info "Log retained:  $log"
    exit 0
fi

# =============================================================
# apply mode
# =============================================================
if [[ "$(id -u)" -ne 0 ]]; then
    log_error "must be run as root (try: sudo)"
    exit 1
fi

# Make sure visudo is available -- without it we can't safely
# validate the drop-in. (visudo ships with macOS at /usr/sbin/visudo,
# so this should never trigger on a healthy system.)
if ! command -v visudo >/dev/null 2>&1; then
    log_error "visudo not found in PATH; cannot validate sudoers drop-in"
    exit 1
fi

run_preflight_audit pre-flight
echo ""

if [[ "$audit_fail" -eq 0 ]]; then
    log_info "Pre-flight: all install locations in desired state; re-asserting permissions only (idempotent)."
else
    log_info "Pre-flight: $audit_fail check(s) need reconciliation; replacing/installing only what differs..."
fi
echo ""

# Write to a temp file, validate with visudo, then atomically
# move into place. We deliberately stage the temp file under
# /etc/sudoers.d/ so the visudo check sees the same path mode
# semantics the live file will have. mktemp + tight cleanup
# ensures we never leave a half-written drop-in behind.
write_dir="$(dirname "$sudoersfile")"
tmp="$(mktemp "${write_dir}/.bos-sudo-cache.XXXXXX")"
trap 'rm -f "$tmp"' EXIT

printf '%s\n' "$desired_content" > "$tmp"
chown "$fileowner" "$tmp"
chmod "$filemode" "$tmp"

if ! visudo -cf "$tmp" >/dev/null; then
    log_error "visudo rejected the generated drop-in; refusing to install"
    visudo -cf "$tmp" || true
    exit 1
fi
log_debug "visudo accepted the generated drop-in"

if [[ -f "$sudoersfile" ]] && diff -q "$tmp" "$sudoersfile" >/dev/null 2>&1; then
    log_info "Sudoers drop-in already up to date: $sudoersfile"
    # Re-assert ownership / mode unconditionally (defense in depth
    # against external chmod/chown that would make sudo ignore it).
    chown "$fileowner" "$sudoersfile"
    chmod "$filemode" "$sudoersfile"
    rm -f "$tmp"
    trap - EXIT
else
    log_info "Writing sudoers drop-in: $sudoersfile"
    if ! mv "$tmp" "$sudoersfile"; then
        log_error "failed to install $sudoersfile"
        rm -f "$tmp"
        exit 1
    fi
    trap - EXIT
    # mv preserves the temp file's owner/mode (already set above),
    # but re-assert anyway to be explicit about the invariant.
    chown "$fileowner" "$sudoersfile"
    chmod "$filemode" "$sudoersfile"
fi

# Post-install verification.
echo ""
run_preflight_audit post-flight
if [[ "$audit_fail" -gt 0 ]]; then
    log_warn "Post-install verification found $audit_fail issue(s); review the audit above."
else
    echo ""
    log_info "Post-install verification: all checks PASS."
fi
echo ""

log_info "$appname installed"
log_info "Drop-in:        $sudoersfile"
log_info "Log:            $log"
log_info "Effective:      sudo timestamp_timeout = ${minutes} minute(s)"
log_info "NEXT STEP:      run 'sudo -v' once to prime the cache;"
log_info "                subsequent sudo calls in this session will not re-prompt"
log_info "                until the cache expires."
exit 0
