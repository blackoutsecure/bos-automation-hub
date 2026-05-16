#!/usr/bin/env python3
"""
Sync standardized "managed" sections into well-known dotfiles.

Each enabled service contributes one or more blocks. A block is text
fenced by `>>> bos-automation-hub:<service> >>>` /
`<<< bos-automation-hub:<service> <<<` marker lines using the comment
syntax of the target file (all four supported file types use `#`).

Rules
-----
* If a service is in `SERVICES`, ensure its block exists and matches the
  canonical content in every target file it owns. Create the file if
  missing.
* If a service is NOT in `SERVICES`, do nothing for it — existing
  blocks are left untouched.
* Nothing outside the marker pair is ever read or written.

Env (set by `action.yml`)
-------------------------
    SERVICES         newline / whitespace list of enabled service names
    DRY_RUN          'true' | 'false'
    FAIL_ON_DRIFT    'true' | 'false' (implies dry_run for writes)
    GITHUB_OUTPUT    workflow output file (provided by runner)
    GITHUB_WORKSPACE optional override of CWD (provided by runner)
"""

from __future__ import annotations

import difflib
import os
import re
import sys
from typing import Dict, List, Tuple

MARKER_NAMESPACE = "bos-automation-hub"
MARKER_NOTE = (
    "Managed by https://github.com/blackoutsecure/bos-automation-hub — "
    "do not edit between markers."
)


# --------------------------------------------------------------------------- #
# Canonical block content per (service, file)                                 #
# --------------------------------------------------------------------------- #
#
# Block content MUST end with a newline so the open/close markers each
# sit on their own line.  Block content is the body BETWEEN markers; the
# markers themselves are added by `make_block`.

_GITIGNORE_COMMON = """\
# OS noise
.DS_Store
Thumbs.db
._*

# Editor / IDE noise
.vscode/
.idea/
*.swp
*.swo
*~

# Local env files (NEVER commit credentials)
.env
.env.*
!.env.example
.envrc
.direnv/

# TLS / private keys
.secrets/
*.pem
*.key
*.crt
*.p12
*.pfx
secrets.*
"""

_GITIGNORE_PYTHON = """\
__pycache__/
*.py[cod]
*.egg-info/
.venv/
venv/
.pytest_cache/
.mypy_cache/
.ruff_cache/
"""

_GITIGNORE_NODE = """\
node_modules/
npm-debug.log*
yarn-debug.log*
yarn-error.log*
.pnpm-debug.log*
"""

# --------------------------------------------------------------------------- #

_DOCKERIGNORE_DOCKER = """\
# CI / automation metadata — not needed inside the Docker build context.
.git
.github/
.gitignore
.gitattributes
.dockerignore
.editorconfig

# Editor / IDE noise
.vscode/
.idea/
*.swp
*~

# OS noise
.DS_Store
Thumbs.db
._*

# Documentation / metadata (re-include explicitly above this block if
# your image legitimately needs e.g. README.md inside the runtime).
README.md
LICENSE
SECURITY.md
CHANGELOG.md

# Local env / secrets — NEVER ship inside an image.
.env
.env.*
.secrets/
*.pem
*.key
*.crt
*.p12
*.pfx
"""

# Must come AFTER any `docker` block in `.dockerignore` because re-includes
# (`!path`) are evaluated in order against preceding excludes.
_DOCKERIGNORE_BALENA = """\
# `balena.yml` is rendered by the bos-automation-hub workflow into the
# repo root before `balena push` runs; @balena/compose reads it from the
# build context, so it MUST NOT be excluded.
!balena.yml
"""

_DOCKERIGNORE_PYTHON = """\
__pycache__/
*.py[cod]
*.egg-info/
.venv/
venv/
.pytest_cache/
.mypy_cache/
.ruff_cache/
"""

_DOCKERIGNORE_NODE = """\
node_modules/
npm-debug.log*
yarn-debug.log*
yarn-error.log*
.pnpm-debug.log*
"""

# --------------------------------------------------------------------------- #

_EDITORCONFIG_COMMON = """\
root = true

[*]
charset = utf-8
end_of_line = lf
insert_final_newline = true
trim_trailing_whitespace = true
indent_style = space
indent_size = 2

[*.md]
trim_trailing_whitespace = false

[Makefile]
indent_style = tab
"""

# --------------------------------------------------------------------------- #

