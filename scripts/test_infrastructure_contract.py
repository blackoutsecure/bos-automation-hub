from __future__ import annotations

import copy
import importlib.util
import io
import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr
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
delivery_spec = importlib.util.spec_from_file_location(
    "worker_delivery", ROOT / ".github" / "actions" / "cloudflare-worker-delivery" / "deliver.py",
)
if delivery_spec is None or delivery_spec.loader is None:
    raise RuntimeError("Worker delivery test module is unavailable.")
delivery = importlib.util.module_from_spec(delivery_spec)
delivery_spec.loader.exec_module(delivery)


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


def protected_environment() -> dict:
    return {
        "can_admins_bypass": False,
        "protection_rules": [{
            "type": "required_reviewers",
            "reviewers": [{"type": "User", "reviewer": {"id": 1, "type": "User", "login": "reviewer"}}],
        }],
        "deployment_branch_policy": {"custom_branch_policies": True, "protected_branches": False},
    }


def dev_branches() -> dict:
    return {"total_count": 1, "branch_policies": [{"name": "dev", "type": "branch"}]}


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

    def test_plan_and_apply_credentials_cannot_share_an_environment(self) -> None:
        value = registry()
        value["stacks"][0]["planning_environment"] = value["stacks"][0]["environment"]
        with self.assertRaisesRegex(ValueError, "separate"):
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
        value = protected_environment()
        value["protection_rules"] = []
        with patch.object(auth, "github_json", side_effect=[value, dev_branches()]):
            with self.assertRaises(ValueError):
                auth.validate_environment("blackoutsecure/int-blackout-infrastructure", "offline-token", "flipiq-production", True)

    def test_portal_app_is_not_a_reviewer(self) -> None:
        value = protected_environment()
        value["protection_rules"][0]["reviewers"] = [{"type": "User", "reviewer": {"id": 1, "type": "Bot", "login": "portal[bot]"}}]
        with patch.object(auth, "github_json", side_effect=[value, dev_branches()]):
            with self.assertRaises(ValueError):
                auth.validate_environment("blackoutsecure/int-blackout-infrastructure", "offline-token", "flipiq-production", True)

    def test_environment_checks_do_not_duplicate_actor_authorization(self) -> None:
        with patch.object(auth, "github_json", side_effect=[protected_environment(), dev_branches()]) as request:
            auth.validate_environment("blackoutsecure/int-blackout-infrastructure", "offline-token", "flipiq-production", True)
        self.assertEqual(request.call_count, 2)
        self.assertTrue(all("/environments/" in call.args[0] for call in request.call_args_list))

    def test_planning_requires_branch_controls_but_not_reviewers(self) -> None:
        value = protected_environment()
        value["can_admins_bypass"] = True
        value["protection_rules"] = []
        with patch.object(auth, "github_json", side_effect=[value, dev_branches()]):
            auth.validate_environment("blackoutsecure/int-blackout-infrastructure", "offline-token", "flipiq-production-plan", False)

    def test_invalid_reviewers_are_not_approval(self) -> None:
        for reviewer in ({}, {"id": 1}, {"type": "User", "reviewer": {"id": True, "type": "User", "login": "owner"}}, {"type": "Team", "reviewer": {"id": 1}}, None):
            with self.subTest(reviewer=reviewer):
                self.assertFalse(auth.is_human_reviewer(reviewer))
        self.assertTrue(auth.is_human_reviewer({"type": "Team", "reviewer": {"id": 1, "slug": "operators"}}))

    def test_admin_bypass_must_be_explicitly_disabled(self) -> None:
        for bypass in (True, None, "false", 0):
            value = protected_environment()
            value["can_admins_bypass"] = bypass
            with self.subTest(bypass=bypass), patch.object(auth, "github_json", side_effect=[value, dev_branches()]):
                with self.assertRaisesRegex(ValueError, "administrator bypass"):
                    auth.validate_environment("blackoutsecure/int-blackout-infrastructure", "offline-token", "flipiq-production", True)

    def test_only_one_exact_dev_branch_is_allowed(self) -> None:
        for branches in (
            {"total_count": 1, "branch_policies": [{"name": "*", "type": "branch"}]},
            {"total_count": 1, "branch_policies": [{"name": "dev", "type": "tag"}]},
            {"total_count": 2, "branch_policies": [{"name": "dev", "type": "branch"}]},
            {"total_count": True, "branch_policies": [{"name": "dev", "type": "branch"}]},
            {"total_count": 1, "branch_policies": []},
            None,
        ):
            with self.subTest(branches=branches), patch.object(auth, "github_json", side_effect=[protected_environment(), branches]):
                with self.assertRaises(ValueError):
                    auth.validate_environment("blackoutsecure/int-blackout-infrastructure", "offline-token", "flipiq-production-plan", False)

    def test_protected_branches_are_not_an_exact_dev_policy(self) -> None:
        for policy in (None, {}, {"custom_branch_policies": True, "protected_branches": True}):
            value = protected_environment()
            value["deployment_branch_policy"] = policy
            with self.subTest(policy=policy), patch.object(auth, "github_json", return_value=value):
                with self.assertRaisesRegex(ValueError, "dev-only"):
                    auth.validate_environment("blackoutsecure/int-blackout-infrastructure", "offline-token", "flipiq-production", True)

    def test_unassessed_gate_never_reaches_environment_api(self) -> None:
        for authorized, enforced in (("", ""), ("true", "false"), ("false", "true")):
            for event, operation in (("workflow_dispatch", "apply"), ("schedule", "drift")):
                with self.subTest(authorized=authorized, enforced=enforced, event=event), patch.dict(os.environ, {
                    "GITHUB_EVENT_NAME": event, "GITHUB_REF": "refs/heads/dev",
                    "INFRA_OPERATION": operation, "GATEKEEPER_AUTHORIZED": authorized, "GATEKEEPER_ENFORCED": enforced,
                }, clear=True), patch.object(auth, "github_json") as request, redirect_stderr(io.StringIO()) as error:
                    self.assertEqual(auth.main([]), 1)
                    request.assert_not_called()
                    self.assertIn("enforced Gatekeeper", error.getvalue())

    def test_api_failure_denies_without_credentials_in_diagnostics(self) -> None:
        with patch.dict(os.environ, {
            "GITHUB_EVENT_NAME": "workflow_dispatch", "GITHUB_REF": "refs/heads/dev",
            "GITHUB_REPOSITORY": "blackoutsecure/int-blackout-infrastructure",
            "INFRA_ENVIRONMENT": "flipiq-production", "INFRA_OPERATION": "apply",
            "GH_TOKEN": "offline-only-secret", "GATEKEEPER_AUTHORIZED": "true", "GATEKEEPER_ENFORCED": "true",
        }, clear=True), patch.object(auth, "github_json", side_effect=OSError("metadata unavailable")), redirect_stderr(io.StringIO()) as error:
            self.assertEqual(auth.main([]), 1)
            self.assertIn("metadata unavailable", error.getvalue())
            self.assertNotIn("offline-only-secret", error.getvalue())

    def test_supported_events_never_enable_scheduled_apply_or_untrusted_refs(self) -> None:
        for event, operation, branch, allowed in (
            ("workflow_dispatch", "plan", "dev", True),
            ("workflow_dispatch", "apply", "dev", True),
            ("schedule", "drift", "dev", True),
            ("schedule", "apply", "dev", False),
            ("pull_request", "plan", "dev", False),
            ("workflow_dispatch", "destroy", "dev", False),
            ("workflow_dispatch", "apply", "main", False),
        ):
            with self.subTest(event=event, operation=operation, branch=branch), patch.dict(os.environ, {
                "GITHUB_EVENT_NAME": event, "GITHUB_REF": f"refs/heads/{branch}",
                "INFRA_OPERATION": operation, "GATEKEEPER_AUTHORIZED": "true", "GATEKEEPER_ENFORCED": "true",
            }, clear=True), patch.object(auth, "validate_environment") as validate, redirect_stderr(io.StringIO()):
                self.assertEqual(auth.main([]), 0 if allowed else 1)
                self.assertEqual(validate.call_count, 1 if allowed else 0)

    def test_gatekeeper_is_enforced_before_secrets_and_again_on_job_reruns(self) -> None:
        source = (ROOT / ".github" / "workflows" / "bos-universal-infrastructure.yml").read_text(encoding="utf-8")
        _, jobs = source.split("\n  authorize:\n", 1)
        authorize_job, execute_job = jobs.split("\n  execute:\n", 1)
        self.assertNotIn("secrets.", authorize_job)
        self.assertNotIn("\n    environment:", authorize_job)
        self.assertIn("needs: [inspect, authorize]", execute_job)
        self.assertIn("needs.authorize.outputs.authorized == 'true' && needs.authorize.outputs.enforced == 'true'", execute_job)
        self.assertIn("workflow_dispatch:*|schedule:drift", source)
        for job in (authorize_job, execute_job):
            self.assertIn("permissions:\n      contents: read", job)
            self.assertIn("uses: ./.hub/.github/actions/infrastructure-authorize", job)
            self.assertIn("trusted_app_slug: ${{ vars.CLOUD_COMPASS_APP_SLUG }}", job)
        self.assertIn("GATEKEEPER_AUTHORIZED: ${{ steps.gate.outputs.authorized }}", execute_job)
        self.assertIn("GATEKEEPER_ENFORCED: ${{ steps.gate.outputs.enforced }}", execute_job)
        self.assertLess(execute_job.index("id: gate"), execute_job.index("READER_APP_PRIVATE_KEY"))
        self.assertLess(execute_job.index("authorize.py"), execute_job.index("READER_APP_PRIVATE_KEY"))

    def test_shared_gatekeeper_policy_has_no_permission_or_event_shortcuts(self) -> None:
        source = (ROOT / ".github" / "actions" / "infrastructure-authorize" / "action.yml").read_text(encoding="utf-8")
        self.assertRegex(source, r"uses: blackoutsecure/bos-workflow-gatekeeper@[a-f0-9]{40} # v\d+\.\d+\.\d+\b")
        self.assertIn("actor: ${{ github.triggering_actor || github.actor }}", source)
        self.assertIn("trusted_app_slugs: ${{ steps.context.outputs.trusted_app_slug }}", source)
        self.assertIn("CLOUD_COMPASS_APP_SLUG must be one exact App slug", source)
        self.assertIn("required_repo_permission: write", source)
        self.assertIn('allow_org_admin: "false"', source)
        self.assertIn('restrict_to_events: "*"', source)
        self.assertIn('fail_closed: "true"', source)
        self.assertNotIn("secrets.", source)
        self.assertNotIn("TOKEN", source.split("id: gate", 1)[0])


class WorkerDeliveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name).resolve()
        self.application = self.root / ".workloads" / "example"
        for relative in (
            "package.json", "node_modules/tsx/dist/cli.mjs",
            "scripts/deployment.ts", "scripts/verify-deployment.ts", ".wrangler/production.jsonc",
        ):
            path = self.application / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("{}", encoding="utf-8")
        self.configuration = self.application / ".wrangler" / "production.jsonc"
        self.secrets = self.root / "private-secrets.json"
        self.secrets.write_text("{}", encoding="utf-8")

    def tearDown(self) -> None:
        self.directory.cleanup()

    def deliver(self) -> None:
        delivery.deliver(self.application, self.configuration, self.secrets, self.root)

    def test_migration_deployment_and_smoke_follow_the_application_contract(self) -> None:
        with patch.object(delivery.subprocess, "run") as run:
            self.deliver()
        self.assertEqual(run.call_count, 3)
        calls = [call.args[0] for call in run.call_args_list]
        self.assertEqual([call[3] for call in calls[:2]], ["migrate", "deploy"])
        self.assertIn("--secrets-file", calls[1])
        self.assertNotIn("--secrets-file", calls[0])
        self.assertEqual(calls[2][2], str(self.application / "scripts" / "verify-deployment.ts"))
        for call in run.call_args_list:
            self.assertEqual(call.kwargs["cwd"], self.application)
            self.assertIs(call.kwargs["check"], True)
            self.assertNotIn("terraform", call.args[0])

    def test_missing_verifier_blocks_before_any_migration(self) -> None:
        (self.application / "scripts" / "verify-deployment.ts").unlink()
        with patch.object(delivery.subprocess, "run") as run, self.assertRaises(ValueError):
            self.deliver()
        run.assert_not_called()

    def test_nonfile_configuration_or_secrets_blocks_before_migration(self) -> None:
        for attribute in ("configuration", "secrets"):
            path = getattr(self, attribute)
            path.unlink()
            path.mkdir()
            with self.subTest(attribute=attribute), patch.object(delivery.subprocess, "run") as run, self.assertRaises(ValueError):
                self.deliver()
            run.assert_not_called()
            path.rmdir()
            path.write_text("{}", encoding="utf-8")

    def test_each_failed_delivery_phase_stops_following_phases(self) -> None:
        for phase in range(3):
            outcomes = [None] * phase + [subprocess.CalledProcessError(1, "offline-delivery")]
            with self.subTest(phase=phase), patch.object(delivery.subprocess, "run", side_effect=outcomes) as run:
                with self.assertRaises(subprocess.CalledProcessError):
                    self.deliver()
                self.assertEqual(run.call_count, phase + 1)

    def test_configuration_cannot_escape_the_application(self) -> None:
        self.configuration = self.secrets
        with patch.object(delivery.subprocess, "run") as run, self.assertRaises(ValueError):
            self.deliver()
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
