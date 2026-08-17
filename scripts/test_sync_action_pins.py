#!/usr/bin/env python3
"""Unit tests for the first-party action pin resolver and rewriter."""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


pins = _load("bos_sync_action_pins", ROOT / "scripts/sync_action_pins.py")
resolver = _load(
    "bos_resolve_latest",
    ROOT / ".github/actions/shared/resolve-latest-action-ref/resolve.py",
)


def test_semver_ordering() -> None:
    key = resolver.semver_key
    # A pre-release of a version sorts BELOW that same version.
    assert key("v1.0.0-rc.1") < key("v1.0.0")
    # Numeric ordering, not lexicographic: 1.0.9 < 1.0.37.
    assert key("v1.0.9") < key("v1.0.37")
    assert key("v0.1.24") < key("v0.1.27")
    assert key("v1.1.1") < key("v1.1.3")
    # Numeric pre-release identifiers rank below alphanumeric ones.
    assert key("v1.0.0-1") < key("v1.0.0-alpha")
    assert key("v1.0.0-rc.2") > key("v1.0.0-rc.1")
    # The `v` prefix is optional and must not change precedence.
    assert key("1.2.3") == key("v1.2.3")
    # Non-SemVer tags can never win a ranking.
    assert key("dev") < key("v0.0.1")


def test_select_release_prefers_newest_even_when_prerelease() -> None:
    """The real-world case: every first-party action's newest tag is a pre-release."""
    releases = [
        {"tag_name": "v1.0.24", "prerelease": False, "draft": False},
        {"tag_name": "v1.0.37", "prerelease": True, "draft": False},
        {"tag_name": "v1.0.36", "prerelease": True, "draft": False},
    ]
    pattern = re.compile(resolver.DEFAULT_TAG_PATTERN)

    auto = resolver.select_release(releases, pattern, "auto")
    assert auto["tag_name"] == "v1.0.37", "auto must return the newest overall"

    stable = resolver.select_release(releases, pattern, "stable")
    assert stable["tag_name"] == "v1.0.24", "stable must ignore pre-releases"

    pre = resolver.select_release(releases, pattern, "prerelease")
    assert pre["tag_name"] == "v1.0.37"


def test_select_release_skips_drafts_and_unmatched_tags() -> None:
    pattern = re.compile(resolver.DEFAULT_TAG_PATTERN)
    releases = [
        {"tag_name": "v2.0.0", "prerelease": False, "draft": True},
        {"tag_name": "nightly", "prerelease": True, "draft": False},
        {"tag_name": "v1.5.0", "prerelease": False, "draft": False},
    ]
    assert resolver.select_release(releases, pattern, "auto")["tag_name"] == "v1.5.0"
    assert resolver.select_release([], pattern, "auto") is None


def test_rewrite_replaces_sha_and_version_comment() -> None:
    sha = "a" * 40
    body = (
        "      - name: Scan\n"
        "        uses: blackoutsecure/bos-code-scanning-kit@"
        + "b" * 40
        + " # dev\n"
        "        with:\n"
        "          fail_on: never\n"
    )
    updated, old = pins.rewrite_text(body, "blackoutsecure/bos-code-scanning-kit", sha, "v1.0.37")

    assert old == ["b" * 40]
    assert f"uses: blackoutsecure/bos-code-scanning-kit@{sha} # v1.0.37" in updated
    # A branch name must never survive a rewrite.
    assert "# dev" not in updated
    # Surrounding lines and indentation are untouched.
    assert "      - name: Scan\n" in updated
    assert "          fail_on: never\n" in updated


def test_rewrite_handles_subpath_actions_and_leaves_others_alone() -> None:
    sha = "c" * 40
    body = (
        "        uses: blackoutsecure/bos-marketplace-kit/.github/actions/check@"
        + "d" * 40
        + " # v0.1.24\n"
        "        uses: blackoutsecure/bos-automation-hub/.github/actions/shared/launchpad@main\n"
        "        uses: actions/checkout@" + "e" * 40 + " # v7.0.1\n"
    )
    updated, old = pins.rewrite_text(body, "blackoutsecure/bos-marketplace-kit", sha, "v0.1.27")

    assert old == ["d" * 40]
    assert f".github/actions/check@{sha} # v0.1.27" in updated
    # Hub self-references and third-party pins must be preserved verbatim.
    assert "bos-automation-hub/.github/actions/shared/launchpad@main" in updated
    assert "actions/checkout@" + "e" * 40 + " # v7.0.1" in updated


def test_rewrite_is_idempotent() -> None:
    sha = "f" * 40
    body = "        uses: blackoutsecure/bos-upstream-watcher@" + sha + " # v1.1.3\n"
    once, _ = pins.rewrite_text(body, "blackoutsecure/bos-upstream-watcher", sha, "v1.1.3")
    twice, _ = pins.rewrite_text(once, "blackoutsecure/bos-upstream-watcher", sha, "v1.1.3")
    assert once == body
    assert twice == once


def test_manifest_repositories_are_all_referenced() -> None:
    manifest = json.loads((ROOT / ".github/action-pins.json").read_text())
    files = pins.iter_files(manifest["scan_globs"])
    assert files, "scan_globs matched no files"

    corpus = "\n".join(path.read_text(encoding="utf-8") for path in files)
    for entry in manifest["repositories"]:
        repository = entry["repository"]
        matches = pins.pin_pattern(repository).findall(corpus)
        assert matches, f"{repository} is in the manifest but never referenced"

    # Every managed pin must be an immutable SHA carrying its version tag.
    for entry in manifest["repositories"]:
        repository = entry["repository"]
        for match in pins.pin_pattern(repository).finditer(corpus):
            ref, trailer = match.group("ref"), match.group("trailer")
            assert re.fullmatch(r"[0-9a-f]{40}", ref), f"{repository} pinned to {ref!r}"
            assert re.search(r"#\s*v\d+\.\d+\.\d+", trailer), (
                f"{repository} pin is missing its '# vX.Y.Z' comment"
            )


def main() -> int:
    tests = [value for name, value in sorted(globals().items()) if name.startswith("test_")]
    for test in tests:
        test()
    print(f"action pin contract valid: {len(tests)} checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
