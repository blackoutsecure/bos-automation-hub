#!/bin/sh

# =============================================================
# Copyright (c) 2026 Blackout Secure
# https://blackoutsecure.app
# License:  Apache-2.0  (see repository root LICENSE)
#
# Script:   configure-wireguard-ipv4-only.sh
# Purpose:  Forces a GL.iNet / OpenWrt WireGuard server
#           interface into IPv4-only mode and keeps it that
#           way against GUI regeneration, reboots, and ifup
#           events. Single-file install/uninstall/status/wizard
#           kicker; safe to pipe via `wget -qO- URL | sh`.
#
# Supported config layouts (auto-detected):
#   netifd  - stock OpenWrt: section in /etc/config/network with
#             option proto wireguard. Section name == kernel
#             device name (e.g. "wg0").
#   glinet  - GL.iNet 4.x firmware (Mango / Slate / Beryl /
#             Flint / AXT1800 etc.): server config lives in
#             /etc/config/wireguard_server (section type
#             "servers", typically named main_server) with
#             peers as "peers"-typed sections; the kernel
#             device is created by GL.iNet's own wireguard_server
#             daemon (default name: "wgserver").
#
# What the script does:
#   1. Auto-detects the WireGuard server config layout (netifd
#      vs glinet) and the kernel device. Respects --iface NAME.
#   2. Persists the chosen iface + layout to /etc/wg-noipv6/
#      so later runs target the same setup.
#   3. Clears ULA prefix; disables IPv6 on lan/wan/guest;
#      disables DHCPv6/RA/NDP on lan and odhcpd.
#   4. Layout-specific WG hardening:
#       netifd  - sets network.<iface>.ipv6=0 and strips IPv6
#                 entries from network.<iface>.addresses.
#       glinet  - clears wireguard_server.<srv>.address_v6 and
#                 strips :-bearing entries from each peer's
#                 client_ip list. Restarts wireguard_server.
#   5. Installs sysctl drop-in disabling IPv6 globally and on
#      the WG iface; auto-detects fw4 vs fw3 and installs the
#      matching IPv6-on-WG drop firewall rule.
#   6. Installs an iface hotplug script (and, for glinet, a
#      net hotplug script) that re-applies IPv4-only state.
#   7. Optionally installs a per-minute cron watchdog that
#      strips any non-link-local IPv6 addresses that reappear.
#
# Modes:
#   install            apply hardening (default in headless contexts)
#   uninstall          remove enforcement; restore IPv6 defaults
#   status             read-only PASS/FAIL audit
#   help               print usage
#   (no subcommand)    interactive wizard whenever /dev/tty is
#                      reachable; falls through to install in
#                      fully headless contexts (cron, CI).
#
# Detection / Idempotency:
#   Every step inspects current state before changing anything
#   and reports whether it actually changed something. A UCI
#   backup of /etc/config/{network,firewall,dhcp,wireguard_server}
#   is taken on each install run. Re-running install/status/
#   uninstall is safe and produces consistent output.
#
# Security notes:
#   - POSIX sh only (BusyBox ash). No bashisms; no extra
#     packages required on the router.
#   - Runs as root only; refuses to start if id -u != 0.
#   - --iface and any auto-detected name are validated against
#     [A-Za-z][A-Za-z0-9_-]* before being interpolated into
#     root-owned hotplug / firewall / watchdog scripts.
#   - Detection is conservative: refuses to guess between
#     multiple WG server candidates -- pass --iface NAME.
#   - Self-contained: every on-router artifact is emitted from
#     inline heredocs at install time. No runtime fetches from
#     this repository, so `wget -qO- URL | sh` works offline
#     after the initial download.
#
# Deployment:
#   Managed (Ansible, gl-config, custom OpenWrt provisioning):
#     Run as root with an explicit subcommand for fully
#     non-interactive use. All activity is logged to $LOG_FILE,
#     mirrored to syslog via `logger -t wg-noipv6`. Exit codes:
#       0 = success / compliant
#       1 = failure (review log for details)
#       2 = drift detected (status only)
#       3 = no WireGuard interface configured (status only)
#       4 = WireGuard configured but package not installed
#           (status only)
#
#   Manual:
#     sh ./linux/openwrt/network-security/wireguard-ipv4-only/configure-wireguard-ipv4-only.sh
#
#   Pipe (default = wizard when /dev/tty is reachable):
#     wget -qO- "$URL" | sh
#     wget -qO- "$URL" | sh -s install [--no-watchdog]
#     wget -qO- "$URL" | sh -s status
#     wget -qO- "$URL" | sh -s uninstall [--restore-backup DIR]
#
# Verification:
#   sh configure-wireguard-ipv4-only.sh status
#   ip -6 addr show dev "$(cat /etc/wg-noipv6/iface)"  # only fe80::/10 should remain
#   logread -e wg-noipv6 | tail
#   tail /var/log/wg-noipv6.log
#
# Variables:
#   TAG / APPNAME       - log + display identifiers
#   WG_IFACE            - current target kernel device (auto-detected)
#   WG_IFACE_SOURCE     - unset|cli|persisted|detected|fallback
#   WG_LAYOUT           - netifd|glinet|unknown (auto-detected)
#   WG_UCI_CONFIG       - "network" (netifd) or "wireguard_server" (glinet)
#   WG_UCI_SECTION      - server section name in WG_UCI_CONFIG
#   LOG_FILE            - per-run log on the router
#   HOTPLUG_PATH        - ifup hotplug script path
#   NET_HOTPLUG_PATH    - net (netdev) hotplug script path (glinet)
#   WATCHDOG_PATH       - cron watchdog binary path
#   SYSCTL_PATH         - sysctl drop-in path
#   NFT_PATH            - fw4 nftables drop-in path
#   FW3_INCLUDE_PATH    - fw3 firewall include path
#   BACKUP_ROOT         - /etc/wg-noipv6 (state + UCI backups)
#   IFACE_FILE          - persisted WG iface name
#   LAYOUT_FILE         - persisted WG layout
#   SECTION_FILE        - persisted WG uci section ($CONFIG.$SECTION)
#   CRONTAB_PATH        - root crontab
#   FW_BACKEND          - fw4 | fw3 (auto-detected)
# =============================================================

set -u
LC_ALL=C; export LC_ALL

TAG="wg-noipv6"
APPNAME="GL.iNet WireGuard IPv4-Only Hardening"

# WG_IFACE empty = auto-detect; --iface or persisted state overrides.
WG_IFACE=""
WG_IFACE_DEFAULT="wgserver"     # GL.iNet convention; only used in diagnostic hints.
WG_IFACE_SOURCE="unset"         # one of: unset|cli|persisted|detected|fallback.
WG_LAYOUT="unknown"             # one of: netifd|glinet|unknown.
WG_UCI_CONFIG=""                # "network" | "wireguard_server".
WG_UCI_SECTION=""               # server section name within WG_UCI_CONFIG.

LOG_FILE="/var/log/wg-noipv6.log"
HOTPLUG_PATH="/etc/hotplug.d/iface/99-wg-noipv6"
NET_HOTPLUG_PATH="/etc/hotplug.d/net/99-wg-noipv6"
WATCHDOG_PATH="/usr/sbin/wg-noipv6-watchdog"
SYSCTL_PATH="/etc/sysctl.d/99-wg-noipv6.conf"
NFT_PATH="/etc/nftables.d/99-wg-noipv6.nft"
FW3_INCLUDE_PATH="/etc/firewall.wg-noipv6"
BACKUP_ROOT="/etc/wg-noipv6"
IFACE_FILE="${BACKUP_ROOT}/iface"
LAYOUT_FILE="${BACKUP_ROOT}/layout"
SECTION_FILE="${BACKUP_ROOT}/section"
CRONTAB_PATH="/etc/crontabs/root"

# Status / main exit codes.
EX_OK=0
EX_DRIFT=2
EX_NO_WG=3
EX_NOT_INSTALLED=4

SUBCMD=""
INSTALL_WATCHDOG=1
FW_BACKEND=""
BACKUP_DIR=""
RESTORE_DIR=""
LOG_REDIRECTED=0

log_init() {
    mkdir -p "$(dirname "$LOG_FILE")" 2>/dev/null || true
    # In wizard mode the script prints menus/panels straight to the user's
    # terminal -- those must NOT be captured into $LOG_FILE (otherwise option
    # 10 just replays the menu). The log_* helpers below append explicit
    # kicker lines to the log file when LOG_REDIRECTED=0.
    case "$SUBCMD" in
        wizard) return 0 ;;
    esac
    _fifo="$(mktemp -u 2>/dev/null || echo "/tmp/${TAG}.$$.fifo")"
    if mkfifo "$_fifo" 2>/dev/null; then
        tee -a "$LOG_FILE" < "$_fifo" &
        exec >"$_fifo" 2>&1
        rm -f "$_fifo"
    else
        exec >>"$LOG_FILE" 2>&1
    fi
    LOG_REDIRECTED=1
}

# Append a single line to $LOG_FILE only when global stdout isn't already
# being tee'd into it (i.e. wizard mode). Silent on failure.
_log_file_append() {
    [ "$LOG_REDIRECTED" = "1" ] && return 0
    [ -n "$LOG_FILE" ] || return 0
    printf '%s\n' "$1" >>"$LOG_FILE" 2>/dev/null || true
}

log_info() {
    _line="[$TAG] $*"
    printf '%s\n' "$_line"
    _log_file_append "$_line"
    logger -t "$TAG" "$*"
}
log_warn() {
    _line="[$TAG][WARN] $*"
    printf '%s\n' "$_line"
    _log_file_append "$_line"
    logger -t "$TAG" -p user.warn "$*"
}
log_err() {
    _line="[$TAG][ERROR] $*"
    printf '%s\n' "$_line" >&2
    _log_file_append "$_line"
    logger -t "$TAG" -p user.err "$*"
}
log_step() {
    _line="[$TAG] === $* ==="
    printf '\n%s\n' "$_line"
    _log_file_append ""
    _log_file_append "$_line"
    logger -t "$TAG" "step: $*"
}
die()      { log_err "$*"; exit 1; }

require_root() { [ "$(id -u)" -eq 0 ] || die "must run as root"; }

require_cmds() {
    for c in "$@"; do
        command -v "$c" >/dev/null 2>&1 || die "required command not found: $c"
    done
}

# Reject shell-meta input -- value is interpolated into root-owned scripts.
iface_is_valid() {
    case "$1" in
        ''|*[!A-Za-z0-9_-]*|[!A-Za-z]*) return 1 ;;
    esac
    return 0
}

detect_fw_backend() {
    if [ -x /sbin/fw4 ] || command -v nft >/dev/null 2>&1; then
        FW_BACKEND="fw4"
    else
        FW_BACKEND="fw3"
    fi
}

