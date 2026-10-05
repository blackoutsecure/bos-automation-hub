from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

SHA = re.compile(r"^[a-f0-9]{40}$")
NAME = re.compile(r"^[a-z][a-z0-9-]{1,62}$")
REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


def object_value(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ValueError(f"{label} must be an object.")
    return value


def text(value: object, label: str, pattern: re.Pattern[str] | None = None) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{label} must be a nonempty canonical string.")
    if "\n" in value or "\r" in value or (pattern and not pattern.fullmatch(value)):
        raise ValueError(f"{label} has an invalid format.")
    return value


def relative_path(value: object, root: Path, label: str, exists: bool = False) -> str:
    raw = text(value, label)
    parts = raw.replace("\\", "/").split("/")
    if raw.startswith(("/", "\\")) or ":" in raw or any(part in ("", ".", "..") for part in parts):
        raise ValueError(f"{label} must be a bounded repository-relative path.")
    resolved = (root / Path(*parts)).resolve()
    if not resolved.is_relative_to(root.resolve()) or (exists and not resolved.exists()):
        raise ValueError(f"{label} escapes the repository or is missing.")
    return "/".join(parts)


def inspect_registry(value: object, root: Path, repository: str, stack_id: str) -> dict[str, str]:
    registry = object_value(value, "Registry")
    if registry.get("schema_version") != 1:
        raise ValueError("Only infrastructure registry schema version 1 is supported.")
    if text(registry.get("repository"), "Repository", REPOSITORY) != repository:
        raise ValueError("The registry does not belong to this repository.")
    hub = object_value(registry.get("automation_hub"), "Automation hub")
    if hub.get("repository") != "blackoutsecure/bos-automation-hub":
        raise ValueError("The execution authority must be the automation hub.")
    revision = text(hub.get("revision"), "Hub revision", SHA)
    stacks = registry.get("stacks")
    if not isinstance(stacks, list) or not stacks or len(stacks) > 100:
        raise ValueError("The registry must contain one to one hundred bounded stacks.")
    seen_ids: set[str] = set()
    seen_states: set[str] = set()
    seen_roots: set[str] = set()
    selected: dict[str, Any] | None = None
    for entry in stacks:
        stack = object_value(entry, "Stack")
        identifier = text(stack.get("id"), "Stack identifier", NAME)
        if identifier in seen_ids:
            raise ValueError("Each stack identifier must have exactly one owner.")
        seen_ids.add(identifier)
        if stack.get("provider") != "cloudflare" or stack.get("contract_version") != "1.0.0":
            raise ValueError("Only the reviewed Cloudflare infrastructure contract is enabled.")
        terraform_root = relative_path(stack.get("terraform_directory"), root, "Terraform directory", True)
        if terraform_root in seen_roots:
            raise ValueError("Terraform roots cannot be shared by independently applied stacks.")
        seen_roots.add(terraform_root)
        backend = object_value(stack.get("backend"), "Backend")
        state_key = relative_path(backend.get("key"), root, "State key")
        if state_key in seen_states:
            raise ValueError("State keys must be isolated per stack and environment.")
        seen_states.add(state_key)
        text(backend.get("bucket_variable"), "Backend bucket variable", re.compile(r"^[A-Z][A-Z0-9_]+$"))
        text(stack.get("environment"), "Apply environment", NAME)
        text(stack.get("planning_environment", f"{identifier}-plan"), "Planning environment", NAME)
        text(stack.get("settings_variable"), "Settings variable", re.compile(r"^[A-Z][A-Z0-9_]+$"))
        resources = stack.get("allowed_resources")
        if not isinstance(resources, list) or not resources or len(resources) > 100:
            raise ValueError("Each stack requires a bounded managed-resource allowlist.")
        if len(set(text(item, "Resource address") for item in resources)) != len(resources):
            raise ValueError("Managed-resource addresses must be unique.")
        application = object_value(stack.get("application"), "Application")
        app_repository = text(application.get("repository"), "Application repository", REPOSITORY)
        if app_repository.split("/", 1)[0].lower() != repository.split("/", 1)[0].lower():
            raise ValueError("Applications must belong to the infrastructure repository owner.")
        text(application.get("revision"), "Application revision", SHA)
        app_directory = relative_path(application.get("working_directory"), root, "Application directory")
        if not app_directory.startswith(".workloads/"):
            raise ValueError("Application checkouts must be isolated under .workloads.")
        if identifier == stack_id:
            selected = stack
    if selected is None:
        raise ValueError("The requested stack is not allowlisted.")
    application = object_value(selected["application"], "Application")
    return {
        "environment": selected["environment"],
        "planning_environment": selected.get("planning_environment", f"{stack_id}-plan"),
        "terraform_directory": selected["terraform_directory"],
        "application_repository": application["repository"],
        "application_name": application["repository"].split("/", 1)[1],
        "application_revision": application["revision"],
        "application_directory": application["working_directory"],
        "hub_revision": revision,
        "settings_variable": selected["settings_variable"],
        "bucket_variable": selected["backend"]["bucket_variable"],
    }


def main(argv: list[str] | None = None) -> int:
    del argv
    try:
        root = Path(os.environ.get("GITHUB_WORKSPACE", ".")).resolve()
        registry_path = relative_path(os.environ.get("REGISTRY_PATH", "infrastructure.json"), root, "Registry", True)
        path = root / registry_path
        if path.stat().st_size > 262144:
            raise ValueError("The registry exceeds its 256 KiB limit.")
        revision = text(os.environ.get("SOURCE_REVISION"), "Source revision", SHA)
        head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
        if head != revision:
            raise ValueError("The checked-out source does not match the requested immutable revision.")
        outputs = inspect_registry(
            json.loads(path.read_text(encoding="utf-8")),
            root,
            text(os.environ.get("GITHUB_REPOSITORY"), "Caller repository", REPOSITORY),
            text(os.environ.get("STACK_ID"), "Stack identifier", NAME),
        )
        output_path = os.environ.get("GITHUB_OUTPUT")
        if output_path:
            with Path(output_path).open("a", encoding="utf-8") as stream:
                for key, value in outputs.items():
                    stream.write(f"{key}={value}\n")
        else:
            print(json.dumps(outputs, sort_keys=True))
        return 0
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        print(f"Infrastructure contract failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
