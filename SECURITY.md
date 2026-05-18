# Security Policy

This repository hosts **public reusable GitHub Actions workflows**, **composite actions**, and **OS administration scripts** consumed by downstream `blackoutsecure` repositories and end hosts. Vulnerabilities here can affect every dependent.

## Supported Versions

| Surface | Supported |
| --- | --- |
| `main` branch (current `HEAD`) | ✅ |
| Pinned commit SHAs / release tags | ✅ — fixes shipped as new releases; old refs remain immutable |
| Forks or rewrites | ❌ |

Consumers should pin reusable workflows by **release tag or commit SHA**, not by mutable branch reference, per [GitHub's security hardening guide](https://docs.github.com/en/actions/security-guides/security-hardening-for-github-actions#using-third-party-actions).

## Reporting a Vulnerability

Please **do not** open a public issue for security vulnerabilities.

Report privately via **[GitHub Security Advisories](https://github.com/blackoutsecure/bos-automation-hub/security/advisories/new)** ("Report a vulnerability"). This delivers the report to maintainers only and provides a coordinated disclosure workflow.

Include:

- Affected workflow / action / script path and ref (`main`, tag, or SHA).
- A minimal reproduction (caller workflow YAML, inputs, expected vs. observed behaviour).
- Impact assessment — does it leak secrets, escalate permissions, or affect downstream consumers?
- Any proposed mitigation.

We aim to:

- Acknowledge within **3 business days**.
- Provide a remediation plan or disposition within **14 days**.
- Publish a patched release and advisory on resolution. Critical issues may also be communicated to known downstream consumers.

## Scope

In-scope examples:

- Secret exposure or log scrubbing bypass in any workflow.
- Privilege escalation via `permissions:` mis-configuration or unsafe `pull_request_target` patterns.
- Command/script injection via untrusted inputs reaching composite actions or shell.
- Supply-chain risk in pinned action versions.
- OS scripts under `linux/` and `macos/` writing world-writable artefacts, downloading unsigned binaries, or running unaudited remote shell.

Out-of-scope examples:

- Vulnerabilities in upstream third-party actions or images. Report those to the respective project; we will bump pins promptly.
- Issues that require attacker-controlled write access to the repo or org variables/secrets.

## Supply-Chain Hardening

- All third-party actions used in reusable workflows are pinned by **commit SHA**, not floating tags.
- [`.github/dependabot.yml`](.github/dependabot.yml) opens weekly PRs to bump action pins.
- [`.github/workflows/lint.yml`](.github/workflows/lint.yml) runs `actionlint` + `shellcheck` on every workflow and script.
- Reusable workflows declare least-privilege `permissions:` at the job level.

## Contact

For non-security questions use [GitHub Issues](https://github.com/blackoutsecure/bos-automation-hub/issues) or [Blackout Secure](https://blackoutsecure.app).
