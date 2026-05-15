# GL.iNet WireGuard IPv4-Only Hardening

> Maintained by [Blackout Secure](https://blackoutsecure.app)

Force a GL.iNet / OpenWrt **WireGuard server** interface into IPv4-only mode
and keep it that way against GUI regeneration, reboots, and `ifup` events.

Two on-disk WireGuard layouts are auto-detected:

- **netifd** — stock OpenWrt: any section in `/etc/config/network` with
  `option proto wireguard`. The section name is the kernel device name
  (e.g. `wg0`). "Server-shaped" means it has `listen_port` set, or has
  peers with no `endpoint_host`.
- **glinet** — GL.iNet 4.x firmware (Mango / Slate / Beryl / Flint /
  AXT1800 etc.): the server lives in `/etc/config/wireguard_server`
  (typically section `main_server` of type `servers`) with peers as
  `peers`-typed sections; the kernel device is brought up by GL.iNet's
  own `wireguard_server` daemon (default name `wgserver`) and is **not**
  represented in `/etc/config/network`.

Pass `--iface NAME` to override (it's the kernel device name, not the
UCI section name). The choice is remembered in `/etc/wg-noipv6/iface`
(plus the layout in `/etc/wg-noipv6/layout` and the UCI section in
`/etc/wg-noipv6/section`) so later `status` / `uninstall` runs target
the same setup even if you rename or rebuild the WireGuard config.

POSIX `sh` (BusyBox `ash`). No bash, no extra packages. Targets GL.iNet
routers (Mango / Slate / Beryl / Flint / AXT1800 / Brume) and generic
OpenWrt 21.02+.

## How to use

Run on the router as root. The kicker is a single subcommand-driven binary:

```sh
sh configure-wireguard-ipv4-only.sh                # interactive wizard (default)
sh configure-wireguard-ipv4-only.sh install        # non-interactive install (with watchdog)
sh configure-wireguard-ipv4-only.sh install --no-watchdog
sh configure-wireguard-ipv4-only.sh status         # PASS/FAIL audit (exit 0 OK, 2 drift)
sh configure-wireguard-ipv4-only.sh uninstall      # remove enforcement; restore IPv6 defaults
sh configure-wireguard-ipv4-only.sh help
```

### One-liners

The documented one-liner drops you straight into the interactive wizard
so you can choose install / status / uninstall from a menu. Pass an
explicit subcommand with `sh -s` for non-interactive use (cron, CI).

```sh
URL=https://raw.githubusercontent.com/blackoutsecure/platform-automation/main/linux/openwrt/network-security/wireguard-ipv4-only/configure-wireguard-ipv4-only.sh

# Default — launches the wizard (works even when piped, because the wizard
# reads from /dev/tty, not stdin)
wget -qO- "$URL" | sh

# Non-interactive install (default subcommand: install with watchdog)
wget -qO- "$URL" | sh -s install
wget -qO- "$URL" | sh -s install --no-watchdog

# Status / uninstall (non-interactive)
wget -qO- "$URL" | sh -s status
wget -qO- "$URL" | sh -s uninstall
```

If `wget` lacks TLS, substitute `curl -fsSL "$URL" | sh` (or
`curl -fsSL "$URL" | sh -s <subcommand>`).

In fully headless contexts (cron, CI, `ssh -T` / non-interactive `ssh
host 'cmd'` without a TTY), there is no `/dev/tty` and the kicker falls
through to a non-interactive `install`.

For production, replace `main` in the URL with a tag (e.g. `v1.0.0`) or a
40-character commit SHA so you get the exact bytes you reviewed.

### Adding WireGuard later

If you run `install` before WireGuard is configured, the script bails
cleanly with exit code `1` and a "configure WireGuard first, then re-run"
hint — nothing on the system is modified. After you set up WireGuard via
the GL.iNet GUI or `uci`, simply re-run `install`. Detection picks up the
new interface, persists the name to `/etc/wg-noipv6/iface`, and applies
the hardening.

Re-running `install` at any time is safe and idempotent: every step
checks the current state first and reports whether it actually changed
anything (e.g. `network.globals.ula_prefix already empty/unset`,
`network.wgserver.addresses already IPv4-only`).

## What it does

1. Auto-detects the WireGuard layout (`netifd` vs `glinet`) and the kernel
   device name (or honours `--iface NAME`); persists the choice to
   `/etc/wg-noipv6/`. Refuses to proceed if detection is ambiguous
   (multiple servers) — pass `--iface NAME`.
2. Clears `network.globals.ula_prefix` and disables IPv6 on `lan` / `wan` /
   `wan6` / `guest`.
3. Layout-specific WG-config pinning:
   - **netifd:** sets `network.<iface>.ipv6=0` and strips IPv6 entries
     from `network.<iface>.addresses`.
   - **glinet:** clears `wireguard_server.<server>.address_v6` and strips
     `:`-bearing entries from each peer's `client_ip` and `allowed_ips`
     (comma-separated lists). Restarts `/etc/init.d/wireguard_server`.
4. Disables DHCPv6 / RA / NDP on `lan` and disables `odhcpd`.
5. Installs a sysctl drop-in disabling IPv6 globally and on the chosen iface.
6. Installs a firewall block for IPv6 on the chosen iface (fw4/nftables when
   present, fw3/ip6tables otherwise — auto-detected).
7. Installs a hotplug script (`/etc/hotplug.d/iface/99-wg-noipv6`) that
   re-applies the IPv4-only state on every ifup of that interface. For
   the **glinet** layout an additional netdev hotplug
   (`/etc/hotplug.d/net/99-wg-noipv6`) is installed because GL.iNet's
   `wireguard_server` daemon brings up the kernel device without firing
   a netifd ifup event.
8. Optionally installs a per-minute cron watchdog (default: enabled) that
   strips any non-link-local IPv6 addresses that reappear on the device.
## Files installed on the router

| Path                                  | Purpose                                |
|---------------------------------------|----------------------------------------|
| `/etc/hotplug.d/iface/99-wg-noipv6`   | Re-pin IPv4-only on the WG iface ifup  |
| `/etc/hotplug.d/net/99-wg-noipv6`     | Same, for kernel netdev add (glinet)   |
| `/etc/nftables.d/99-wg-noipv6.nft`    | fw4 IPv6 drop on the WG iface          |
| `/etc/firewall.wg-noipv6`             | fw3 IPv6 drop include                  |
| `/etc/sysctl.d/99-wg-noipv6.conf`     | Disable IPv6 in the kernel             |
| `/usr/sbin/wg-noipv6-watchdog`        | Cron watchdog (every minute)           |
| `/etc/wg-noipv6/iface`                | Remembered kernel device name          |
| `/etc/wg-noipv6/layout`               | Remembered layout (`netifd`/`glinet`)  |
| `/etc/wg-noipv6/section`              | Remembered UCI section name            |
| `/etc/wg-noipv6/backup-<timestamp>/`  | UCI backup taken on each `install` (network/firewall/dhcp/wireguard_server) |
| `/var/log/wg-noipv6.log`              | Per-run kicker log                     |

The kicker is **self-contained**: every artifact above is emitted inline
from heredocs in the install functions, so a piped `wget … | sh` install
works with no `files/` directory and no runtime fetches from this repo.

## Subcommand reference

| Subcommand          | Purpose                                                 |
|---------------------|---------------------------------------------------------|
| `install`           | Apply hardening. Default fallback when no TTY exists.   |
| `uninstall`         | Remove enforcement; restore IPv6 defaults.              |
| `status`            | Read-only audit. See exit codes below.                  |
| `help`              | Print usage.                                            |

With no subcommand the script launches the interactive wizard whenever a
controlling terminal is reachable (including the documented `wget … |
sh` one-liner) and falls through to non-interactive `install` only in
fully headless contexts.

### The wizard

The wizard reprints a live state panel on every loop, auto-detecting:

- WireGuard configurations across both layouts: netifd interfaces in
  `/etc/config/network` (annotated `(netifd-server)` if listener-shaped,
  otherwise `(netifd)`) and glinet `servers` sections in
  `/etc/config/wireguard_server` (annotated `(glinet-server)`). If
  neither file has a config but `wg show interfaces` lists a kernel
  device, that's reported as `(kernel-only)`.
- The currently selected iface, the layout it belongs to, and how it
  was chosen (`detected` / `persisted` / `cli`).
- Whether the kernel netdev for that iface is up, and how many global
  IPv6 addresses are currently bound to it.
- Install state of the hardening package: `not-installed` / `partial`
  (some core artifacts present) / `installed` (all 3 core artifacts
  present), based on the actual files at `HOTPLUG_PATH`, `SYSCTL_PATH`,
  and the firewall drop-in.
- The active firewall backend (fw4 or fw3).
- Sysctl drop-in presence and the live kernel value of
  `net.ipv6.conf.all.disable_ipv6` (drift-flagged if not `1`).
- Watchdog state: `enabled` / `disabled` / `binary-only` / `cron-only`
  (the last two flag drift between the binary and its crontab entry).
- The persisted iface file at `/etc/wg-noipv6/iface` and the most
  recent UCI backup directory under `/etc/wg-noipv6/`.

It then prints a one-line **Suggested next** action computed from that
state — e.g. *"1 (install with watchdog) — package is not installed yet"*
or *"3 (status audit) — package looks installed; run audit to verify no
drift"*.

The menu has 10 numbered actions:

| # | Action                                                                  |
|---|-------------------------------------------------------------------------|
| 1 | Install / re-apply (with watchdog)                                      |
| 2 | Install / re-apply (no watchdog)                                        |
| 3 | Status — full PASS/FAIL audit                                           |
| 4 | Uninstall — restore IPv6 defaults                                       |
| 5 | Uninstall + restore from a UCI backup (interactive picker)              |
| 6 | Choose / re-detect WireGuard interface                                  |
| 7 | Toggle watchdog (add or remove only — no full re-install)               |
| 8 | Strip live IPv6 from the WG device now (one-shot, no install)           |
| 9 | Diagnostics — kernel sysctl, IPv6 addresses, firewall rules, log size   |
|10 | Recent log entries (last 30 lines of `/var/log/wg-noipv6.log`)          |

`h` reprints the full usage; `q` quits. After each action the panel
re-runs detection so you can verify the change took effect.

| Option                  | Subcommand   | Purpose                                       |
|-------------------------|--------------|-----------------------------------------------|
| `--no-watchdog`         | `install`    | Skip installing the cron watchdog.            |
| `--restore-backup DIR`  | `uninstall`  | Restore `/etc/config/{network,firewall,dhcp,wireguard_server}` from `DIR` (e.g. `/etc/wg-noipv6/backup-20260505-120000`). |
| `--iface NAME`          | any          | Override auto-detection. Kernel device name (not UCI section). GL.iNet default: `wgserver`. |

`status` exit codes (machine-readable):

| Code | Meaning                                                                |
|------|------------------------------------------------------------------------|
| `0`  | Compliant.                                                             |
| `2`  | Drift — artifacts present but at least one check failed.               |
| `3`  | No WireGuard interface configured on this router.                      |
| `4`  | WireGuard configured, but this hardening package is not installed.     |

## Verify

```sh
sh configure-wireguard-ipv4-only.sh status   # see exit codes above
ip -6 addr show dev "$(cat /etc/wg-noipv6/iface)"   # only fe80::/10 should remain
logread -e wg-noipv6 | tail                  # syslog history
tail /var/log/wg-noipv6.log                  # last run's full log
```

## Security notes

- **Audit before piping to a shell.** `wget … | sh` removes your chance to
  read the code first. Prefer the download → `less` → run flow.
- **Pin to a tag or commit SHA in production** — `main` is mutable.
- **Runs as root only.** Refuses to start if `id -u` ≠ 0.
- **Input validation.** `--iface` and any auto-detected interface name are
  restricted to `[A-Za-z][A-Za-z0-9_-]*` before being interpolated into the
  generated hotplug/watchdog/firewall scripts.
- **Detection is conservative.** When multiple WireGuard server candidates
  are present the script refuses to guess and asks for `--iface NAME`.
- **No network egress at runtime.** Once installed, none of the on-router
  artifacts call out to the internet.
- **Backups.** `install` snapshots `/etc/config/{network,firewall,dhcp,wireguard_server}`
  to `/etc/wg-noipv6/backup-<timestamp>/` every run; `uninstall --restore-backup
  DIR` puts them back.
## Manual deployment (no kicker)

If you'd rather deploy the artifacts directly without running the kicker,
open `configure-wireguard-ipv4-only.sh` and copy the heredoc bodies of
`apply_sysctl`, `apply_firewall_fw4` (or `apply_firewall_fw3`),
`apply_hotplug`, and `apply_watchdog` to the matching paths in the table
above. Replace `${WG_IFACE}` with your interface name, then on the router:

```sh
ssh root@router '
  chmod +x /etc/hotplug.d/iface/99-wg-noipv6 /etc/firewall.wg-noipv6 /usr/sbin/wg-noipv6-watchdog 2>/dev/null
  sysctl -p /etc/sysctl.d/99-wg-noipv6.conf
  echo "* * * * * /usr/sbin/wg-noipv6-watchdog" >> /etc/crontabs/root
  /etc/init.d/cron restart
  # fw3 only: register the include so it runs.
  if ! uci -q get firewall.wg_noipv6_include >/dev/null; then
      uci set firewall.wg_noipv6_include=include
      uci set firewall.wg_noipv6_include.path=/etc/firewall.wg-noipv6
      uci set firewall.wg_noipv6_include.reload=1
      uci commit firewall
  fi
  /etc/init.d/firewall reload
'
```

In practice the kicker is much less error-prone — it auto-detects fw4 vs
fw3, picks the right WG interface, takes a UCI backup, and reports drift
via `status`.

## Troubleshooting

- **`status` exits 3.** No `proto=wireguard` interface in `/etc/config/network`,
  no `servers` section in `/etc/config/wireguard_server`, and no kernel
  WireGuard device. Set up WireGuard first (or pass `--iface NAME` to
  inspect a specific one).
- **`status` exits 4.** WireGuard exists but the package isn't installed yet.
  Run `install`.
- **Wizard panel says `Detected: NONE configured` but `wg show interfaces`
  prints something.** Older versions of the script only scanned
  `/etc/config/network`. Re-pull `main` (or your pinned tag) — it now also
  reads `/etc/config/wireguard_server` and falls back to `wg show interfaces`.
- **"multiple WireGuard server candidates".** The script found more than one
  iface that looks like a server. Disambiguate with `--iface NAME` (use the
  kernel device name, e.g. `wgserver` on GL.iNet).
- **Renamed the WG interface after install.** The persisted name in
  `/etc/wg-noipv6/iface` keeps `status` / `uninstall` pointed at the old
  artifacts. Run `uninstall`, then `install` again to retarget.
- **Drift after a GL.iNet GUI change.** The iface hotplug re-pins state on
  the next `ifup`; the netdev hotplug pins on every kernel netdev add
  (glinet); the cron watchdog catches anything else within 60s. `install`
  is idempotent.
- **fw4 vs fw3** is auto-detected (`/sbin/fw4` or `nft` ⇒ fw4).
- **No `logread` output.** BusyBox `logger` requires `syslogd`. The kicker
  also writes `/var/log/wg-noipv6.log` regardless.
- **Watchdog quiet** = nothing to fix. It only logs when it deletes an addr.

## Copyright

Copyright (c) 2026 [Blackout Secure](https://blackoutsecure.app). Licensed under the Apache 2.0 License.
