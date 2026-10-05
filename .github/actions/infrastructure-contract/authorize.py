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


def authorize(repository: str, actor: str, token: str, trusted_app: str, environment: str, applying: bool) -> None:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository) or not re.fullmatch(r"[A-Za-z0-9_.\[\]-]+", actor):
        raise ValueError("Invalid authoritative repository or triggering actor.")
    if not token:
        raise ValueError("Repository authorization token is unavailable.")
    if not trusted_app or actor != trusted_app + "[bot]":
        value = github_json(f"repos/{repository}/collaborators/{quote(actor)}/permission", token)
        if not isinstance(value, dict) or value.get("permission") not in ("admin", "maintain", "write"):
            raise ValueError("The triggering actor is not authorized to request infrastructure operations.")
    if applying:
        value = github_json(f"repos/{repository}/environments/{quote(environment)}", token)
        if not isinstance(value, dict):
            raise ValueError("Protected environment metadata is unavailable.")
        rules = value.get("protection_rules")
        if not isinstance(rules, list) or not any(
            isinstance(rule, dict) and rule.get("type") == "required_reviewers" and isinstance(rule.get("reviewers"), list) and bool(rule["reviewers"])
            for rule in rules
        ):
            raise ValueError("Apply requires a configured GitHub environment reviewer; an unprotected environment cannot authorize it.")
        policy = value.get("deployment_branch_policy")
        if not isinstance(policy, dict) or not policy.get("custom_branch_policies"):
            raise ValueError("Apply requires an explicit protected deployment branch policy.")


def main(argv: list[str] | None = None) -> int:
    del argv
    try:
        if os.environ.get("GITHUB_EVENT_NAME") != "workflow_dispatch" or os.environ.get("GITHUB_REF") != "refs/heads/dev":
            raise ValueError("Infrastructure operations require the trusted dev dispatch.")
        actor = os.environ.get("GITHUB_TRIGGERING_ACTOR") or os.environ.get("GITHUB_ACTOR", "")
        authorize(
            os.environ.get("GITHUB_REPOSITORY", ""), actor, os.environ.get("GH_TOKEN", ""),
            os.environ.get("CLOUD_COMPASS_APP_SLUG", ""), os.environ.get("INFRA_ENVIRONMENT", ""),
            os.environ.get("INFRA_OPERATION") == "apply",
        )
        print("Triggering actor authorization verified; environment approval remains the apply authority.")
        return 0
    except (ValueError, HTTPError, URLError, OSError) as error:
        print(f"Infrastructure authorization denied: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
