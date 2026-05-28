# Sudo Cache

Installs a sudoers drop-in that extends the lifetime of the macOS `sudo`
credential cache, so multi-step automation flows (this repo's installers,
coding-agent driven sessions, MDM scripts, etc.) only need to authenticate
once instead of being interrupted by a password prompt at every `sudo`.

> Maintained by [Blackout Secure](https://blackoutsecure.app)

## Why

macOS `sudo` defaults to a `timestamp_timeout` of `5` minutes. That's
fine for interactive shells but gets in the way when you're chaining
multiple `sudo` commands across a longer session — including:

- Running several of the installers in [`../application-management/`](../application-management/) back-to-back.
- Letting a Copilot / Cline / Aider-style agent drive a series of
  `sudo`-required commands. Coding agents are deliberately blocked
  from supplying passwords (a password sent through a tool call could
  leak into telemetry or training data), so they can't unblock a
  `sudo` prompt mid-run.
- MDM remediation scripts that build on each other within a single
  managed session.

Bumping `timestamp_timeout` once means you `sudo -v` at the start of
the session and every subsequent `sudo` runs unattended for the cache
window.

## Available Scripts

| Script | Description |
|---|---|
| `setup-sudo-cache.sh` | Installs `/etc/sudoers.d/bos-sudo-cache-timeout` containing a single `Defaults timestamp_timeout=<minutes>` line. Validated with `visudo -cf` before install. |

## How the Install Script Works

1. **Pre-flight audit** (runs at the start of every `apply` and `--check`).
   Inspects the drop-in's presence, content, ownership (`root:wheel` — **required**
   by `sudo`), and mode (`0440` — **required** by `sudo`). Prints a PASS/FAIL
   report with "(will install)" / "(will replace)" / "(will fix)" hints so you
   can see what an `apply` will change *before* it touches `/etc/sudoers.d/`.
2. Generates the drop-in content in memory:

   ```
   # Installed by macos/sudo-cache/setup-sudo-cache.sh (BOS automation hub)
   # Do not edit by hand; re-run the installer with BOS_SUDO_CACHE_MINUTES=<n>
   # to change the value, or with --uninstall to remove.
   ...
   Defaults timestamp_timeout=60
   ```

3. Writes the content to a temp file in `/etc/sudoers.d/`, sets ownership
   and mode, **runs `visudo -cf` against it**, and only then atomically
   `mv`s it into place. If `visudo` rejects the content the install
   aborts — a malformed sudoers file can lock you out of `sudo` entirely
   and this script will not let that happen.
4. Re-asserts ownership (`root:wheel`) and mode (`0440`) unconditionally on
   every run as defense in depth — `sudo` silently *ignores* any drop-in
   whose perms are wrong, so we never trust external `chmod` / `chown`.
5. Runs the same pre-flight audit again as a **post-install verification**
   so you get a final PASS/FAIL confirmation in the same log/console output.
6. Re-runs are idempotent: the drop-in is byte-compared against the rendered
   desired state and only rewritten when content actually differs.

## Configuration

All configuration is supplied via environment variables read at install
time and baked into the drop-in. Re-run the installer to change them.

| Variable | Default | Purpose |
|---|---|---|
| `BOS_SUDO_CACHE_MINUTES` | `60` | Value of `timestamp_timeout` in minutes. Special values: `0` = always prompt (cache disabled); `-1` = cache **never expires** until the user logs out (use with care — anyone who can `su` to your account inherits the cached credential). |
| `LOG_LEVEL` | `info` | `trace` \| `debug` \| `info` \| `warn` \| `error` (see [`../README.md`](../README.md#log_level-optional)). |

## Files Installed

| Path | Owner | Mode | Purpose |
|---|---|---|---|
| `/etc/sudoers.d/bos-sudo-cache-timeout` | `root:wheel` | `0440` | Single `Defaults timestamp_timeout=<minutes>` line. Owner and mode are mandatory — `sudo` ignores drop-ins that don't match. |
| `/var/log/bos-sudo-cache.log` | `root:wheel` | `0644` | Install / `--check` / `--uninstall` activity log. |

## Usage

### One-time install (default 60-minute cache)

```bash
sudo bash ./macos/sudo-cache/setup-sudo-cache.sh
```

Output ends with `NEXT STEP: run 'sudo -v' once to prime the cache; ...`.

### Pick a different cache window

```bash
# 4-hour cache window:
sudo BOS_SUDO_CACHE_MINUTES=240 bash ./macos/sudo-cache/setup-sudo-cache.sh
```

### Audit (read-only)

```bash
sudo bash ./macos/sudo-cache/setup-sudo-cache.sh --check
```

Exit codes: `0` = compliant, `2` = drift detected (re-run without
`--check` to reconcile).

### Uninstall

```bash
sudo bash ./macos/sudo-cache/setup-sudo-cache.sh --uninstall
```

Removes `/etc/sudoers.d/bos-sudo-cache-timeout`. macOS reverts to the
default `timestamp_timeout=5` immediately. The log file under
`/var/log/` is left in place as an audit trail.

## Typical Session Workflow

```bash
# 1. Install once (per Mac). Default 60-minute cache is sensible.
sudo bash ./macos/sudo-cache/setup-sudo-cache.sh

# 2. At the start of an automation / agent session, prime the cache:
sudo -v

# 3. Run as many sudo-required commands as you want for the next hour.
#    No password prompts:
sudo bash ./macos/application-management/utm-vm-autostart/install-utm-vm-autostart.sh
sudo bash ./macos/application-management/homebrew/install-homebrew.sh
# ... etc ...

# 4. Optional sanity check that the cache is still hot:
sudo -n true && echo cached || echo expired
```

## Safety Notes

- **`visudo -cf` validates every write.** A malformed `sudoers` file
  causes `sudo` to refuse to run *anything*; this installer's
  visudo-then-mv pattern guarantees the live `/etc/sudoers.d/` never
  contains content that hasn't passed the same syntax check.
- **Drop-in perms are mandatory, not advisory.** `sudo` silently ignores
  any file in `/etc/sudoers.d/` that isn't owned `root:wheel` with mode
  `0440`. This installer re-asserts both on every run so an external
  `chmod` can't quietly disable the timeout.
- **The cache is per-tty by default on macOS.** Each terminal session
  (each `tty`) gets its own cache, so a long timeout in one window
  doesn't unlock `sudo` in another. This is sudo's default behaviour
  (`Defaults timestamp_type=tty`); this installer does not change it.
- **Don't use `BOS_SUDO_CACHE_MINUTES=-1` on unattended / shared Macs.**
  `-1` means the cache lasts until logout. Combined with a screen-lock
  policy that doesn't terminate the session, that effectively makes
  `sudo` passwordless for the rest of the day. Fine for a focused
  workstation session you're actively driving; risky everywhere else.

## Deployment

### MDM (Intune / Company Portal, Jamf, Kandji, Mosyle, Workspace ONE)

Deploy `setup-sudo-cache.sh` as a managed shell script. The installer
respects the standard MDM exit codes:

- `0` — success (installed / already in desired state)
- `1` — failure (review log for details)
- `2` — drift detected (only emitted by `--check`)

Example MDM script body:

```bash
sudo BOS_SUDO_CACHE_MINUTES=120 \
     bash ./macos/sudo-cache/setup-sudo-cache.sh
```

### Manual

```bash
sudo bash ./macos/sudo-cache/setup-sudo-cache.sh
```

## Copyright

Copyright (c) 2026 [Blackout Secure](https://blackoutsecure.app). Licensed under the Apache 2.0 License.
