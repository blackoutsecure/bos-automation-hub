# Mac App Store

Scripts and documentation for resetting the Mac App Store on macOS when it
hangs on "Loading", will not sign in, gets stuck on a pending download, or
refuses to apply updates.

> Maintained by [Blackout Secure](https://blackoutsecure.app)

## Application

- [Apple — Mac App Store](https://www.apple.com/app-store/)

## Available Scripts

| Script | Description |
|---|---|
| `reset-mac-app-store.sh` | Closes the App Store, kills the Apple `store*` agents, clears per-user App Store caches / preferences / containers, and refreshes the `softwareupdate` catalog |

## How the Reset Script Works

1. Refuses to run as root (apply mode) and confirms it is on macOS, then primes `sudo` once so the later `/var/log` write and `softwareupdate` calls do not re-prompt.
2. Quits `App Store.app` and force-kills `appstoreagent`, `storeaccountd`, `storedownloadd`, and `storeinstalld`.
3. Removes per-user Darwin caches under `$TMPDIR/../C/com.apple.appstore{,agent}`.
4. Deletes per-user defaults for `com.apple.appstore`, `com.apple.appstoreagent`, and `com.apple.storeagent`.
5. Removes the App Store sandbox containers and caches under `~/Library/Containers/` and `~/Library/Caches/`.
6. On macOS 10.x, runs `sudo softwareupdate --clear-catalog` to discard the cached update catalog. On macOS 11+ this flag is deprecated (`Catalog management is no longer supported.`) and is skipped; `sudo softwareupdate --list` is run regardless to refresh metadata.
7. Prompts the operator to restart the Mac so macOS can recreate the App Store containers cleanly.

## Modes

| Mode | Flag | Behaviour |
|---|---|---|
| Apply (default) | _(no flag)_ | Performs the reset. Requires non-root user; prompts for `sudo` once. |
| Check | `--check` (`--status`) | Read-only audit. Reports whether the environment is ready to run the reset (OS, user, `$HOME`, `$TMPDIR`, required tools, **Full Disk Access**, **macOS major version**), plus an informational snapshot of currently running App Store processes and existing cache/container paths. Never invokes `sudo`. |
| Dry-run | `--dry-run` (`-n`) | Prints every destructive command with `printf %q` quoting without executing it. Never invokes `sudo`. |

## Side Effects

This script is **destructive on the invoking user account** in apply mode:

- The Mac App Store is force-quit; any in-flight download or install is interrupted.
- The user will be **signed out** of the App Store after the containers are recreated.
- Per-user App Store preferences (`com.apple.appstore`, `com.apple.appstoreagent`, `com.apple.storeagent`) are wiped.
- The system-wide `softwareupdate` catalog cache is cleared (affects all users on the Mac).

Use `--check` to inventory what would be touched, or `--dry-run` to see the exact commands, before running the reset.

## Privilege Model

This script must run as the **logged-in console user**, not via `sudo bash …`:

- `~/Library/...`, `$TMPDIR`, and `defaults` operations are per-user; if launched as root they would resolve to `/var/root` and silently miss the real target.
- In apply mode, `sudo` is primed once up front and reused by the `/var/log` write and the two `softwareupdate` calls, so the operator is prompted at most once.
- `--check` and `--dry-run` are pure read-only and never invoke `sudo`.

## macOS Permissions (Full Disk Access)

Removing `~/Library/Containers/com.apple.appstore` and `~/Library/Containers/com.apple.appstoreagent` is protected by macOS TCC (App Sandbox container privacy). The terminal app that invokes this script needs **Full Disk Access**:

1. Open **System Settings → Privacy & Security → Full Disk Access**.
2. Add (or toggle on) the terminal you are using — Terminal.app, iTerm2, Ghostty, Warp, the MDM script runner, etc.
3. **Quit and reopen** the terminal so the new permission takes effect.
4. Re-run the script.

Without Full Disk Access the script reports a clear `WARN: cannot remove ... -- macOS denied access (TCC).` for each blocked container and **continues**. Caches, preferences, and processes are still reset, but the user may remain signed in to the App Store because the sandbox container survives.

## Deployment

### MDM (Intune / Company Portal, Jamf, Kandji, Mosyle, Workspace ONE)

Deploy `reset-mac-app-store.sh` as a **user-context** managed shell script so `$HOME`, `$TMPDIR`, and `defaults` resolve to the target user. The MDM agent must be able to invoke `sudo` non-interactively (apply mode only) for the `softwareupdate` and `/var/log` steps.

- All apply-mode activity is logged to `/var/log/resetmacappstore.log`.
- Monitor the exit code in your MDM console:
  - `0` — success (reset completed, or `--dry-run` completed, or `--check` found no drift)
  - `1` — failure (review log for details)
  - `2` — drift detected (only emitted by `--check`)

### Manual

```bash
# Read-only audit -- environment readiness plus inventory of what would be touched.
bash ./macos/application-management/mac-app-store/reset-mac-app-store.sh --check

# Dry run -- print every destructive command without executing it.
bash ./macos/application-management/mac-app-store/reset-mac-app-store.sh --dry-run

# Apply.
bash ./macos/application-management/mac-app-store/reset-mac-app-store.sh
```

## Copyright

Copyright (c) 2026 [Blackout Secure](https://blackoutsecure.app). Licensed under the Apache 2.0 License.
