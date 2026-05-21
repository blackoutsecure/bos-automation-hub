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
#   find it, log an error to /tmp/bos-utm-vm-autostart.log, and
#   exit non-zero -- so the Login Item is effectively the opt-in
#   switch: no Login Item, no VM autostart for that user.
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
#
# Idempotency:
#   Re-running with the same configuration is a no-op: the helper
#   script and plist are byte-compared, ownership and permissions
#   are reasserted, and the LaunchAgent is only re-bootstrapped
#   when content actually changed.
#
# Selection model (set ONE of these at install time):
#   UTM_AUTOSTART_VMS              Explicit list: newline- or comma-
#                                  separated VM names, started in the
#                                  order given. Highest precedence.
#   UTM_AUTOSTART_MATCH            Dynamic match: POSIX ERE regex tested
#                                  against the name column of
#                                  `utmctl list` at every login. Picked
#                                  up automatically when VMs are added,
#                                  removed, or renamed in the UTM GUI.
#                                    Examples:
#                                      ^prod-                       (all prod-* VMs)
#                                      \[autostart\]$              (suffix tag)
#   UTM_AUTOSTART_EXCLUDE          Optional ERE; dynamic-mode names
#                                  matching this are skipped.
#   (neither set)                  Falls back to the `defaultvms` variable
#                                  in the variables block below. If that
#                                  is also empty (the default), the
#                                  installer aborts with an error.
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
log="/var/log/installutmvmautostart.log"
runtimelog="/tmp/bos-utm-vm-autostart.log"
runtimeerr="/tmp/bos-utm-vm-autostart.err.log"

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

# ----- Selection defaults (which VMs / which users) -----
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
mode="apply"   # apply | check
for arg in "$@"; do
    case "$arg" in
        --check|--status) mode="check" ;;
        -h|--help)
            printf '%s\n' "Usage: $0 [--check]
  (no args)   Apply: install helper + LaunchAgent, reconcile state
  --check     Read-only audit: report whether prerequisites and the
              helper + LaunchAgent are present and current.
              Exit 0 if compliant, 2 on drift.

Environment variables (apply + check) - set ONE of:
  UTM_AUTOSTART_VMS             explicit list (newline- or comma-separated)
  UTM_AUTOSTART_MATCH           regex (POSIX ERE) matched against
                                'utmctl list' at every login. Dynamic:
                                picks up new/renamed VMs without re-install.
  UTM_AUTOSTART_EXCLUDE         optional ERE; matching names are skipped
                                (only relevant with UTM_AUTOSTART_MATCH)
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

# Mode resolution (precedence: explicit env list > regex match > baked-in default).
autostart_mode="explicit"
match_pattern=""
exclude_pattern=""
vmsraw=""
if [[ -n "${UTM_AUTOSTART_VMS:-}" ]]; then
    autostart_mode="explicit"
    vmsraw="$UTM_AUTOSTART_VMS"
elif [[ -n "${UTM_AUTOSTART_MATCH:-}" ]]; then
    autostart_mode="dynamic"
    match_pattern="$UTM_AUTOSTART_MATCH"
    exclude_pattern="${UTM_AUTOSTART_EXCLUDE:-}"
elif [[ -n "$defaultvms" ]]; then
    autostart_mode="explicit"
    vmsraw="$defaultvms"
else
    printf '%s\n' >&2 "ERROR: no VMs configured.

Set one of the following at install time, or edit the 'defaultvms'
variable at the top of this script to bake in a deployment-specific
fallback list:

  UTM_AUTOSTART_VMS='vm-one,vm-two'    (explicit list; comma- or newline-separated)
  UTM_AUTOSTART_MATCH='^prod-'         (POSIX ERE matched against 'utmctl list' at login)

Try --help for the full list of supported environment variables."
    exit 1
fi

# Normalise the explicit list (only consulted when autostart_mode == "explicit").
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

exec > >(tee -a "$log") 2>&1

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

