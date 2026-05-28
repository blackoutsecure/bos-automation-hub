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

1. **Pre-flight audit** (runs at the start of every `apply` and `--check`). Inspects each install target location and prints a PASS/FAIL report covering: OS, `UTM.app` install, `utmctl` reachability, helper script presence / content / executable bit / ownership, LaunchAgent plist presence / content / ownership. Failing rows include a "(will install)" / "(will replace)" / "(will fix)" hint so you can see — before any disk write — exactly what an apply will change.
2. Renders a helper script at `/usr/local/bin/bos-utm-vm-autostart` that:
   - Resolves `utmctl` from `/usr/local/bin`, `/opt/homebrew/bin`, or `/Applications/UTM.app/Contents/MacOS/`.
   - With `UTM_AUTOSTART_LAUNCH_APP=true` (the default), launches `UTM.app` itself in the background (`open -ga /Applications/UTM.app`) if it isn't already running, then waits up to `UTM_AUTOSTART_BOOT_TIMEOUT` seconds for it to appear in the process list. With `LAUNCH_APP=false`, the helper does *not* launch the app and only waits — `UTM.app` must be the user's Login Item for autostart to work.
   - Resolves the start list using one of three modes — `list` (a fixed set of VM names), `regex` (a POSIX ERE re-evaluated against `utmctl list` at every login), or `auto` (try the explicit list first; fall back to the regex; and — when neither selector was configured AND `UTM_AUTOSTART_DYNAMIC_FALLBACK` is on (the default) — autostart **every** VM `utmctl list` returns). See [Selection mode](#selection-mode--list-regex-or-auto) below.
   - For each VM in order, optionally skips it if its current status is not `stopped`, then calls `utmctl start "<vm name>"` with `UTM_AUTOSTART_DELAY_SECONDS` between starts.
   - Logs every step to `/var/log/bos-utm-vm-autostart.log` (the same file the installer writes to, so every install run and every login-time helper run lands in one place).
   - Supports `bos-utm-vm-autostart --dry-run` to print the resolved start list (with current per-VM status) without invoking `utmctl start`.
   - **Runtime-only** — the helper deliberately does *no* self-integrity / install-state checks. Its only job at login is to start the configured VMs. File / permission integrity is the installer's job (re-run `install-utm-vm-autostart.sh` to repair drift).
3. Renders a system-wide LaunchAgent at `/Library/LaunchAgents/app.blackoutsecure.utm-vm-autostart.plist` that runs the helper at every user login (`RunAtLoad`).
4. Sets ownership (`root:wheel`) and permissions (`0755` helper, `0644` plist) on both files and `plutil -lint`s the plist before exiting.
5. If a user is already logged in at the console, the LaunchAgent is best-effort `launchctl bootstrap`-ed into their GUI domain so changes take effect immediately; otherwise it loads at next login.
6. Re-runs are idempotent: the pre-flight audit shows what's drifted, helper + plist content are byte-compared against the rendered desired state, and only files that actually differ are rewritten. Permissions and ownership are unconditionally re-asserted as defense in depth against external `chmod`/`chown`.

## Why a single helper + one LaunchAgent (instead of one LaunchAgent per VM)

- Easier to maintain — add/remove VMs by editing one env var and re-running the installer; no plists to touch.
- Single load/unload surface — one label, one boot order, one log file.
- Adds a guard for the UTM.app boot race (`pgrep UTM` wait loop), which a raw multi-LaunchAgent approach can't express cleanly.

## Configuration

All configuration is supplied via environment variables read at install
time and baked into the generated helper script. Re-run the installer
to change them.

### Selection mode — `list`, `regex`, or `auto`

The helper picks VMs to start in one of three modes. Set
`UTM_AUTOSTART_MODE` to force `list` or `regex`, or leave it on the
default `auto` to accept either input — or both — and resolve at every
login against the live `utmctl list` output.

| `UTM_AUTOSTART_MODE` value | Behaviour |
|---|---|
| `auto` _(default)_ | Accept `UTM_AUTOSTART_VMS`, `UTM_AUTOSTART_MATCH`, or **both** (this is the only mode that allows both). At every login the helper: **(1)** tries the explicit list first, counting only names that actually exist in `utmctl list`; **(2)** if no list names matched, falls back to the regex; **(3)** if neither selector was configured AND `UTM_AUTOSTART_DYNAMIC_FALLBACK` is `true` (the default), starts **every** VM `utmctl list` returns (`UTM_AUTOSTART_EXCLUDE` is still honored); **(4)** if all three yielded nothing, logs `"Nothing to autostart."` and exits 0. Missing list names are logged individually so you can spot typos. |
| `list` | Force list mode. Requires `UTM_AUTOSTART_VMS` (or a baked-in `defaultvms`). Errors fast if `UTM_AUTOSTART_MATCH` is also set. **Strict** — passes every name to `utmctl start` verbatim; missing names produce a `utmctl` error in the log (no silent skip). |
| `regex` | Force regex mode. Requires `UTM_AUTOSTART_MATCH` to be set. Errors fast if `UTM_AUTOSTART_VMS` is also set. |

When to force a mode instead of using `auto`:

- **`list`** — when you want missing VMs to fail loudly. `auto` silently skips list names that don't exist in `utmctl list` (so a typo just falls through to the regex); `list` mode makes them visible as `utmctl` errors per VM.
- **`regex`** — when you've baked in a `defaultvms` and want to **prevent** it from short-circuiting the regex. In `auto` mode, a non-empty list (including `defaultvms`) is tried first.
- Either forced mode also fails-fast if both env vars are supplied by mistake (CI/MDM ambiguity guard), which `auto` deliberately allows.

There is also a `defaultmode` variable at the top of the installer
(default `auto`) — flip it to `list` or `regex` to bake in the
deployment's preferred mode.

At every helper run, regardless of mode, a debug summary line is
written to the log so you can confirm what was resolved:

```
... | Detected 2 VM(s) for autostart (via regex): prod-api, prod-web
```

In `auto` mode you'll also see one of:

```
... | Auto mode: trying explicit list first (3 candidate name(s) baked in)
... |   auto/list: 'ghost-vm' not present in 'utmctl list' (skipped)
... | Auto mode: explicit list matched 2 existing VM(s).
```

or, on fallback:

```
... | Auto mode: none of the explicit names exist in 'utmctl list'; falling back to regex.
... | Auto mode: trying regex MATCH='^prod-' EXCLUDE='-dev$'
... | Auto mode: regex matched 2 VM(s).
```

or, on total miss:

```
... | No VMs specified/found via either method (explicit list candidates=1, regex MATCH='^nope-' EXCLUDE=''). Nothing to autostart.
```

### Selection inputs

| Variable | Default | Purpose |
|---|---|---|
| `UTM_AUTOSTART_VMS` | _(unset)_ | **Explicit name list.** Newline- or comma-separated VM names, started in the order given. Predictable, version-controlled. Used by `list` mode and by `auto` mode's first-attempt resolution. |
| `UTM_AUTOSTART_MATCH` | _(unset)_ | **Regex.** POSIX ERE tested against the name column of `utmctl list` at every login. Picks up renames and new VMs automatically with no installer re-run. Used by `regex` mode and by `auto` mode's fallback resolution. |
| `UTM_AUTOSTART_EXCLUDE` | _(unset)_ | Optional ERE; names matching this are skipped. Honored in `regex` mode, in `auto` mode's regex fallback, **and** in `auto` mode's dynamic-all fallback (so you can mute noisy VMs without enumerating the rest). Does **not** apply to list resolution. |
| `UTM_AUTOSTART_DYNAMIC_FALLBACK` | `true` | **Auto-mode-only.** When `true` (default) and neither `UTM_AUTOSTART_VMS` nor `UTM_AUTOSTART_MATCH` is set, the helper autostarts **every** VM `utmctl list` returns at each login. Makes a no-config install "just work" while still respecting `UTM_AUTOSTART_EXCLUDE`, `UTM_AUTOSTART_SKIP_RUNNING`, and `UTM_AUTOSTART_USER_EXCLUDE`. Set to `false` if you want an unconfigured install to be an intentional no-op until you wire up `VMS` or `MATCH`. Ignored in `list` / `regex` mode. |

In `auto` mode you may set `UTM_AUTOSTART_VMS` and `UTM_AUTOSTART_MATCH`
simultaneously — the helper will try the explicit list first and fall
back to the regex if the list yielded nothing (see
[Selection mode](#selection-mode--list-regex-or-auto)). Forced `list`
and `regex` modes reject the conflict at install time.

When `UTM_AUTOSTART_MODE=auto` and neither selection env var is set,
the installer first falls back to the `defaultvms` variable defined in
the variables block at the top of the installer (empty by default).
If `defaultvms` is also empty (the out-of-the-box state):

- **Dynamic fallback ON** (`UTM_AUTOSTART_DYNAMIC_FALLBACK=true`, the
  default) — the helper starts **every** VM `utmctl list` returns at
  each login. `UTM_AUTOSTART_EXCLUDE`, `UTM_AUTOSTART_SKIP_RUNNING`,
  and `UTM_AUTOSTART_USER_EXCLUDE` are still honored. This is the
  intended "install once and forget" path.
- **Dynamic fallback OFF** (`UTM_AUTOSTART_DYNAMIC_FALLBACK=false`) —
  the helper logs `"Nothing to autostart."` at every login until a
  selector is provided. Choose this when you want install to be a
  no-op until you've explicitly enumerated the VMs to start.

### Runtime tuning — always honored

| Variable | Default | Purpose |
|---|---|---|
| `UTM_AUTOSTART_DELAY_SECONDS` | `5` | Delay between successive VM starts |
| `UTM_AUTOSTART_BOOT_TIMEOUT` | `60` | Max seconds to wait for `UTM.app` to be running before aborting the run |
| `UTM_AUTOSTART_LAUNCH_APP` | `true` | At login, if `UTM.app` isn't already running, have the helper launch it in the background (`open -ga /Applications/UTM.app`) before the boot-timeout wait. Removes the per-user "Open at Login" prerequisite, so a fresh install autostarts VMs for every user without any per-user setup. Set to `false` for the legacy opt-in behaviour where only users who explicitly added `UTM.app` to their Login Items get autostart. |
| `UTM_AUTOSTART_WAIT_POLL_INTERVAL` | `1` | Seconds between `pgrep` polls while waiting for `UTM.app`. Lower = faster detection (slightly more CPU); higher = less CPU on slow hardware. Must be a positive integer |
| `UTM_AUTOSTART_SKIP_RUNNING` | `true` | Skip VMs whose `utmctl list` status is not `stopped` (true/false). Keeps re-runs safe — you can `launchctl kickstart` the agent without disturbing running VMs |
| `UTM_AUTOSTART_USER_EXCLUDE` | _(unset)_ | Comma- or newline-separated list of macOS usernames whose login should **not** trigger autostart. The helper detects `$(id -un)` early in its run and exits 0 immediately (no UTM.app wait, no `utmctl` call) when the current user is on the list. Useful on shared Macs with guest, kiosk, demo, or service accounts. Usernames are validated at install time (`[a-zA-Z_][a-zA-Z0-9_.-]*`). |
| `UTM_AUTOSTART_OPEN_APP` | `false` | **Install-time only** (not baked into the helper). Controls whether an `apply` run activates the autostart pipeline **immediately** for the active console user, or just stages the files and waits for the next login. <br><br>**`false` (default)** — files-only install. Any stale running LaunchAgent is booted out so new helper code takes over cleanly, but the new LaunchAgent is **not** bootstrapped and UTM.app is **not** launched. The LaunchAgent loads naturally at the next user login (when UTM.app is opened as a Login Item). **No GUI side effects on install** — use this for unattended MDM rollouts and for any flow where surprising the console user with a UTM.app pop-up (and possibly first-run Gatekeeper / permission dialogs) is unacceptable. <br><br>**`true`** — bootstrap the LaunchAgent into the console user's GUI domain now AND `open -a UTM.app` so the autostart chain runs end-to-end without a logout. UTM.app's GUI may appear on the console user's screen. Opt in when you want the install run to take effect immediately. Skipped (logged) if UTM.app isn't installed or no console user is logged in. |

### Choosing which VMs to autostart

From most predictable to most dynamic:

1. **List mode (env var)** — best when you have a small, stable set
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

6. **List with regex fallback** (`auto` mode) — pin a known-good baseline
   of explicitly-named VMs, and let a regex auto-pick up additional VMs
   if the baseline list drifts (e.g. VMs renamed in the UTM GUI, fleet
   roll-out where some Macs don't have the baseline VMs yet):

   ```bash
   # Prefer the explicit baseline; if no baseline names exist in
   # 'utmctl list', fall back to anything matching '^prod-':
   sudo UTM_AUTOSTART_MODE=auto \
        UTM_AUTOSTART_VMS=$'web-vm\napi-vm\ndb-vm' \
        UTM_AUTOSTART_MATCH='^prod-' \
        UTM_AUTOSTART_EXCLUDE='-dev$' \
        bash ./macos/application-management/utm-vm-autostart/install-utm-vm-autostart.sh
   ```

   The audit / `--check` output for this configuration shows both
   sections so you can confirm both inputs are wired:

   ```
   Mode: auto  (try explicit list first; fall back to regex if no list names exist)
   Explicit VMs:
       - web-vm
       - api-vm
       - db-vm
   Include regex: ^prod-
   Exclude regex: -dev$
   ```

At every login, the helper re-runs `utmctl list`, applies the configured
selection (list, regex, or list-then-regex in `auto` mode), and (by
default) skips any VM that isn’t in `stopped` state. There’s no daemon
to restart and no installer re-run when you add or rename VMs in the
UTM GUI.

### Choosing which users to autostart for

The system-wide LaunchAgent triggers at **every** user login on the
Mac. There are two complementary opt-out mechanisms:

1. **`UTM_AUTOSTART_LAUNCH_APP=false` + per-user "Open at Login"** —
   implicit, opt-in per user. With `LAUNCH_APP=false`, the helper
   waits for `UTM.app` but does **not** launch it, so a user who
   hasn't added `/Applications/UTM.app` to their Login Items will
   trigger the LaunchAgent, time out after `UTM_AUTOSTART_BOOT_TIMEOUT`
   seconds, log an error, and exit non-zero. With the default
   `LAUNCH_APP=true` this lever is disabled and **every** user logging
   into the Mac gets VM autostart — use `UTM_AUTOSTART_USER_EXCLUDE`
   below to gate which users participate.
   See [Configuring UTM.app launch](#configuring-utmapp-launch).

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

Sample output (regex mode, `MATCH='^prod-'`,
`SKIP_RUNNING=true`, one VM already running):

```
... | UTM.app detected after 0s; using /Applications/UTM.app/Contents/MacOS/utmctl
... | ==== UTM VM autostart run begin (mode=regex, dry_run=true) ====
... | Detected 2 VM(s) for autostart (via regex): prod-db-01, prod-web-01
... | Skipping (status=started): prod-web-01
... | DRY-RUN would start (status=stopped): prod-db-01
... | ==== UTM VM autostart run end ====
```

Sample output (auto mode with `VMS=web-vm,api-vm` and `MATCH='^prod-'`,
where `web-vm` was renamed in the UTM GUI to `web-vm-new`):

```
... | ==== UTM VM autostart run begin (mode=auto, dry_run=true) ====
... | Auto mode: trying explicit list first (2 candidate name(s) baked in)
... |   auto/list: 'web-vm' not present in 'utmctl list' (skipped)
... | Auto mode: explicit list matched 1 existing VM(s).
... | Detected 1 VM(s) for autostart (via list): api-vm
... | DRY-RUN would start (status=stopped): api-vm
... | ==== UTM VM autostart run end ====
```

## Configuring UTM.app launch

`utmctl` is a thin client that talks to the running `UTM.app` process; it
cannot start VMs on its own, so the helper has to make sure `UTM.app`
is running before issuing any `start` commands.

By default (`UTM_AUTOSTART_LAUNCH_APP=true`), the helper handles this
itself: it runs `open -ga /Applications/UTM.app` at the start of every
login-time invocation, which launches `UTM.app` in the background
(`-g` keeps focus on whatever the user is actually doing) and then
waits for the process to appear. No per-user Login Item configuration
is required.

If you want the legacy opt-in behaviour — where only users who
explicitly added `UTM.app` to their Login Items get autostart —
install with `UTM_AUTOSTART_LAUNCH_APP=false` and configure UTM as a
Login Item once, per user:

`System Settings → General → Login Items → "Open at Login" → +` →
select `/Applications/UTM.app`.

## Files Installed

| Path | Owner | Mode | Purpose |
|---|---|---|---|
| `/usr/local/bin/bos-utm-vm-autostart` | `root:wheel` | `0755` | Helper script run by the LaunchAgent |
| `/Library/LaunchAgents/app.blackoutsecure.utm-vm-autostart.plist` | `root:wheel` | `0644` | System-wide LaunchAgent (runs per-user at login) |
| `/var/log/bos-utm-vm-autostart.log` | `root:wheel` | `0666` | Shared log: installer (apply / `--check` / `--uninstall`) **and** every per-user helper run (`stdout` + helper `log()` lines + LaunchAgent `stderr`). World-writable so the per-user LaunchAgent context can append. |

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

- All installer **and** helper activity is logged to `/var/log/bos-utm-vm-autostart.log`.
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

`--check` runs the same pre-flight audit that `apply` runs (file
locations, content, ownership, permissions) but exits without
writing anything. Exit code `2` means drift was detected; re-run
without `--check` to reconcile. `apply` always shows the audit
first too, so you can see what it's about to change before any
file is written.

## Uninstall

```bash
# Remove the LaunchAgent for the current user (if loaded), then the files.
launchctl bootout "gui/$(id -u)/app.blackoutsecure.utm-vm-autostart" 2>/dev/null || true
sudo rm -f /Library/LaunchAgents/app.blackoutsecure.utm-vm-autostart.plist
sudo rm -f /usr/local/bin/bos-utm-vm-autostart
```

## Copyright

Copyright (c) 2026 [Blackout Secure](https://blackoutsecure.app). Licensed under the Apache 2.0 License.
