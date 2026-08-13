# Blackout Secure

Blackout Secure delivers expert cybersecurity, cloud, and secure
development solutions backed by 20+ years of Fortune-level
experience. We build safer systems and train stronger teams.

## Focus areas

- Security engineering and architecture
- Cloud security and hardening
- Secure software development practices
- Training and enablement for engineering teams

## Public projects

A selection of what we build in the open under this org:

### GitHub Actions

- [`bos-marketplace-kit`](https://github.com/blackoutsecure/bos-marketplace-kit)
  — composite action + CLI that checks any repo against our
  Marketplace-publishing baseline (community-health files, security
  policy, CodeQL, Dependabot, lint configs, GHAS toggles, secret
  scanning, and supply-chain hygiene).
- [`bos-automation-hub`](https://github.com/blackoutsecure/bos-automation-hub)
  — reusable workflows + managed-file syncs that keep every repo in
  the org consistent (release plumbing, hygiene config, linter
  pipelines).
- [`bos-upstream-watcher`](https://github.com/blackoutsecure/bos-upstream-watcher)
  — watches upstream container projects and opens PRs when new
  versions are released.
- [`bos-sitemap-generator`](https://github.com/blackoutsecure/bos-sitemap-generator)
  — generates and validates `sitemap.xml` files from static-site
  build output.
- [`bos-nginx-config-validator`](https://github.com/blackoutsecure/bos-nginx-config-validator)
  — runs `nginx -t` against a config tree inside a pinned official
  nginx image for CI-friendly validation.

### Docker images

Hardened Docker images for the
[ADS-B / ADS-R / Mode-S](https://en.wikipedia.org/wiki/Automatic_Dependent_Surveillance%E2%80%93Broadcast)
receiver and feeder ecosystem, plus self-hosted CI runners:

- [`docker-readsb`](https://github.com/blackoutsecure/docker-readsb)
  — Mode-S decoder backend.
- [`docker-dump978`](https://github.com/blackoutsecure/docker-dump978)
  — UAT (978 MHz) decoder backend.
- [`docker-tar1090`](https://github.com/blackoutsecure/docker-tar1090)
  — web UI for readsb/dump1090 receivers.
- [`docker-graphs1090`](https://github.com/blackoutsecure/docker-graphs1090)
  — receiver performance graphing.
- [`docker-mlat-hub`](https://github.com/blackoutsecure/docker-mlat-hub)
  — multilateration aggregation hub.
- [`docker-github-runner`](https://github.com/blackoutsecure/docker-github-runner)
  — self-hosted GitHub Actions runner image with full balena +
  per-fleet ARC scaling support.

## How we work in the open

- Every public repo inherits the same security policy, code of
  conduct, contributing guide, and issue / PR templates from this
  `.github` repo. See the [README](../README.md) for the inheritance
  contract.
- Security issues are accepted privately via GitHub Security
  Advisories — never as public issues.
  See <https://github.com/blackoutsecure/.github/security/policy>.
- Production-bound projects publish from a `dev → main` promote
  flow with required reviews and signed-by-SHA action references.

## Get in touch

- Website: <https://blackoutsecure.app>
- Sponsors: <https://github.com/sponsors/blackoutsecure>
- Security policy: <https://github.com/blackoutsecure/.github/security/policy>
- Support guidance: <https://github.com/blackoutsecure/.github/blob/main/SUPPORT.md>