echo ""
echo "##############################################################"
echo "# $(date) | Starting install of $appname"
echo "##############################################################"
echo ""

if [[ "$autostart_mode" == "explicit" && -z "$vmlist" ]]; then
    echo "ERROR: no VM names provided. Set UTM_AUTOSTART_VMS (newline- or comma-separated) or UTM_AUTOSTART_MATCH."
    exit 1
fi

# ----- build the helper script content -----
# Each name / pattern is single-quote-escaped (embedded ' -> '\'').
escape_sq() { printf "%s" "$1" | sed "s/'/'\\\\''/g"; }

# In explicit mode, render the EXPLICIT_VMS array body. In dynamic mode
# the array stays empty and the helper drives selection off the regex.
explicit_block=""
if [[ "$autostart_mode" == "explicit" ]]; then
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
# is running. Selection is either an explicit list (baked in at
# install time) or a regex matched against 'utmctl list' output
# at every login.
#
# Usage: bos-utm-vm-autostart [--dry-run|--help]
#   --dry-run   Resolve the start list and print what would be
#               started, without invoking 'utmctl start'.
# =============================================================

LOGFILE=\"${runtimelog}\"
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

# Build the wanted list in start order.
wanted=()
if [[ \"\$AUTOSTART_MODE\" == \"explicit\" ]]; then
    for vm in \"\${EXPLICIT_VMS[@]}\"; do
        wanted+=(\"\$vm\")
    done
else
    # Walk the cached list sorted by name; include/exclude with bash ERE.
    while IFS=\$'\t' read -r status name; do
        [[ -z \"\$name\" ]] && continue
        if [[ -n \"\$MATCH\"   && ! \"\$name\" =~ \$MATCH   ]]; then continue; fi
        if [[ -n \"\$EXCLUDE\" &&   \"\$name\" =~ \$EXCLUDE ]]; then continue; fi
        wanted+=(\"\$name\")
    done < <(printf '%s\n' \"\$list_cache\" | sort -t \$'\t' -k2,2)
fi

log \"==== UTM VM autostart run begin (mode=\$AUTOSTART_MODE, dry_run=\$DRY_RUN) ====\"

if [[ \${#wanted[@]} -eq 0 ]]; then
    if [[ \"\$AUTOSTART_MODE\" == \"dynamic\" ]]; then
        log \"No VMs matched MATCH='\$MATCH' EXCLUDE='\$EXCLUDE'; nothing to do.\"
    else
        log \"Explicit VM list is empty; nothing to do.\"
    fi
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
    <string>${runtimelog}</string>

    <key>StandardErrorPath</key>
    <string>${runtimeerr}</string>
</dict>
</plist>"

# =============================================================
# --check (read-only status) mode
# Exit 0 = all PASS, 2 = drift detected.
# =============================================================
if [[ "$mode" == "check" ]]; then
    echo "=== --check (read-only) ==="
    echo "Mode: $autostart_mode"
    if [[ "$autostart_mode" == "explicit" ]]; then
        echo "Explicit VMs:"
        while IFS= read -r vm; do
            [[ -z "$vm" ]] && continue
            echo "    - $vm"
        done <<< "$vmlist"
    else
        echo "Include regex: $match_pattern"
        [[ -n "$exclude_pattern" ]] && echo "Exclude regex: $exclude_pattern"
    fi
    echo "Skip running: $skiprunning  |  Delay: ${delaybetween}s  |  Boot timeout: ${boottimeout}s  |  Poll: ${waitpollinterval}s"
    echo ""
    pass=0; fail=0
    report() {
        local verdict="$1" name="$2" detail="$3"
        printf "  [%-4s] %-32s %s\n" "$verdict" "$name" "$detail"
        case "$verdict" in PASS) ((pass++));; FAIL) ((fail++));; esac
    }

    # OS must be macOS
    os="$(uname -s 2>/dev/null || echo unknown)"
    if [[ "$os" == "$supportedos" ]]; then
        report PASS "operating system" "$os (macOS)"
    else
        report FAIL "operating system" "got '$os', want $supportedos"
    fi

    # UTM.app installed
    if [[ -d "$utmappdir" ]]; then
        report PASS "UTM.app installed" "$utmappdir"
    else
        report FAIL "UTM.app installed" "$utmappdir not present"
    fi

    # utmctl reachable somewhere
    utmctl_found=""
    for candidate in $utmctlpaths; do
        if [[ -x "$candidate" ]]; then utmctl_found="$candidate"; break; fi
    done
    if [[ -n "$utmctl_found" ]]; then
        report PASS "utmctl reachable" "$utmctl_found"
    else
        report FAIL "utmctl reachable" "not found in any of: $utmctlpaths"
    fi

    # Helper script present + content matches the rendered desired state.
    if [[ -f "$helperpath" ]]; then
        if diff -q <(printf '%s\n' "$helper_content") "$helperpath" >/dev/null 2>&1; then
            report PASS "helper script content" "$helperpath up to date"
        else
            report FAIL "helper script content" "$helperpath differs from desired"
        fi
        if [[ -x "$helperpath" ]]; then
            report PASS "helper script executable" "yes"
        else
            report FAIL "helper script executable" "missing +x"
        fi
        owner="$(stat -f '%Su:%Sg' "$helperpath" 2>/dev/null || echo unknown)"
        if [[ "$owner" == "$fileowner" ]]; then
            report PASS "helper script ownership" "$owner"
        else
            report FAIL "helper script ownership" "got $owner, want $fileowner"
        fi
    else
        report FAIL "helper script present" "$helperpath missing"
    fi

    # Plist present + content matches.
    if [[ -f "$plistpath" ]]; then
        if diff -q <(printf '%s\n' "$plist_content") "$plistpath" >/dev/null 2>&1; then
            report PASS "launch agent plist" "$plistpath up to date"
        else
            report FAIL "launch agent plist" "$plistpath differs from desired"
        fi
        owner="$(stat -f '%Su:%Sg' "$plistpath" 2>/dev/null || echo unknown)"
        if [[ "$owner" == "$fileowner" ]]; then
            report PASS "plist ownership" "$owner"
        else
            report FAIL "plist ownership" "got $owner, want $fileowner"
        fi
    else
        report FAIL "launch agent plist" "$plistpath missing"
    fi

    echo ""
    echo "Summary: $pass PASS / $fail FAIL"
    if [[ "$fail" -gt 0 ]]; then
        echo "DRIFT DETECTED. Re-run without --check to reconcile."
        exit 2
    fi
    echo "All applicable settings already configured."
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

# UTM.app isn't strictly required to *install* the agent, but warn loudly.
if [[ ! -d "$utmappdir" ]]; then
    log_warn "$utmappdir is not installed; the LaunchAgent will fail until UTM.app is present."
fi

echo "$(date) | Autostart mode: $autostart_mode"
if [[ "$autostart_mode" == "explicit" ]]; then
    echo "$(date) | VMs to autostart (in order):"
    while IFS= read -r vm; do
        [[ -z "$vm" ]] && continue
        echo "    - $vm"
    done <<< "$vmlist"
else
    echo "$(date) | Include regex: $match_pattern"
    if [[ -n "$exclude_pattern" ]]; then
        echo "$(date) | Exclude regex: $exclude_pattern"
    fi
    echo "$(date) | (VM list resolved at each login via 'utmctl list')"
fi
echo "$(date) | Skip already-running: $skiprunning"
echo "$(date) | Delay between starts: ${delaybetween}s"
echo "$(date) | UTM.app boot timeout: ${boottimeout}s"

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
echo "$(date) | Runtime log:   $runtimelog"
echo "$(date) | NEXT STEP (per user): add ${utmappdir} to 'Open at Login'"
echo "$(date) |   System Settings -> General -> Login Items & Extensions -> +"
echo "$(date) |   (do this once per macOS account whose VMs should auto-start)"
exit 0
