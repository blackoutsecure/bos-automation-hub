# UTM VM Autostart

Scripts and documentation for auto-starting a fixed list of UTM virtual
machines on macOS at login.

> Maintained by [Blackout Secure](https://blackoutsecure.app)

## Application

- [mac.getutm.app](https://mac.getutm.app/)
- [`utmctl` CLI reference](https://docs.getutm.app/scripting/scripting/#utmctl)

## Available Scripts

| Script | Description |
|---|---|
| `install-utm-vm-autostart.sh` | Installs a helper script in `/usr/local/bin` and a system-wide LaunchAgent that calls `utmctl start` on each configured VM at every user login |

## How the Install Script Works

1. Renders a helper script at `/usr/local/bin/bos-utm-vm-autostart` that:
   - Resolves `utmctl` from `/usr/local/bin`, `/opt/homebrew/bin`, or `/Applications/UTM.app/Contents/MacOS/`.
   - Waits up to `UTM_AUTOSTART_BOOT_TIMEOUT` seconds for `UTM.app` to be running (UTM.app itself is expected to launch at login via its own Login Item — see "Prerequisite" below).
   - Resolves the start list (either a baked-in explicit list or a regex match against `utmctl list` output — see "Choosing which VMs to autostart" below).
   - For each VM in order, optionally skips it if its current status is not `stopped`, then calls `utmctl start "<vm name>"` with `UTM_AUTOSTART_DELAY_SECONDS` between starts.
   - Logs every step to `/tmp/bos-utm-vm-autostart.log`.
   - Supports `bos-utm-vm-autostart --dry-run` to print the resolved start list (with current per-VM status) without invoking `utmctl start`.
2. Renders a system-wide LaunchAgent at `/Library/LaunchAgents/app.blackoutsecure.utm-vm-autostart.plist` that runs the helper at every user login (`RunAtLoad`).
3. Sets ownership (`root:wheel`) and permissions (`0755` helper, `0644` plist) on both files and `plutil -lint`s the plist before exiting.
4. If a user is already logged in at the console, the LaunchAgent is best-effort `launchctl bootstrap`-ed into their GUI domain so changes take effect immediately; otherwise it loads at next login.
5. Re-runs are idempotent: helper + plist content are byte-compared against the rendered desired state and only rewritten on drift.

## Why a single helper + one LaunchAgent (instead of one LaunchAgent per VM)

- Easier to maintain — add/remove VMs by editing one env var and re-running the installer; no plists to touch.
- Single load/unload surface — one label, one boot order, one log file.
- Adds a guard for the UTM.app boot race (`pgrep UTM` wait loop), which a raw multi-LaunchAgent approach can't express cleanly.

## Configuration

All configuration is supplied via environment variables read at install
time and baked into the generated helper script. Re-run the installer
to change them.

### Selection model — set one of:

| Variable | Default | Purpose |
|---|---|---|
| `UTM_AUTOSTART_VMS` | _(unset)_ | **Explicit list.** Newline- or comma-separated VM names, started in the order given. Highest precedence. Predictable, version-controlled. |
| `UTM_AUTOSTART_MATCH` | _(unset)_ | **Dynamic match.** POSIX ERE regex tested against the name column of `utmctl list` at every login. Picks up renames and new VMs automatically with no installer re-run. |
| `UTM_AUTOSTART_EXCLUDE` | _(unset)_ | Optional ERE; dynamic-mode names matching this are skipped (only relevant with `UTM_AUTOSTART_MATCH`). |

When `UTM_AUTOSTART_VMS` is set, the regex variables are ignored. When
neither env var is set, the installer falls back to the `defaultvms`
variable defined in the variables block at the top of the installer
(empty by default). If `defaultvms` is also empty, the installer aborts
with a helpful error rather than silently doing nothing — so you must
either pass an env var at install time **or** edit `defaultvms` once to
bake in a deployment-specific fallback list.

### Runtime tuning — always honored

| Variable | Default | Purpose |
|---|---|---|
| `UTM_AUTOSTART_DELAY_SECONDS` | `5` | Delay between successive VM starts |
| `UTM_AUTOSTART_BOOT_TIMEOUT` | `60` | Max seconds to wait for `UTM.app` to be running before aborting the run |
| `UTM_AUTOSTART_WAIT_POLL_INTERVAL` | `1` | Seconds between `pgrep` polls while waiting for `UTM.app`. Lower = faster detection (slightly more CPU); higher = less CPU on slow hardware. Must be a positive integer |
| `UTM_AUTOSTART_SKIP_RUNNING` | `true` | Skip VMs whose `utmctl list` status is not `stopped` (true/false). Keeps re-runs safe — you can `launchctl kickstart` the agent without disturbing running VMs |
| `UTM_AUTOSTART_USER_EXCLUDE` | _(unset)_ | Comma- or newline-separated list of macOS usernames whose login should **not** trigger autostart. The helper detects `$(id -un)` early in its run and exits 0 immediately (no UTM.app wait, no `utmctl` call) when the current user is on the list. Useful on shared Macs with guest, kiosk, demo, or service accounts. Usernames are validated at install time (`[a-zA-Z_][a-zA-Z0-9_.-]*`). |

### Choosing which VMs to autostart

From most predictable to most dynamic:

1. **Explicit list (env var)** — best when you have a small, stable set
   of VMs you want to manage from source control or your MDM script.

   ```bash
   sudo UTM_AUTOSTART_VMS=$'web-vm\napi-vm\ndb-vm' \
        bash ./macos/application-management/utm-vm-autostart/install-utm-vm-autostart.sh
   ```

2. **Baked-in default list** — if you'd rather not pass env vars on every
   install, edit the `defaultvms` variable in the variables block at the
   top of `install-utm-vm-autostart.sh` once. Any caller that omits
   `UTM_AUTOSTART_VMS` / `UTM_AUTOSTART_MATCH` then gets this list:

   ```bash
   # In install-utm-vm-autostart.sh, change:
   defaultvms=""
   # to, for example:
   defaultvms=$'ubuntu-server-24.04\ndebian-12-dev'
   ```

3. **Name-prefix regex** — great when you use a naming convention. New
   VMs that match the prefix are picked up at the next login.

   ```bash
   # Start every VM whose name begins with "prod-":
   sudo UTM_AUTOSTART_MATCH='^prod-' \
        bash ./macos/application-management/utm-vm-autostart/install-utm-vm-autostart.sh
   ```

4. **Suffix "tag"** — opt-in per VM by renaming it in the UTM GUI. Add a
   marker like ` [autostart]` to any VM you want started; remove the
   marker to take it out without touching the script.

   ```bash
   # Match any VM whose name ends with " [autostart]":
   sudo UTM_AUTOSTART_MATCH=' \[autostart\]$' \
        bash ./macos/application-management/utm-vm-autostart/install-utm-vm-autostart.sh
   ```

5. **All VMs except a denylist** — broad match + exclude:

   ```bash
   # Start every VM except those whose name contains "-dev" or "-scratch":
   sudo UTM_AUTOSTART_MATCH='.*' \
        UTM_AUTOSTART_EXCLUDE='-(dev|scratch)' \
        bash ./macos/application-management/utm-vm-autostart/install-utm-vm-autostart.sh
   ```

At every login, the helper re-runs `utmctl list`, applies the regex,
and (by default) skips any VM that isn’t in `stopped` state. There’s no
daemon to restart and no installer re-run when you add or rename VMs
in the UTM GUI.

### Choosing which users to autostart for

The system-wide LaunchAgent triggers at **every** user login on the
Mac. There are two complementary opt-out mechanisms:

1. **Per-user "Open at Login"** — implicit. A user who doesn't have
   `/Applications/UTM.app` in their Login Items will still trigger the
   LaunchAgent, but the helper will wait `UTM_AUTOSTART_BOOT_TIMEOUT`
   seconds for `UTM.app`, time out, log an error, and exit non-zero.
   See [Prerequisite — UTM.app must launch at login](#prerequisite--utmapp-must-launch-at-login).

2. **`UTM_AUTOSTART_USER_EXCLUDE`** — explicit. A clean, fast no-op for
   accounts you know should never run VMs. The helper checks the
   current username before doing anything else and exits 0 immediately,
   so launchd records a successful run and there's nothing in the log
   beyond a single "Skipping autostart" line.

   ```bash
   # On a shared/family Mac, only run autostart for the admin account;
   # skip the guest, kiosk, and service accounts entirely:
   sudo UTM_AUTOSTART_VMS=$'web-vm\napi-vm' \
        UTM_AUTOSTART_USER_EXCLUDE='guest,kiosk,_mdm' \
        bash ./macos/application-management/utm-vm-autostart/install-utm-vm-autostart.sh
   ```

   ```bash
   # Mix newlines and commas freely; whitespace is trimmed:
   sudo UTM_AUTOSTART_USER_EXCLUDE=$'guest\nkiosk, demo' \
        UTM_AUTOSTART_VMS='my-vm' \
        bash ./macos/application-management/utm-vm-autostart/install-utm-vm-autostart.sh
   ```

   The installer validates each username against the POSIX portable
   filename character set (`[a-zA-Z_][a-zA-Z0-9_.-]*`); invalid names
   (typos, shell metacharacters) fail fast at install time rather than
   getting baked into the helper.

### Previewing what will be started

Once installed, ask the helper exactly what it would do at next login:

```bash
# Reads utmctl list, applies the configured selection, prints decisions,
# does NOT call 'utmctl start':
/usr/local/bin/bos-utm-vm-autostart --dry-run
```

Sample output (dynamic mode, `MATCH='^prod-'`,
`SKIP_RUNNING=true`, one VM already running):

```
... | UTM.app detected after 0s; using /Applications/UTM.app/Contents/MacOS/utmctl
... | ==== UTM VM autostart run begin (mode=dynamic, dry_run=true) ====
... | Skipping (status=started): prod-web-01
... | DRY-RUN would start (status=stopped): prod-db-01
... | ==== UTM VM autostart run end ====
```

## Prerequisite — UTM.app must launch at login

`utmctl` is a thin client that talks to the running `UTM.app` process; it
cannot start VMs on its own. The helper script waits for `UTM.app` to
appear in the process list before issuing any `start` commands.

Configure UTM as a Login Item once, per user:

`System Settings → General → Login Items → "Open at Login" → +` → select
`/Applications/UTM.app`.

## Files Installed

| Path | Owner | Mode | Purpose |
|---|---|---|---|
| `/usr/local/bin/bos-utm-vm-autostart` | `root:wheel` | `0755` | Helper script run by the LaunchAgent |
| `/Library/LaunchAgents/app.blackoutsecure.utm-vm-autostart.plist` | `root:wheel` | `0644` | System-wide LaunchAgent (runs per-user at login) |
| `/tmp/bos-utm-vm-autostart.log` | (login user) | `0644` | Per-run helper log (`stdout` + helper `log()` lines) |
| `/tmp/bos-utm-vm-autostart.err.log` | (login user) | `0644` | LaunchAgent `stderr` |
| `/var/log/installutmvmautostart.log` | `root:wheel` | `0644` | Installer log (apply + check modes) |

## Deployment

### MDM (Intune / Company Portal, Jamf, Kandji, Mosyle, Workspace ONE)

Deploy `install-utm-vm-autostart.sh` as a managed shell script or
custom app install step. The installer ships with no built-in VM list,
so each MDM script must either pass `UTM_AUTOSTART_VMS` /
`UTM_AUTOSTART_MATCH` in the script body, or check in a customised copy
of the installer with its `defaultvms` variable set.

Example MDM script body:

```bash
sudo UTM_AUTOSTART_VMS=$'ubuntu-server-24.04\ndebian-12-dev' \
     bash ./macos/application-management/utm-vm-autostart/install-utm-vm-autostart.sh
```

- All installer activity is logged to `/var/log/installutmvmautostart.log`.
- Monitor the exit code in your MDM console:
  - `0` — success (installed or already in desired state)
  - `1` — failure (review log for details; includes "no VMs configured")
  - `2` — drift detected (only emitted by `--check`)

### Manual

```bash
sudo UTM_AUTOSTART_VMS=$'my-vm-one\nmy-vm-two' \
     bash ./macos/application-management/utm-vm-autostart/install-utm-vm-autostart.sh
```

### Audit (read-only)

```bash
# Pass the same env vars you'd use for an apply run:
sudo UTM_AUTOSTART_VMS=$'my-vm-one\nmy-vm-two' \
     bash ./macos/application-management/utm-vm-autostart/install-utm-vm-autostart.sh --check
```

## Uninstall

```bash
# Remove the LaunchAgent for the current user (if loaded), then the files.
launchctl bootout "gui/$(id -u)/app.blackoutsecure.utm-vm-autostart" 2>/dev/null || true
sudo rm -f /Library/LaunchAgents/app.blackoutsecure.utm-vm-autostart.plist
sudo rm -f /usr/local/bin/bos-utm-vm-autostart
```

## Copyright

Copyright (c) 2026 [Blackout Secure](https://blackoutsecure.app). Licensed under the Apache 2.0 License.
