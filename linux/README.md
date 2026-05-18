# Linux

Linux administration scripts grouped by distribution.

> Maintained by [Blackout Secure](https://blackoutsecure.app)

| Distribution | Folder |
|---|---|
| Ubuntu | [`ubuntu/`](ubuntu/) |
| OpenWrt (incl. GL.iNet) | [`openwrt/`](openwrt/) |

Add new distribution folders (`debian/`, `rhel/`, `fedora/`, …) as needed.
Place truly distro-agnostic scripts here only when they are verified to work
across every distribution under this folder.

See the [repository root README](../README.md) for layout, conventions, and
deployment targets.

## `LOG_LEVEL` (optional)

All bash install/configure scripts under this tree (Ubuntu) honour an
opt-in `LOG_LEVEL` environment variable: `trace`, `debug`, `info`
(default), `warn`, `error`. The default preserves existing console
output exactly; `trace` additionally enables bash shell tracing
(`set -x`) for production debugging. The OpenWrt POSIX-sh scripts have
their own logging (`logger -t ...`) and ignore this variable.

See [`ubuntu/README.md`](ubuntu/README.md#log_level-optional) for the
full level table and examples.

## Copyright

Copyright (c) 2026 [Blackout Secure](https://blackoutsecure.app). Licensed under the Apache 2.0 License.
