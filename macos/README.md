# macOS

macOS administration scripts and supporting documentation.

> Maintained by [Blackout Secure](https://blackoutsecure.app)

## Structure

```
macos/
  application-management/
    <application-name>/
      install-<application-name>.sh
      README.md
```

| Category | Folder |
|---|---|
| Application management | [`application-management/`](application-management/) |

## Deployment

Assets here are suitable for:

- MDM automated deployment (Intune / Company Portal, Jamf, Kandji, Mosyle, Workspace ONE)
- Manual execution by administrators

## Conventions

### `LOG_LEVEL` (optional)

All bash install scripts under this tree honour an opt-in `LOG_LEVEL`
environment variable. The default (`info`) preserves the existing
console output unchanged — the gate only controls the new `log_debug` /
`log_warn` helpers and bash shell tracing.

| `LOG_LEVEL` | `log_debug` | `log_warn` | `set -x` trace | Existing `echo` / `ERROR:` lines |
|---|:---:|:---:|:---:|:---:|
| `trace`           | ✓ | ✓ | ✓ | always |
| `debug`           | ✓ | ✓ | – | always |
| `info` (default)  | – | ✓ | – | always |
| `warn`            | – | ✓ | – | always |
| `error`           | – | – | – | always |

The existing `echo` lines (including `ERROR: ...`) are never suppressed;
the gate is purely additive so MDM pipelines that already parse the log
stream are unaffected.

Examples:

```bash
# Full bash trace on next run (useful for MDM support / post-mortem)
sudo LOG_LEVEL=trace ./install-homebrew.sh

# Surface any opt-in DEBUG output the script emits
sudo LOG_LEVEL=debug ./install-plex-media-server.sh
```

All output continues to be tee'd to the script's log file under `/var/log/`.

## Copyright

Copyright (c) 2026 [Blackout Secure](https://blackoutsecure.app). Licensed under the Apache 2.0 License.
