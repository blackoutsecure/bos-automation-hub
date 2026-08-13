# Security Policy

## Scope

This policy is the organization-wide default for repositories under the
`blackoutsecure` GitHub organization. It applies to any repo that does
not ship its own `SECURITY.md`. A repo's own copy (at the repo root or
under its `.github/` folder), if present, takes precedence.

## Supported Versions

Security fixes target the default branch of each repository. Older
releases are not back-ported unless the repository explicitly states
otherwise in its own README or release notes.

## Reporting a Vulnerability

Please report security issues **privately** — do not open public
issues, pull requests, or discussions for suspected vulnerabilities.

Preferred channel:

1. Open a **private GitHub Security Advisory** on the affected
   repository (`Security` tab → `Report a vulnerability`).

Fallback channel:

2. If the affected repository does not expose the Security Advisory
   workflow, open one on this `.github` repository instead and
   reference the affected repo + commit / version in the report.

## What to Include

- Affected repository, branch, and commit SHA or release version.
- A clear description of the issue and its impact.
- Steps to reproduce safely (no live exploitation against systems
  you do not own).
- Proof-of-concept details — redact any sensitive data, credentials,
  PII, or third-party information.
- Suggested mitigations, if you have any.

## Response Targets

- Acknowledgement: within **3 business days** of receipt.
- Triage outcome (accepted / needs-more-info / out-of-scope): within
  **10 business days**.
- Fix timeline: communicated after triage based on severity and
  reproducibility.

These are targets, not contractual SLAs.

## Disclosure Policy

We follow a coordinated-disclosure model. Please do not publish or
share details of an unpatched issue while remediation is in progress.
We will agree on a public-disclosure date with you once a fix or
mitigation is available.

## Out of Scope

- Social engineering of organization members.
- Denial-of-service via volumetric or rate-based traffic.
- Findings only reproducible on heavily modified forks.
- Vulnerabilities in third-party services that we merely consume.
