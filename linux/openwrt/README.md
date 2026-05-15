# OpenWrt (including GL.iNet)

Hardening and configuration scripts for OpenWrt 21.02+ and the GL.iNet
firmware family (Mango, Slate, Beryl, Flint, Brume, etc.).

> Maintained by [Blackout Secure](https://blackoutsecure.app)

## Categories

| Category | Folder |
|---|---|
| Network security | [`network-security/`](network-security/) |

## Conventions

- POSIX `sh` (BusyBox `ash`). No bash, no extra packages.
- Subcommand-driven: `install`, `uninstall`, `status` (alias `check`),
  `wizard`, `help`. The default with no subcommand is `wizard` on a TTY and
  `install` when piped (`| sh`).
- Idempotent: re-running a subcommand never makes things worse.
- 3-channel logging: console + `/var/log/<package>.log` + `logger -t <tag>`
  (visible in `logread -e <tag>`).
- Backups: every `install` snapshots affected `/etc/config/*` files into
  `/etc/<package>/backup-<timestamp>/`. `uninstall --restore-backup DIR`
  restores them.
- Each package ships a `files/` tree mirroring the on-router paths so you
  can review or `scp` artifacts without running the kicker.

See the [repository root README](../../README.md) for the broader layout.

## Copyright

Copyright (c) 2026 [Blackout Secure](https://blackoutsecure.app). Licensed under the Apache 2.0 License.
