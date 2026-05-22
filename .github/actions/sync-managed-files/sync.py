#!/usr/bin/env python3
"""
Sync standardized "managed" sections — and whole files — into
consumer repositories.

Two registries are supported:

* ``SERVICE_BLOCKS`` (section mode) — each enabled service contributes
  one or more blocks fenced by
  ``>>> bos-automation-hub:<service> >>>`` /
  ``<<< bos-automation-hub:<service> <<<`` marker lines using the
  comment syntax of the target file. Used for multi-tenant dotfiles
  (``.gitignore``, ``.dockerignore``, ``.editorconfig``,
  ``.gitattributes``) where multiple services contribute distinct
  blocks to the same file and hand-authored content must coexist
  outside the markers.

* ``SERVICE_FILES`` (whole-file mode) — each enabled service may own
  one or more files outright. The hub overwrites the file with the
  canonical content (prefixed by a single-line ``Managed by…`` header
  comment) on every run. Used for shared scripts where the entire file
  body is authoritative (e.g. ``log-functions.sh``). No markers; no
  merging — a file may only be claimed by exactly one whole-file
  service.

Rules
-----
* If a service is in ``SERVICES``, ensure its blocks / files exist and
  match the canonical content. Create files (and parent dirs) if
  missing.
* If a service is NOT in ``SERVICES``, do nothing for it — existing
  blocks AND existing whole-file targets are left untouched.
* For section mode, nothing outside the marker pair is ever read or
  written.
* A single file path may not be registered under both modes, nor
  claimed by more than one whole-file service.

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

# Documentation / metadata (if your image legitimately needs one of
# these inside the runtime, add a `!README.md` style re-include AFTER
# this managed block — `.dockerignore` is last-match-wins, so a
# re-include placed BEFORE the block would be overridden by these
# excludes).
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
# Whole-file canonical content                                                #
# --------------------------------------------------------------------------- #
#
# These entries are written VERBATIM (preceded by a "managed by" header
# comment, prepended by `_make_whole_file`) to their target paths. The
# entire file body is owned by the hub — no markers, no merging.

# Note: this is a Python raw-string. The `\` escapes inside `log_pipe_cmd`'s
# awk template are intended to reach bash unmodified — keep them as-is.
_LOG_FUNCTIONS_SH = r"""#!/usr/bin/env bash
# shellcheck shell=bash
#
# Canonical shared logging library for s6-overlay init and svc scripts
# across the blackoutsecure container fleet.
#
# Sourced (not executed). Provides one consistent log line format:
#
#     <RFC3339 UTC> <tag>[<level>]: <message>
#
# Two API styles are supported (mix freely; pick whichever reads best
# at the call site):
#
#   1. Function-per-level:
#          SVC_NAME="svc-readsb"      # OR: LOG_TAG="svc-readsb"
#          . /usr/local/bin/log-functions.sh
#          log_info  "starting up"
#          log_warn  "degraded"
#          log_error "connection refused"
#          log_fatal "cannot continue"
#          log_debug "fyi"            # gated by LOG_LEVEL
#      warn/error/fatal route to stderr; debug/info to stdout.
#
#   2. Generic dispatcher:
#          LOG_TAG="svc-gh-runner"    # OR: SVC_NAME="svc-gh-runner"
#          . /usr/local/bin/log-functions.sh
#          log info  "starting up"
#          log warn  "degraded"
#          log error "connection refused"
#          log fatal "cannot continue"
#      All levels route to stdout (legacy docker-github-runner shape).
#      Callers that want stderr add `>&2` at the call site.
#
# Severity ordering (case-insensitive):
#     debug < info < warn < error < fatal
# Lines below ${LOG_LEVEL:-info} are dropped; `fatal` is always emitted.
#
# Tag resolution: SVC_NAME wins, then LOG_TAG, then "unknown" (with a
# one-shot warning on stderr) so a misconfigured caller is noisy but not
# fatal.
#
# Extras (readsb provenance):
#   log_kv KEY value             # pretty key/value (gated at info)
#   log_pipe_cmd [priority]      # awk pipe that prefixes each stdin
#                                # line with the syslog format. Usage:
#                                #   exec mybinary 2>&1 \
#                                #     | eval "$(log_pipe_cmd decoder)"