# ----------------------------------------------------------------
# WG layout detection
#
# We support two on-disk layouts:
#
#   netifd  - section in /etc/config/network with proto=wireguard.
#             Section name == kernel device name. Peers are
#             "wireguard_<iface>" sections in the same config.
#
#   glinet  - GL.iNet 4.x: server lives in
#             /etc/config/wireguard_server (section type
#             "servers", typically "main_server"). Peers are
#             "peers"-typed sections in the same config. The
#             kernel device is created by GL.iNet's wireguard_server
#             daemon (default name "wgserver"), independent of
#             /etc/config/network.
# ----------------------------------------------------------------

# Resolve kernel device name for current $WG_IFACE / layout.
iface_dev() {
    case "$WG_LAYOUT" in
        netifd)
            _d="$(uci -q get "network.${WG_IFACE}.ifname")"
            [ -n "$_d" ] || _d="$WG_IFACE"
            printf '%s\n' "$_d"
            ;;
        *)
            # glinet / unknown: kernel device == $WG_IFACE.
            printf '%s\n' "$WG_IFACE"
            ;;
    esac
}

# List uci interfaces with proto=wireguard in /etc/config/network.
list_netifd_wg_ifaces() {
    uci -q show network 2>/dev/null | awk -F= '
        /\.proto=.?wireguard.?$/ {
            n = split($1, parts, ".")
            if (n >= 2) print parts[2]
        }
    ' | sort -u
}

# List peer section names under a given netifd WG iface.
list_netifd_wg_peers() {
    _i="$1"
    uci -q show network 2>/dev/null | awk -F= -v t="wireguard_${_i}" '
        $2==t {
            n = split($1, parts, ".")
            if (n >= 2) print parts[2]
        }
    '
}

# Heuristic (netifd): treat WG iface as a server if listen_port is set,
# OR it has at least one peer with no endpoint_host.
netifd_iface_looks_like_server() {
    _i="$1"
    [ -n "$(uci -q get "network.${_i}.listen_port")" ] && return 0
    _peers=0; _outbound=0
    for _p in $(list_netifd_wg_peers "$_i"); do
        _peers=$((_peers+1))
        [ -n "$(uci -q get "network.${_p}.endpoint_host")" ] \
            && _outbound=$((_outbound+1))
    done
    [ "$_peers" -gt 0 ] && [ "$_outbound" -eq 0 ]
}

# List server section names under /etc/config/wireguard_server (glinet).
list_glinet_servers() {
    [ -f /etc/config/wireguard_server ] || return 0
    uci -q show wireguard_server 2>/dev/null | awk -F= '
        /=.?servers.?$/ {
            n = split($1, parts, ".")
            if (n >= 2) print parts[2]
        }
    ' | sort -u
}

# List peers section names under /etc/config/wireguard_server (glinet).
list_glinet_peers() {
    [ -f /etc/config/wireguard_server ] || return 0
    uci -q show wireguard_server 2>/dev/null | awk -F= '
        /=.?peers.?$/ {
            n = split($1, parts, ".")
            if (n >= 2) print parts[2]
        }
    ' | sort -u
}

# Kernel devices currently known to wireguard (one per line).
wg_kernel_ifaces() {
    if command -v wg >/dev/null 2>&1; then
        wg show interfaces 2>/dev/null | tr -s ' \t' '\n' | awk 'NF'
    fi
}

# Pick a kernel device for the glinet layout. If wg(8) reports exactly
# one interface, use it. Otherwise fall back to $WG_IFACE_DEFAULT
# ("wgserver"), which is what GL.iNet firmware uses by default.
glinet_pick_kernel_dev() {
    _kifaces="$(wg_kernel_ifaces)"
    _kcount=0
    for _k in $_kifaces; do _kcount=$((_kcount+1)); done
    if [ "$_kcount" -eq 1 ]; then
        printf '%s\n' "$_kifaces"
        return 0
    fi
    if [ "$_kcount" -gt 1 ]; then
        # Prefer one that begins with "wg" if present.
        for _k in $_kifaces; do
            case "$_k" in wg*) printf '%s\n' "$_k"; return 0 ;; esac
        done
        printf '%s\n' "$_kifaces" | head -n1
        return 0
    fi
    printf '%s\n' "$WG_IFACE_DEFAULT"
}

# Is the current $WG_IFACE / $WG_LAYOUT / $WG_UCI_SECTION combo
# actually backed by a wireguard config in uci?
wg_iface_configured() {
    [ -n "$WG_IFACE" ] || return 1
    case "$WG_LAYOUT" in
        netifd)
            [ -n "$(uci -q get "network.${WG_UCI_SECTION}")" ] || return 1
            [ "$(uci -q get "network.${WG_UCI_SECTION}.proto")" = "wireguard" ]
            ;;
        glinet)
            [ -f /etc/config/wireguard_server ] || return 1
            [ -n "$(uci -q get "wireguard_server.${WG_UCI_SECTION}")" ] || return 1
            [ "$(uci -q get "wireguard_server.${WG_UCI_SECTION}")" = "servers" ]
            ;;
        *)
            return 1
            ;;
    esac
}

# Set WG_LAYOUT / WG_UCI_CONFIG / WG_UCI_SECTION / WG_IFACE for an
# explicit user-provided iface name. Order: netifd section, then
# glinet (kernel-device-only).
classify_iface() {
    _name="$1"
    # netifd section?
    if [ -n "$(uci -q get "network.${_name}")" ] \
       && [ "$(uci -q get "network.${_name}.proto")" = "wireguard" ]; then
        WG_LAYOUT="netifd"
        WG_UCI_CONFIG="network"
        WG_UCI_SECTION="$_name"
        WG_IFACE="$_name"
        return 0
    fi
    # glinet: only one server section is supported per wireguard_server file
    # in shipping firmware. If the explicit name matches the kernel device
    # currently advertised by wg(8), or wg(8) shows nothing yet, accept it.
    _srvs="$(list_glinet_servers)"
    _scount=0
    for _s in $_srvs; do _scount=$((_scount+1)); done
    if [ "$_scount" -ge 1 ]; then
        WG_LAYOUT="glinet"
        WG_UCI_CONFIG="wireguard_server"
        # Pick the first (or only) server section.
        for _s in $_srvs; do WG_UCI_SECTION="$_s"; break; done
        WG_IFACE="$_name"
        return 0
    fi
    # Nothing configured -- still accept with layout=unknown so that
    # host-side hardening (sysctl / dhcp / globals) can be applied.
    WG_LAYOUT="unknown"
    WG_UCI_CONFIG=""
    WG_UCI_SECTION=""
    WG_IFACE="$_name"
    return 0
}

# Auto-detect a single WG server. Sets WG_LAYOUT/WG_UCI_*/WG_IFACE
# on success. On ambiguity or absence, prints diagnostics to stderr
# and returns non-zero.
detect_wg_iface() {
    # 1) netifd: prefer an iface that looks like a server.
    _netifd="$(list_netifd_wg_ifaces)"
    _servers=""
    for _i in $_netifd; do
        if netifd_iface_looks_like_server "$_i"; then
            _servers="${_servers:+$_servers }$_i"
        fi
    done
    _scount=0
    for _x in $_servers; do _scount=$((_scount+1)); done
    if [ "$_scount" -eq 1 ]; then
        WG_LAYOUT="netifd"; WG_UCI_CONFIG="network"
        WG_UCI_SECTION="$_servers"; WG_IFACE="$_servers"
        return 0
    fi
    if [ "$_scount" -gt 1 ]; then
        printf 'multiple netifd WireGuard server candidates: %s\n' "$_servers" >&2
        printf 'pass --iface NAME to choose one explicitly\n' >&2
        return 1
    fi
    # 2) netifd: exactly one wg iface (even if not "server-shaped").
    _ncount=0
    for _x in $_netifd; do _ncount=$((_ncount+1)); done
    if [ "$_ncount" -eq 1 ]; then
        WG_LAYOUT="netifd"; WG_UCI_CONFIG="network"
        WG_UCI_SECTION="$_netifd"; WG_IFACE="$_netifd"
        return 0
    fi
    # 3) glinet: server section in /etc/config/wireguard_server.
    _glsrvs="$(list_glinet_servers)"
    _gcount=0
    for _x in $_glsrvs; do _gcount=$((_gcount+1)); done
    if [ "$_gcount" -eq 1 ]; then
        WG_LAYOUT="glinet"; WG_UCI_CONFIG="wireguard_server"
        WG_UCI_SECTION="$_glsrvs"
        WG_IFACE="$(glinet_pick_kernel_dev)"
        return 0
    fi
    if [ "$_gcount" -gt 1 ]; then
        printf 'multiple glinet WireGuard server sections: %s\n' "$_glsrvs" >&2
        printf 'pass --iface NAME to choose one explicitly\n' >&2
        return 1
    fi
    # 4) Nothing in any uci layout, but wg(8) shows a kernel iface.
    _kifaces="$(wg_kernel_ifaces)"
    _kcount=0
    for _x in $_kifaces; do _kcount=$((_kcount+1)); done
    if [ "$_kcount" -eq 1 ]; then
        WG_LAYOUT="unknown"; WG_UCI_CONFIG=""; WG_UCI_SECTION=""
        WG_IFACE="$_kifaces"
        return 0
    fi
    if [ "$_kcount" -gt 1 ]; then
        printf 'multiple kernel WireGuard interfaces: %s\n' "$_kifaces" >&2
        printf 'pass --iface NAME to choose one explicitly\n' >&2
        return 1
    fi
    printf 'no WireGuard interface found in /etc/config/network or /etc/config/wireguard_server, and none in kernel\n' >&2
    return 1
}

read_persisted_iface() {
    [ -f "$IFACE_FILE" ] || return 1
    _v="$(head -n1 "$IFACE_FILE" 2>/dev/null | tr -d ' \t\n')"
    [ -n "$_v" ] || return 1
    printf '%s\n' "$_v"
}

read_persisted_layout() {
    [ -f "$LAYOUT_FILE" ] || return 1
    _v="$(head -n1 "$LAYOUT_FILE" 2>/dev/null | tr -d ' \t\n')"
    [ -n "$_v" ] || return 1
    printf '%s\n' "$_v"
}

read_persisted_section() {
    [ -f "$SECTION_FILE" ] || return 1
    _v="$(head -n1 "$SECTION_FILE" 2>/dev/null | tr -d ' \t\n')"
    [ -n "$_v" ] || return 1
    printf '%s\n' "$_v"
}

write_persisted_state() {
    mkdir -p "$BACKUP_ROOT" 2>/dev/null || true
    printf '%s\n' "$WG_IFACE"        > "$IFACE_FILE"   2>/dev/null || true
    printf '%s\n' "$WG_LAYOUT"       > "$LAYOUT_FILE"  2>/dev/null || true
    printf '%s\n' "$WG_UCI_SECTION"  > "$SECTION_FILE" 2>/dev/null || true
}

