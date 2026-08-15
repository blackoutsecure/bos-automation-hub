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
    # Stop at the job-level `secrets:` key, whether it's an inline value
    # (`secrets: inherit`, the common case) or a nested mapping — both start
    # a line with exactly 4 spaces of indent then `secrets:`. Without this,
    # a caller with more than one job invoking the same reusable workflow
    # (e.g. a dev/main split) would bleed into the next job's `permissions:`
    # block, since a plain `"    secrets:\n"` literal never matches
    # `secrets: inherit`.
    inputs = re.split(r"\n    secrets:", call.split("    with:\n", 1)[1], maxsplit=1)[0]
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
        config_path = temp / ".github" / "bos-universal-config.json"
        config_path.parent.mkdir()
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
                "blocked_paths": [".github/workflows/", "test/"],
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
    legacy_paths = run_universal_config(
        {"marketplace": {"allowlist_paths": "action.yml\nREADME.md"}}
    )
    assert legacy_paths.returncode == 1
    assert "marketplace.allowlist_paths must be an array of strings" in legacy_paths.stderr

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
        "smoke_test_output_name": "version",
        "timeout_pytest": 10,
        "timeout_smoke": 5,
        "enable_ai_failure_summary": True,
        "ai_provider": "auto",
        "ai_model": "openai/gpt-4o-mini",
    }

    # ── organization section ──────────────────────────────────────
    # Runner topology and report policy are data, so an empty config
    # must still yield a complete, directly usable organization block.
    def org_from(result: subprocess.CompletedProcess[str]) -> dict:
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout.split("organization=", 1)[1].split("\n", 1)[0])

    org_defaults = org_from(run_universal_config({}))
    assert org_defaults["runners"] == {
        "default": "ubuntu-latest",
        "x64": "ubuntu-latest",
        "arm64": "ubuntu-latest",
    }
    assert org_defaults["reporting"] == {
        "enable_job_summary": True,
        "enable_annotations": True,
        "enable_html": True,
        "enable_pdf": False,
        "html_path": "blackout-secure-report.html",
        "pdf_path": "blackout-secure-report.pdf",
        "artifact_name": "blackout-secure-audit-report",
        "title_prefix": "Blackout Secure",
        "fail_on": "fail",
    }
    assert org_defaults["defaults"] == {"timeout_minutes": 30}
    assert set(org_defaults["workflows"]) == {
        "security", "sync", "launchpad", "marketplace", "action_test", "release",
    }
    assert all(
        entry == {"runs_on": "ubuntu-latest", "timeout_minutes": 30}
        for entry in org_defaults["workflows"].values()
    )

    # A per-workflow override wins over the org default; unset workflows
    # keep inheriting it.
    org_override = org_from(
        run_universal_config(
            {
                "organization": {
                    "runners": {"default": "ubuntu-24.04", "arm64": "ubuntu-24.04-arm"},
                    "defaults": {"timeout_minutes": 15},
                    "reporting": {"fail_on": "never", "enable_annotations": False},
                    "workflows": {"security": {"runs_on": ["self-hosted", "Linux"], "timeout_minutes": 45}},
                }
            }
        )
    )
    assert org_override["runners"]["arm64"] == "ubuntu-24.04-arm"
    assert org_override["runners"]["x64"] == "ubuntu-24.04"
    assert org_override["reporting"]["fail_on"] == "never"
    assert org_override["reporting"]["enable_annotations"] is False
    assert org_override["workflows"]["security"] == {
        "runs_on": ["self-hosted", "Linux"],
        "timeout_minutes": 45,
    }
    assert org_override["workflows"]["sync"] == {
        "runs_on": "ubuntu-24.04",
        "timeout_minutes": 15,
    }

    # A JSON-array label string resolves to a real array so callers can
    # feed the value straight into `runs-on:` without a startsWith guard.
    org_json_labels = org_from(
        run_universal_config(
            {"organization": {"runners": {"default": '["self-hosted","X64"]'}}}
        )
    )
    assert org_json_labels["runners"]["default"] == ["self-hosted", "X64"]

    bad_fail_on = run_universal_config(
        {"organization": {"reporting": {"fail_on": "sometimes"}}}
    )
    assert bad_fail_on.returncode == 1
    assert "organization.reporting.fail_on must be" in bad_fail_on.stderr

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
    action_test_workflow = (
        ROOT / ".github/workflows/bos-universal-action-test.yml"
    ).read_text()
    assert "source: ${{ fromJSON(needs.resolve-config.outputs.test).smoke_test_config.source || '' }}" in action_test_workflow
    assert "package_name: ${{ fromJSON(needs.resolve-config.outputs.test).smoke_test_config.package_name || '' }}" in action_test_workflow
    kicker = (
        ROOT / "sync-files/workflows/bos-universal-launchpad-kicker.yml"
    ).read_text()

    declared = workflow_input_names(workflow)
    forwarded = caller_input_names(kicker, "bos-universal-launchpad.yml")
    assert declared == forwarded, {
        "missing": sorted(declared - forwarded),
        "unknown": sorted(forwarded - declared),
    }
    assert "config_path: .github/bos-universal-config.json" in kicker
    assert "config_path: .github/bos-universal-config.json" in action_test_workflow

    monitor_workflow = (ROOT / ".github/workflows/monitor-upstream-release.yml").read_text()
    assert "bos-upstream-watcher@c91a6fd7d42161f1b37ab6da12ca6a6bbaabe739" in monitor_workflow
    assert "config_path: .github/bos-universal-config.json" in monitor_workflow
    assert "global_config_path: hub-config/sync-files/config/upstream-watcher-global-config.json" in monitor_workflow
    assert "use_global_config: 'auto'" in monitor_workflow
    assert "upstream_update_type:" in monitor_workflow
    assert "upstream_ai_status:" in monitor_workflow

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
        ROOT / "sync-files/workflows/bos-universal-security-kicker.yml"
    ).read_text()
    gate_declared = workflow_input_names(gate_workflow)
    security_job = security_kicker.split(
        "    uses: blackoutsecure/bos-automation-hub/.github/workflows/"
        "bos-universal-security.yml@dev\n",
        1,
    )[1]
    assert "      config_authoritative: true\n" in security_job
    assert "  workflow_dispatch:" in gate_workflow
    assert (ROOT / ".github/workflows/bos-universal-security-kicker.yml").exists()
    assert "name: Blackout Secure Universal Security" in gate_workflow
    assert "name: security" in security_kicker
    assert "name: Security summary" in gate_workflow
    assert "kit_version:" not in gate_workflow
    assert "code_scanning_kit_version:" not in gate_workflow
    assert "marketplace-action-ci.yml@main" not in gate_workflow
    assert "bos-universal-marketplace.yml@main" not in gate_workflow
    assert "enable_marketplace_ci:" not in gate_workflow
    assert "  schedule:\n    - cron: '43 14 * * *'" in security_kicker
    assert "  push:\n    branches: [main, dev]" in security_kicker
    assert "enable_baseline:" not in gate_workflow
    assert "needs.resolve-config.outputs.gate" in gate_workflow
    assert "uses: ./hub-runtime/.github/actions/shared/universal-config" in gate_workflow
    assert "inputs.hub_ref != 'auto' && inputs.hub_ref" in gate_workflow
    assert "github.event_name == 'pull_request' && github.base_ref" in gate_workflow
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
    assert "## Managed file sync" in readme
    assert "The reusable workflow never self-triggers" in readme
    assert "### Elevated posture scanning (`SCANNING_PAT`)" in readme
    assert "security_scan.use_advanced_pat" in readme
    assert "bos-launchpad-release.yml" not in readme_header_action
    assert "bos-universal-launchpad-kicker.yml" in readme_header_action
    assert "Launchpad intentionally has one managed event kicker" in readme
    assert "stage implementation belongs in the hub backend" in readme
    assert "# Blackout Secure README Header Audit" in readme_header_action
    assert "outcome=${outcome}" in readme_header_action
    assert "RH001" in readme_header_action and "RH030" in readme_header_action

    marketplace_kicker = (
        ROOT / "sync-files/workflows/bos-universal-marketplace-kicker.yml"
    ).read_text()
    for managed_template in (
        ROOT / "sync-files/workflows"
    ).glob("*.yml"):
        assert "\non:\n" not in managed_template.read_text()
        assert "\n\"on\":\n" in managed_template.read_text()
    assert not (ROOT / ".github/workflows/bos-launchpad-marketplace.yml").exists()
    assert not (
        ROOT / "sync-files/workflows/bos-launchpad-marketplace.yml"
    ).exists()
    assert marketplace_kicker.count("bos-universal-marketplace.yml@main") == 1
    assert marketplace_kicker.count("marketplace-repo-guard.yml@main") == 1
    assert marketplace_kicker.count("release-promote.yml@main") == 1
    assert marketplace_kicker.count("repo-metadata-sync.yml@main") == 1
    assert "options: [validate, name-check, release, metadata]" in marketplace_kicker
    assert "default: release" in marketplace_kicker
    assert "needs.release.outputs.tag_name" in marketplace_kicker
    assert "needs.release.result == 'success'" in marketplace_kicker
    assert "&& !inputs.dry_run" in marketplace_kicker
    assert "&& !inputs.draft" in marketplace_kicker
    assert "secrets: inherit" in marketplace_kicker
    assert "secrets: inherit" in marketplace_kicker
    assert "REPO_ADMIN_PAT: ${{ secrets.REPO_ADMIN_PAT }}" not in marketplace_kicker
    assert "RELEASE_PAT: ${{ secrets.RELEASE_PAT }}" not in marketplace_kicker
    assert "outputs.cfg" in marketplace_kicker
    assert "`.github/bos-universal-config.json`" in marketplace_kicker
    assert "config_path: .github/bos-universal-config.json" in marketplace_kicker
    assert "pull_request_target:" in marketplace_kicker
    assert "github.event.repository.default_branch" in marketplace_kicker
    assert not re.search(r"source_branch:\s+dev\b", marketplace_kicker)
    marketplace_workflow = (
        ROOT / ".github/workflows/bos-universal-marketplace.yml"
    ).read_text()
    assert "use_global_config:       'true'" in marketplace_workflow
    assert (
        "global_config_path:      hub-config/sync-files/config/"
        "marketplace-kit-global-config.json"
        in marketplace_workflow
    )
    assert "config_path:             .github/bos-universal-config.json" in marketplace_workflow
    assert "bos-marketplace-kit/.github/actions/check@93affaddfa4f5b6e6c162112d334baa989a31d34" in marketplace_workflow

    for kicker_path in (
        ROOT / "sync-files/workflows/bos-universal-action-test-kicker.yml",
        ROOT / "sync-files/workflows/bos-universal-marketplace-kicker.yml",
        ROOT / "sync-files/workflows/bos-universal-security-kicker.yml",
        ROOT / "sync-files/workflows/bos-universal-sync-kicker.yml",
    ):
        kicker_body = kicker_path.read_text()
        assert "parse-config" not in kicker_body
        assert "target-ref" not in kicker_body
        assert "uses: ./hub-runtime/.github/actions/shared/resolve-hub-ref" in kicker_body

    resolver = (ROOT / ".github/actions/shared/resolve-hub-ref/action.yml").read_text()
    assert "name: Resolve hub ref" in resolver
    assert 'echo "ref=${ref}"' in resolver

    sync_kicker = (
        ROOT / "sync-files/workflows/bos-universal-sync-kicker.yml"
    ).read_text()
    assert (ROOT / ".github/workflows/bos-universal-sync-kicker.yml").exists()
    assert "parse_config:" in sync_kicker
    assert "resolve-target:" not in sync_kicker
    assert "bos-universal-sync.yml@dev" in sync_kicker
    assert "bos-universal-sync.yml@main" in sync_kicker
    assert "mode: ${{ inputs.mode || '' }}" in sync_kicker
    assert "global_config_json" not in sync_kicker
    assert "bos-managed-file-sync-action@" not in sync_kicker
    assert "content_file" not in sync_kicker
    assert "service_definitions" not in sync_kicker

    repo_metadata_workflow = (
        ROOT / ".github/workflows/repo-metadata-sync.yml"
    ).read_text()
    assert "  workflow_call:" in repo_metadata_workflow
    assert "\n  release:\n" not in repo_metadata_workflow
    assert repo_metadata_workflow.count(
        "uses: blackoutsecure/bos-repo-about-sync-action@"
    ) == 1
    assert ".github/actions/repo-metadata@main" not in repo_metadata_workflow
    assert not (ROOT / ".github/actions/repo-metadata").exists()
    assert "secrets.REPO_ADMIN_PAT || secrets.RELEASE_PAT || github.token" in repo_metadata_workflow
    assert "group: repo-metadata-${{ github.repository }}" in repo_metadata_workflow
    assert "inputs.checkout_ref || github.sha" in repo_metadata_workflow
    assert workflow.count("uses: ./.github/workflows/repo-metadata-sync.yml") == 1
    assert ".github/actions/repo-metadata@main" not in workflow

    artifact_release = (ROOT / ".github/workflows/release.yml").read_text()
    marketplace_promote = (
        ROOT / ".github/workflows/release-promote.yml"
    ).read_text()
    assert artifact_release.startswith(
        "# Tag-driven release **pipeline**."
    )
    assert "name: Artifact Release" in artifact_release
    assert "name: Marketplace Promotion Release" in marketplace_promote
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
        "bos-org-kicker-fanout.yml",
        "bos-hub-managed-sync-propagate.yml",
        "bos-universal-security-kicker.yml",
        "bos-universal-sync-kicker.yml",
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

    # Every dual-branch kicker (including `launchpad`, since its `sync-check`
    # pre-flight and `release` stage now resolve `target_ref` like the rest)
    # resolves a static @dev or @main ref per run.
    for managed_caller, expected_refs in (
        (kicker, {"main", "dev"}),
        (security_kicker, {"main", "dev"}),
        (marketplace_kicker, {"main", "dev"}),
        (sync_kicker, {"main", "dev"}),
    ):
        refs = re.findall(r"uses: blackoutsecure/bos-automation-hub/[^\s]+@(\w+)", managed_caller)
        assert refs and set(refs) == expected_refs, refs

    sync_backend = (ROOT / ".github/workflows/bos-universal-sync.yml").read_text()
    assert "name: Blackout Secure Managed File Sync" in sync_backend
    assert "uses: ./hub-runtime/.github/actions/shared/universal-config" in sync_backend
    assert "inputs.hub_ref != 'auto' && inputs.hub_ref" in sync_backend
    assert "github.event_name == 'merge_group'" in sync_backend

    # ── standardized reporting ────────────────────────────────────
    # One shared audit-report surface, driven by findings data, so every
    # workflow reports status the same way instead of hand-rolling a
    # summary block per job.
    job_report = (ROOT / ".github/actions/shared/job-report/action.yml").read_text()
    assert "name: Job report" in job_report
    for token in ("outcome", "verdict", "passes", "warns", "fails", "skips", "total"):
        assert f"{token}:" in job_report
    assert "Provided by [Blackout Secure](https://blackoutsecure.app)" in job_report
    assert "## Recommended Actions" in job_report
    assert "## Detailed Findings" in job_report
    assert '"Not Assessed"' in job_report

    report_refs = {
        "uses: ./hub-runtime/.github/actions/shared/job-report",
        "uses: ./hub-source/.github/actions/shared/job-report",
    }
    for reporting_workflow in (gate_workflow, sync_backend):
        assert any(ref in reporting_workflow for ref in report_refs)
        # Report policy is read through step outputs, never a bare
        # `fromJSON` of a possibly-empty needs output, so the report
        # still renders when config resolution failed.
        assert "fail_on: ${{ steps.findings.outputs.fail_on }}" in reporting_workflow
        assert (
            "enable_summary: ${{ steps.findings.outputs.enable_summary }}"
            in reporting_workflow
        )
        assert (
            "enable_annotations: ${{ steps.findings.outputs.enable_annotations }}"
            in reporting_workflow
        )

    # Runner topology comes from the organization block, never a literal.
    assert "org: ${{ steps.config.outputs.organization }}" in gate_workflow
    assert "config: ${{ steps.config.outputs.cfg }}" in gate_workflow
    assert "title: ${{ steps.findings.outputs.title_prefix }} Security Gate Report" in gate_workflow
    assert "org: ${{ steps.config.outputs.organization }}" in sync_backend
    assert "config: ${{ steps.config.outputs.cfg }}" in sync_backend
    assert "title: ${{ steps.findings.outputs.title_prefix }} Managed File Sync Report" in sync_backend
    assert (
        "runs-on: ${{ fromJSON(needs.resolve-config.outputs.org)"
        ".workflows.security.runs_on }}"
    ) in gate_workflow
    assert "workflows.sync.runs_on }}" in sync_backend
    # Two literal runners survive by design: `resolve-config` bootstraps
    # the runner topology, and the aggregated summary must still run when
    # that bootstrap failed.
    assert gate_workflow.count("runs-on: ubuntu-latest") == 2
    assert sync_backend.count("runs-on: ubuntu-latest") == 1
    assert "vars.DEFAULT_RUNNER" not in sync_backend
    assert 'elif counts["skip"]' in job_report
    assert "Coverage incomplete" in job_report
    assert 'set_value(row.get("value"))' in job_report
    assert 'for key in LABELS if counts[key]' in job_report

    hub_config_raw = json.loads((ROOT / ".github/bos-universal-config.json").read_text())
    assert set(hub_config_raw) == {"launchpad", "organization"}
    hub_org = hub_config_raw["organization"]
    assert hub_org["runners"]["default"] == "ubuntu-latest"
    assert hub_org["reporting"]["fail_on"] == "fail"
    assert hub_org["defaults"]["timeout_minutes"] == 30
    # Hub config has no workflow overrides; they're just examples for consumers.
    assert "workflows" not in hub_org

    global_code_scan_config = json.loads(
        (ROOT / "sync-files/config/code-scanning-kit-global-config.json").read_text()
    )
    assert global_code_scan_config["code_scanning"] == {
        "posture": {
            "workflows": {
                "require_permissions_block": "fail",
                "forbid_write_all": "fail",
                "require_pinned_actions": "fail",
            },
            "branches": {
                "main": {
                    "require_conversation_resolution": True,
                    "severity": "fail",
                },
                "dev": {},
            },
        },
        "remediation": {"enable_ai_findings_summary": False},
    }

    global_marketplace_config = json.loads(
        (ROOT / "sync-files/config/marketplace-kit-global-config.json").read_text()
    )
    assert global_marketplace_config["marketplace_kit"] == {
        "profile": "strict",
        "org_health_repo": "blackoutsecure/.github",
        "check_org_health": True,
        "community_health_source": "inherit",
        "enable_security_scan": True,
        "defer_to_code_scanning_kit": True,
        "enable_ai_findings_summary": False,
    }

    assert (
        "global_config_path: hub-config/config/code-scanning-kit-global-config.json"
        in gate_workflow
    )
    assert "use_global_config: 'true'" in gate_workflow
    assert "config: .github/bos-universal-config.json" in gate_workflow
    assert "bos-code-scanning-kit@ddf31a70cbc1f4e8d3d3e24d6dd358574a48c8bf" in gate_workflow
    standalone_scan_workflow = (
        ROOT / ".github/workflows/security-scan.yml"
    ).read_text()
    assert "sparse-checkout: config/code-scanning-kit-global-config.json" in standalone_scan_workflow
    assert "use_global_config: 'true'" in standalone_scan_workflow
    assert "config: .github/bos-universal-config.json" in standalone_scan_workflow
    assert "bos-code-scanning-kit@ddf31a70cbc1f4e8d3d3e24d6dd358574a48c8bf" in standalone_scan_workflow
    global_sync_config = json.loads(
        (ROOT / "sync-files/config/managed-file-sync-global-config.json").read_text()
    )
    sync_policy = global_sync_config["managed_file_sync"]
    assert "exclude_services" not in sync_policy
    assert "exclude_sevices" not in sync_policy
    assert sync_policy["take_over_managed_files"] is True
    # The hub is checked out into `sync-files/`, and its canonical template
    # root is `sync-files/` itself (not just `sync-files/workflows/`) so
    # `content_file` entries can also reach `sync-files/community-health/`,
    # `sync-files/github-meta/`, and `sync-files/org-profile/` (see
    # `org_defaults` below) alongside the `workflows/` subdirectory.
    assert sync_policy["managed_files_path"] == "sync-files"
    assert sync_policy["services"] == [
        "shellcheck",
        "yamllint",
        "coverage_artifacts",
        "bos_universal_security_kicker",
        "bos_universal_sync_kicker",
    ]
    assert "bos_universal_upstream_kicker" in sync_policy["service_definitions"]
    assert sync_policy["service_definitions"]["bos_universal_upstream_kicker"] == {
        "mode": "file",
        "files": [
            {
                "path": ".github/workflows/bos-universal-upstream-kicker.yml",
                "content_file": "bos-universal-upstream-kicker.yml",
            }
        ],
    }
    assert all(
        definition["mode"] == "file"
        for name, definition in sync_policy["service_definitions"].items()
        if name.startswith("bos_universal_")
    )
    assert "variables" not in sync_policy
    hub_config = cfg_from(
        run_universal_config_raw((ROOT / ".github/bos-universal-config.json").read_text())
    )
    assert "gate" not in hub_config
    assert hub_config["repo_metadata"] == {
        "enable": True,
        "homepage": "https://github.com/blackoutsecure/bos-automation-hub",
        "ai_model": "auto",
        "description_mode": "auto",
        "description_fallback": "",
        "use_existing_readme": True,
        "generate_readme": False,
        "generate_topics": True,
        "topics_fallback": (
            "github-actions automation reusable-workflows composite-actions "
            "devops ci-cd workflow-automation"
        ),
    }
    assert "security_scan" not in hub_config
    assert not (ROOT / ".github/workflows/sync-managed-config.yml").exists()
    assert "  workflow_call:" in sync_backend
    assert "  schedule:" not in sync_backend
    assert "  workflow_dispatch:" not in sync_backend
    assert "global_config_json" not in sync_backend
    assert (
        "global_config_path: hub-source/sync-files/config/managed-file-sync-global-config.json"
        in sync_backend
    )
    assert "Ensure managed-file-sync global config is available" in sync_backend
    assert (
        'git -C hub-source show "HEAD:sync-files/config/managed-file-sync-global-config.json"'
        in sync_backend
    )
    assert "managed_files_path: hub-source/sync-files" in sync_backend
    assert "inputs.hub_ref != 'auto' && inputs.hub_ref" in sync_backend
    assert "config_path: .github/bos-universal-config.json" not in sync_backend
    assert "dry_run: ${{ (inputs.mode || 'commit') == 'check' }}" in sync_backend
    assert "use_global_config: 'auto'" in sync_backend
    assert "bos-managed-file-sync-action@f6802f4443566d8443809f6f0e21576f345e8fdf" in sync_backend
    assert "uses: ./hub-source/.github/actions/shared/commit-and-push" in sync_backend
    assert "workflows: write" not in sync_backend
    assert "workflow_sync_pat:" in sync_backend
    assert "token: ${{ secrets.WORKFLOW_SYNC_PAT || github.token }}" in sync_backend
    assert "secrets.WORKFLOW_SYNC_PAT != '' && 'true' || 'false'" in sync_backend
    assert "disabled_services" in sync_backend
    assert "bos_universal_sync_kicker" in sync_backend
    managed_sync_caller = sync_kicker
    assert "name: Blackout Secure Managed File Sync" in managed_sync_caller
    assert "name: Resolve target hub ref" in managed_sync_caller
    assert "sync-dev:" in managed_sync_caller
    assert "sync-main:" in managed_sync_caller
    assert "contents: write" in managed_sync_caller
    assert managed_sync_caller.count("secrets: inherit") == 2
    assert "workflow_sync_pat: ${{ secrets.WORKFLOW_SYNC_PAT }}" not in managed_sync_caller

    assert security_kicker.count("secrets: inherit") == 2
    assert "scanning_pat: ${{ secrets.SCANNING_PAT }}" not in security_kicker

    launchpad_workflow = (
        ROOT / ".github/workflows/bos-universal-launchpad.yml"
    ).read_text()
    assert "secrets: inherit" in kicker
    assert "REPO_ADMIN_PAT: ${{ secrets.REPO_ADMIN_PAT }}" not in kicker
    assert (
        "REPO_ADMIN_PAT: ${{ secrets.REPO_ADMIN_PAT || secrets.RELEASE_PAT }}"
        in launchpad_workflow
    )
    assert "RELEASE_PAT:\n        description:" in launchpad_workflow

    assert_markdown_links_exist(ROOT / "README.md")
    assert_markdown_links_exist(ROOT / "sync-files/README.md")

    print(
        f"repository contract valid: {len(declared)} launchpad inputs, "
        f"{len(gate_declared)} gate inputs, {len(reusable)} runtime workflows"
    )


if __name__ == "__main__":
    main()
