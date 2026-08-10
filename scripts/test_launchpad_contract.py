#!/usr/bin/env python3
"""Validate the managed launchpad caller against hub-owned contracts."""

from __future__ import annotations

import importlib.util
import re
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def load_sync_module():
    path = ROOT / ".github/actions/sync-managed-files/sync.py"
    spec = importlib.util.spec_from_file_location("sync_managed_files", path)
    if spec is None or spec.loader is None:
        raise AssertionError(f"could not load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def workflow_input_names(body: str) -> set[str]:
    inputs = body.split("    inputs:\n", 1)[1].split("    secrets:\n", 1)[0]
    return set(re.findall(r"^      ([a-z][a-z0-9_]+):", inputs, re.MULTILINE))


def caller_input_names(body: str) -> set[str]:
    call = body.split(
        "    uses: blackoutsecure/bos-automation-hub/"
        ".github/workflows/bos-universal-launchpad.yml@main\n",
        1,
    )[1]
    inputs = call.split("    with:\n", 1)[1].split("    secrets:\n", 1)[0]
    return set(re.findall(r"^      ([a-z][a-z0-9_]+):", inputs, re.MULTILINE))


def main() -> None:
    workflow = (ROOT / ".github/workflows/bos-universal-launchpad.yml").read_text()
    kicker = (
        ROOT / "managed-files/workflows/bos-universal-launchpad-kicker.yml"
    ).read_text()

    declared = workflow_input_names(workflow)
    forwarded = caller_input_names(kicker)
    assert declared == forwarded, {
        "missing": sorted(declared - forwarded),
        "unknown": sorted(forwarded - declared),
    }

    guard = kicker.split("  managed-files-guard:\n", 1)[1].split(
        "\n  release:\n", 1
    )[0]
    assert r"^\.github/workflows/" in guard
    assert r"^\.github/dependabot\.yml$" not in guard

    promote = (ROOT / ".github/workflows/release-promote.yml").read_text()
    dependabot_input = promote.split("      include_dependabot_config:\n", 1)[
        1
    ].split("      include_github_metadata:\n", 1)[0]
    assert "        default: true\n" in dependabot_input

    sync = load_sync_module()
    services = sync.parse_services(
        "bos_launchpad bos_launchpad_config bos_launchpad_sync_files"
    )
    with tempfile.TemporaryDirectory() as root:
        _, drift = sync.sync_files(services, root)
    assert {change.path for change in drift} == {
        ".github/workflows/bos-universal-launchpad-kicker.yml",
        ".github/workflows/bos-launchpad-sync-files.yml",
        "bos-launchpad-config.json",
    }
    assert "bos_launchpad_reference" not in sync.KNOWN_SERVICES

    print(
        f"launchpad contract valid: {len(declared)} inputs, "
        f"{len(services)} managed services"
    )


if __name__ == "__main__":
    main()