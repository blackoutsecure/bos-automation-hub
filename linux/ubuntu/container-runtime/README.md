# Container Runtime

Scripts that install and configure container runtimes on Ubuntu.

> Maintained by [Blackout Secure](https://blackoutsecure.app)

## Structure

```
linux/ubuntu/container-runtime/
  <target>/
    <action>-<target>.sh
    README.md
```

## Available Targets

| Target | Folder |
|---|---|
| Rootless Docker | [`rootless-docker/`](rootless-docker/) |

## Conventions

- Use `#!/bin/bash`.
- Run as root; require `EUID 0` and exit non-zero otherwise.
- Prefer `apt-get` with `DEBIAN_FRONTEND=noninteractive` over interactive `apt`.
- Use the modern APT pattern: dedicated keyring under `/etc/apt/keyrings/`
  referenced via `signed-by=` in the sources list. Do not use `apt-key`.
- Log to `/var/log/<script>.log` and return `0` on success, non-zero on failure.
- Keep installs idempotent (safe to re-run).
- Where the runtime supports it, expose a `--check` (read-only audit) mode
  that exits `2` on drift.

## Copyright

Copyright (c) 2026 [Blackout Secure](https://blackoutsecure.app). Licensed under the Apache 2.0 License.
