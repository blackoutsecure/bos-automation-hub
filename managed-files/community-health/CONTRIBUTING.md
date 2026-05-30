# Contributing

Thanks for helping improve the blackoutsecure organization repositories.
This guide is the organization-wide default. Individual repositories
may define their own `CONTRIBUTING.md` that overrides or extends what
is below — when in doubt, the repo's own copy wins.

## Before You Start

- Search existing issues and pull requests in the affected repo.
- For security issues, follow the process in
  [SECURITY.md](SECURITY.md) — never open a public issue or PR for a
  suspected vulnerability.
- Read the repo's `README.md` first; many repos document a specific
  branching strategy, release process, or local-development workflow
  that supersedes the generic guidance here.

## Scope of Changes

- Keep changes focused and easy to review. Prefer multiple small PRs
  over one large one when possible.
- Prefer explicit, readable implementations over clever shortcuts.
- Avoid introducing new runtime dependencies unless necessary.

## Development Standards

- Use clear, descriptive names for functions, variables, and files.
- Validate inputs and avoid hard-coded secrets or credentials.
- Use secure defaults (HTTPS, TLS, encryption at rest where
  applicable, least-privilege tokens / scopes).
- Match the surrounding style of the file you are editing.

## Documentation

- Update the affected repo's `README.md` or other docs when
  user-visible behavior changes.
- Add docstrings or doc-comments for new public modules, classes, or
  functions in the language the repo uses.
- Include usage examples for new APIs, flags, or workflows.

## Tests

- Add or update tests for new or changed behavior.
- Use whichever framework the affected repo already uses (e.g.
  `pytest` for Python, the language's standard runner for Go, Jest /
  Vitest for JavaScript / TypeScript, etc.).
- Keep tests deterministic and self-contained.

## Security Review

- Call out security-sensitive changes explicitly in the PR
  description.
- Avoid logging secrets, tokens, or sensitive payloads — redact when
  needed.
- Redact or anonymize customer data, PII, and third-party
  identifiers in examples and fixtures.

## Pull Requests

- Describe **the problem** and **the solution** — not just the diff.
- Include steps to test (commands, fixtures, expected output).
- Note security considerations, breaking changes, and migration
  steps if any.
- Link the issue the PR closes (`Closes #123`) when applicable.

## Code Style

Follow each repository's language-specific style guides and
formatting tools. If the repo provides a `Makefile`, `task` runner,
or pre-commit configuration, use it.

## License

By contributing, you agree your contributions are licensed under the
license declared in the repository you are contributing to (typically
shown in the repo's `LICENSE` file).
