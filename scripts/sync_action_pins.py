#!/usr/bin/env python3
"""Keep hub pins for first-party Blackout Secure actions on the newest tag.

`uses:` cannot contain expressions, so a workflow can never resolve "latest"
at run time without abandoning SHA pinning. This script closes that gap the
other way round: it resolves the newest tag out-of-band and rewrites the
pinned SHA in place, so the committed reference stays immutable and
reviewable while still tracking upstream.

Usage:
    sync_action_pins.py --check    # report drift, exit 1 when pins are stale
    sync_action_pins.py --write    # rewrite pins in place
    sync_action_pins.py --check --json
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / ".github/action-pins.json"
RESOLVER = ROOT / ".github/actions/shared/resolve-latest-action-ref/resolve.py"


def _load_resolver():
    """Import the shared resolver so ranking logic has a single definition."""
    spec = importlib.util.spec_from_file_location("bos_resolve_latest", RESOLVER)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise SystemExit(f"cannot import resolver from {RESOLVER}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def pin_pattern(repository: str) -> re.Pattern:
    """Match `uses: <repository>[/subpath]@<ref>` plus any trailing comment."""
    return re.compile(
        rf"(?P<head>uses:[ \t]+{re.escape(repository)}(?P<path>(?:/[^@\s]+)?)@)"
        rf"(?P<ref>[^\s#]+)"
        rf"(?P<trailer>[ \t]*(?:#[^\n]*)?)"
    )


def rewrite_text(text: str, repository: str, sha: str, tag: str) -> tuple[str, list[str]]:
    """Repin every reference to `repository`. Returns (new_text, old_refs)."""
    old_refs: list[str] = []

    def _replace(match: re.Match) -> str:
        old_refs.append(match.group("ref"))
        return f"{match.group('head')}{sha} # {tag}"

    return pin_pattern(repository).sub(_replace, text), old_refs


def iter_files(globs: list[str]) -> list[Path]:
    seen: dict[Path, None] = {}
    for pattern in globs:
        for path in sorted(ROOT.glob(pattern)):
            if path.is_file():
                seen.setdefault(path, None)
    return list(seen)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="report drift only")
    mode.add_argument("--write", action="store_true", help="rewrite pins in place")
    parser.add_argument("--json", action="store_true", help="emit a JSON summary")
    args = parser.parse_args()

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    default_channel = manifest.get("channel", "auto")
    globs = manifest.get("scan_globs") or []
    files = iter_files(globs)
    resolver = _load_resolver()
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or ""

    summary: list[dict] = []
    changed_files: set[Path] = set()

    for entry in manifest.get("repositories", []):
        repository = entry["repository"]
        resolved = resolver.resolve(
            repo=repository,
            channel=entry.get("channel", default_channel),
            source=entry.get("source", "auto"),
            pattern_text=entry.get("tag_pattern", ""),
            token=token,
        )
        sha, tag = resolved["sha"], resolved["tag"]
        stale: list[str] = []

        for path in files:
            original = path.read_text(encoding="utf-8")
            updated, old_refs = rewrite_text(original, repository, sha, tag)
            if not old_refs:
                continue
            if updated != original:
                stale.append(str(path.relative_to(ROOT)))
                if args.write:
                    path.write_text(updated, encoding="utf-8")
                    changed_files.add(path)

        summary.append(
            {
                "repository": repository,
                "tag": tag,
                "sha": sha,
                "is_prerelease": resolved["is_prerelease"] == "true",
                "source": resolved["source"],
                "stale_files": sorted(stale),
            }
        )

    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        for item in summary:
            flag = " (pre-release)" if item["is_prerelease"] else ""
            state = "STALE" if item["stale_files"] else "current"
            print(f"{state:>7}  {item['repository']} -> {item['tag']}{flag} @ {item['sha'][:12]}")
            for path in item["stale_files"]:
                print(f"           {path}")

    drift = [item for item in summary if item["stale_files"]]

    step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if step_summary:
        with open(step_summary, "a", encoding="utf-8") as handle:
            handle.write("## First-party action pins\n\n")
            handle.write("| Action | Newest tag | Channel | Pins updated |\n")
            handle.write("|---|---|---|---|\n")
            for item in summary:
                kind = "pre-release" if item["is_prerelease"] else "stable"
                handle.write(
                    f"| `{item['repository']}` | `{item['tag']}` | {kind} "
                    f"| {len(item['stale_files'])} |\n"
                )

    if args.write:
        output_path = os.environ.get("GITHUB_OUTPUT")
        if output_path:
            with open(output_path, "a", encoding="utf-8") as handle:
                handle.write(f"changed={'true' if changed_files else 'false'}\n")
        return 0

    if drift:
        print(
            f"\n{len(drift)} first-party action(s) are pinned below their newest tag.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
