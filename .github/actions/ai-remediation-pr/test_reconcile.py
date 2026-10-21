"""Tests for structured recommendation identity and PR reconciliation helpers."""

from __future__ import annotations

import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

MODULE_PATH = Path(__file__).with_name("reconcile.py")
spec = importlib.util.spec_from_file_location("reconcile", MODULE_PATH)
if spec is None or spec.loader is None:
    raise RuntimeError("Reconciliation module is unavailable.")
reconcile = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reconcile)


class ReconciliationTests(unittest.TestCase):
    def test_structured_marker_round_trips_keys_with_spaces(self):
        key = "ps010:.github/workflows/ci.yml"
        marker = reconcile.structured_marker(key)
        indexed = reconcile.index_open_prs([{"body": marker}])
        self.assertEqual(indexed[key], {"body": marker})

    def test_open_pr_index_supports_legacy_marker(self):
        pr = {"body": reconcile.MARKER.format(key="legacy-key")}
        self.assertEqual(reconcile.index_open_prs([pr])["legacy-key"], pr)

    def test_open_pr_index_deduplicates_by_stable_key(self):
        first = {"body": reconcile.structured_marker("same"), "number": 1}
        second = {"body": reconcile.structured_marker("same"), "number": 2}
        self.assertEqual(reconcile.index_open_prs([first, second])["same"]["number"], 2)

    def test_normalize_accepts_wrapped_payload_and_defaults_patch_status(self):
        items = reconcile.normalize_recommendations({
            "recommendations": [{
                "finding_key": "ps010-ci",
                "recommendation": "Add permissions",
            }],
        })
        self.assertEqual(items[0]["patch_status"], "unavailable")

    def test_normalize_rejects_missing_stable_identity(self):
        with self.assertRaises(SystemExit):
            reconcile.normalize_recommendations([{"recommendation": "Review"}])

    def test_only_validated_nonempty_patches_are_eligible(self):
        base = {"finding_key": "key", "recommendation": "Review"}
        self.assertFalse(reconcile.has_validated_patch({
            **base, "patch": "diff", "patch_status": "unavailable",
        }))
        self.assertFalse(reconcile.has_validated_patch({**base, "patch_status": "validated"}))
        self.assertTrue(reconcile.has_validated_patch({
            **base, "patch": "diff", "patch_status": "validated",
        }))

    def test_request_sends_bearer_token_and_parses_response(self):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b'{"ok":true}'
        with patch.object(
            reconcile.urllib.request, "urlopen", return_value=response
        ) as urlopen:
            result = reconcile.request("/repos/acme/app/pulls", "secret-token")

        request = urlopen.call_args.args[0]
        self.assertEqual(request.get_header("Authorization"), "Bearer secret-token")
        self.assertEqual(result, {"ok": True})

    def test_empty_recommendations_write_report_without_token(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            recommendations = root / "recommendations.json"
            output = root / "output"
            recommendations.write_text("[]", encoding="utf-8")
            environment = {
                "REMEDIATION_RECOMMENDATIONS": str(recommendations),
                "REMEDIATION_REPO": "acme/app",
                "GITHUB_OUTPUT": str(output),
            }
            with patch.dict(os.environ, environment, clear=False):
                reconcile.main()

            self.assertIn(
                "report<<__BOS_REMEDIATION__\n[]",
                output.read_text(encoding="utf-8"),
            )

    def test_unvalidated_patch_never_creates_a_pull_request(self):
        with tempfile.TemporaryDirectory() as directory:
            recommendations = Path(directory) / "recommendations.json"
            output = Path(directory) / "output"
            recommendations.write_text(json.dumps([{
                "finding_key": "rule-1",
                "recommendation": "Review this finding",
                "patch": "diff --git a/file b/file",
                "patch_status": "unavailable",
            }]), encoding="utf-8")
            environment = {
                "REMEDIATION_RECOMMENDATIONS": str(recommendations),
                "REMEDIATION_MODE": "draft_pr",
                "REMEDIATION_TOKEN": "token",
                "REMEDIATION_REPO": "acme/app",
                "GITHUB_OUTPUT": str(output),
            }
            with (
                patch.dict(os.environ, environment, clear=False),
                patch.object(reconcile, "request", return_value=[]) as request,
                patch.object(reconcile, "run") as run,
            ):
                reconcile.main()

            self.assertEqual(request.call_count, 1)
            run.assert_not_called()
            self.assertIn('"status":"no_pr"', output.read_text(encoding="utf-8"))

    def test_validated_patch_creates_draft_pr_and_labels_it(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            recommendations = root / "recommendations.json"
            output = root / "output"
            recommendations.write_text(json.dumps([{
                "finding_key": "rule-1",
                "recommendation": "Review this finding",
                "patch": "diff --git a/file b/file",
                "patch_status": "validated",
            }]), encoding="utf-8")
            environment = {
                "REMEDIATION_RECOMMENDATIONS": str(recommendations),
                "REMEDIATION_MODE": "draft_pr",
                "REMEDIATION_TOKEN": "token",
                "REMEDIATION_REPO": "acme/app",
                "REMEDIATION_BASE": "main",
                "REMEDIATION_LABELS": "security,automation",
                "GITHUB_OUTPUT": str(output),
            }
            api_responses = [
                [],
                {"number": 42, "html_url": "https://github.com/acme/app/pull/42"},
                {},
                {},
            ]
            commands = []

            def record_command(*args):
                commands.append(args)

            original_cwd = os.getcwd()
            os.chdir(root)
            try:
                with (
                    patch.dict(os.environ, environment, clear=False),
                    patch.object(reconcile, "request", side_effect=api_responses) as request,
                    patch.object(reconcile, "run", side_effect=record_command),
                ):
                    reconcile.main()
            finally:
                os.chdir(original_cwd)

            create_call = request.call_args_list[1]
            self.assertEqual(create_call.args[1], "token")
            payload = create_call.args[3]
            self.assertTrue(payload["draft"])
            self.assertIn(reconcile.structured_marker("rule-1"), payload["body"])
            label_requests = request.call_args_list[2:]
            self.assertEqual(len(label_requests), 2)
            self.assertEqual(
                [call.args[3]["labels"] for call in label_requests],
                [["security"], ["automation"]],
            )
            self.assertTrue(
                any(command[:3] == ("git", "apply", "--check") for command in commands)
            )
            self.assertIn('"status":"pr_created"', output.read_text(encoding="utf-8"))

    def test_existing_pr_is_updated_when_recommendation_changes(self):
        recommendation = {
            "finding_key": "rule-1",
            "recommendation": "Updated guidance",
        }
        existing = {
            "number": 7,
            "html_url": "https://github.com/acme/app/pull/7",
            "body": reconcile.structured_marker("rule-1") + "\nrecommendation_hash: old",
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            recommendations = root / "recommendations.json"
            output = root / "output"
            recommendations.write_text(json.dumps([recommendation]), encoding="utf-8")
            environment = {
                "REMEDIATION_RECOMMENDATIONS": str(recommendations),
                "REMEDIATION_MODE": "draft_pr",
                "REMEDIATION_TOKEN": "token",
                "REMEDIATION_REPO": "acme/app",
                "GITHUB_OUTPUT": str(output),
            }
            with (
                patch.dict(os.environ, environment, clear=False),
                patch.object(reconcile, "request", side_effect=[[existing], {}]) as request,
            ):
                reconcile.main()

            self.assertEqual(request.call_count, 2)
            self.assertEqual(
                request.call_args.args[0],
                "/repos/acme/app/issues/7/comments",
            )
            self.assertIn(
                '"status":"existing_pr_updated"',
                output.read_text(encoding="utf-8"),
            )
