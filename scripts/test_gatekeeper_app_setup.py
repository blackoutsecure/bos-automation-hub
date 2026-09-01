#!/usr/bin/env python3
"""Offline tests for the loopback Gatekeeper App setup helper."""

from __future__ import annotations

import importlib.util
import subprocess
import tempfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
MODULE_PATH = ROOT / "tools" / "gatekeeper-app-setup" / "server.py"
SPEC = importlib.util.spec_from_file_location("gatekeeper_app_setup", MODULE_PATH)
assert SPEC and SPEC.loader
setup = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(setup)


def completed(args: list[str], stdout: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args, 0, stdout=stdout, stderr="")


def main() -> None:
    assert setup.validate_org("blackoutsecure") == "blackoutsecure"
    for invalid in ("", "-owner", "owner/other", "owner name", "a" * 40):
        try:
            setup.validate_org(invalid)
        except setup.SetupError:
            pass
        else:
            raise AssertionError(f"invalid organization accepted: {invalid!r}")

    assert setup.validate_repository_and_run_id("owner/repository", "123") == (
        "owner/repository",
        "123",
    )
    for repository, run_id in (
        ("owner/repository", ""),
        ("bad", "123"),
        ("owner/repo", "abc"),
    ):
        try:
            setup.validate_repository_and_run_id(repository, run_id)
        except setup.SetupError:
            pass
        else:
            raise AssertionError(
                f"invalid run target accepted: {repository!r}, {run_id!r}"
            )
    assert (
        setup.validate_app_slug("blackoutsecure-gatekeeper")
        == "blackoutsecure-gatekeeper"
    )

    pem = (
        "-----BEGIN RSA PRIVATE KEY-----\n"
        "test-only-placeholder\n"
        "-----END RSA PRIVATE KEY-----\n"
    )
    app_id, normalized_pem = setup.validate_credentials("12345", pem)
    assert app_id == "12345"
    assert normalized_pem == pem

    calls: list[tuple[list[str], str | None]] = []

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        stdin = kwargs.get("input")
        calls.append((args, stdin if isinstance(stdin, str) else None))
        if args[1:4] == ["api", "user", "--jq"]:
            return completed(args, "owner-login\n")
        if args[1:3] == ["api", "orgs/blackoutsecure/memberships/owner-login"]:
            return completed(args, "admin active\n")
        return completed(args)

    with mock.patch.object(setup.subprocess, "run", side_effect=fake_run):
        setup.GhClient().configure("blackoutsecure", app_id, pem)

    variable_call = next(args for args, _ in calls if args[1:3] == ["variable", "set"])
    secret_call, secret_stdin = next(
        (args, stdin) for args, stdin in calls if args[1:3] == ["secret", "set"]
    )
    assert variable_call[-4:] == ["--visibility", "all", "--body", app_id]
    assert secret_call[-2:] == ["--visibility", "all"]
    assert secret_stdin == pem
    assert all(pem not in " ".join(args) for args, _ in calls)

    status_responses = {
        ("variable", "list"): '[{"name":"GATEKEEPER_APP_ID"}]',
        ("variable", "get"): "4788890\n",
        ("secret", "list"): '[{"name":"GATEKEEPER_APP_PRIVATE_KEY"}]',
        ("api", "orgs/blackoutsecure/installations"): (
            '{"installations":[{"id":123,"app_id":4788890,'
            '"app_slug":"gatekeeper-app","repository_selection":"selected",'
            '"permissions":{"members":"read"}}]}'
        ),
    }
    client = setup.GhClient()
    client.assert_org_admin = mock.Mock()
    client._run = mock.Mock(
        side_effect=lambda *args, **_kwargs: status_responses[args[:2]]
    )
    status = client.status("blackoutsecure", "gatekeeper-app")
    assert status == {
        "variable_set": True,
        "secret_set": True,
        "configured_app_id": 4788890,
        "app_slug": "gatekeeper-app",
        "installed": True,
        "repository_selection": "selected",
        "installation_id": 123,
        "members_permission": "read",
        "healthy": True,
    }

    detected = client.status("blackoutsecure")
    assert detected["app_slug"] == "gatekeeper-app"
    assert detected["healthy"] is True

    with tempfile.TemporaryDirectory() as temp_dir:
        html = Path(temp_dir) / "index.html"
        html.write_text("__SETUP_TOKEN__ __DEFAULT_ORGANIZATION__", encoding="utf-8")
        server = setup.SetupServer((setup.HOST, 0), html, "blackoutsecure", "", "", gh=mock.Mock())
        try:
            assert server.server_address[0] == "127.0.0.1"
            assert server.csrf_token
        finally:
            server.server_close()

    print("gatekeeper app setup tests passed")


if __name__ == "__main__":
    main()
