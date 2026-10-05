from __future__ import annotations

import copy
import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent


def load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / ".github" / "actions" / "infrastructure-contract" / filename)
    if spec is None or spec.loader is None:
        raise RuntimeError("Infrastructure test module is unavailable.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


contract = load("infrastructure_contract", "contract.py")
auth = load("infrastructure_authorization", "authorize.py")
preflight = load("infrastructure_preflight", "preflight.py")


def registry() -> dict:
    return {
        "schema_version": 1, "repository": "blackoutsecure/int-blackout-infrastructure",
        "automation_hub": {"repository": "blackoutsecure/bos-automation-hub", "revision": "a" * 40},
        "stacks": [{
            "id": "flipiq-production", "display_name": "FlipIQ", "provider": "cloudflare", "contract_version": "1.0.0",
            "environment": "flipiq-production", "planning_environment": "flipiq-production-plan",
            "terraform_directory": "stacks/flipiq", "settings_variable": "FLIPIQ_IAC_SETTINGS",
            "backend": {"key": "flipiq/production/terraform.tfstate", "bucket_variable": "FLIPIQ_STATE_BUCKET"},
            "allowed_resources": ["cloudflare_d1_database.scans"],
            "application": {"repository": "blackoutsecure/int-blackout-flipiq", "revision": "b" * 40, "working_directory": ".workloads/flipiq"},
        }],
    }


class ContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        (self.root / "stacks" / "flipiq").mkdir(parents=True)

    def tearDown(self) -> None:
        self.directory.cleanup()

    def test_valid_ownership(self) -> None:
        result = contract.inspect_registry(registry(), self.root, registry()["repository"], "flipiq-production")
        self.assertEqual(result["application_name"], "int-blackout-flipiq")
        self.assertEqual(result["environment"], "flipiq-production")

    def test_reader_inputs_are_explicit_without_fallback(self) -> None:
        self.assertEqual(
            preflight.missing_reader_inputs({}),
            ["INFRA_READER_APP_ID", "READER_APP_PRIVATE_KEY"],
        )
        self.assertEqual(
            preflight.missing_reader_inputs({
                "INFRA_READER_APP_ID": "12345",
                "READER_APP_PRIVATE_KEY": "offline-only-private-key",
            }),
            [],
        )
        self.assertEqual(
            preflight.missing_reader_inputs({"GITHUB_TOKEN": "unrelated-token"}),
            ["INFRA_READER_APP_ID", "READER_APP_PRIVATE_KEY"],
        )

    def test_duplicate_owner_rejected(self) -> None:
        value = registry()
        value["stacks"].append(copy.deepcopy(value["stacks"][0]))
        with self.assertRaises(ValueError):
            contract.inspect_registry(value, self.root, value["repository"], "flipiq-production")

    def test_unbounded_path_rejected(self) -> None:
        for path in ("../elsewhere", "C:\\private", "/absolute", "stacks/../flipiq", "stacks//flipiq"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                contract.relative_path(path, self.root, "Stack")

    def test_unknown_stack_rejected(self) -> None:
        with self.assertRaises(ValueError):
            contract.inspect_registry(registry(), self.root, registry()["repository"], "unknown")

    def test_mutable_application_ref_rejected(self) -> None:
        value = registry()
        value["stacks"][0]["application"]["revision"] = "dev"
        with self.assertRaises(ValueError):
            contract.inspect_registry(value, self.root, value["repository"], "flipiq-production")

    def test_repository_scope_rejected(self) -> None:
        with self.assertRaises(ValueError):
            contract.inspect_registry(registry(), self.root, "unrelated/repository", "flipiq-production")

    def test_production_cannot_skip_reviewers(self) -> None:
        with patch.object(auth, "github_json", side_effect=[{"permission": "admin"}, {"protection_rules": [], "deployment_branch_policy": {"custom_branch_policies": True}}]):
            with self.assertRaises(ValueError):
                auth.authorize("blackoutsecure/int-blackout-infrastructure", "owner", "offline-token", "", "flipiq-production", True)

    def test_portal_app_is_not_a_reviewer(self) -> None:
        with patch.object(auth, "github_json", return_value={"protection_rules": [], "deployment_branch_policy": {"custom_branch_policies": True}}):
            with self.assertRaises(ValueError):
                auth.authorize("blackoutsecure/int-blackout-infrastructure", "portal[bot]", "offline-token", "portal", "flipiq-production", True)

    def test_authorized_requester_and_environment(self) -> None:
        environment = {"protection_rules": [{"type": "required_reviewers", "reviewers": [{"id": 1}]}], "deployment_branch_policy": {"custom_branch_policies": True}}
        with patch.object(auth, "github_json", side_effect=[{"permission": "write"}, environment]):
            auth.authorize("blackoutsecure/int-blackout-infrastructure", "writer", "offline-token", "", "flipiq-production", True)

    def test_reader_cannot_request_operations(self) -> None:
        with patch.object(auth, "github_json", return_value={"permission": "read"}):
            with self.assertRaises(ValueError):
                auth.authorize("blackoutsecure/int-blackout-infrastructure", "reader", "offline-token", "", "flipiq-production", False)


if __name__ == "__main__":
    unittest.main()
