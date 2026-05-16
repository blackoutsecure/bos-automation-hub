# Ubuntu

Ubuntu administration scripts and supporting documentation.

> Maintained by [Blackout Secure](https://blackoutsecure.app)

## Structure

```
linux/ubuntu/
  <category>/
    <target>/
      <action>-<target>.sh
      README.md
```

| Category | Folder |
|---|---|
| Application management | [`application-management/`](application-management/) |
| Container runtime | [`container-runtime/`](container-runtime/) |
| Power management | [`power-management/`](power-management/) |
| Storage optimization | [`storage-optimization/`](storage-optimization/) |
| System configuration | [`system-configuration/`](system-configuration/) |

Additional categories (for example `security/`) will be added as scripts are contributed.

## Conventions

- Use `#!/bin/bash` unless a script explicitly requires another shell.
- Quote variable expansions to avoid path/word-splitting issues.
- Return `0` for success and non-zero for failures.
- Prefer `apt-get` (non-interactive friendly) over `apt` in scripts.
- Keep install scripts idempotent where possible.

### `LOG_LEVEL` (optional)

All bash install/configure scripts under this tree honour an opt-in
`LOG_LEVEL` environment variable. The default (`info`) preserves the
existing console output unchanged — the gate only controls the new
`log_debug` / `log_warn` helpers and bash shell tracing.

| `LOG_LEVEL` | `log_debug` | `log_warn` | `set -x` trace | Existing `echo` / `ERROR:` lines |
|---|:---:|:---:|:---:|:---:|
| `trace`           | ✓ | ✓ | ✓ | always |
| `debug`           | ✓ | ✓ | – | always |
| `info` (default)  | – | ✓ | – | always |
| `warn`            | – | ✓ | – | always |
| `error`           | – | – | – | always |

The existing `echo` lines (including `ERROR: ...`) are never suppressed;
the gate is purely additive so MDM / CM pipelines that already parse the
log stream are unaffected.

Examples:

```bash
# Full bash trace on next run (useful for support / post-mortem)
sudo LOG_LEVEL=trace ./install-nodejs.sh

# Surface any opt-in DEBUG output the script emits
sudo LOG_LEVEL=debug ./configure-no-suspend.sh
```

All output continues to be tee'd to the script's log file (`/var/log/<script>.log`).

## Copyright

Copyright (c) 2026 [Blackout Secure](https://blackoutsecure.app). Licensed under the Apache 2.0 License.
