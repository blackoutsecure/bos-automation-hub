# /bin/sh → bash

Repoints `/bin/sh` from `dash` to `bash` on Ubuntu / Debian systems by
reconfiguring the `dash` package non-interactively (equivalent to
answering "No" to the `dpkg-reconfigure dash` prompt).

Some workloads, vendor install scripts, and legacy shell code rely on
bash-isms while still invoking `/bin/sh`. On Ubuntu, `/bin/sh` defaults
to `dash` for boot-time speed and POSIX strictness, which breaks those
scripts. This script flips `/bin/sh` → `bash` and verifies the change.

## Script

[`configure-sh-to-bash.sh`](configure-sh-to-bash.sh)

## What it does

1. Verifies whether `/bin/sh` already resolves to `bash` and skips
   reconfiguration when it does.
2. Sets the debconf selection `dash dash/sh boolean false` and runs
   `dpkg-reconfigure -f noninteractive dash`, which re-points `/bin/sh`
   → `bash`.
3. Verifies the change with `readlink /bin/sh`,
   `sh -c 'echo $BASH_VERSION'`, and `debconf-show dash`.

No reboot is required. Already-running shells keep their existing
`/bin/sh`; new shells pick up `bash` immediately.

## Modes

| Mode | Purpose |
|---|---|
| `apply` (default) | Reconcile the system to the desired state. |
| `--check` / `--status` | Read-only audit. Exit `0` = all PASS, `2` = drift. |

## Usage

### Manual

```bash
sudo bash ./linux/ubuntu/system-configuration/sh-to-bash/configure-sh-to-bash.sh
```

### Read-only audit

```bash
sudo ./configure-sh-to-bash.sh --check
# Exit: 0 = all PASS, 2 = drift detected, 1 = error
```

### Managed deployment (Ansible / Intune for Linux / Chef / Puppet / Salt)

Deploy as a one-shot configuration script executed as root. All activity
is logged to `/var/log/configure-sh-to-bash.log`.

| Code | Meaning |
|---|---|
| `0` | Success (configured or already configured) |
| `1` | Failure (review the log) |
| `2` | Drift detected (only emitted by `--check`) |

## Idempotency

If `/bin/sh` already resolves to `bash` the `dpkg-reconfigure` step is
skipped. Verification still runs every time. Safe to run multiple times.

## Verification

```bash
ls -l /bin/sh                       # /bin/sh -> bash
readlink /bin/sh                    # bash
sh -c 'echo $BASH_VERSION'          # non-empty
sudo debconf-show dash | grep dash/sh
    # * dash/sh: false   => /bin/sh is bash (correct)
```

## Reverting

```bash
sudo dpkg-reconfigure dash
# Answer "Yes" to "Use dash as the default system shell"
```

Or non-interactively:

```bash
printf 'dash dash/sh boolean true\ndash dash/sh seen true\n' \
    | sudo debconf-set-selections
sudo DEBIAN_FRONTEND=noninteractive dpkg-reconfigure dash
```

## Security notes

- Switching `/bin/sh` to `bash` removes a defence-in-depth layer:
  POSIX-only constructs in vendor scripts will silently start using
  bash extensions. Pin to a tag or commit SHA in production and
  re-audit when bumping.
- The script refuses to run unless `bash` is already installed at
  `/bin/bash`, so a misconfiguration cannot leave the system without a
  working `/bin/sh`.

## Copyright

Copyright (c) 2026 [Blackout Secure](https://blackoutsecure.app). Licensed under the Apache 2.0 License.