_GITATTRIBUTES_LF = """\
# Normalize all text files to LF on checkout and in the index.
* text=auto eol=lf

# Explicit binary types (defensive — git usually auto-detects).
*.png  binary
*.jpg  binary
*.jpeg binary
*.gif  binary
*.webp binary
*.ico  binary
*.pdf  binary
"""


# --------------------------------------------------------------------------- #
# Service registry                                                            #
# --------------------------------------------------------------------------- #
#
# Per-service: ordered dict of {file_path: block_content}.  When a service
# contributes to multiple files, each file is processed independently.
#
# Priority dictates the order blocks appear in a FRESH file (created
# during this run).  Existing blocks are left in place — priority only
# affects newly-appended blocks for a given file.

SERVICE_BLOCKS: Dict[str, Dict[str, str]] = {
    "common": {
        ".gitignore": _GITIGNORE_COMMON,
        ".editorconfig": _EDITORCONFIG_COMMON,
    },
    "docker": {
        ".dockerignore": _DOCKERIGNORE_DOCKER,
    },
    "balena": {
        ".dockerignore": _DOCKERIGNORE_BALENA,
    },
    "python": {
        ".gitignore": _GITIGNORE_PYTHON,
        ".dockerignore": _DOCKERIGNORE_PYTHON,
    },
    "node": {
        ".gitignore": _GITIGNORE_NODE,
        ".dockerignore": _DOCKERIGNORE_NODE,
    },
    "lf_line_endings": {
        ".gitattributes": _GITATTRIBUTES_LF,
    },
}

KNOWN_SERVICES = list(SERVICE_BLOCKS.keys())


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #


def die(msg: str) -> "NoReturn":  # type: ignore[name-defined]
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def parse_services(raw: str) -> List[str]:
    """Parse the SERVICES input. Newline OR whitespace separated. Strip
    blank lines and `#` comment lines (whole-line only, not inline).
    Preserve order; dedupe."""
    seen = set()
    result: List[str] = []
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        for tok in stripped.split():
            if not tok:
                continue
            if tok not in KNOWN_SERVICES:
                die(
                    f"unknown service '{tok}'. "
                    f"Known: {', '.join(KNOWN_SERVICES)}"
                )
            if tok in seen:
                continue
            seen.add(tok)
            result.append(tok)
    if not result:
        die("input 'services' resolved to zero entries")
    return result


def make_block(service: str, body: str) -> str:
    """Wrap `body` (already ending with `\\n`) in the marker lines."""
    if not body.endswith("\n"):
        body = body + "\n"
    open_marker = f"# >>> {MARKER_NAMESPACE}:{service} >>>"
    close_marker = f"# <<< {MARKER_NAMESPACE}:{service} <<<"
    return f"{open_marker}\n# {MARKER_NOTE}\n{body}{close_marker}\n"


def block_pattern(service: str) -> "re.Pattern[str]":
    """Compile a regex matching the entire managed block for `service`,
    including the marker lines and any trailing newline. Tolerates
    whitespace variation in marker lines so legacy hand-edits still
    match."""
    svc_re = re.escape(service)
    ns_re = re.escape(MARKER_NAMESPACE)
    return re.compile(
        rf"^#\s*>>>\s*{ns_re}:{svc_re}\s*>>>.*?\n"
        rf"(?:.*?\n)*?"
        rf"^#\s*<<<\s*{ns_re}:{svc_re}\s*<<<.*?\n",
        re.MULTILINE,
    )


def apply_block(content: str, service: str, body: str) -> str:
    """Return updated file content with `service`'s managed block
    inserted or replaced. Existing block (if any) is replaced in place;
    otherwise the block is appended to the end of the file."""
    new_block = make_block(service, body)
    pattern = block_pattern(service)
    if pattern.search(content):
        return pattern.sub(_escape_replacement(new_block), content, count=1)
    # Append.  Ensure file ends with exactly one newline before the new block,
    # and that the new block is preceded by a blank-line separator if the
    # file is non-empty.
    if content and not content.endswith("\n"):
        content = content + "\n"
    if content:
        return content + "\n" + new_block
    return new_block


def _escape_replacement(s: str) -> str:
    """`re.sub` interprets backslashes in the replacement string. Escape
    them so canonical content is inserted byte-for-byte."""
    return s.replace("\\", "\\\\")


