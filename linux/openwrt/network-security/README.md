# OpenWrt — Network security

Network-layer hardening packages for OpenWrt and GL.iNet routers.

> Maintained by [Blackout Secure](https://blackoutsecure.app)

## Packages

| Package | Purpose |
|---|---|
| [`wireguard-ipv4-only/`](wireguard-ipv4-only/) | Force the WireGuard server interface (`wgserver`) into IPv4-only mode and keep it pinned across reboots, GUI changes, and `ifup` events. |

See each package's `README.md` for usage, one-liners, and the audit /
uninstall workflow. Conventions (subcommands, logging, backups) are
documented in the [parent OpenWrt README](../README.md).

## Copyright

Copyright (c) 2026 [Blackout Secure](https://blackoutsecure.app). Licensed under the Apache 2.0 License.
