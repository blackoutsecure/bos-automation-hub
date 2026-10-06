from __future__ import annotations

import json
import os
import re
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


def github_json(path: str, token: str) -> object:
    request = Request(
        "https://api.github.com/" + path,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"},
    )
    with urlopen(request, timeout=15) as response:
        source = response.read(262145)
    if len(source) > 262144:
        raise ValueError("Authorization metadata exceeds its limit.")
    return json.loads(source)


def is_human_reviewer(value: object) -> bool:
    if not isinstance(value, dict) or not isinstance(value.get("reviewer"), dict):
        return False
    reviewer = value["reviewer"]
    if type(reviewer.get("id")) is not int or reviewer["id"] <= 0:
        return False
    if value.get("type") == "Team":
        return isinstance(reviewer.get("slug"), str) and bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]*", reviewer["slug"]))
    return (
        value.get("type") == "User"
        and reviewer.get("type") == "User"
        and isinstance(reviewer.get("login"), str)
        and bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]*", reviewer["login"]))
    )


def validate_environment(repository: str, token: str, environment: str, applying: bool) -> None:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", environment):
        raise ValueError("Invalid authoritative repository or execution environment.")
    if not token:
        raise ValueError("Environment metadata token is unavailable.")
    path = f"repos/{repository}/environments/{quote(environment, safe='')}"
    value = github_json(path, token)
    if not isinstance(value, dict):
        raise ValueError("Execution environment metadata is unavailable.")
    policy = value.get("deployment_branch_policy")
    if not isinstance(policy, dict) or policy.get("custom_branch_policies") is not True or policy.get("protected_branches") is not False:
        raise ValueError("Execution requires an explicit dev-only deployment branch policy.")
    branches = github_json(f"{path}/deployment-branch-policies?per_page=100", token)
    if not isinstance(branches, dict):
        raise ValueError("Deployment branch metadata is unavailable.")
    entries = branches.get("branch_policies")
    if (
        type(branches.get("total_count")) is not int or branches["total_count"] != 1
        or not isinstance(entries, list) or len(entries) != 1
        or not isinstance(entries[0], dict) or entries[0].get("name") != "dev"
        or entries[0].get("type") != "branch"
    ):
        raise ValueError("Execution requires exactly the dev branch; tags, wildcards and additional policies are denied.")
    if applying:
        if value.get("can_admins_bypass") is not False:
            raise ValueError("Apply requires administrator bypass to be disabled.")
        rules = value.get("protection_rules")
        if not isinstance(rules, list) or not any(
            isinstance(rule, dict) and rule.get("type") == "required_reviewers"
            and isinstance(rule.get("reviewers"), list) and any(is_human_reviewer(reviewer) for reviewer in rule["reviewers"])
            for rule in rules
        ):
            raise ValueError("Apply requires an eligible human or team environment reviewer; an App is not an approver.")


def main(argv: list[str] | None = None) -> int:
    del argv
    try:
        operation = os.environ.get("INFRA_OPERATION", "")
        if operation not in {"plan", "apply", "drift"}:
            raise ValueError("Unsupported infrastructure operation.")
        scheduled_drift = os.environ.get("GITHUB_EVENT_NAME") == "schedule" and operation == "drift"
        if (os.environ.get("GITHUB_EVENT_NAME") != "workflow_dispatch" and not scheduled_drift) or os.environ.get("GITHUB_REF") != "refs/heads/dev":
            raise ValueError("Infrastructure operations require the trusted dev dispatch.")
        if os.environ.get("GATEKEEPER_AUTHORIZED") != "true" or os.environ.get("GATEKEEPER_ENFORCED") != "true":
            raise ValueError("An enforced Gatekeeper authorization is required; a skipped or unassessed gate cannot authorize execution.")
        validate_environment(
            os.environ.get("GITHUB_REPOSITORY", ""), os.environ.get("GH_TOKEN", ""),
            os.environ.get("INFRA_ENVIRONMENT", ""), operation == "apply",
        )
        print("Gatekeeper enforcement and environment controls verified; human approval remains the apply authority.")
        return 0
    except (ValueError, HTTPError, URLError, OSError) as error:
        print(f"Infrastructure authorization denied: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