class FileChange:
    """Pending change for a single file. Hand-rolled (not a dataclass)
    so this module loads cleanly under Python 3.9 when imported via
    `importlib.util.spec_from_file_location` for local self-tests; the
    `dataclasses` interaction with PEP 563 string annotations breaks
    there. GitHub runners use Python 3.10+ where either form works."""

    __slots__ = ("path", "before", "after")

    def __init__(self, path: str, before: str, after: str) -> None:
        self.path = path
        self.before = before
        self.after = after

    @property
    def changed(self) -> bool:
        return self.before != self.after

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, FileChange):
            return NotImplemented
        return (
            self.path == other.path
            and self.before == other.before
            and self.after == other.after
        )

    def __repr__(self) -> str:
        return f"FileChange(path={self.path!r}, changed={self.changed})"


def sync_files(
    services: List[str], root: str
) -> Tuple[List[FileChange], List[FileChange]]:
    """Compute proposed changes. Returns (all_changes, drift_only)."""
    # Group by file so we apply all enabled services for a file in one pass
    # and write only once. Preserve service input order so the order of
    # newly-appended blocks is predictable.
    file_to_services: Dict[str, List[str]] = {}
    for svc in services:
        for path in SERVICE_BLOCKS[svc].keys():
            file_to_services.setdefault(path, []).append(svc)

    all_changes: List[FileChange] = []
    for rel_path, svcs in file_to_services.items():
        abs_path = os.path.join(root, rel_path)
        if os.path.exists(abs_path):
            with open(abs_path, "r", encoding="utf-8") as fh:
                before = fh.read()
        else:
            before = ""
        after = before
        for svc in svcs:
            body = SERVICE_BLOCKS[svc][rel_path]
            after = apply_block(after, svc, body)
        all_changes.append(FileChange(path=rel_path, before=before, after=after))

    drift = [c for c in all_changes if c.changed]
    return all_changes, drift


def render_diff(change: FileChange) -> str:
    label = change.path if change.before else f"{change.path} (new file)"
    diff = difflib.unified_diff(
        change.before.splitlines(keepends=True),
        change.after.splitlines(keepends=True),
        fromfile=f"a/{label}",
        tofile=f"b/{change.path}",
        n=2,
    )
    return "".join(diff)


def write_outputs(pairs: List[Tuple[str, str]]) -> None:
    out = os.environ.get("GITHUB_OUTPUT")
    if not out:
        return  # tests don't set this
    with open(out, "a", encoding="utf-8") as fh:
        for k, v in pairs:
            if "\n" in v:
                # Multi-line via heredoc.
                fh.write(f"{k}<<__EOF__\n{v}\n__EOF__\n")
            else:
                fh.write(f"{k}={v}\n")


# --------------------------------------------------------------------------- #
# Main                                                                        #
# --------------------------------------------------------------------------- #


def main() -> int:
    raw_services = os.environ.get("SERVICES", "")
    dry_run = os.environ.get("DRY_RUN", "false").lower() == "true"
    fail_on_drift = os.environ.get("FAIL_ON_DRIFT", "false").lower() == "true"

    services = parse_services(raw_services)
    root = os.environ.get("GITHUB_WORKSPACE") or os.getcwd()

    # `fail_on_drift` implies we never write — we're only checking.
    if fail_on_drift:
        dry_run = True

    _, drift = sync_files(services, root)

    print(f"Enabled services: {', '.join(services)}")
    print(f"Root: {root}")

    if not drift:
        print("All managed sections are up to date.")
        write_outputs([("changed", "false"), ("changed_files", "")])
        return 0

    print(f"\n{len(drift)} file(s) would change:")
    for change in drift:
        print(f"  - {change.path}")
        diff_text = render_diff(change)
        # Indent diff for readability in job logs.
        for line in diff_text.splitlines():
            print(f"    {line}")

    if not dry_run:
        for change in drift:
            abs_path = os.path.join(root, change.path)
            os.makedirs(os.path.dirname(abs_path) or ".", exist_ok=True)
            with open(abs_path, "w", encoding="utf-8") as fh:
                fh.write(change.after)
        print(f"\nWrote {len(drift)} file(s).")

    changed_files = "\n".join(c.path for c in drift)
    write_outputs([("changed", "true"), ("changed_files", changed_files)])

    if fail_on_drift:
        print(
            "\nfail_on_drift=true: exiting non-zero because the managed "
            "sections drifted from the canonical content.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
