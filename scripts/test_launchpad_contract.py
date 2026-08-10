#!/usr/bin/env python3
"""Validate hub runtime, managed caller, branch, and documentation contracts."""

from __future__ import annotations

import importlib.util
import io
import json
import re
import tempfile
from contextlib import redirect_stderr
from pathlib import Path
from urllib.parse import unquote


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


def caller_input_names(body: str, workflow_name: str) -> set[str]:
    call = body.split(
        "    uses: blackoutsecure/bos-automation-hub/"
        f".github/workflows/{workflow_name}@main\n",
        1,
    )[1]
    inputs = call.split("    with:\n", 1)[1].split("    secrets:\n", 1)[0]
    return set(re.findall(r"^      ([a-z][a-z0-9_]+):", inputs, re.MULTILINE))


def assert_markdown_links_exist(path: Path) -> None:
    missing = set()
    for raw_target in re.findall(r"\[[^]]+\]\(([^)]+)\)", path.read_text()):
        target = unquote(raw_target.split("#", 1)[0].strip())
        if not target or "://" in target or target.startswith(("#", "mailto:")):
            continue
        if not (path.parent / target).resolve().exists():
            missing.add(raw_target)
    assert not missing, {str(path.relative_to(ROOT)): sorted(missing)}


def main() -> None:
    workflow = (ROOT / ".github/workflows/bos-universal-launchpad.yml").read_text()
    kicker = (
        ROOT / "managed-files/workflows/bos-universal-launchpad-kicker.yml"
    ).read_text()

    declared = workflow_input_names(workflow)
    forwarded = caller_input_names(kicker, "bos-universal-launchpad.yml")
    assert declared == forwarded, {
        "missing": sorted(declared - forwarded),
        "unknown": sorted(forwarded - declared),
    }

    assert "managed-files-guard:" not in kicker
    assert "sync_managed_files:" not in kicker
    assert "sync_mode:" not in kicker
    assert "sync-managed-files.yml@main" not in workflow
    assert (
        "security_scan.enable != false" in kicker
    ), "managed Universal callers must enable the release security gate by default"

    promote = (ROOT / ".github/workflows/release-promote.yml").read_text()
    dependabot_input = promote.split("      include_dependabot_config:\n", 1)[
        1
    ].split("      include_github_metadata:\n", 1)[0]
    assert "        default: true\n" in dependabot_input

    sync_path = ROOT / ".github/actions/sync-managed-files/sync.py"
    sync_source = sync_path.read_text()
    sync = load_sync_module()
    services = sync.parse_services(
        "bos_launchpad bos_universal_security bos_launchpad_config "
        "bos_universal_marketplace bos_universal_sync"
    )
    with tempfile.TemporaryDirectory() as root:
        _, drift = sync.sync_files(services, root)
    generated_config = json.loads(sync.SERVICE_INIT_FILES["bos_launchpad_config"]["bos-launchpad-config.json"])
    assert generated_config["marketplace"]["enabled"] is False
    assert generated_config["marketplace"]["target_branch"] == "main"
    assert {change.path for change in drift} == {
        ".github/workflows/bos-universal-launchpad-kicker.yml",
        ".github/workflows/bos-universal-security-kicker.yml",
        ".github/workflows/bos-universal-marketplace-kicker.yml",
        ".github/workflows/bos-universal-sync-kicker.yml",
        "bos-launchpad-config.json",
    }
    for removed_service in (
        "bos_launchpad_gate",
        "bos_marketplace",
        "gha_lint_node",
        "gha_lint_python",
        "gha_lint_shell",
        "license_apache2",
        "bos_launchpad_sync_files",
    ):
        assert removed_service not in sync.KNOWN_SERVICES
    sync_action = (
        ROOT / ".github/actions/sync-managed-files/action.yml"
    ).read_text()
    for removed_service in (
        "bos_launchpad_gate",
        "bos_marketplace",
        "gha_lint_node",
        "gha_lint_python",
        "gha_lint_shell",
        "license_apache2",
        "bos_launchpad_sync_files",
    ):
        assert removed_service not in sync_action
    assert "bos_universal_security" in sync_action
    assert "bos_universal_marketplace" in sync_action
    assert "bos_universal_sync" in sync_action
    assert "_GHA_LINT_NODE_YML" not in sync_source
    assert "_BOS_LAUNCHPAD_CF_PAGES_YML" not in sync_source
    assert not (
        ROOT / ".github/actions/summarize-launchpad-config/action.yml"
    ).exists()
    managed_sync_caller = sync.SERVICE_FILES["bos_universal_sync"][
        ".github/workflows/bos-universal-sync-kicker.yml"
    ]
    assert "github.event.repository.default_branch" in managed_sync_caller
    assert "branches: [main]" not in managed_sync_caller
    assert managed_sync_caller.count("sync-managed-files.yml@main") == 1
    assert "fromJson(needs.parse-config.outputs.sync).enabled == true" in managed_sync_caller
    assert "bos-launchpad-config.json" in managed_sync_caller
    assert "bos-managed-files.yaml" in managed_sync_caller
    assert ".github/workflows/bos-universal-sync-kicker.yml" not in managed_sync_caller
    assert sync.parse_services("bos_launchpad bos_universal_sync") == [
        "bos_launchpad",
        "bos_universal_sync",
    ]

    gate_workflow = (ROOT / ".github/workflows/bos-gate.yml").read_text()
    security_kicker = (
        ROOT / "managed-files/workflows/bos-universal-security-kicker.yml"
    ).read_text()
    gate_declared = workflow_input_names(gate_workflow)
    gate_forwarded = caller_input_names(security_kicker, "bos-gate.yml")
    assert gate_declared == gate_forwarded, {
        "missing": sorted(gate_declared - gate_forwarded),
        "unknown": sorted(gate_forwarded - gate_declared),
    }
    assert "name: Blackout Secure universal security (reusable)" in gate_workflow
    assert "name: security" in security_kicker
    assert "name: Security summary" in gate_workflow
    assert "kit_version:" not in gate_workflow
    assert "code_scanning_kit_version:" not in gate_workflow
    assert "marketplace-action-ci.yml@main" not in gate_workflow
    assert "enable_marketplace_ci:" not in gate_workflow
    assert "enable_baseline:" not in gate_workflow
    assert "if: ${{ !inputs.enable_lint }}" in gate_workflow

    readme = (ROOT / "README.md").read_text()
    readme_header_action = (
        ROOT / ".github/actions/shared/check-readme-header/action.yml"
    ).read_text()
    assert "enable_baseline" not in readme
    assert "## Universal sync" in readme
    assert "never traverses the release" in readme
    assert "bos-launchpad-release.yml" not in readme_header_action
    assert "bos-universal-launchpad-kicker.yml" in readme_header_action

    assert set(sync.SERVICE_FILES["org_defaults"]) == {
        "CODE_OF_CONDUCT.md",
        "CONTRIBUTING.md",
        ".github/FUNDING.yml",
        "SECURITY.md",
        "SUPPORT.md",
        ".github/PULL_REQUEST_TEMPLATE.md",
        ".github/ISSUE_TEMPLATE/bug_report.md",
        ".github/ISSUE_TEMPLATE/config.yml",
        ".github/ISSUE_TEMPLATE/feature_request.md",
        "profile/README.md",
    }
    assert sync.SERVICE_FILES["org_defaults"]["SECURITY.md"] == (
        ROOT / "managed-files/community-health/SECURITY.md"
    ).read_text()
    assert sync.SERVICE_FILES["org_defaults"]["profile/README.md"] == (
        ROOT / "managed-files/org-profile/README.md"
    ).read_text()
    with tempfile.TemporaryDirectory() as temp_dir:
        with redirect_stderr(io.StringIO()):
            try:
                sync.sync_files(["org_defaults"], temp_dir)
            except SystemExit as exc:
                assert exc.code == 1
            else:
                raise AssertionError("org_defaults must reject consumer repositories")
        Path(temp_dir, "bos-managed-files.yaml").write_text(
            "target_repo_role: org-default-repo\n"
        )
        changes, _ = sync.sync_files(["org_defaults"], temp_dir)
        assert {change.path for change in changes if change.changed} == set(
            sync.SERVICE_FILES["org_defaults"]
        )

    marketplace_kicker = (
        ROOT / "managed-files/workflows/bos-universal-marketplace-kicker.yml"
    ).read_text()
    assert set(sync.SERVICE_FILES["bos_universal_marketplace"]) == {
        ".github/workflows/bos-universal-marketplace-kicker.yml",
    }
    assert not (ROOT / ".github/workflows/bos-launchpad-marketplace.yml").exists()
    assert not (
        ROOT / "managed-files/workflows/bos-launchpad-marketplace.yml"
    ).exists()
    assert marketplace_kicker.count("marketplace-action-ci.yml@main") == 1
    assert marketplace_kicker.count("marketplace-repo-guard.yml@main") == 1
    assert marketplace_kicker.count("release-promote.yml@main") == 1
    assert "pull_request_target:" in marketplace_kicker
    assert "github.event.repository.default_branch" in marketplace_kicker
    assert not re.search(r"source_branch:\s+dev\b", marketplace_kicker)

    with tempfile.TemporaryDirectory() as temp_dir:
        workflow_dir = Path(temp_dir, ".github/workflows")
        workflow_dir.mkdir(parents=True)
        for legacy_path, legacy_service in (
            ("bos-launchpad-sync-files.yml", "bos_launchpad_sync_files"),
            ("bos-launchpad-gate.yml", "bos_launchpad_gate"),
            ("bos-marketplace-guard.yml", "bos_marketplace"),
            ("bos-marketplace-release.yml", "bos_marketplace"),
        ):
            Path(workflow_dir, legacy_path).write_text(
                sync._make_whole_file(legacy_service, "name: legacy\n")
            )
        _, retirement_drift = sync.sync_files(
            [
                "bos_universal_sync",
                "bos_universal_security",
                "bos_universal_marketplace",
            ],
            temp_dir,
        )
        retired = {change.path for change in retirement_drift if change.delete}
        assert retired == {
            ".github/workflows/bos-launchpad-sync-files.yml",
            ".github/workflows/bos-launchpad-gate.yml",
            ".github/workflows/bos-marketplace-guard.yml",
            ".github/workflows/bos-marketplace-release.yml",
        }

    artifact_release = (ROOT / ".github/workflows/release.yml").read_text()
    marketplace_promote = (
        ROOT / ".github/workflows/release-promote.yml"
    ).read_text()
    assert artifact_release.startswith(
        "# Tag-driven release **pipeline** (reusable meta-workflow)."
    )
    assert "name: Artifact release (reusable)" in artifact_release
    assert "name: Marketplace promotion release (reusable)" in marketplace_promote
    assert "release.yml@main" in workflow
    assert "release-promote.yml@main" in marketplace_kicker
    assert "release-promote.yml" not in artifact_release
    assert ".github/workflows/release.yml@main" not in marketplace_promote
    publisher_call = (
        "uses: blackoutsecure/bos-automation-hub/"
        ".github/workflows/github-release.yml@main"
    )
    assert len(
        re.findall(
            rf"^\s+{re.escape(publisher_call)}$", artifact_release, re.MULTILINE
        )
    ) == 1
    assert len(
        re.findall(
            rf"^\s+{re.escape(publisher_call)}$", marketplace_promote, re.MULTILINE
        )
    ) == 1

    workflows = sorted((ROOT / ".github/workflows").glob("*.yml"))
    reusable = {
        path.name for path in workflows if "\n  workflow_call:\n" in path.read_text()
    }
    event_only = {path.name for path in workflows} - reusable
    assert event_only == {
        "lint.yml",
        "openwrt-readsb-wiedehopf-bump.yml",
        "release-hub.yml",
        "sync-managed-config.yml",
    }

    release_hub = (ROOT / ".github/workflows/release-hub.yml").read_text()
    assert "name: Hub runtime release" in release_hub
    assert "grep -lE '^  workflow_call:'" in release_hub
    assert "DENYLIST=(" not in release_hub
    assert "${{ github.event.repository.default_branch }}" in release_hub
    assert not re.search(r"(?:ref:|source_branch:)\s+dev\b", release_hub)
    assert "origin/dev" not in release_hub
    assert "refs/heads/dev" not in release_hub
    assert "release-promote.yml@main" not in release_hub
    assert "uses: ./.github/workflows/github-release.yml" in release_hub

    balena_block = (
        ROOT / ".github/workflows/balena-block-publish.yml"
    ).read_text()
    balena_fleet = (
        ROOT / ".github/workflows/balena-fleet-deploy.yml"
    ).read_text()
    balena_publisher = (
        ROOT / ".github/actions/shared/balena-publish/action.yml"
    ).read_text()
    shared_balena_action = (
        "uses: blackoutsecure/bos-automation-hub/"
        ".github/actions/shared/balena-publish@main"
    )
    for balena_workflow in (balena_block, balena_fleet):
        assert balena_workflow.count(shared_balena_action) == 1
        assert "balena-io/deploy-to-balena-action@" not in balena_workflow
        assert "require_runner_x64: 'true'" not in balena_workflow
        runs_on = re.findall(r"^\s+runs-on:.*$", balena_workflow, re.MULTILINE)
        assert runs_on and all("RUNNER_X64" not in line for line in runs_on)
    assert "default: 'v24.1.4'" in balena_publisher
    assert "${plat}-${arch}-standalone.tar.gz" in balena_publisher
    assert "sync-balena-yml:" in balena_block
    assert "matrix: ${{ fromJSON(needs.plan.outputs.matrix) }}" in balena_fleet

    for managed_caller in (
        kicker,
        security_kicker,
        marketplace_kicker,
        managed_sync_caller,
    ):
        refs = re.findall(r"uses: blackoutsecure/bos-automation-hub/[^\s]+@(\w+)", managed_caller)
        assert refs and set(refs) == {"main"}, refs

    sync_config = (ROOT / ".github/workflows/sync-managed-config.yml").read_text()
    hub_config = json.loads((ROOT / "bos-launchpad-config.json").read_text())
    assert hub_config["sync_files"]["services"] == ["common", "lf_line_endings"]
    assert "join(fromJson(needs.parse-config.outputs.sync).services" in sync_config
    assert "uses: ./.github/actions/sync-managed-files" in sync_config
    assert "uses: ./.github/actions/shared/commit-and-push" in sync_config
    assert "bos-automation-hub/.github/workflows/sync-managed-files.yml@main" not in sync_config

    assert_markdown_links_exist(ROOT / "README.md")
    assert_markdown_links_exist(ROOT / "managed-files/README.md")

    print(
        f"repository contract valid: {len(declared)} launchpad inputs, "
        f"{len(gate_declared)} gate inputs, {len(reusable)} runtime workflows, "
        f"{len(services) + 1} managed services"
    )


if __name__ == "__main__":
    main()