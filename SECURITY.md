# Security Policy

## Supported versions

Only the `main` branch of this repository is actively maintained. Downstream
callers should pin to a specific commit SHA or release tag.

## Reporting a vulnerability

Please report security issues **privately** via GitHub's
[private vulnerability reporting](https://github.com/blackoutsecure/platform-automation/security/advisories/new)
form. Do **not** open a public issue for security reports.

We aim to acknowledge new reports within 5 business days.

## Scope

In scope:

- The reusable workflows in [`.github/workflows/`](.github/workflows/).
- The composite actions in [`.github/actions/`](.github/actions/).
- The example caller workflow in [`examples/`](examples/).

Out of scope:

- Vulnerabilities in upstream actions we depend on (please report to the
  maintaining org). We pin every third-party action to a commit SHA;
  Dependabot opens PRs for new releases.
- Misconfiguration in downstream repositories that consume these workflows.

## Hardening summary

These workflows follow the practices recommended by
[OpenSSF Scorecard](https://github.com/ossf/scorecard) and
[Securing GitHub Actions](https://docs.github.com/en/actions/security-guides/security-hardening-for-github-actions):

- **Pinned actions** — every `uses:` reference is pinned to a 40-char
  commit SHA, with the human-readable version in a trailing comment.
- **Least-privilege tokens** — `permissions: contents: read` at the
  workflow level; jobs that need `contents: write` (only
  `sync-balena-yml`) opt in explicitly.
- **No credential persistence** — every checkout uses
  `persist-credentials: false`, except the one job that needs to push a
  commit back to the default branch.
- **Injection-safe scripts** — every `${{ ... }}` interpolation that
  feeds a shell is routed through an `env:` block; no template
  expansion occurs inside `run:` bodies.
- **Input validation** — image names, tags, slugs, and version strings
  are regex-validated; secrets are checked for stray whitespace before
  use.
- **Masked outputs** — `DOCKERHUB_NAMESPACE` is registered with
  `::add-mask::` before any log line that could echo it.
- **Concurrency safety** — publish jobs use `cancel-in-progress: false`
  to avoid leaving partial releases behind.
- **No `pull_request_target`** — workflows that handle PRs use the
  `pull_request` trigger and never check out untrusted code with
  elevated privileges.
