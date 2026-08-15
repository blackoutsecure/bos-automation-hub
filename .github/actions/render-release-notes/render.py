#!/usr/bin/env python3
"""Render release-notes Markdown from a `{{ key }}` template. Inputs via
env vars; extra keys must match ^[A-Z][A-Z0-9_]*$ (blocks runner-command
injection through caller-supplied Markdown)."""

from __future__ import annotations

import os
import re
import sys


def main() -> int:
    builtins = {
        "release_name":      os.environ["RELEASE_NAME"],
        "version":           os.environ["VERSION"],
        "tag_name":          os.environ["TAG_NAME"],
        "short_sha":         os.environ["SHORT_SHA"],
        "commit_url":        os.environ["COMMIT_URL"],
        "build_date":        os.environ["BUILD_DATE"],
        "image_section":     os.environ.get("IMAGE_SECTION", ""),
        "platforms_section": os.environ.get("PLATFORMS_SECTION", ""),
        "changelog_section": os.environ.get("CHANGELOG_SECTION", ""),
    }

    extra: dict[str, str] = {}
    key_re = re.compile(r"^[A-Z][A-Z0-9_]*$")
    for lineno, line in enumerate(os.environ.get("EXTRA_CONTEXT", "").splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" not in stripped:
            sys.exit(f"ERROR: extra_context line {lineno} missing '=': {stripped!r}")
        k, _, v = stripped.partition("=")
        k = k.strip()
        if not key_re.fullmatch(k):
            sys.exit(f"ERROR: extra_context key must match ^[A-Z][A-Z0-9_]*$: {k!r}")
        extra[k] = v

    builtins["extra_section"] = "\n".join(
        f"- **{k.replace('_', ' ').title()}:** {v}" for k, v in extra.items()
    )

    with open(os.environ["TEMPLATE_PATH"], "r", encoding="utf-8") as f:
        tpl = f.read()

    def replace(match: re.Match[str]) -> str:
        key = match.group(1).strip()
        if key in builtins:
            return builtins[key]
        if key.upper() in extra:
            return extra[key.upper()]
        sys.exit(f"ERROR: template references unknown placeholder: {{{{ {key} }}}}")

    rendered = re.sub(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}", replace, tpl)

    out_path = os.environ["OUTPUT_PATH"]
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(rendered)
    print(f"Wrote {len(rendered)} bytes to {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
