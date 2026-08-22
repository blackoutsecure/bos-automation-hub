"""Verify the runner provides the toolchain a gate level requires.

Reads a declarative dependency spec (commands, minimum versions, Python
distributions) and reports what is missing BEFORE a gated job spends time
or touches credentials. Version comparison is numeric-segment based so
`3.10` correctly sorts above `3.9`.
"""

from __future__ import annotations

import importlib.metadata as md
import json
import os
import re
import shutil
import subprocess
import sys

SPEC = os.environ.get("PREFLIGHT_SPEC", "").strip()
GATE_LEVEL = os.environ.get("GATE_LEVEL", "standard")


def parse_version(text: str) -> tuple[int, ...]:
    nums = re.findall(r"\d+", text or "")
    return tuple(int(n) for n in nums[:4]) or (0,)


def probe_version(command: str, args: list[str]) -> str:
    try:
        out = subprocess.run(  # noqa: S603 - fixed argv, no shell
            [command, *args],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    blob = f"{out.stdout}\n{out.stderr}"
    match = re.search(r"(\d+\.\d+(?:\.\d+)?)", blob)
    return match.group(1) if match else ""


def emit(**kwargs: str) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as handle:
        for key, value in kwargs.items():
            handle.write(f"{key}={value}\n")


def summary(lines: list[str]) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")


def main() -> int:
    if not SPEC:
        emit(satisfied="true", missing="", report="No preflight spec configured.")
        print("::notice title=Preflight::No dependency spec configured; nothing to verify.")
        return 0

    try:
        spec = json.loads(SPEC)
    except json.JSONDecodeError as exc:
        print(f"::error title=Preflight::Dependency spec is not valid JSON: {exc}")
        return 1
    if not isinstance(spec, dict):
        print("::error title=Preflight::Dependency spec must be a JSON object.")
        return 1

    commands = spec.get("required_commands") or []
    min_versions = spec.get("min_versions") or {}
    packages = spec.get("required_python_packages") or []
    version_args = spec.get("version_args") or {}
    fail_on_missing = spec.get("fail_on_missing", True) is not False

    rows: list[str] = [
        "### Gatekeeper preflight",
        "",
        f"Gate level: `{GATE_LEVEL}`",
        "",
        "| Requirement | Kind | Found | Status |",
        "| --- | --- | --- | --- |",
    ]
    missing: list[str] = []

    for command in commands:
        path = shutil.which(str(command))
        if not path:
            missing.append(f"command:{command}")
            rows.append(f"| `{command}` | command | — | missing |")
            continue
        need = min_versions.get(command)
        if not need:
            rows.append(f"| `{command}` | command | present | ok |")
            continue
        args = version_args.get(command) or ["--version"]
        found = probe_version(str(command), [str(a) for a in args])
        if not found:
            missing.append(f"version-unknown:{command}")
            rows.append(f"| `{command}` | version | unreadable | missing |")
        elif parse_version(found) < parse_version(str(need)):
            missing.append(f"version:{command}<{need}")
            rows.append(f"| `{command}` >= `{need}` | version | `{found}` | too old |")
        else:
            rows.append(f"| `{command}` >= `{need}` | version | `{found}` | ok |")

    for dist in packages:
        name = re.split(r"[<>=!~\[]", str(dist), 1)[0].strip()
        want = ""
        bound = re.search(r">=\s*([0-9][0-9.]*)", str(dist))
        if bound:
            want = bound.group(1)
        try:
            found = md.version(name)
        except md.PackageNotFoundError:
            missing.append(f"python:{name}")
            rows.append(f"| `{dist}` | python | — | missing |")
            continue
        if want and parse_version(found) < parse_version(want):
            missing.append(f"python:{name}<{want}")
            rows.append(f"| `{dist}` | python | `{found}` | too old |")
        else:
            rows.append(f"| `{dist}` | python | `{found}` | ok |")

    satisfied = not missing
    rows.append("")
    rows.append(
        "All requirements satisfied."
        if satisfied
        else f"**Unsatisfied:** {', '.join(missing)}"
    )
    summary(rows)
    emit(
        satisfied="true" if satisfied else "false",
        missing=",".join(missing),
    )

    if satisfied:
        print("::notice title=Preflight::All declared dependencies are available.")
        return 0

    detail = ", ".join(missing)
    if fail_on_missing:
        print(f"::error title=Preflight failed::Runner is missing: {detail}")
        return 1
    print(f"::warning title=Preflight::Runner is missing (not enforced): {detail}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
