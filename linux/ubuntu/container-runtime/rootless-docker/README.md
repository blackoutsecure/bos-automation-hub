# Rootless Docker

Installs Docker Engine from Docker's official APT repository and configures
it to run in **rootless mode** for a target non-root user on Ubuntu.

Rootless Docker runs the daemon and containers in a user namespace, which
removes the need to add the target user to the privileged `docker` group
and reduces the blast radius of a container escape.

## Script

[`install-rootless-docker.sh`](install-rootless-docker.sh)

## What it does

1. Installs prerequisites (`uidmap`, `dbus-user-session`, `slirp4netns`,
   `fuse-overlayfs`, `iptables`, `curl`, `gpg`).
2. Adds Docker's official APT repository (HTTPS, signed-by a dedicated
   keyring under `/etc/apt/keyrings/`) and installs `docker-ce`,
   `docker-ce-cli`, `containerd.io`, the rootless-extras package, and the
   `buildx` / `compose` plugins.
3. Disables and **masks** the system-wide `docker.service` and
   `docker.socket` so the rootful daemon cannot run.
4. Allocates `subuid`/`subgid` for the target user (if missing) and
   enables systemd lingering so the rootless daemon starts at boot and
   survives logout.
5. Permits unprivileged user namespaces via a sysctl drop-in
   (`kernel.apparmor_restrict_unprivileged_userns=0`) — required on
   Ubuntu 24.04+ AppArmor defaults.
6. Runs `dockerd-rootless-setuptool.sh install` as the target user and
   enables the per-user `docker.service`.
7. Adds `DOCKER_HOST` + `XDG_RUNTIME_DIR` to the user's `~/.profile` so
   `docker` CLI talks to the rootless socket by default, then verifies
   with `docker version`.

## Modes

| Mode | Purpose |
|---|---|
| `apply` (default) | Reconcile the system to the desired state. |
| `--check` / `--status` | Read-only audit. Exit `0` = all PASS, `2` = drift. |

## Usage

### Manual

```bash
sudo bash ./linux/ubuntu/container-runtime/rootless-docker/install-rootless-docker.sh "$USER"
```

### Pin the target user

The user can be passed as the first positional argument or via the
`TARGET_USER` environment variable. It must be a non-root local account
with UID >= 1000.

```bash
sudo ./install-rootless-docker.sh builder
sudo TARGET_USER=builder ./install-rootless-docker.sh
```

### Read-only audit

```bash
sudo ./install-rootless-docker.sh --check
# Exit: 0 = all PASS, 2 = drift detected, 1 = error
```

### Disable privileged-port binding

By default the script grants `cap_net_bind_service` to
`/usr/bin/rootlesskit` so rootless containers can publish ports < 1024.
Pass `--no-privileged-ports` to skip this step.

```bash
sudo ./install-rootless-docker.sh --no-privileged-ports builder
```

### Managed deployment (Ansible / Intune for Linux / Chef / Puppet / Salt)

Run the script as root. Pass the target user via the first positional
argument or `TARGET_USER`. All activity is logged to
`/var/log/install-rootless-docker.log`.

| Code | Meaning |
|---|---|
| `0` | Success (configured or already configured) |
| `1` | Failure (review the log) |
| `2` | Drift detected (only emitted by `--check`) |

## Idempotency

- APT repo, signing key, sysctl drop-in, `subuid`/`subgid` entries,
  lingering, and `~/.profile` lines are all guarded and only added when
  missing.
- `systemctl mask` / `disable` on already-masked units is a no-op.
- The setuptool is invoked with `install --force` only when no prior
  rootless install is detected; otherwise the existing user service is
  just (re)started.

Safe to run multiple times.

## Verification

```bash
sudo ./install-rootless-docker.sh --check
sudo -u <user> XDG_RUNTIME_DIR=/run/user/$(id -u <user>) docker version
sudo -u <user> XDG_RUNTIME_DIR=/run/user/$(id -u <user>) systemctl --user status docker
```

## Security notes

- The script must run as root (EUID 0). The Docker repo is pinned by
  `signed-by=` to a dedicated keyring; the APT key is fetched over HTTPS
  only.
- The target user **must** be a real, non-root local account. Service /
  system accounts (UID < 1000) are rejected.
- The system-wide rootful daemon is disabled **and masked** to prevent
  accidental privileged container execution.
- `kernel.apparmor_restrict_unprivileged_userns=0` is required for
  rootless containers on Ubuntu 24.04+. This loosens an AppArmor
  hardening default; review against your threat model before deploying
  broadly.
- `cap_net_bind_service` is granted to `/usr/bin/rootlesskit` so rootless
  containers can publish ports < 1024. Omit by passing
  `--no-privileged-ports`.

## Copyright

Copyright (c) 2026 [Blackout Secure](https://blackoutsecure.app). Licensed under the Apache 2.0 License.
