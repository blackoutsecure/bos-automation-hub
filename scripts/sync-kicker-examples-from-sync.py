#!/usr/bin/env python3
"""Refresh / verify the launchpad kicker snapshot examples in ``examples/``.

The three on-disk snapshots

  * ``examples/bos-launchpad-release.kicker.example.yml``
  * ``examples/bos-launchpad-cf-pages.kicker.example.yml``
  * ``examples/bos-launchpad-sync-files.kicker.example.yml``

are read-only reference renders of the kicker workflows that the hub's
``sync-managed-files`` action writes into each consumer repo when the
``bos_launchpad_release`` / ``bos_launchpad_cf_pages`` /
``bos_launchpad_sync_files`` service is enabled. The canonical source of
truth lives in ``.github/actions/sync-managed-files/sync.py`` as the
Python string constants ``_BOS_LAUNCHPAD_RELEASE_YML``,
``_BOS_LAUNCHPAD_CF_PAGES_YML``, and ``_BOS_LAUNCHPAD_SYNC_FILES_YML``.

This script keeps the two in sync.

  * ``python3 scripts/sync-kicker-examples-from-sync.py`` — refresh the
    on-disk snapshots from ``sync.py``.
  * ``python3 scripts/sync-kicker-examples-from-sync.py --check`` —
    exit non-zero if the on-disk snapshots are out of date. Used by the
    ``Lint`` workflow's ``kicker-examples-drift`` job.

The disclaimer header at the top of each snapshot is regenerated every
run, so updating any field below (e.g., reformatting the warning copy)
propagates to both snapshots on the next refresh.
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SYNC_PY = REPO_ROOT / ".github" / "actions" / "sync-managed-files" / "sync.py"
EXAMPLES_DIR = REPO_ROOT / "examples"

# Disclaimer header rendered at the top of each snapshot. Kept verbose on
# purpose so a reader who lands on the snapshot in isolation (e.g. from a
# search result) understands it is generated, not authored.
_DISCLAIMER_TEMPLATE = """\
# =============================================================================
# SNAPSHOT — DO NOT EDIT. Auto-extracted from the hub's sync-managed-files
#   action; the canonical source lives in
#   `.github/actions/sync-managed-files/sync.py` as the Python string
#   constant {const_name}.
#
# Purpose: shows what the hub writes into a consumer repo's
#   `.github/workflows/{target_file}` when the `{service}`
#   service is enabled in that repo's `bos-managed-files.yaml`. Read this
#   file to understand the kicker contract; do NOT copy it by hand into a
#   consumer repo — the hub will overwrite any hand-authored copy on the
#   next sync (and silently drift from the example here if you do).
#
# Per-repo customization happens in `.bos-launchpad.yaml` at the consumer
# repo root, NOT in this file. See the matching data-file example:
#   * release flavor    → `examples/bos-launchpad-release.example.yaml`
#   * cf-pages flavor   → `examples/bos-launchpad-cf-pages.example.yaml`
#   * sync-files flavor → `examples/bos-launchpad-sync-files.example.yaml`
#
# To refresh this snapshot after editing the constant in sync.py:
#   python3 scripts/sync-kicker-examples-from-sync.py
# =============================================================================
"""


# (const-name-in-sync.py, service-name, target-workflow-filename,
#  output-snapshot-path)
_CASES = [
    (
        "_BOS_LAUNCHPAD_RELEASE_YML",
        "bos_launchpad_release",
        "bos-launchpad-release.yml",
        EXAMPLES_DIR / "bos-launchpad-release.kicker.example.yml",
    ),
    (
        "_BOS_LAUNCHPAD_CF_PAGES_YML",
        "bos_launchpad_cf_pages",
        "bos-launchpad-cf-pages.yml",
        EXAMPLES_DIR / "bos-launchpad-cf-pages.kicker.example.yml",
    ),
    (
        "_BOS_LAUNCHPAD_SYNC_FILES_YML",
        "bos_launchpad_sync_files",
        "bos-launchpad-sync-files.yml",
        EXAMPLES_DIR / "bos-launchpad-sync-files.kicker.example.yml",
    ),
]


def _load_sync_module():
    """Import ``sync.py`` directly so the script has no dependency on the
    GitHub-Action runtime layout."""
    spec = importlib.util.spec_from_file_location("sync", SYNC_PY)
    if spec is None or spec.loader is None:  # pragma: no cover — defensive
        raise RuntimeError(f"could not load {SYNC_PY}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _render(mod, const_name: str, service: str, target_file: str) -> str:
    body = getattr(mod, const_name)
    header = _DISCLAIMER_TEMPLATE.format(
        const_name=const_name,
        service=service,
        target_file=target_file,
    )
    # Blank line between disclaimer and kicker body keeps the rendered
    # file visually clean and lets the kicker's own leading `#` comment
    # block stand on its own.
    return header + "\n" + body


def cmd_write(mod) -> int:
    for const_name, service, target_file, out_path in _CASES:
        rendered = _render(mod, const_name, service, target_file)
        out_path.write_text(rendered)
        print(f"wrote {out_path.relative_to(REPO_ROOT)}")
    return 0


def cmd_check(mod) -> int:
    drift = False
    for const_name, service, target_file, out_path in _CASES:
        expected = _render(mod, const_name, service, target_file)
        actual = out_path.read_text() if out_path.exists() else ""
        if actual != expected:
            drift = True
            print(
                f"::error file={out_path.relative_to(REPO_ROOT)}::"
                f"snapshot out of date vs. sync.py {const_name}. "
                "Re-run `python3 scripts/sync-kicker-examples-from-sync.py`."
            )
    if drift:
        return 1
    print("kicker snapshots match sync.py")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit non-zero if any on-disk snapshot differs from sync.py "
        "(no write).",
    )
    args = parser.parse_args()

    mod = _load_sync_module()
    return cmd_check(mod) if args.check else cmd_write(mod)


if __name__ == "__main__":
    sys.exit(main())