clear_persisted_state() {
    [ -f "$IFACE_FILE" ]   && rm -f "$IFACE_FILE"
    [ -f "$LAYOUT_FILE" ]  && rm -f "$LAYOUT_FILE"
    [ -f "$SECTION_FILE" ] && rm -f "$SECTION_FILE"
}

# Resolve $WG_IFACE for `install`: CLI > detected > persisted (recovery).
resolve_iface_for_install() {
    if [ -n "$WG_IFACE" ]; then
        # CLI provided a name; classify it.
        classify_iface "$WG_IFACE"
        WG_IFACE_SOURCE="cli"
        return 0
    fi
    if detect_wg_iface 2>/dev/null; then
        WG_IFACE_SOURCE="detected"
        return 0
    fi
    _p="$(read_persisted_iface)"
    if [ -n "$_p" ]; then
        WG_IFACE="$_p"
        _l="$(read_persisted_layout)"; [ -n "$_l" ] && WG_LAYOUT="$_l"
        _s="$(read_persisted_section)"; [ -n "$_s" ] && WG_UCI_SECTION="$_s"
        case "$WG_LAYOUT" in
            netifd) WG_UCI_CONFIG="network" ;;
            glinet) WG_UCI_CONFIG="wireguard_server" ;;
        esac
        WG_IFACE_SOURCE="persisted ($IFACE_FILE)"
        return 0
    fi
    return 1
}

# Resolve $WG_IFACE for `uninstall`/`status`/`wizard`:
# CLI > persisted > detected.
resolve_iface_for_audit() {
    if [ -n "$WG_IFACE" ]; then
        classify_iface "$WG_IFACE"
        WG_IFACE_SOURCE="cli"
        return 0
    fi
    _p="$(read_persisted_iface)"
    if [ -n "$_p" ]; then
        WG_IFACE="$_p"
        _l="$(read_persisted_layout)"; [ -n "$_l" ] && WG_LAYOUT="$_l"
        _s="$(read_persisted_section)"; [ -n "$_s" ] && WG_UCI_SECTION="$_s"
        case "$WG_LAYOUT" in
            netifd) WG_UCI_CONFIG="network" ;;
            glinet) WG_UCI_CONFIG="wireguard_server" ;;
        esac
        WG_IFACE_SOURCE="persisted ($IFACE_FILE)"
        return 0
    fi
    if detect_wg_iface 2>/dev/null; then
        WG_IFACE_SOURCE="detected"
        return 0
    fi
    return 1
}

strip_live_ipv6() {
    _dev="$1"
    [ -d "/sys/class/net/$_dev" ] || { printf '0\n'; return 0; }
    _n=0
    for _a in $(ip -6 addr show dev "$_dev" 2>/dev/null \
                | awk '/inet6/ && $2 !~ /^fe80/ {print $2}'); do
        ip -6 addr del "$_a" dev "$_dev" 2>/dev/null && _n=$((_n+1))
    done
    printf '%s\n' "$_n"
}

# Reconcile the live kernel WireGuard peer state to drop any IPv6 entries
# from each peer's AllowedIPs. Needed because GL.iNet's wireguard_server
# daemon may not re-push peer programming after a UCI rewrite + restart
# (existing sessions keep their old AllowedIPs in the kernel until the
# peer re-handshakes). Safe no-op if `wg` isn't installed, the device
# isn't up, or no peers are present. Echoes the count of peers updated.
reconcile_kernel_peers_ipv4() {
    _dev="$1"
    [ -d "/sys/class/net/$_dev" ] || { printf '0\n'; return 0; }
    command -v wg >/dev/null 2>&1 || { printf '0\n'; return 0; }
    _tmp="/tmp/.wg_noipv6_peers.$$"
    wg show "$_dev" allowed-ips >"$_tmp" 2>/dev/null || { rm -f "$_tmp"; printf '0\n'; return 0; }
    _updated=0
    # `wg show <dev> allowed-ips` prints lines: "<pubkey>\t<ip1> <ip2> ..."
    # or "<pubkey>\t(none)". Rebuild without ":"-bearing entries and only
    # re-set when something actually changes.
    while IFS="$(printf '\t')" read -r _pub _ips; do
        [ -n "$_pub" ] || continue
        case "$_ips" in '(none)'|'') continue ;; esac
        _new=""
        _had_v6=0
        for _ip in $_ips; do
            case "$_ip" in
                *:*) _had_v6=1 ;;
                '') ;;
                *)  _new="${_new:+$_new,}$_ip" ;;
            esac
        done
        [ "$_had_v6" -eq 1 ] || continue
        if [ -z "$_new" ]; then
            log_warn "kernel peer ${_pub} has only IPv6 allowed-ips; refusing to clear (would unroute peer)"
            continue
        fi
        if wg set "$_dev" peer "$_pub" allowed-ips "$_new" 2>/dev/null; then
            log_info "kernel peer ${_pub}: AllowedIPs reset to $_new"
            _updated=$((_updated+1))
        else
            log_warn "kernel peer ${_pub}: wg set failed"
        fi
    done <"$_tmp"
    rm -f "$_tmp"
    printf '%s\n' "$_updated"
}

# Strip IPv6 entries from a netifd network.<iface>.addresses list.
strip_uci_ipv6_addrs() {
    _iface="$1"
    _addrs=""
    for _a in $(uci -q get "network.${_iface}.addresses"); do
        case "$_a" in *:*) ;; *) _addrs="$_addrs $_a" ;; esac
    done
    uci -q delete "network.${_iface}.addresses"
    for _a in $_addrs; do uci -q add_list "network.${_iface}.addresses=$_a"; done
}

# Strip ":"-bearing entries from a comma-separated list value
# (e.g. GL.iNet peer client_ip="10.1.0.2/24,fd00::2/64").
# Echoes the cleaned list to stdout.
strip_v6_from_csv() {
    printf '%s' "$1" | awk -v RS=',' '{
        gsub(/^[ \t]+|[ \t]+$/, "", $0)
        if (length($0) == 0) next
        if (index($0, ":") > 0) next
        if (out == "") out = $0
        else            out = out "," $0
    } END { print out }'
}

latest_backup() {
    [ -d "$BACKUP_ROOT" ] || return 0
    _list=""
    for _d in "$BACKUP_ROOT"/backup-*; do
        [ -d "$_d" ] || continue
        _list="${_list}${_d}
"
    done
    [ -n "$_list" ] || return 0
    printf '%s' "$_list" | sort | tail -n1
}

is_installed() {
    [ -x "$HOTPLUG_PATH" ] || return 1
    [ -f "$SYSCTL_PATH" ]  || return 1
    return 0
}

