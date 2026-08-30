#!/usr/bin/env python3
"""Exercise release validation pass/fail behavior without network access."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VALIDATOR = ROOT / ".github/actions/release-validation/validate.py"


def run_validator(repo: Path, config: dict) -> dict[str, str]:
    output = repo / "gha-output.txt"
    output.unlink(missing_ok=True)
    env = os.environ | {
        "RELEASE_VALIDATION_CONFIG": json.dumps(config),
        "RELEASE_VALIDATION_EXPECTED_TAG": "v1.2.3",
        "RELEASE_VALIDATION_KIND": "artifact",
        "GITHUB_OUTPUT": str(output),
    }
    result = subprocess.run(
        [sys.executable, str(VALIDATOR)],
        cwd=repo,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    text = output.read_text(encoding="utf-8")
    findings = text.split("findings<<__BOS_RELEASE_FINDINGS__\n", 1)[1].split(
        "\n__BOS_RELEASE_FINDINGS__", 1
    )[0]
    passed = text.split("passed=", 1)[1].splitlines()[0]
    return {"findings": findings, "passed": passed}


def main() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        repo = Path(temp_dir)
        (repo / "README.md").write_text("# Fixture\n", encoding="utf-8")
        (repo / "LICENSE").write_text("fixture\n", encoding="utf-8")
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.name", "Fixture"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.email", "fixture@example.invalid"], cwd=repo, check=True)
        subprocess.run(["git", "add", "README.md", "LICENSE"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-qm", "fixture"], cwd=repo, check=True)

        config = {
            "enabled": True,
            "run_node": False,
            "run_python": False,
            "run_custom": False,
            "verify_clean_tree": True,
            "required_paths": ["README.md", "LICENSE"],
        }
        clean = run_validator(repo, config)
        assert clean["passed"] == "true", clean

        (repo / "README.md").write_text("# Changed\n", encoding="utf-8")
        dirty = run_validator(repo, config)
        assert dirty["passed"] == "false", dirty
        findings = json.loads(dirty["findings"])
        artifact = next(item for item in findings if item["id"] == "RV030")
        assert artifact["severity"] == "fail", artifact

    print("release validation tests passed")


if __name__ == "__main__":
    main()