if [[ -z "${SVC_NAME:-}" && -z "${LOG_TAG:-}" ]]; then
    printf '%s log-functions.sh[warn]: neither SVC_NAME nor LOG_TAG set; using "unknown"\n' \
        "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" >&2
fi

_log_tag() {
    printf '%s' "${SVC_NAME:-${LOG_TAG:-unknown}}"
}

_log_severity() {
    case "${1,,}" in
        debug) printf '10' ;;
        info)  printf '20' ;;
        warn)  printf '30' ;;
        error) printf '40' ;;
        fatal) printf '50' ;;
        *)     printf '20' ;;
    esac
}

_log_should_emit() {
    # _log_should_emit <level> -> 0 if yes, 1 if no
    local level="${1,,}" cur min
    [[ "${level}" == "fatal" ]] && return 0
    cur=$(_log_severity "${level}")
    min=$(_log_severity "${LOG_LEVEL:-info}")
    [[ "${cur}" -ge "${min}" ]]
}

_log_emit() {
    # _log_emit <level> <fd> <msg ...>
    local level="$1" fd="$2"; shift 2
    printf '%s %s[%s]: %s\n' \
        "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" \
        "$(_log_tag)" \
        "${level}" \
        "$*" >&"${fd}"
}

# Generic dispatcher (docker-github-runner API). All levels to stdout to
# preserve legacy behavior; callers add `>&2` when they want stderr.
log() {
    local level="$1"; shift
    _log_should_emit "${level}" || return 0
    _log_emit "${level}" 1 "$*"
}

# Function-per-level (readsb / mlat-hub API). warn/error/fatal -> stderr.
log_debug() { _log_should_emit debug && _log_emit debug 1 "$@"; return 0; }
log_info()  { _log_should_emit info  && _log_emit info  1 "$@"; return 0; }
log_warn()  { _log_should_emit warn  && _log_emit warn  2 "$@"; return 0; }
log_error() { _log_should_emit error && _log_emit error 2 "$@"; return 0; }
log_fatal() { _log_emit fatal 2 "$@"; }

# Pretty key/value (readsb provenance). Gated at info.
log_kv() {
    _log_should_emit info || return 0
    local key="$1" value="$2"
    printf '%s %s[%s]: %-15s %s\n' \
        "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" \
        "$(_log_tag)" \
        "info" \
        "${key}:" \
        "${value}"
}