usage() {
    cat <<USAGE
$APPNAME

Usage: $(basename "$0") [SUBCOMMAND] [OPTIONS]

Subcommands:
  install              Apply hardening (hotplug + cron watchdog by default).
  uninstall            Remove all enforcement; restore IPv6 defaults.
  status               Read-only audit.
  help                 Show this message.

With no subcommand the script launches the interactive wizard whenever a
controlling terminal is available (including the documented one-liner
\`wget -qO- URL | sh\`); fully headless contexts (cron, CI) fall through
to a non-interactive \`install\`.

Options:
  --no-watchdog          install:   skip the cron watchdog
  --restore-backup DIR   uninstall: restore /etc/config from a backup, e.g.
                                    /etc/wg-noipv6/backup-20260505-120000
  --iface NAME           any:       force WireGuard kernel device name
  -h, --help             any:       show this message

Auto-detection:
  Two layouts are supported:
    netifd  - /etc/config/network section with proto=wireguard.
    glinet  - /etc/config/wireguard_server (GL.iNet 4.x firmware).
  When --iface is not given the script picks whichever layout is present
  and resolves the kernel device. The choice is persisted to
  $IFACE_FILE so subsequent status/uninstall runs target the same setup.
  GL.iNet default kernel device: $WG_IFACE_DEFAULT.

Exit codes (status):
  0  compliant
  2  drift detected (artifacts present but failing checks)
  3  no WireGuard interface configured on this router
  4  WireGuard configured but this hardening package is not installed
USAGE
}

parse_args() {
    if [ $# -gt 0 ]; then
        case "$1" in
            install|uninstall|status|help)
                SUBCMD="$1"; shift
                ;;
        esac
    fi

    while [ $# -gt 0 ]; do
        case "$1" in
            --no-watchdog)      INSTALL_WATCHDOG=0; shift ;;
            --iface)            WG_IFACE="${2:?--iface requires a value}"; shift 2 ;;
            --restore-backup)   RESTORE_DIR="${2:?--restore-backup requires a value}"; shift 2 ;;
            -h|--help)          SUBCMD="help"; shift ;;
            *)                  usage >&2; die "unknown argument '$1'" ;;
        esac
    done

    if [ -z "$SUBCMD" ]; then
        # Default to the interactive wizard whenever a controlling TTY is
        # reachable, even when the script body is piped (`wget ... | sh`):
        # the wizard reads from /dev/tty, not stdin, so it still works.
        # Truly headless invocations (cron, CI, ssh -T-less, etc.) have no
        # /dev/tty and fall through to a non-interactive `install`.
        if (exec </dev/tty) 2>/dev/null; then
            SUBCMD="wizard"
        else
            SUBCMD="install"
        fi
    fi
}

backup_uci() {
    BACKUP_DIR="${BACKUP_ROOT}/backup-$(date +%Y%m%d-%H%M%S)"
    mkdir -p "$BACKUP_DIR" || die "cannot create $BACKUP_DIR"
    for f in network firewall dhcp wireguard_server; do
        [ -f "/etc/config/$f" ] && cp "/etc/config/$f" "$BACKUP_DIR/$f"
    done
    log_info "uci backup written to $BACKUP_DIR"
}

apply_globals() {
    if [ -z "$(uci -q get network.globals)" ]; then
        log_info "network.globals section not present; nothing to clear"
        return 0
    fi
    _cur="$(uci -q get network.globals.ula_prefix)"
    if [ -z "$_cur" ]; then
        log_info "network.globals.ula_prefix already empty/unset"
        return 0
    fi
    uci -q delete network.globals.ula_prefix
    log_info "cleared network.globals.ula_prefix (was: $_cur)"
}

apply_disable_v6_on_ifaces() {
    _changed=0; _skipped=0
    for s in lan wan guest; do
        if [ -z "$(uci -q get "network.$s")" ]; then
            _skipped=$((_skipped+1))
            continue
        fi
        if [ "$(uci -q get "network.$s.ipv6")" != "0" ]; then
            uci -q set "network.$s.ipv6=0"
            _changed=$((_changed+1))
        fi
        uci -q delete "network.$s.ip6assign" 2>/dev/null || true
        uci -q delete "network.$s.ip6addr"   2>/dev/null || true
    done
    if [ -n "$(uci -q get network.wan6)" ]; then
        if [ "$(uci -q get network.wan6.proto)" != "none" ]; then
            uci -q set network.wan6.proto='none'
            uci -q set network.wan6.auto='0'
            log_info "set network.wan6.proto=none"
            _changed=$((_changed+1))
        else
            log_info "network.wan6.proto already none"
        fi
    else
        _skipped=$((_skipped+1))
    fi
    log_info "lan/wan/guest/wan6: $_changed change(s), $_skipped section(s) absent"
}

# Layout-aware: pin the WG server config itself to IPv4-only.
apply_pin_wg_ipv4() {
    case "$WG_LAYOUT" in
        netifd)
            apply_pin_wg_ipv4_netifd
            ;;
        glinet)
            apply_pin_wg_ipv4_glinet
            ;;
        *)
            log_warn "unknown WG layout for $WG_IFACE; skipping uci pin"
            log_warn "(host-side IPv6 disablement will still take effect)"
            ;;
    esac
}

apply_pin_wg_ipv4_netifd() {
    if ! wg_iface_configured; then
        log_warn "uci section network.${WG_UCI_SECTION} is not a wireguard interface; skipping uci pin"
        log_warn "(re-run install after WireGuard is configured for $WG_UCI_SECTION)"
        return 0
    fi
    if [ "$(uci -q get "network.${WG_UCI_SECTION}.ipv6")" != "0" ]; then
        uci -q set "network.${WG_UCI_SECTION}.ipv6=0"
        log_info "set network.${WG_UCI_SECTION}.ipv6=0"
    else
        log_info "network.${WG_UCI_SECTION}.ipv6 already 0"
    fi
    _v6=0
    for _a in $(uci -q get "network.${WG_UCI_SECTION}.addresses"); do
        case "$_a" in *:*) _v6=$((_v6+1));; esac
    done
    if [ "$_v6" -gt 0 ]; then
        strip_uci_ipv6_addrs "$WG_UCI_SECTION"
        log_info "stripped $_v6 IPv6 entry/entries from network.${WG_UCI_SECTION}.addresses"
    else
        log_info "network.${WG_UCI_SECTION}.addresses already IPv4-only"
    fi
}

apply_pin_wg_ipv4_glinet() {
    if ! wg_iface_configured; then
        log_warn "uci section wireguard_server.${WG_UCI_SECTION} is not a 'servers' section; skipping uci pin"
        return 0
    fi
    _srv="$WG_UCI_SECTION"

    # 1) Server section: clear address_v6.
    _v6srv="$(uci -q get "wireguard_server.${_srv}.address_v6")"
    if [ -n "$_v6srv" ]; then
        uci -q delete "wireguard_server.${_srv}.address_v6"
        log_info "cleared wireguard_server.${_srv}.address_v6 (was: $_v6srv)"
    else
        log_info "wireguard_server.${_srv}.address_v6 already empty/unset"
    fi

    # 2) Each peer section: strip ":"-bearing entries from client_ip
    #    and from allowed_ips (comma-separated).
    _changed_peers=0
    for _p in $(list_glinet_peers); do
        _changed=0
        for _key in client_ip allowed_ips; do
            _cur="$(uci -q get "wireguard_server.${_p}.${_key}")"
            [ -n "$_cur" ] || continue
            _new="$(strip_v6_from_csv "$_cur")"
            if [ "$_cur" != "$_new" ]; then
                if [ -n "$_new" ]; then
                    uci -q set "wireguard_server.${_p}.${_key}=$_new"
                else
                    uci -q delete "wireguard_server.${_p}.${_key}"
                fi
                log_info "wireguard_server.${_p}.${_key}: stripped IPv6 (now: ${_new:-<unset>})"
                _changed=1
            fi
        done
        [ "$_changed" -eq 1 ] && _changed_peers=$((_changed_peers+1))
    done
    log_info "glinet peers updated: $_changed_peers"
}

apply_dhcp() {
    if [ -n "$(uci -q get dhcp.lan)" ]; then
        _changed=0
        for opt in dhcpv6 ra ndp; do
            if [ "$(uci -q get "dhcp.lan.$opt")" != "disabled" ]; then
                uci -q set "dhcp.lan.$opt=disabled"
                _changed=$((_changed+1))
            fi
        done
        log_info "dhcp.lan dhcpv6/ra/ndp: $_changed change(s)"
    else
        log_info "dhcp.lan section not present; skipping ipv6 distribution disable"
    fi
    if [ -x /etc/init.d/odhcpd ]; then
        if /etc/init.d/odhcpd enabled 2>/dev/null; then
            /etc/init.d/odhcpd disable >/dev/null 2>&1
            /etc/init.d/odhcpd stop    >/dev/null 2>&1
            log_info "disabled and stopped odhcpd"
        else
            log_info "odhcpd already disabled at boot"
        fi
    else
        log_info "odhcpd init script not present; skipping"
    fi
}

apply_sysctl() {
    mkdir -p "$(dirname "$SYSCTL_PATH")"
    cat > "$SYSCTL_PATH" <<EOF
# Managed by $(basename "$0")
net.ipv6.conf.all.disable_ipv6 = 1
net.ipv6.conf.default.disable_ipv6 = 1
net.ipv6.conf.lo.disable_ipv6 = 0
net.ipv6.conf.${WG_IFACE}.disable_ipv6 = 1
EOF
    sysctl -p "$SYSCTL_PATH" >/dev/null 2>&1 || true
    log_info "wrote $SYSCTL_PATH"
}

apply_firewall_fw4() {
    mkdir -p "$(dirname "$NFT_PATH")"
    cat > "$NFT_PATH" <<EOF
# Managed by $(basename "$0")
chain wg_noipv6_input {
    type filter hook input priority -1; policy accept;
    iifname "${WG_IFACE}" meta nfproto ipv6 drop
}
chain wg_noipv6_forward {
    type filter hook forward priority -1; policy accept;
    iifname "${WG_IFACE}" meta nfproto ipv6 drop
    oifname "${WG_IFACE}" meta nfproto ipv6 drop
}
chain wg_noipv6_output {
    type filter hook output priority -1; policy accept;
    oifname "${WG_IFACE}" meta nfproto ipv6 drop
}
EOF
    log_info "wrote $NFT_PATH"
}

apply_firewall_fw3() {
    mkdir -p "$(dirname "$FW3_INCLUDE_PATH")"
    cat > "$FW3_INCLUDE_PATH" <<EOF
#!/bin/sh
# Managed by $(basename "$0")
WG_IFACE="${WG_IFACE}"
ip6tables -D forwarding_rule -i "\$WG_IFACE" -j DROP 2>/dev/null
ip6tables -D forwarding_rule -o "\$WG_IFACE" -j DROP 2>/dev/null
ip6tables -D input_rule      -i "\$WG_IFACE" -j DROP 2>/dev/null
ip6tables -D output_rule     -o "\$WG_IFACE" -j DROP 2>/dev/null
ip6tables -I forwarding_rule -i "\$WG_IFACE" -j DROP
ip6tables -I forwarding_rule -o "\$WG_IFACE" -j DROP
ip6tables -I input_rule      -i "\$WG_IFACE" -j DROP
ip6tables -I output_rule     -o "\$WG_IFACE" -j DROP
EOF
    chmod 0755 "$FW3_INCLUDE_PATH"
    uci -q delete firewall.wg_noipv6_include
    uci set firewall.wg_noipv6_include=include
    uci set firewall.wg_noipv6_include.path="$FW3_INCLUDE_PATH"
    uci set firewall.wg_noipv6_include.reload='1'
    log_info "wrote $FW3_INCLUDE_PATH and registered fw3 include"
}

# Iface hotplug (netifd ifup events). Always installed; only fires for
# netifd-managed wireguard interfaces. Harmless on glinet-only systems.
apply_hotplug() {
    mkdir -p "$(dirname "$HOTPLUG_PATH")"
    cat > "$HOTPLUG_PATH" <<EOF
#!/bin/sh
# Managed by $(basename "$0")
WG_IFACE="${WG_IFACE}"
WG_LAYOUT="${WG_LAYOUT}"
WG_UCI_SECTION="${WG_UCI_SECTION}"
TAG="${TAG}"
[ "\$INTERFACE" = "\$WG_IFACE" ] || [ "\$INTERFACE" = "\$WG_UCI_SECTION" ] || exit 0
[ "\$ACTION" = "ifup" ] || exit 0
DEV="\$WG_IFACE"
if [ "\$WG_LAYOUT" = "netifd" ] && [ -n "\$WG_UCI_SECTION" ]; then
    _d="\$(uci -q get "network.\${WG_UCI_SECTION}.ifname")"
    [ -n "\$_d" ] && DEV="\$_d"
fi
n=0
for a in \$(ip -6 addr show dev "\$DEV" 2>/dev/null | awk '/inet6/ && \$2 !~ /^fe80/ {print \$2}'); do
    ip -6 addr del "\$a" dev "\$DEV" 2>/dev/null && n=\$((n+1))
done
if [ "\$WG_LAYOUT" = "netifd" ] && [ -n "\$WG_UCI_SECTION" ]; then
    uci -q set "network.\${WG_UCI_SECTION}.ipv6=0"
    addrs=""
    for a in \$(uci -q get "network.\${WG_UCI_SECTION}.addresses"); do
        case "\$a" in *:*) ;; *) addrs="\$addrs \$a" ;; esac
    done
    uci -q delete "network.\${WG_UCI_SECTION}.addresses"
    for a in \$addrs; do uci -q add_list "network.\${WG_UCI_SECTION}.addresses=\$a"; done
    uci -q commit network
fi
[ "\$n" -gt 0 ] && logger -t "\$TAG" "iface-hotplug removed \$n IPv6 address(es) from \$DEV"
if command -v wg >/dev/null 2>&1; then
    p=0; tmp="/tmp/.wg_noipv6_iface_peers.\$\$"
    wg show "\$DEV" allowed-ips 2>/dev/null > "\$tmp"
    while IFS="\$(printf '\t')" read -r pub ips; do
        [ -n "\$pub" ] || continue
        case "\$ips" in '(none)'|'') continue ;; esac
        new=""; had_v6=0
        for ip in \$ips; do
            case "\$ip" in
                *:*) had_v6=1 ;;
                '') ;;
                *)  new="\${new:+\$new,}\$ip" ;;
            esac
        done
        [ "\$had_v6" -eq 1 ] || continue
        [ -z "\$new" ] && continue
        wg set "\$DEV" peer "\$pub" allowed-ips "\$new" 2>/dev/null && p=\$((p+1))
    done < "\$tmp"
    rm -f "\$tmp"
    [ "\$p" -gt 0 ] && logger -t "\$TAG" "iface-hotplug reconciled \$p peer AllowedIPs to IPv4-only on \$DEV"
fi
exit 0
EOF
    chmod 0755 "$HOTPLUG_PATH"
    log_info "iface hotplug installed at $HOTPLUG_PATH"
}

# Net (kernel netdev) hotplug. Fires when a netdev appears regardless of
# whether netifd manages it -- this is the only reliable hook for the
# glinet wireguard_server daemon, which brings up its kernel device
# without a netifd ifup event.
apply_net_hotplug() {
    mkdir -p "$(dirname "$NET_HOTPLUG_PATH")"
    cat > "$NET_HOTPLUG_PATH" <<EOF
#!/bin/sh
# Managed by $(basename "$0")
WG_IFACE="${WG_IFACE}"
TAG="${TAG}"
[ "\$DEVICENAME" = "\$WG_IFACE" ] || [ "\$INTERFACE" = "\$WG_IFACE" ] || exit 0
case "\$ACTION" in add|register|"") ;; *) exit 0 ;; esac
[ -d "/sys/class/net/\$WG_IFACE" ] || exit 0
n=0
for a in \$(ip -6 addr show dev "\$WG_IFACE" 2>/dev/null | awk '/inet6/ && \$2 !~ /^fe80/ {print \$2}'); do
    ip -6 addr del "\$a" dev "\$WG_IFACE" 2>/dev/null && n=\$((n+1))
done
[ "\$n" -gt 0 ] && logger -t "\$TAG" "net-hotplug removed \$n IPv6 address(es) from \$WG_IFACE"
if command -v wg >/dev/null 2>&1; then
    p=0; tmp="/tmp/.wg_noipv6_net_peers.\$\$"
    wg show "\$WG_IFACE" allowed-ips 2>/dev/null > "\$tmp"
    while IFS="\$(printf '\t')" read -r pub ips; do
        [ -n "\$pub" ] || continue
        case "\$ips" in '(none)'|'') continue ;; esac
        new=""; had_v6=0
        for ip in \$ips; do
            case "\$ip" in
                *:*) had_v6=1 ;;
                '') ;;
                *)  new="\${new:+\$new,}\$ip" ;;
            esac
        done
        [ "\$had_v6" -eq 1 ] || continue
        [ -z "\$new" ] && continue
        wg set "\$WG_IFACE" peer "\$pub" allowed-ips "\$new" 2>/dev/null && p=\$((p+1))
    done < "\$tmp"
    rm -f "\$tmp"
    [ "\$p" -gt 0 ] && logger -t "\$TAG" "net-hotplug reconciled \$p peer AllowedIPs to IPv4-only on \$WG_IFACE"
fi
exit 0
EOF
    chmod 0755 "$NET_HOTPLUG_PATH"
    log_info "net hotplug installed at $NET_HOTPLUG_PATH"
}

apply_watchdog() {
    mkdir -p "$(dirname "$WATCHDOG_PATH")"
    cat > "$WATCHDOG_PATH" <<EOF
#!/bin/sh
# Managed by $(basename "$0")
WG_IFACE="${WG_IFACE}"
WG_LAYOUT="${WG_LAYOUT}"
WG_UCI_SECTION="${WG_UCI_SECTION}"
TAG="${TAG}"
DEV="\$WG_IFACE"
if [ "\$WG_LAYOUT" = "netifd" ] && [ -n "\$WG_UCI_SECTION" ]; then
    _d="\$(uci -q get "network.\${WG_UCI_SECTION}.ifname")"
    [ -n "\$_d" ] && DEV="\$_d"
fi
[ -d "/sys/class/net/\$DEV" ] || exit 0
n=0
for a in \$(ip -6 addr show dev "\$DEV" 2>/dev/null | awk '/inet6/ && \$2 !~ /^fe80/ {print \$2}'); do
    ip -6 addr del "\$a" dev "\$DEV" 2>/dev/null && n=\$((n+1))
done
[ "\$n" -gt 0 ] && logger -t "\$TAG" "watchdog removed \$n IPv6 address(es) from \$DEV"
if command -v wg >/dev/null 2>&1; then
    p=0; tmp="/tmp/.wg_noipv6_wd_peers.\$\$"
    wg show "\$DEV" allowed-ips 2>/dev/null > "\$tmp"
    while IFS="\$(printf '\t')" read -r pub ips; do
        [ -n "\$pub" ] || continue
        case "\$ips" in '(none)'|'') continue ;; esac
        new=""; had_v6=0
        for ip in \$ips; do
            case "\$ip" in
                *:*) had_v6=1 ;;
                '') ;;
                *)  new="\${new:+\$new,}\$ip" ;;
            esac
        done
        [ "\$had_v6" -eq 1 ] || continue
        [ -z "\$new" ] && continue
        wg set "\$DEV" peer "\$pub" allowed-ips "\$new" 2>/dev/null && p=\$((p+1))
    done < "\$tmp"
    rm -f "\$tmp"
    [ "\$p" -gt 0 ] && logger -t "\$TAG" "watchdog reconciled \$p peer AllowedIPs to IPv4-only on \$DEV"
fi
exit 0
EOF
    chmod 0755 "$WATCHDOG_PATH"

    mkdir -p "$(dirname "$CRONTAB_PATH")"
    touch "$CRONTAB_PATH"
    grep -v -F "$WATCHDOG_PATH" "$CRONTAB_PATH" > "$CRONTAB_PATH.tmp" 2>/dev/null || true
    mv "$CRONTAB_PATH.tmp" "$CRONTAB_PATH"
    echo "* * * * * $WATCHDOG_PATH" >> "$CRONTAB_PATH"
    /etc/init.d/cron enable  >/dev/null 2>&1
    /etc/init.d/cron restart >/dev/null 2>&1

    log_info "watchdog installed at $WATCHDOG_PATH (cron: every minute)"
}

restore_backup() {
    [ -d "$RESTORE_DIR" ] || die "backup directory '$RESTORE_DIR' does not exist"
    for f in network firewall dhcp wireguard_server; do
        if [ -f "$RESTORE_DIR/$f" ]; then
            cp "$RESTORE_DIR/$f" "/etc/config/$f" \
                || die "failed to restore /etc/config/$f"
            log_info "restored /etc/config/$f from $RESTORE_DIR"
        fi
    done
}

remove_hotplug() {
    if [ -e "$HOTPLUG_PATH" ]; then
        rm -f "$HOTPLUG_PATH" && log_info "removed $HOTPLUG_PATH"
    fi
    if [ -e "$NET_HOTPLUG_PATH" ]; then
        rm -f "$NET_HOTPLUG_PATH" && log_info "removed $NET_HOTPLUG_PATH"
    fi
}

remove_watchdog() {
    if [ -f "$CRONTAB_PATH" ] && grep -q -F "$WATCHDOG_PATH" "$CRONTAB_PATH"; then
        grep -v -F "$WATCHDOG_PATH" "$CRONTAB_PATH" > "$CRONTAB_PATH.tmp"
        mv "$CRONTAB_PATH.tmp" "$CRONTAB_PATH"
        /etc/init.d/cron restart >/dev/null 2>&1
        log_info "removed cron entries for watchdog"
    fi
    [ -f "$WATCHDOG_PATH" ] && rm -f "$WATCHDOG_PATH" && log_info "removed $WATCHDOG_PATH"
}

remove_firewall() {
    [ -f "$NFT_PATH" ]         && rm -f "$NFT_PATH"         && log_info "removed $NFT_PATH"
    [ -f "$FW3_INCLUDE_PATH" ] && rm -f "$FW3_INCLUDE_PATH" && log_info "removed $FW3_INCLUDE_PATH"
    if [ -n "$(uci -q get firewall.wg_noipv6_include)" ]; then
        uci -q delete firewall.wg_noipv6_include
        log_info "removed uci firewall.wg_noipv6_include"
    fi
    if command -v ip6tables >/dev/null 2>&1 && [ -n "$WG_IFACE" ]; then
        ip6tables -D forwarding_rule -i "$WG_IFACE" -j DROP 2>/dev/null || true
        ip6tables -D forwarding_rule -o "$WG_IFACE" -j DROP 2>/dev/null || true
        ip6tables -D input_rule      -i "$WG_IFACE" -j DROP 2>/dev/null || true
        ip6tables -D output_rule     -o "$WG_IFACE" -j DROP 2>/dev/null || true
    fi
}

remove_sysctl() {
    if [ -f "$SYSCTL_PATH" ]; then
        rm -f "$SYSCTL_PATH" && log_info "removed $SYSCTL_PATH"
    fi
    echo 0 > /proc/sys/net/ipv6/conf/all/disable_ipv6     2>/dev/null || true
    echo 0 > /proc/sys/net/ipv6/conf/default/disable_ipv6 2>/dev/null || true
    if [ -n "$WG_IFACE" ] && [ -f "/proc/sys/net/ipv6/conf/$WG_IFACE/disable_ipv6" ]; then
        echo 0 > "/proc/sys/net/ipv6/conf/$WG_IFACE/disable_ipv6" 2>/dev/null || true
    fi
    sysctl --system >/dev/null 2>&1 || true
}

restore_uci_defaults() {
    _changed=0
    for s in lan wan guest; do
        if [ -n "$(uci -q get "network.$s")" ] \
           && [ "$(uci -q get "network.$s.ipv6")" = "0" ]; then
            uci -q delete "network.$s.ipv6" && _changed=$((_changed+1))
        fi
    done
    if [ "$WG_LAYOUT" = "netifd" ] && [ -n "$WG_UCI_SECTION" ] \
       && [ -n "$(uci -q get "network.${WG_UCI_SECTION}")" ] \
       && [ "$(uci -q get "network.${WG_UCI_SECTION}.ipv6")" = "0" ]; then
        uci -q delete "network.${WG_UCI_SECTION}.ipv6" \
            && _changed=$((_changed+1))
    fi
    if [ -z "$(uci -q get network.globals.ula_prefix)" ] \
       && [ -n "$(uci -q get network.globals)" ]; then
        uci -q delete network.globals.ula_prefix
        _changed=$((_changed+1))
    fi

    if [ "$(uci -q get network.wan6.proto)" = "none" ]; then
        uci -q delete network.wan6.proto
        uci -q delete network.wan6.auto
        _changed=$((_changed+1))
    fi

    if [ -n "$(uci -q get dhcp.lan)" ]; then
        [ "$(uci -q get dhcp.lan.dhcpv6)" = "disabled" ] && uci -q set dhcp.lan.dhcpv6='server'
        [ "$(uci -q get dhcp.lan.ra)"     = "disabled" ] && uci -q set dhcp.lan.ra='server'
        [ "$(uci -q get dhcp.lan.ndp)"    = "disabled" ] && uci -q set dhcp.lan.ndp='hybrid'
    fi

    if [ -x /etc/init.d/odhcpd ]; then
        /etc/init.d/odhcpd enable >/dev/null 2>&1
        /etc/init.d/odhcpd start  >/dev/null 2>&1
        log_info "re-enabled odhcpd"
    fi
    log_info "reverted $_changed uci ipv6 marker(s)"
}

reload_services() {
    uci -q commit network
    uci -q commit dhcp
    uci -q commit firewall
    [ "$WG_LAYOUT" = "glinet" ] && uci -q commit wireguard_server
    /etc/init.d/network  reload >/dev/null 2>&1 || true
    /etc/init.d/firewall reload >/dev/null 2>&1 || true
    if [ "$WG_LAYOUT" = "glinet" ] && [ -x /etc/init.d/wireguard_server ]; then
        /etc/init.d/wireguard_server restart >/dev/null 2>&1 || true
        log_info "restarted /etc/init.d/wireguard_server"
    fi
}

PASS=0; FAIL=0
report() {
    _v="$1"; _n="$2"; _d="$3"
    printf '  [%-4s] %-40s %s\n' "$_v" "$_n" "$_d"
    case "$_v" in PASS) PASS=$((PASS+1));; FAIL) FAIL=$((FAIL+1));; esac
}

run_status() {
    PASS=0; FAIL=0
    log_step "audit (iface=$WG_IFACE [$WG_IFACE_SOURCE], layout=$WG_LAYOUT, fw=$FW_BACKEND)"

    if ! is_installed; then
        log_warn "package is NOT installed (missing $HOTPLUG_PATH or $SYSCTL_PATH)"
        log_warn "run '$(basename "$0") install' to apply hardening"
        return $EX_NOT_INSTALLED
    fi

    case "$WG_LAYOUT" in
        netifd)
            if [ -z "$(uci -q get "network.${WG_UCI_SECTION}")" ]; then
                log_warn "package installed for netifd iface '$WG_UCI_SECTION' but that uci section no longer exists"
            fi
            if [ "$(uci -q get "network.${WG_UCI_SECTION}.ipv6")" = "0" ]; then
                report PASS "network.${WG_UCI_SECTION}.ipv6" "0"
            else
                report FAIL "network.${WG_UCI_SECTION}.ipv6" "expected 0"
            fi
            _v6=0
            for _a in $(uci -q get "network.${WG_UCI_SECTION}.addresses"); do
                case "$_a" in *:*) _v6=$((_v6+1));; esac
            done
            if [ "$_v6" -eq 0 ]; then
                report PASS "network.${WG_UCI_SECTION}.addresses" "no IPv6 entries"
            else
                report FAIL "network.${WG_UCI_SECTION}.addresses" "$_v6 IPv6 entries present"
            fi
            ;;
        glinet)
            if [ -z "$(uci -q get "wireguard_server.${WG_UCI_SECTION}")" ]; then
                log_warn "package installed for glinet section '$WG_UCI_SECTION' but that uci section no longer exists"
            fi
            _v6srv="$(uci -q get "wireguard_server.${WG_UCI_SECTION}.address_v6")"
            if [ -z "$_v6srv" ]; then
                report PASS "wireguard_server.${WG_UCI_SECTION}.address_v6" "empty/unset"
            else
                report FAIL "wireguard_server.${WG_UCI_SECTION}.address_v6" "still set: $_v6srv"
            fi
            _bad_peers=0
            for _p in $(list_glinet_peers); do
                for _key in client_ip allowed_ips; do
                    _cur="$(uci -q get "wireguard_server.${_p}.${_key}")"
                    [ -n "$_cur" ] || continue
                    case "$_cur" in *:*) _bad_peers=$((_bad_peers+1));; esac
                done
            done
            if [ "$_bad_peers" -eq 0 ]; then
                report PASS "glinet peer client_ip/allowed_ips" "no IPv6 entries"
            else
                report FAIL "glinet peer client_ip/allowed_ips" "$_bad_peers entry/entries with IPv6"
            fi
            ;;
        *)
            report INFO "WG layout" "unknown -- only host-side checks apply"
            ;;
    esac

    if [ -z "$(uci -q get network.globals.ula_prefix)" ]; then
        report PASS "network.globals.ula_prefix" "cleared"
    else
        report FAIL "network.globals.ula_prefix" "still set"
    fi

    _dev="$(iface_dev)"
    if [ -n "$_dev" ] && [ -d "/sys/class/net/$_dev" ]; then
        report INFO "$_dev kernel state" "link present"
        _live=$(ip -6 addr show dev "$_dev" 2>/dev/null | awk '/inet6/ && $2 !~ /^fe80/' | wc -l)
        if [ "$_live" -eq 0 ]; then
            report PASS "live $_dev IPv6 addrs" "none (link-local ignored)"
        else
            report FAIL "live $_dev IPv6 addrs" "$_live global IPv6 present"
        fi
    else
        report INFO "${_dev:-?} kernel state" "interface not up (config will apply on next ifup)"
    fi

    for opt in dhcpv6 ra ndp; do
        _v="$(uci -q get "dhcp.lan.$opt")"
        if [ "$_v" = "disabled" ]; then
            report PASS "dhcp.lan.$opt" "disabled"
        else
            report FAIL "dhcp.lan.$opt" "expected 'disabled', got '${_v:-unset}'"
        fi
    done

    if [ -x /etc/init.d/odhcpd ]; then
        if /etc/init.d/odhcpd enabled 2>/dev/null; then
            report FAIL "service odhcpd" "still enabled at boot"
        else
            report PASS "service odhcpd" "disabled at boot"
        fi
    fi

    if [ -f "$SYSCTL_PATH" ]; then
        report PASS "sysctl drop-in" "$SYSCTL_PATH"
    else
        report FAIL "sysctl drop-in" "missing"
    fi

    if [ "$(cat /proc/sys/net/ipv6/conf/all/disable_ipv6 2>/dev/null)" = "1" ]; then
        report PASS "ipv6.all.disable_ipv6" "1"
    else
        report FAIL "ipv6.all.disable_ipv6" "expected 1"
    fi

    if [ "$FW_BACKEND" = "fw4" ]; then
        if [ -f "$NFT_PATH" ]; then
            report PASS "fw4 nft drop-in" "$NFT_PATH"
        else
            report FAIL "fw4 nft drop-in" "missing"
        fi
    else
        if [ -f "$FW3_INCLUDE_PATH" ] \
           && [ "$(uci -q get firewall.wg_noipv6_include.path)" = "$FW3_INCLUDE_PATH" ]; then
            report PASS "fw3 firewall include" "$FW3_INCLUDE_PATH"
        else
            report FAIL "fw3 firewall include" "missing or unlinked"
        fi
    fi

    if [ -x "$HOTPLUG_PATH" ]; then
        report PASS "iface hotplug script" "$HOTPLUG_PATH"
    else
        report FAIL "iface hotplug script" "missing or not executable"
    fi
    if [ "$WG_LAYOUT" = "glinet" ]; then
        if [ -x "$NET_HOTPLUG_PATH" ]; then
            report PASS "net hotplug script" "$NET_HOTPLUG_PATH"
        else
            report FAIL "net hotplug script" "missing or not executable"
        fi
    fi

    if [ -x "$WATCHDOG_PATH" ]; then
        if grep -q "$WATCHDOG_PATH" "$CRONTAB_PATH" 2>/dev/null; then
            report PASS "watchdog cron" "$WATCHDOG_PATH"
        else
            report FAIL "watchdog cron" "$WATCHDOG_PATH present but no cron entry"
        fi
    fi

    printf '\n[%s] summary: %s PASS / %s FAIL\n' "$TAG" "$PASS" "$FAIL"
    if [ "$FAIL" -gt 0 ]; then
        log_warn "drift detected ($FAIL fail)"
        return $EX_DRIFT
    fi
    log_info "all checks passed"
    return $EX_OK
}

run_install() {
    log_step "install (iface=$WG_IFACE [$WG_IFACE_SOURCE], layout=$WG_LAYOUT, watchdog=$INSTALL_WATCHDOG, fw=$FW_BACKEND)"

    _dev="$(iface_dev)"

    # Preflight summary -- surface exactly what state we're acting on.
    if wg_iface_configured; then
        case "$WG_LAYOUT" in
            netifd) log_info "preflight: network.${WG_UCI_SECTION} is a wireguard interface" ;;
            glinet) log_info "preflight: wireguard_server.${WG_UCI_SECTION} is a 'servers' section" ;;
        esac
    else
        log_warn "preflight: $WG_IFACE has no recognised WireGuard uci section"
        log_warn "preflight: host-side IPv6 disablement will still apply now;"
        log_warn "preflight: WG-iface-specific pinning will activate once you add"
        log_warn "preflight: the wireguard config and re-run install."
    fi
    if [ -d "/sys/class/net/$_dev" ]; then
        log_info "preflight: $_dev is up in the kernel"
    else
        log_info "preflight: $_dev is not currently up; hotplug will pin on next ifup"
    fi

    log_step "1/8 backup uci"                     ; backup_uci
    log_step "2/8 clear ipv6 globals"             ; apply_globals
    log_step "3/8 disable v6 on lan/wan/guest"    ; apply_disable_v6_on_ifaces
    log_step "4/8 pin $WG_IFACE to ipv4"          ; apply_pin_wg_ipv4
    log_step "5/8 disable dhcpv6/ra/ndp + odhcpd" ; apply_dhcp
    log_step "6/8 sysctl drop-in"                 ; apply_sysctl
    if [ "$FW_BACKEND" = "fw4" ]; then
        log_step "7/8 firewall (fw4)"             ; apply_firewall_fw4
    else
        log_step "7/8 firewall (fw3)"             ; apply_firewall_fw3
    fi

    log_step "8/8 hotplug + watchdog"
    apply_hotplug
    if [ "$WG_LAYOUT" = "glinet" ]; then
        apply_net_hotplug
    fi
    if [ "$INSTALL_WATCHDOG" -eq 1 ]; then
        apply_watchdog
    else
        log_info "watchdog skipped (--no-watchdog)"
    fi

    reload_services
    _n="$(strip_live_ipv6 "$_dev")"
    [ "$_n" -gt 0 ] && log_info "removed $_n live IPv6 address(es) from $_dev"
    _p="$(reconcile_kernel_peers_ipv4 "$_dev")"
    [ "$_p" -gt 0 ] && log_info "reconciled $_p kernel peer(s) to IPv4-only AllowedIPs"

    write_persisted_state
    log_info "$APPNAME complete (iface: $WG_IFACE; layout: $WG_LAYOUT; backup: $BACKUP_DIR; logs: $LOG_FILE; logread -e $TAG)"
    return 0
}

run_uninstall() {
    log_step "uninstall (iface=$WG_IFACE, layout=$WG_LAYOUT)"
    [ -n "$RESTORE_DIR" ] && { log_step "restore uci backup from $RESTORE_DIR"; restore_backup; }

    log_step "1/5 remove hotplug"            ; remove_hotplug
    log_step "2/5 remove watchdog + cron"    ; remove_watchdog
    log_step "3/5 remove firewall block"     ; remove_firewall
    log_step "4/5 remove sysctl drop-in"     ; remove_sysctl
    log_step "5/5 restore uci ipv6 defaults" ; restore_uci_defaults

    reload_services
    clear_persisted_state

    log_info "$APPNAME uninstall complete; IPv6 stack restored (logs: $LOG_FILE; logread -e $TAG)"
    return 0
}

press_enter() {
    printf '\nPress Enter to continue... '
    read -r _ </dev/tty 2>/dev/null || true
}

wizard_override_iface() {
    _netifd="$(list_netifd_wg_ifaces)"
    _glsrvs="$(list_glinet_servers)"
    _kifaces="$(wg_kernel_ifaces)"
    printf '\nDetected WireGuard configurations:\n'
    if [ -n "$_netifd" ]; then
        printf '  netifd (/etc/config/network):\n'
        for _i in $_netifd; do
            if netifd_iface_looks_like_server "$_i"; then
                printf '    %s  (looks like server)\n' "$_i"
            else
                printf '    %s\n' "$_i"
            fi
        done
    fi
    if [ -n "$_glsrvs" ]; then
        printf '  glinet (/etc/config/wireguard_server):\n'
        for _s in $_glsrvs; do
            _v6="$(uci -q get "wireguard_server.${_s}.address_v6")"
            _port="$(uci -q get "wireguard_server.${_s}.port")"
            printf '    %s  (port=%s%s)\n' "$_s" "${_port:-?}" \
                "$( [ -n "$_v6" ] && printf ' v6=%s' "$_v6" )"
        done
    fi
    if [ -n "$_kifaces" ]; then
        printf '  kernel (wg show interfaces):\n'
        for _k in $_kifaces; do printf '    %s\n' "$_k"; done
    fi
    if [ -z "$_netifd" ] && [ -z "$_glsrvs" ] && [ -z "$_kifaces" ]; then
        printf '  (none)\n'
    fi
    printf 'Enter kernel device name (blank to re-auto-detect): '
    if ! read -r _new </dev/tty 2>/dev/null; then printf '\n'; return 0; fi
    if [ -z "$_new" ]; then
        WG_IFACE=""; WG_LAYOUT="unknown"
        WG_UCI_CONFIG=""; WG_UCI_SECTION=""
        if resolve_iface_for_audit; then
            log_info "re-detected iface: $WG_IFACE ($WG_IFACE_SOURCE, layout=$WG_LAYOUT)"
        else
            log_warn "could not auto-detect a WireGuard server interface"
        fi
        return 0
    fi
    if ! iface_is_valid "$_new"; then
        log_err "invalid interface name '$_new' (allowed: [A-Za-z][A-Za-z0-9_-]*)"
        return 1
    fi
    classify_iface "$_new"
    WG_IFACE_SOURCE="cli"
    log_info "interface override accepted: $WG_IFACE (layout=$WG_LAYOUT)"
}

# --- Wizard state helpers (read-only; safe to call on every menu loop) ---

# Re-resolve $WG_IFACE on every menu iteration unless user pinned it via
# --iface / wizard option 6. Lets the panel reflect external changes
# (uninstall clears persisted state, install creates one, etc.).
wizard_refresh_state() {
    if [ "$WG_IFACE_SOURCE" != "cli" ]; then
        WG_IFACE=""; WG_LAYOUT="unknown"
        WG_UCI_CONFIG=""; WG_UCI_SECTION=""
        WG_IFACE_SOURCE="unset"
        resolve_iface_for_audit >/dev/null 2>&1 || true
    fi
}

# echo: not-installed | partial | installed
wizard_install_state_keyword() {
    _present=0
    [ -e "$HOTPLUG_PATH" ] && _present=$((_present+1))
    [ -f "$SYSCTL_PATH" ]  && _present=$((_present+1))
    if [ "$FW_BACKEND" = "fw4" ]; then
        [ -f "$NFT_PATH" ] && _present=$((_present+1))
    else
        [ -f "$FW3_INCLUDE_PATH" ] && _present=$((_present+1))
    fi
    if   [ "$_present" -eq 0 ]; then printf 'not-installed\n'
    elif [ "$_present" -lt 3 ]; then printf 'partial\n'
    else                              printf 'installed\n'
    fi
}

# echo: enabled | binary-only | cron-only | disabled
wizard_watchdog_state_keyword() {
    _f=0; _c=0
    [ -x "$WATCHDOG_PATH" ] && _f=1
    if [ -f "$CRONTAB_PATH" ] && grep -q -F "$WATCHDOG_PATH" "$CRONTAB_PATH"; then
        _c=1
    fi
    if   [ "$_f" -eq 1 ] && [ "$_c" -eq 1 ]; then printf 'enabled\n'
    elif [ "$_f" -eq 1 ];                    then printf 'binary-only\n'
    elif [ "$_c" -eq 1 ];                    then printf 'cron-only\n'
    else                                          printf 'disabled\n'
    fi
}

wizard_iface_kernel_state() {
    if [ -z "$WG_IFACE" ]; then printf 'n/a (no iface selected)\n'; return; fi
    _dev="$(iface_dev)"
    if [ ! -d "/sys/class/net/$_dev" ]; then
        printf 'down (kernel device %s not present)\n' "$_dev"
        return
    fi
    _live=$(ip -6 addr show dev "$_dev" 2>/dev/null \
            | awk '/inet6/ && $2 !~ /^fe80/' | wc -l)
    printf 'up (%s; %d global IPv6 address(es))\n' "$_dev" "$_live"
}

wizard_sysctl_state() {
    if [ -f "$SYSCTL_PATH" ]; then
        _v="$(cat /proc/sys/net/ipv6/conf/all/disable_ipv6 2>/dev/null)"
        if [ "$_v" = "1" ]; then
            printf 'drop-in present (kernel: all.disable_ipv6 = 1)\n'
        else
            printf 'drop-in present (kernel: all.disable_ipv6 = %s -- DRIFT)\n' "${_v:-?}"
        fi
    else
        printf 'drop-in absent\n'
    fi
}

wizard_wg_summary() {
    _netifd="$(list_netifd_wg_ifaces)"
    _glsrvs="$(list_glinet_servers)"
    _kifaces="$(wg_kernel_ifaces)"
    _line=""
    if [ -n "$_netifd" ]; then
        for _i in $_netifd; do
            if netifd_iface_looks_like_server "$_i"; then
                _line="${_line:+$_line, }${_i} (netifd-server)"
            else
                _line="${_line:+$_line, }${_i} (netifd)"
            fi
        done
    fi
    if [ -n "$_glsrvs" ]; then
        for _s in $_glsrvs; do
            _line="${_line:+$_line, }${_s} (glinet-server)"
        done
    fi
    if [ -z "$_line" ] && [ -n "$_kifaces" ]; then
        for _k in $_kifaces; do
            _line="${_line:+$_line, }${_k} (kernel-only)"
        done
    fi
    if [ -z "$_line" ]; then
        printf 'NONE configured (no proto=wireguard sections, no /etc/config/wireguard_server, no kernel wg ifaces)\n'
        return
    fi
    printf '%s\n' "$_line"
}

wizard_suggest_action() {
    if [ -z "$WG_IFACE" ]; then
        if [ -z "$(list_netifd_wg_ifaces)" ] \
           && [ -z "$(list_glinet_servers)" ] \
           && [ -z "$(wg_kernel_ifaces)" ]; then
            printf 'configure WireGuard on this router first, then return here.'
        else
            printf '6 (choose / re-detect interface) -- pick one of the detected candidates.'
        fi
        return
    fi
    case "$(wizard_install_state_keyword)" in
        not-installed)
            printf '1 (install with watchdog) -- package is not installed yet.'; return ;;
        partial)
            printf '1 (install with watchdog) -- partial install detected; re-apply to complete.'; return ;;
    esac
    if ! wg_iface_configured; then
        printf '6 (re-detect) -- selected iface "%s" is no longer a wireguard uci section.' "$WG_IFACE"
        return
    fi
    case "$(wizard_watchdog_state_keyword)" in
        enabled|disabled) ;;
        *) printf '7 (toggle watchdog) -- watchdog is in a mixed state.'; return ;;
    esac
    printf '3 (status audit) -- package looks installed; run audit to verify no drift.'
}

# --- Wizard action handlers -----------------------------------------------

wizard_pick_backup() {
    if [ ! -d "$BACKUP_ROOT" ]; then
        log_warn "no backup directory at $BACKUP_ROOT"
        return 1
    fi
    _i=0
    _list=""
    for _d in "$BACKUP_ROOT"/backup-*; do
        [ -d "$_d" ] || continue
        _i=$((_i+1))
        _list="${_list}${_i} ${_d}
"
    done
    if [ "$_i" -eq 0 ]; then
        log_warn "no backups found under $BACKUP_ROOT"
        return 1
    fi
    printf '\nAvailable UCI backups:\n'
    printf '%s' "$_list" | awk '{ printf "  %s) %s\n", $1, $2 }'
    printf 'Pick a backup [1-%s] (blank to cancel): ' "$_i"
    if ! read -r _pick </dev/tty 2>/dev/null; then printf '\n'; return 1; fi
    [ -z "$_pick" ] && return 1
    case "$_pick" in
        ''|*[!0-9]*) log_err "invalid choice"; return 1 ;;
    esac
    if [ "$_pick" -lt 1 ] || [ "$_pick" -gt "$_i" ]; then
        log_err "out of range"; return 1
    fi
    RESTORE_DIR="$(printf '%s' "$_list" | awk -v n="$_pick" '$1==n {print $2}')"
    log_info "selected restore: $RESTORE_DIR"
    return 0
}

wizard_watchdog_toggle() {
    case "$(wizard_watchdog_state_keyword)" in
        enabled)
            remove_watchdog ;;
        disabled)
            if [ -z "$WG_IFACE" ]; then
                log_err "no iface selected; use option 6 first"
                return 1
            fi
            apply_watchdog ;;
        *)
            log_warn "watchdog is in mixed state ($(wizard_watchdog_state_keyword))"
            log_warn "removing both binary and cron entry to reset, then re-applying"
            remove_watchdog
            if [ -n "$WG_IFACE" ]; then apply_watchdog; fi
            ;;
    esac
}

wizard_strip_live() {
    if [ -z "$WG_IFACE" ]; then
        log_err "no iface selected; use option 6 first"
        return 1
    fi
    _dev="$(iface_dev)"
    if [ ! -d "/sys/class/net/$_dev" ]; then
        log_warn "kernel device $_dev not present; nothing to strip"
        return 0
    fi
    _n="$(strip_live_ipv6 "$_dev")"
    log_info "stripped $_n live IPv6 address(es) from $_dev"
}

wizard_diagnostics() {
    printf '\n--- WG layout/iface ---\n'
    printf '  layout    : %s\n' "$WG_LAYOUT"
    printf '  iface     : %s\n' "${WG_IFACE:-(unset)}"
    printf '  uci       : %s%s\n' \
        "${WG_UCI_CONFIG:-?}" \
        "$( [ -n "$WG_UCI_SECTION" ] && printf '.%s' "$WG_UCI_SECTION" )"

    printf '\n--- kernel sysctl (ipv6.conf.*.disable_ipv6) ---\n'
    for _k in all default lo "$WG_IFACE"; do
        [ -n "$_k" ] || continue
        _f="/proc/sys/net/ipv6/conf/$_k/disable_ipv6"
        if [ -f "$_f" ]; then
            printf '  %-20s = %s\n' "$_k" "$(cat "$_f")"
        fi
    done

    if [ -n "$WG_IFACE" ]; then
        _dev="$(iface_dev)"
        printf '\n--- ip -6 addr show dev %s ---\n' "$_dev"
        if [ -d "/sys/class/net/$_dev" ]; then
            ip -6 addr show dev "$_dev" 2>&1 | sed 's/^/  /'
        else
            printf '  (device %s not present in kernel)\n' "$_dev"
        fi
    fi

    printf '\n--- firewall (%s) ---\n' "$FW_BACKEND"
    if [ "$FW_BACKEND" = "fw4" ]; then
        if command -v nft >/dev/null 2>&1; then
            _chains="$(nft list chains 2>/dev/null | grep -E 'wg_noipv6_')"
            if [ -n "$_chains" ]; then
                printf '%s\n' "$_chains" | sed 's/^/  /'
            else
                printf '  (no wg_noipv6_* chains loaded into kernel)\n'
            fi
        else
            printf '  (nft binary missing)\n'
        fi
        if [ -f "$NFT_PATH" ]; then
            printf '  drop-in : %s\n' "$NFT_PATH"
        else
            printf '  drop-in : (absent)\n'
        fi
    else
        if command -v ip6tables >/dev/null 2>&1 && [ -n "$WG_IFACE" ]; then
            _rules="$(ip6tables -S 2>/dev/null | grep -E "$WG_IFACE")"
            if [ -n "$_rules" ]; then
                printf '%s\n' "$_rules" | sed 's/^/  /'
            else
                printf '  (no ip6tables rules referencing %s)\n' "$WG_IFACE"
            fi
        fi
        if [ -f "$FW3_INCLUDE_PATH" ]; then
            printf '  include : %s\n' "$FW3_INCLUDE_PATH"
        else
            printf '  include : (absent)\n'
        fi
    fi

    printf '\n--- watchdog ---\n'
    if [ -x "$WATCHDOG_PATH" ]; then
        printf '  binary : %s\n' "$WATCHDOG_PATH"
    else
        printf '  binary : (absent)\n'
    fi
    if [ -f "$CRONTAB_PATH" ] && grep -q -F "$WATCHDOG_PATH" "$CRONTAB_PATH"; then
        printf '  cron   : %s\n' "$(grep -F "$WATCHDOG_PATH" "$CRONTAB_PATH")"
    else
        printf '  cron   : (no entry in %s)\n' "$CRONTAB_PATH"
    fi

    printf '\n--- backups under %s ---\n' "$BACKUP_ROOT"
    if [ -d "$BACKUP_ROOT" ]; then
        _b=0
        for _d in "$BACKUP_ROOT"/backup-*; do
            if [ -d "$_d" ]; then
                printf '  %s\n' "$_d"
                _b=$((_b+1))
            fi
        done
        [ "$_b" -eq 0 ] && printf '  (none yet)\n'
    else
        printf '  (no backup root)\n'
    fi

    printf '\n--- log file ---\n'
    if [ -f "$LOG_FILE" ]; then
        printf '  %s (%s bytes)\n' "$LOG_FILE" \
               "$(wc -c < "$LOG_FILE" 2>/dev/null | tr -d ' ')"
    else
        printf '  (no log file at %s yet)\n' "$LOG_FILE"
    fi
}

wizard_log_tail() {
    if [ -f "$LOG_FILE" ]; then
        printf '\n--- last 30 lines of %s ---\n' "$LOG_FILE"
        tail -n 30 "$LOG_FILE" 2>/dev/null | sed 's/^/  /'
    else
        log_warn "no log file at $LOG_FILE yet"
    fi
}

run_wizard() {
    while :; do
        wizard_refresh_state

        case "$(wizard_install_state_keyword)" in
            installed)     _inst_h="installed (all 3 core components present)" ;;
            partial)       _inst_h="partial (some components missing -- option 1 will repair)" ;;
            not-installed) _inst_h="not installed" ;;
        esac

        printf '\n=========================================================\n'
        printf '%s -- Wizard\n' "$APPNAME"
        printf '=========================================================\n'

        printf '\nWireGuard\n'
        printf '  Detected         : %s\n' "$(wizard_wg_summary)"
        if [ -n "$WG_IFACE" ]; then
            printf '  Selected iface   : %s  [%s, layout=%s]\n' \
                "$WG_IFACE" "$WG_IFACE_SOURCE" "$WG_LAYOUT"
            if [ -n "$WG_UCI_SECTION" ]; then
                printf '  UCI section      : %s.%s\n' \
                    "$WG_UCI_CONFIG" "$WG_UCI_SECTION"
            fi
        else
            printf '  Selected iface   : (none -- use option 6)\n'
        fi
        printf '  Kernel state     : %s\n' "$(wizard_iface_kernel_state)"

        printf '\nHardening package\n'
        printf '  Install state    : %s\n' "$_inst_h"
        printf '  Firewall backend : %s\n' "$FW_BACKEND"
        printf '  Sysctl           : %s\n' "$(wizard_sysctl_state)"
        printf '  Watchdog         : %s\n' "$(wizard_watchdog_state_keyword)"
        if [ -f "$IFACE_FILE" ]; then
            printf '  Persisted iface  : %s -> %s\n' "$IFACE_FILE" \
                   "$(head -n1 "$IFACE_FILE" 2>/dev/null)"
        else
            printf '  Persisted iface  : (none at %s)\n' "$IFACE_FILE"
        fi
        _last="$(latest_backup)"
        if [ -n "$_last" ]; then
            printf '  Last UCI backup  : %s\n' "$_last"
        else
            printf '  Last UCI backup  : (none)\n'
        fi

        printf '\nSuggested next: %s\n' "$(wizard_suggest_action)"

        cat <<'MENU'

Install / Uninstall
  1) Install / re-apply (with watchdog)
  2) Install / re-apply (no watchdog)
  3) Status -- full PASS/FAIL audit
  4) Uninstall -- restore IPv6 defaults
  5) Uninstall + restore from a UCI backup (picker)

Tools
  6) Choose / re-detect WireGuard interface
  7) Toggle watchdog (add or remove only)
  8) Strip live IPv6 from WG device now (one-shot, no install)
  9) Diagnostics -- kernel state, sysctl, firewall rules, log size
 10) Recent log entries (last 30 lines)

  h) Help (full usage)
  q) Quit

MENU
        printf 'Choice [1-10,h,q]: '
        if ! read -r choice </dev/tty 2>/dev/null; then printf '\n'; return 0; fi
        case "$choice" in
            1|2)
                if [ -z "$WG_IFACE" ]; then
                    log_err "no interface selected; use option 6 first"
                    press_enter; continue
                fi
                if [ "$choice" = "1" ]; then INSTALL_WATCHDOG=1; else INSTALL_WATCHDOG=0; fi
                run_install
                press_enter ;;
            3)
                if [ -z "$WG_IFACE" ]; then
                    log_err "no interface selected; use option 6 first"
                    press_enter; continue
                fi
                run_status || true
                press_enter ;;
            4)
                if [ -z "$WG_IFACE" ]; then
                    log_err "no interface selected; use option 6 first"
                    press_enter; continue
                fi
                RESTORE_DIR=""
                run_uninstall
                press_enter ;;
            5)
                if [ -z "$WG_IFACE" ]; then
                    log_err "no interface selected; use option 6 first"
                    press_enter; continue
                fi
                if wizard_pick_backup; then
                    run_uninstall
                fi
                press_enter ;;
            6)   wizard_override_iface;   press_enter ;;
            7)   wizard_watchdog_toggle;  press_enter ;;
            8)   wizard_strip_live;       press_enter ;;
            9)   wizard_diagnostics;      press_enter ;;
            10)  wizard_log_tail;         press_enter ;;
            h|H) usage;                   press_enter ;;
            q|Q) return 0 ;;
            *)   printf 'invalid choice\n' ;;
        esac
    done
}

main() {
    parse_args "$@"
    if [ "$SUBCMD" = "help" ]; then usage; exit 0; fi

    log_init
    log_info "starting $(basename "$0") (subcommand=$SUBCMD)"
    require_root
    require_cmds uci ip logger awk
    detect_fw_backend
    log_info "firewall backend: $FW_BACKEND"

    case "$SUBCMD" in
        install)
            if ! resolve_iface_for_install; then
                _detect_msg="$(detect_wg_iface 2>&1 1>/dev/null || true)"
                if [ -n "$_detect_msg" ]; then
                    printf '%s\n' "$_detect_msg" | while IFS= read -r _line; do
                        log_err "$_line"
                    done
                fi
                log_err "cannot install: WireGuard server interface not determined"
                log_err "next steps: configure WireGuard first, then re-run; or pass --iface NAME"
                exit 1
            fi
            ;;
        uninstall)
            if ! resolve_iface_for_audit; then
                log_warn "no remembered iface and no WireGuard interface detected"
                log_warn "falling back to '$WG_IFACE_DEFAULT' for cleanup"
                WG_IFACE="$WG_IFACE_DEFAULT"
                WG_IFACE_SOURCE="fallback"
                WG_LAYOUT="unknown"
            fi
            ;;
        status)
            if ! resolve_iface_for_audit; then
                log_warn "WireGuard does not appear to be configured on this router"
                log_warn "and no previous install state found at $IFACE_FILE"
                log_warn "hint: pass --iface NAME to inspect a specific interface"
                exit $EX_NO_WG
            fi
            ;;
        wizard)
            resolve_iface_for_audit >/dev/null 2>&1 || true
            ;;
    esac

    [ -n "$WG_IFACE" ] && {
        iface_is_valid "$WG_IFACE" \
            || die "invalid interface name '$WG_IFACE' (allowed: [A-Za-z][A-Za-z0-9_-]*)"
    }
    [ -n "$WG_IFACE" ] && log_info "interface: $WG_IFACE ($WG_IFACE_SOURCE, layout=$WG_LAYOUT)"

    case "$SUBCMD" in
        install)   run_install   ;;
        uninstall) run_uninstall ;;
        status)    run_status; exit $? ;;
        wizard)    run_wizard    ;;
        *)         usage >&2; exit 1 ;;
    esac
    exit 0
}

main "$@"
