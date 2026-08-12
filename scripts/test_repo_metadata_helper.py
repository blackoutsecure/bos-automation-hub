#!/usr/bin/env python3
"""Test the shared repo-metadata helper and composite dry-run contract."""

from __future__ import annotations

import importlib.util
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import ModuleType


ROOT = Path(__file__).resolve().parent.parent
ACTION_ROOT = ROOT / ".github/actions/repo-metadata"


def load_helper() -> ModuleType:
    path = ACTION_ROOT / "helper.py"
    spec = importlib.util.spec_from_file_location("repo_metadata_helper", path)
    if spec is None or spec.loader is None:
        raise AssertionError(f"could not load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


HELPER = load_helper()


class ReadmeSummaryTests(unittest.TestCase):
    def test_empty_readme_has_no_summary(self) -> None:
        self.assertEqual(HELPER.extract_readme_summary("\n\n"), "")

    def test_first_prose_follows_heading_and_badges(self) -> None:
        readme = """# Example

[![CI](https://img.shields.io/badge/ci-passing-green)](https://example.com)

The first useful prose paragraph.

Another paragraph.
"""
        self.assertEqual(
            HELPER.extract_readme_summary(readme),
            "The first useful prose paragraph.",
        )

    def test_blockquote_tagline_wins(self) -> None:
        readme = """# Example

Ordinary prose appears first.

> A canonical project tagline.
"""
        self.assertEqual(
            HELPER.extract_readme_summary(readme),
            "A canonical project tagline.",
        )

    def test_code_lists_and_tables_are_skipped(self) -> None:
        readme = """# Example

```bash
echo ignored
```

- ignored item

| ignored | table |
| --- | --- |

Usable prose after structural content.
"""
        self.assertEqual(
            HELPER.extract_readme_summary(readme),
            "Usable prose after structural content.",
        )

    def test_seed_clips_at_a_word_boundary(self) -> None:
        readme = f"# Example\n\n{'word ' * 100}\n"
        summary = HELPER.extract_readme_summary(readme, max_len=40)
        self.assertLessEqual(len(summary), 40)
        self.assertTrue(summary.endswith("word"))
        self.assertFalse(summary.endswith("\u2026"))


class DescriptionTests(unittest.TestCase):
    def test_whitespace_is_normalized(self) -> None:
        self.assertEqual(
            HELPER.clamp_description("one\n two\tthree", max_len=50),
            "one two three",
        )

    def test_long_description_includes_ellipsis_inside_limit(self) -> None:
        result = HELPER.clamp_description(
            "one two three four five six seven", max_len=20
        )
        self.assertLessEqual(len(result), 20)
        self.assertTrue(result.endswith("\u2026"))

    def test_non_positive_limit_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            HELPER.clamp_description("text", max_len=0)


class TopicTests(unittest.TestCase):
    def test_topics_are_normalized_deduplicated_and_ordered(self) -> None:
        self.assertEqual(
            HELPER.sanitize_topics(
                "GitHub_Actions, security github_actions posture.audit"
            ),
            ["github-actions", "security", "posture-audit"],
        )

    def test_invalid_and_unicode_characters_are_replaced(self) -> None:
        self.assertEqual(
            HELPER.sanitize_topics("___ caf\N{LATIN SMALL LETTER E WITH ACUTE} naive/path"),
            ["caf", "naive-path"],
        )

    def test_topic_limits_are_enforced(self) -> None:
        topics = " ".join(f"topic-{index}" for index in range(30))
        self.assertEqual(len(HELPER.sanitize_topics(topics, max_count=100)), 20)
        self.assertEqual(HELPER.sanitize_topics(topics, max_count=-1), [])
        self.assertEqual(
            len(HELPER.sanitize_topics("x" * 80)[0]),
            50,
        )


class CompositeDryRunTests(unittest.TestCase):
    def run_action(
        self,
        temp_dir: Path,
        *,
        repo: str = "blackoutsecure/example",
    ) -> subprocess.CompletedProcess[str]:
        output_path = temp_dir / "output"
        summary_path = temp_dir / "summary"
        env = os.environ | {
            "GITHUB_ACTION_PATH": str(ACTION_ROOT),
            "GITHUB_OUTPUT": str(output_path),
            "GITHUB_STEP_SUMMARY": str(summary_path),
            "RUNNER_TEMP": str(temp_dir),
            "REPO": repo,
            "GH_TOKEN": "dry-run-token",
            "README_PATH": "README.md",
            "DESCRIPTION": "A deterministic repository description.",
            "DESCRIPTION_MAX_LEN": "350",
            "HOMEPAGE": "https://example.com/project",
            "TOPICS": "GitHub_Actions security github_actions",
            "GENERATE_TOPICS": "false",
            "TOPICS_FALLBACK": "",
            "MAX_TOPICS": "20",
            "AI_ENABLED": "false",
            "AI_MODEL": "openai/gpt-4o-mini",
            "SHOW_RELEASES": "true",
            "SHOW_DEPLOYMENTS": "false",
            "SHOW_PACKAGES": "false",
            "DRY_RUN": "true",
        }
        return subprocess.run(
            ["bash", str(ACTION_ROOT / "run.sh")],
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_dry_run_resolves_outputs_without_writes(self) -> None:
        with tempfile.TemporaryDirectory() as raw_temp_dir:
            temp_dir = Path(raw_temp_dir)
            result = self.run_action(temp_dir)
            self.assertEqual(result.returncode, 0, result.stderr)
            outputs = dict(
                line.split("=", 1)
                for line in (temp_dir / "output").read_text().splitlines()
            )
            self.assertEqual(outputs["description_source"], "explicit")
            self.assertEqual(outputs["topics"], "github-actions security")
            self.assertEqual(outputs["topics_source"], "explicit")
            self.assertEqual(outputs["ai_used"], "false")
            self.assertEqual(outputs["applied"], "false")
            self.assertIn("dry_run=true", (temp_dir / "summary").read_text())

    def test_repo_must_have_exactly_two_segments(self) -> None:
        with tempfile.TemporaryDirectory() as raw_temp_dir:
            result = self.run_action(
                Path(raw_temp_dir), repo="owner/intermediate/repo"
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("exactly 'owner/repo'", result.stderr)


if __name__ == "__main__":
    unittest.main()