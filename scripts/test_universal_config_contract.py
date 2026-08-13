#!/usr/bin/env python3
"""Validate hub runtime, managed caller, branch, and documentation contracts."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path
from urllib.parse import unquote


ROOT = Path(__file__).resolve().parent.parent


def workflow_input_names(body: str) -> set[str]:
    inputs = body.split("    inputs:\n", 1)[1].split("    secrets:\n", 1)[0]
    return set(re.findall(r"^      ([a-z][a-z0-9_]+):", inputs, re.MULTILINE))


def caller_input_names(body: str, workflow_name: str) -> set[str]:
    call_pattern = re.compile(
        r"^    uses: (?:\./|blackoutsecure/bos-automation-hub/)"
        rf"\.github/workflows/{re.escape(workflow_name)}(?:@\w+)?$",
        re.MULTILINE,
    )
    match = call_pattern.search(body)
    assert match is not None, workflow_name
    call = body[match.end() :]
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


def run_universal_config(config: object) -> subprocess.CompletedProcess[str]:
    return run_universal_config_raw(json.dumps(config))


def run_universal_config_raw(raw_text: str) -> subprocess.CompletedProcess[str]:
    action = (
        ROOT / ".github/actions/shared/universal-config/action.yml"
    ).read_text()
    script = action.split("        python3 - <<'PY'\n", 1)[1].split(
        "\n        PY", 1
    )[0]
    with tempfile.TemporaryDirectory() as temp_dir:
        temp = Path(temp_dir)
        config_path = temp / ".github/bos-universal-config.json"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(raw_text)
        env = os.environ | {
            "CONFIG_PATH": ".github/bos-universal-config.json",
            "ALLOW_MISSING": "false",
            "GITHUB_OUTPUT": str(temp / "output"),
            "GITHUB_STEP_SUMMARY": str(temp / "summary"),
        }
        result = subprocess.run(
            [sys.executable, "-c", textwrap.dedent(script)],
            cwd=temp,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode == 0:
            result.stdout += (temp / "output").read_text()
        return result


def main() -> None:
    normalized = run_universal_config(
        {
            "marketplace": {
                "allowlist_paths": ["action.yml", "README.md"],
                "blocked_paths": ".github/workflows/\ntest/",
                "required_paths": [],
                "extra_sync_paths": ["NOTICE"],
                "repo_metadata": {
                    "enable": True,
                    "topics_fallback": "github-actions security",
                },
            }
        }
    )
    assert normalized.returncode == 0, normalized.stderr
    cfg_output = normalized.stdout.split("cfg<<__BOS_EOF__\n", 1)[1].split(
        "\n__BOS_EOF__", 1
    )[0]
    marketplace = json.loads(cfg_output)["marketplace"]
    assert marketplace["allowlist_paths"] == "action.yml\nREADME.md"
    assert marketplace["blocked_paths"] == ".github/workflows/\ntest/"
    assert marketplace["required_paths"] == ""
    assert marketplace["extra_sync_paths"] == "NOTICE"
    assert marketplace["repo_metadata"] == {
        "enable": True,
        "topics_fallback": "github-actions security",
    }
    malformed = run_universal_config(
        {"marketplace": {"allowlist_paths": ["action.yml", 3]}}
    )
    assert malformed.returncode == 1
    assert "marketplace.allowlist_paths[1] must be a non-empty string" in malformed.stderr

    # Invalid JSON syntax must fail cleanly with a line/column-annotated
    # error, not a raw Python traceback.
    invalid_json = run_universal_config_raw('{"gate": {"enable_lint": true,}}')
    assert invalid_json.returncode == 1
    assert "Invalid JSON" in invalid_json.stderr
    assert "line" in invalid_json.stderr and "column" in invalid_json.stderr
    assert "Traceback (most recent call last)" not in invalid_json.stderr

    def cfg_from(result: subprocess.CompletedProcess[str]) -> dict:
        assert result.returncode == 0, result.stderr
        return json.loads(
            result.stdout.split("cfg<<__BOS_EOF__\n", 1)[1].split(
                "\n__BOS_EOF__", 1
            )[0]
        )

    action_test_defaults = run_universal_config({})
    assert action_test_defaults.returncode == 0, action_test_defaults.stderr
    action_test_output = action_test_defaults.stdout.split("action_test=", 1)[1].split(
        "\n", 1
    )[0]
    assert json.loads(action_test_output) == {
        "python_versions": ["3.11"],
        "os_matrix": ["ubuntu-latest"],
        "python_packages": ["pytest>=8.0", "ruff>=0.6", "PyYAML>=6.0"],
        "pytest_args": "-q",
        "enable_smoke_test": False,
        "smoke_trigger": "push-dev",
        "smoke_test_config": {},
        "timeout_pytest": 10,
        "timeout_smoke": 5,
    }

    # Grouped-section authoring layout hoists to the flat keys every
    # downstream kicker/normalizer reads — both layouts must resolve
    # identically, and a flat key always wins over its section alias.
    grouped = cfg_from(
        run_universal_config(
            {
                "security": {"enable_lint": True, "enable_shell_lint": True},
                "launchpad": {
                    "upstream": {"repo": "owner/grouped"},
                    "docker": {"image_name": "grouped-image"},
                },
                "marketplace": {"enabled": True},
            }
        )
    )
    assert grouped["gate"] == {
        "enable_lint": True,
        "enable_shell_lint": True,
    }
    assert grouped["upstream"]["repo"] == "owner/grouped"
    assert grouped["docker"]["image_name"] == "grouped-image"
    assert grouped["marketplace"]["enabled"] is True

    flat_wins = cfg_from(
        run_universal_config(
            {
                "gate": {"enable_lint": False},
                "security": {"enable_lint": True},
            }
        )
    )
    assert flat_wins["gate"] == {"enable_lint": False}

    # "general" is a catch-all for keys owned by neither of the four named
    # services — every key it holds is hoisted as-is (no fixed allowlist),
    # so a brand-new standalone service's block lands there first.
    general = cfg_from(
        run_universal_config(
            {
                "general": {
                    "action_test": {"python_versions": ["3.12"]},
                    "upstream": {"repo": "should-not-win"},
                },
                "upstream": {"repo": "owner/flat-wins"},
            }
        )
    )
    assert general["action_test"] == {"python_versions": ["3.12"]}
    assert general["upstream"]["repo"] == "owner/flat-wins"

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
    assert "bos-universal-sync.yml@main" not in workflow
    assert (
        "security_scan.enable != false" in kicker
    ), "managed Universal callers must enable the release security gate by default"

    promote = (ROOT / ".github/workflows/release-promote.yml").read_text()
    dependabot_input = promote.split("      include_dependabot_config:\n", 1)[
        1
    ].split("      include_github_metadata:\n", 1)[0]
    assert "        default: true\n" in dependabot_input


    gate_workflow = (ROOT / ".github/workflows/bos-universal-security.yml").read_text()
    security_kicker = (
        ROOT / "managed-files/workflows/bos-universal-security-kicker.yml"
    ).read_text()
    gate_declared = workflow_input_names(gate_workflow)
    security_job = security_kicker.split(
        "    uses: blackoutsecure/bos-automation-hub/.github/workflows/"
        "bos-universal-security.yml@dev\n",
        1,
    )[1]
    assert "      config_authoritative: true\n" in security_job
    assert "  workflow_dispatch:" in gate_workflow
    assert not (ROOT / ".github/workflows/bos-universal-security-kicker.yml").exists()
    assert "name: Blackout Secure universal security (reusable)" in gate_workflow
    assert "name: security" in security_kicker
    assert "name: Security summary" in gate_workflow
    assert "kit_version:" not in gate_workflow
    assert "code_scanning_kit_version:" not in gate_workflow
    assert "marketplace-action-ci.yml@main" not in gate_workflow
    assert "bos-universal-marketplace.yml@main" not in gate_workflow
    assert "enable_marketplace_ci:" not in gate_workflow
    assert "enable_baseline:" not in gate_workflow
    assert "needs.resolve-config.outputs.gate" in gate_workflow
    assert "actions/shared/universal-config@main" in gate_workflow
    assert "code_scan_fail_on:" in gate_workflow
    assert "code_scan_http_timeout:" in gate_workflow
    assert (
        "fail_on: ${{ fromJSON(needs.resolve-config.outputs.gate).code_scan_fail_on }}"
        in gate_workflow
    )
    assert (
        "http_timeout: ${{ fromJSON(needs.resolve-config.outputs.gate).code_scan_http_timeout }}"
        in gate_workflow
    )
    assert "github_token: ${{ secrets.SCANNING_PAT || secrets.GITHUB_TOKEN }}" in gate_workflow

    readme = (ROOT / "README.md").read_text()
    readme_header_action = (
        ROOT / ".github/actions/shared/check-readme-header/action.yml"
    ).read_text()
    assert "enable_baseline" not in readme
    assert "## Universal sync" in readme
    assert "never traverses the release" in readme
    assert "### Elevated posture scanning (`SCANNING_PAT`)" in readme
    assert "security_scan.use_advanced_pat" in readme
    assert "bos-launchpad-release.yml" not in readme_header_action
    assert "bos-universal-launchpad-kicker.yml" in readme_header_action
    assert "# Blackout Secure README Header Audit" in readme_header_action
    assert "outcome=${outcome}" in readme_header_action
    assert "RH001" in readme_header_action and "RH030" in readme_header_action

    marketplace_kicker = (
        ROOT / "managed-files/workflows/bos-universal-marketplace-kicker.yml"
    ).read_text()
    for managed_template in (
        ROOT / "managed-files/workflows"
    ).glob("*.yml"):
        assert "\non:\n" not in managed_template.read_text()
        assert "\n\"on\":\n" in managed_template.read_text()
    assert not (ROOT / ".github/workflows/bos-launchpad-marketplace.yml").exists()
    assert not (
        ROOT / "managed-files/workflows/bos-launchpad-marketplace.yml"
    ).exists()
    assert marketplace_kicker.count("bos-universal-marketplace.yml@main") == 1
    assert marketplace_kicker.count("marketplace-repo-guard.yml@main") == 1
    assert marketplace_kicker.count("release-promote.yml@main") == 1
    assert marketplace_kicker.count("repo-metadata-sync.yml@main") == 1
    assert "options: [validate, name-check, release, metadata]" in marketplace_kicker
    assert "needs.release.outputs.tag_name" in marketplace_kicker
    assert "needs.release.result == 'success'" in marketplace_kicker
    assert "&& !inputs.dry_run" in marketplace_kicker
    assert "&& !inputs.draft" in marketplace_kicker
    assert "REPO_ADMIN_PAT: ${{ secrets.REPO_ADMIN_PAT }}" in marketplace_kicker
    assert "RELEASE_PAT: ${{ secrets.RELEASE_PAT }}" in marketplace_kicker
    assert "outputs.cfg" in marketplace_kicker
    assert "`.github/bos-universal-config.json`" in marketplace_kicker
    assert "pull_request_target:" in marketplace_kicker
    assert "github.event.repository.default_branch" in marketplace_kicker
    assert not re.search(r"source_branch:\s+dev\b", marketplace_kicker)

    repo_metadata_workflow = (
        ROOT / ".github/workflows/repo-metadata-sync.yml"
    ).read_text()
    assert "  workflow_call:" in repo_metadata_workflow
    assert "\n  release:\n" not in repo_metadata_workflow
    assert repo_metadata_workflow.count(
        "uses: blackoutsecure/bos-automation-hub/"
        ".github/actions/repo-metadata@main"
    ) == 1
    assert "secrets.REPO_ADMIN_PAT || secrets.RELEASE_PAT || github.token" in repo_metadata_workflow
    repo_metadata_action = (
        ROOT / ".github/actions/repo-metadata/action.yml"
    ).read_text()
    assert "MODELS_TOKEN:        ${{ github.token || inputs.github_token }}" in repo_metadata_action
    assert "group: repo-metadata-${{ github.repository }}" in repo_metadata_workflow
    assert "inputs.checkout_ref || github.sha" in repo_metadata_workflow
    assert workflow.count("uses: ./.github/workflows/repo-metadata-sync.yml") == 1
    assert ".github/actions/repo-metadata@main" not in workflow

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
    assert marketplace_promote.count(
        "uses: blackoutsecure/bos-automation-hub/"
        ".github/actions/shared/resolve-release-tag@main"
    ) == 1
    promote_hub_refs = re.findall(
        r"uses: blackoutsecure/bos-automation-hub/[^\s]+@(\w+)",
        marketplace_promote,
    )
    assert promote_hub_refs and set(promote_hub_refs) == {"main"}, promote_hub_refs
    assert marketplace_promote.count(
        "uses: blackoutsecure/bos-automation-hub/"
        ".github/actions/shared/preflight-runner-config@main"
    ) == 1
    assert "LATEST=\"$(git tag --list" not in marketplace_promote
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
    lint_workflow = (ROOT / ".github/workflows/lint.yml").read_text()
    assert "branches: [main, dev]" in lint_workflow
    reusable = {
        path.name for path in workflows if "\n  workflow_call:\n" in path.read_text()
    }
    event_only = {path.name for path in workflows} - reusable
    assert event_only == {
        "lint.yml",
        "openwrt-readsb-wiedehopf-bump.yml",
        "release-hub.yml",
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
    assert release_hub.count(
        "uses: ./.github/actions/shared/resolve-release-tag"
    ) == 1
    assert release_hub.count(
        "uses: ./.github/actions/shared/universal-config"
    ) == 1
    assert release_hub.count(
        "uses: ./.github/workflows/repo-metadata-sync.yml"
    ) == 1
    assert "checkout_ref: ${{ needs.compute-tag.outputs.tag_name }}" in release_hub
    assert "needs.release.result == 'success'" in release_hub
    assert "inputs.release_draft != true" in release_hub
    assert "REPO_ADMIN_PAT: ${{ secrets.REPO_ADMIN_PAT }}" in release_hub
    assert "RELEASE_PAT: ${{ secrets.RELEASE_PAT }}" in release_hub
    assert "LATEST=\"$(git tag --list" not in release_hub

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

    docker_workflow = (
        ROOT / ".github/workflows/docker-build-push.yml"
    ).read_text()
    compose_build_args = (
        "uses: blackoutsecure/bos-automation-hub/"
        ".github/actions/shared/compose-docker-build-args@main"
    )
    assert docker_workflow.count(compose_build_args) == 2
    assert "echo \"build_args<<__EOF__\"" not in docker_workflow

    # `kicker` (launchpad) only ever fires on `main` pushes; the other
    # dual-branch kickers resolve a static @dev or @main ref per run.
    for managed_caller, expected_refs in (
        (kicker, {"main"}),
        (security_kicker, {"main", "dev"}),
        (marketplace_kicker, {"main", "dev"}),
    ):
        refs = re.findall(r"uses: blackoutsecure/bos-automation-hub/[^\s]+@(\w+)", managed_caller)
        assert refs and set(refs) == expected_refs, refs

    sync_backend = (ROOT / ".github/workflows/bos-universal-sync.yml").read_text()
    hub_config_raw = json.loads((ROOT / ".github/bos-universal-config.json").read_text())
    assert set(hub_config_raw) == {"security", "launchpad"}
    global_sync_config = json.loads(
        (ROOT / ".github/blackout-secure-managed-file-sync-global-config.json").read_text()
    )
    assert set(global_sync_config) == {"managed_file_sync"}
    hub_config = cfg_from(
        run_universal_config_raw((ROOT / ".github/bos-universal-config.json").read_text())
    )
    assert hub_config["gate"] == {}
    assert hub_config["repo_metadata"] == {
        "enable": True,
        "homepage": "https://github.com/blackoutsecure/bos-automation-hub",
        "generate_topics": True,
        "topics_fallback": (
            "github-actions automation reusable-workflows composite-actions "
            "devops ci-cd workflow-automation"
        ),
    }
    assert "security_scan" not in hub_config
    assert global_sync_config["managed_file_sync"]["services"] == [
        "dotfiles",
        "dependabot_actions",
        "shellcheck",
    ]
    assert "use_marketplace_config" not in global_sync_config["managed_file_sync"]
    assert global_sync_config["managed_file_sync"]["variables"] == {
        "org_name": "Blackout Secure",
        "support_email": "engineering@blackoutsecure.com",
        "license": "Apache-2.0",
    }
    assert not (ROOT / ".github/workflows/sync-managed-config.yml").exists()
    assert "  workflow_call:" in sync_backend
    assert "  schedule:" in sync_backend
    assert "  workflow_dispatch:" in sync_backend
    assert ".github/blackout-secure-managed-file-sync-global-config.json" in sync_backend
    assert "use_global_config: 'true'" in sync_backend
    assert "config_path: .github/bos-universal-config.json" in sync_backend
    assert "dry_run: ${{ (inputs.mode || 'commit') == 'check' }}" in sync_backend
    assert "bos-managed-file-sync-action@v1" in sync_backend
    assert "actions/shared/commit-and-push@main" in sync_backend
    assert "workflows: write" not in sync_backend
    managed_sync_caller = (
        ROOT / "managed-files/workflows/bos-universal-sync-kicker.yml"
    ).read_text()
    assert ".github/blackout-secure-managed-file-sync-global-config.json" in managed_sync_caller
    assert "use_global_config: 'true'" in managed_sync_caller
    assert "config_path: .github/bos-universal-config.json" in managed_sync_caller
    assert "dry_run: ${{ (inputs.mode || 'commit') == 'check' }}" in managed_sync_caller
    assert "bos-managed-file-sync-action@v1" in managed_sync_caller
    assert "bos-universal-sync.yml@" not in managed_sync_caller
    assert "resolve-target:" not in managed_sync_caller

    assert_markdown_links_exist(ROOT / "README.md")
    assert_markdown_links_exist(ROOT / "managed-files/README.md")

    print(
        f"repository contract valid: {len(declared)} launchpad inputs, "
        f"{len(gate_declared)} gate inputs, {len(reusable)} runtime workflows"
    )


if __name__ == "__main__":
    main()