# Returns an awk pipeline string for prefixing each stdin line with the
# syslog format. fflush() avoids buffering so log lines surface in real
# time. Usage:
#     exec some-binary 2>&1 | eval "$(log_pipe_cmd decoder)"
log_pipe_cmd() {
    local priority="${1:-stdout}"
    local tag
    tag="$(_log_tag)"
    printf "awk '{ printf \"%%s %s[%s]: %%s\\\\n\", strftime(\"%%Y-%%m-%%dT%%H:%%M:%%SZ\", systime(), 1), \$0; fflush() }'" \
        "${tag}" "${priority}"
}
"""


# --------------------------------------------------------------------------- #
# Service registry                                                            #
# --------------------------------------------------------------------------- #
#
# Two registries — see module docstring for the section vs whole-file
# distinction.
#
# `SERVICE_BLOCKS` — per-service: ordered dict of {file_path: block_body}.
# When a service contributes to multiple files, each file is processed
# independently. Priority dictates the order blocks appear in a FRESH
# file (created during this run). Existing blocks are left in place —
# priority only affects newly-appended blocks for a given file.
#
# `SERVICE_FILES`  — per-service: ordered dict of {file_path: full_body}.
# The hub overwrites the file outright on every run. A file path may
# appear in at most one whole-file service AND must not also appear in
# any section service.

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

SERVICE_FILES: Dict[str, Dict[str, str]] = {
    "logger": {
        "root/usr/local/bin/log-functions.sh": _LOG_FUNCTIONS_SH,
    },
}

KNOWN_SERVICES = list(SERVICE_BLOCKS.keys()) + list(SERVICE_FILES.keys())

# Cross-mode sanity: a path may appear in only ONE registry, and within
# SERVICE_FILES only ONE service may claim a given path. Detected at
# import so a registry typo fails CI immediately rather than at runtime.
_seen_whole_file_paths: Dict[str, str] = {}
for _svc, _files in SERVICE_FILES.items():
    for _path in _files:
        if _path in _seen_whole_file_paths:
            raise RuntimeError(
                f"sync.py registry conflict: file '{_path}' is claimed by "
                f"both SERVICE_FILES['{_seen_whole_file_paths[_path]}'] and "
                f"SERVICE_FILES['{_svc}'] — a whole-file path may only "
                f"have one owner."
            )
        _seen_whole_file_paths[_path] = _svc
for _svc, _blocks in SERVICE_BLOCKS.items():
    for _path in _blocks:
        if _path in _seen_whole_file_paths:
            raise RuntimeError(
                f"sync.py registry conflict: file '{_path}' is registered "
                f"as both a section target (SERVICE_BLOCKS['{_svc}']) and a "
                f"whole-file target (SERVICE_FILES['{_seen_whole_file_paths[_path]}']) "
                f"— these modes are mutually exclusive per path."
            )
del _seen_whole_file_paths, _svc, _files, _blocks, _path  # tidy module scope


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


# Header injected at the top of every whole-file managed asset. Uses
# `#` comments which is correct for all current whole-file targets
# (shell scripts). If we add a non-`#`-comment target in the future
# (e.g. an XML or JSON file), upgrade this to a per-file-extension
# comment style table rather than hard-coding `#` here.
_WHOLE_FILE_HEADER_TEMPLATE = (
    "# Managed by https://github.com/blackoutsecure/bos-automation-hub —\n"
    "# do not edit. To modify, update the `{service}` service in\n"
    "# .github/actions/sync-managed-files/sync.py.\n"
    "#\n"
)


def _make_whole_file(service: str, body: str) -> str:
    """Return ``body`` with a 'Managed by …' header injected after the
    shebang (if present, line 1) so editors and `head` immediately
    reveal the file is hub-managed. Ensures the result ends with
    exactly one trailing newline."""
    if not body.endswith("\n"):
        body = body + "\n"
    header = _WHOLE_FILE_HEADER_TEMPLATE.format(service=service)
    lines = body.splitlines(keepends=True)
    if lines and lines[0].startswith("#!"):
        return lines[0] + header + "".join(lines[1:])
    return header + body


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
    """Compute proposed changes. Returns (all_changes, drift_only).

    Handles BOTH section-mode services (SERVICE_BLOCKS) and whole-file
    services (SERVICE_FILES). Section files are computed first so the
    diff output groups deterministically (sections before whole-files);
    the registry-level cross-mode conflict check at import time ensures
    no path can appear in both buckets here.
    """
    # ------- Section mode -------
    # Group by file so we apply all enabled services for a file in one pass
    # and write only once. Preserve service input order so the order of
    # newly-appended blocks is predictable.
    file_to_services: Dict[str, List[str]] = {}
    for svc in services:
        if svc not in SERVICE_BLOCKS:
            continue
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

    # ------- Whole-file mode -------
    for svc in services:
        if svc not in SERVICE_FILES:
            continue
        for rel_path, body in SERVICE_FILES[svc].items():
            abs_path = os.path.join(root, rel_path)
            if os.path.exists(abs_path):
                with open(abs_path, "r", encoding="utf-8") as fh:
                    before = fh.read()
            else:
                before = ""
            after = _make_whole_file(svc, body)
            all_changes.append(
                FileChange(path=rel_path, before=before, after=after)
            )

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
        print("All managed sections and files are up to date.")
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
            "content drifted from the canonical sections / files.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
