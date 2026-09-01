#!/usr/bin/env python3
"""Loopback-only setup service for the universal gatekeeper GitHub App."""

from __future__ import annotations

import argparse
import json
import re
import secrets
import subprocess
import sys
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

HOST = "127.0.0.1"
DEFAULT_PORT = 8765
MAX_BODY_BYTES = 256 * 1024
ORG_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")
APP_ID_RE = re.compile(r"^[0-9]+$")
REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
RUN_ID_RE = re.compile(r"^[0-9]+$")
APP_SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$")
PEM_BEGIN = "-----BEGIN RSA PRIVATE KEY-----"
PEM_END = "-----END RSA PRIVATE KEY-----"


class SetupError(RuntimeError):
    """Safe, user-facing setup failure."""


class GhClient:
    """Small wrapper around authenticated GitHub CLI operations."""

    def __init__(self, executable: str = "gh") -> None:
        self.executable = executable

    def _run(self, *args: str, stdin: str | None = None) -> str:
        try:
            result = subprocess.run(
                [self.executable, *args],
                input=stdin,
                text=True,
                capture_output=True,
                check=False,
                timeout=45,
            )
        except FileNotFoundError as exc:
            raise SetupError("GitHub CLI (gh) was not found. Install it and run gh auth login.") from exc
        except subprocess.TimeoutExpired as exc:
            raise SetupError("GitHub CLI timed out while contacting GitHub.") from exc

        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip().splitlines()
            message = detail[-1] if detail else "unknown GitHub CLI error"
            raise SetupError(message)
        return result.stdout

    def authenticated_user(self) -> str:
        output = self._run("api", "user", "--jq", ".login")
        return output.strip()

    def assert_org_admin(self, organization: str) -> None:
        role = self._run(
            "api",
            f"orgs/{organization}/memberships/{self.authenticated_user()}",
            "--jq",
            ".role + \" \" + .state",
        ).strip()
        if role != "admin active":
            raise SetupError(
                f"The active gh account is not an active owner of {organization}; "
                "organization variables and secrets cannot be configured."
            )

    def configure(self, organization: str, app_id: str, private_key: str) -> None:
        self.assert_org_admin(organization)
        self._run(
            "variable",
            "set",
            "GATEKEEPER_APP_ID",
            "--org",
            organization,
            "--visibility",
            "all",
            "--body",
            app_id,
        )
        self._run(
            "secret",
            "set",
            "GATEKEEPER_APP_PRIVATE_KEY",
            "--org",
            organization,
            "--visibility",
            "all",
            stdin=private_key,
        )

    def status(self, organization: str, app_slug: str = "") -> dict[str, object]:
        self.assert_org_admin(organization)
        variables = self._run("variable", "list", "--org", organization, "--json", "name")
        secret_names = self._run("secret", "list", "--org", organization, "--json", "name")
        variable_set = any(item.get("name") == "GATEKEEPER_APP_ID" for item in json.loads(variables))
        configured_app_id: int | None = None
        if variable_set:
            raw_app_id = self._run(
                "variable", "get", "GATEKEEPER_APP_ID", "--org", organization
            ).strip()
            if APP_ID_RE.fullmatch(raw_app_id):
                configured_app_id = int(raw_app_id)
        secret_set = any(
            item.get("name") == "GATEKEEPER_APP_PRIVATE_KEY" for item in json.loads(secret_names)
        )
        installed: bool | None = None
        repository_selection: str | None = None
        installation_id: int | None = None
        detected_app_slug = app_slug
        members_permission: str | None = None
        try:
            installations = json.loads(self._run("api", f"orgs/{organization}/installations"))
            installation = next(
                (
                    item
                    for item in installations.get("installations", [])
                    if (app_slug and item.get("app_slug") == app_slug)
                    or (configured_app_id is not None and item.get("app_id") == configured_app_id)
                ),
                None,
            )
            installed = installation is not None
            if installation:
                detected_app_slug = str(installation.get("app_slug") or detected_app_slug)
                repository_selection = str(installation.get("repository_selection") or "")
                permissions = installation.get("permissions")
                if isinstance(permissions, dict):
                    members_permission = str(permissions.get("members") or "none")
                raw_installation_id = installation.get("id")
                if isinstance(raw_installation_id, int):
                    installation_id = raw_installation_id
        except (SetupError, json.JSONDecodeError):
            installed = None
        healthy = (
            variable_set
            and secret_set
            and installed is True
            and members_permission == "read"
        )
        return {
            "variable_set": variable_set,
            "secret_set": secret_set,
            "configured_app_id": configured_app_id,
            "app_slug": detected_app_slug or None,
            "installed": installed,
            "repository_selection": repository_selection,
            "installation_id": installation_id,
            "members_permission": members_permission,
            "healthy": healthy,
        }

    def rerun(self, repository: str, run_id: str) -> None:
        self._run("run", "rerun", run_id, "--repo", repository, "--failed")


def validate_org(value: object) -> str:
    organization = str(value or "").strip()
    if not ORG_RE.fullmatch(organization):
        raise SetupError("Enter a valid GitHub organization login.")
    return organization


def validate_repository_and_run_id(repository_value: object, run_id_value: object) -> tuple[str, str]:
    repository = str(repository_value or "").strip()
    run_id = str(run_id_value or "").strip()
    if bool(repository) != bool(run_id):
        raise SetupError("--repository and --run-id must be supplied together.")
    if repository and not REPOSITORY_RE.fullmatch(repository):
        raise SetupError("Enter a valid repository in owner/name format.")
    if run_id and not RUN_ID_RE.fullmatch(run_id):
        raise SetupError("Enter a valid numeric workflow run ID.")
    return repository, run_id


def validate_app_slug(value: object) -> str:
    app_slug = str(value or "").strip()
    if app_slug and not APP_SLUG_RE.fullmatch(app_slug):
        raise SetupError("GitHub returned an invalid App slug.")
    return app_slug


def validate_credentials(app_id_value: object, key_value: object) -> tuple[str, str]:
    app_id = str(app_id_value or "").strip()
    private_key = str(key_value or "").strip() + "\n"
    if not APP_ID_RE.fullmatch(app_id):
        raise SetupError("GitHub returned an invalid App ID.")
    if not (private_key.startswith(PEM_BEGIN) and private_key.rstrip().endswith(PEM_END)):
        raise SetupError("GitHub returned an invalid RSA private key.")
    return app_id, private_key


class SetupServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        address: tuple[str, int],
        html_path: Path,
        organization: str,
        repository: str,
        run_id: str,
        gh: GhClient | None = None,
    ) -> None:
        super().__init__(address, SetupHandler)
        self.html_path = html_path
        self.organization = organization
        self.repository = repository
        self.run_id = run_id
        self.csrf_token = secrets.token_urlsafe(32)
        self.gh = gh or GhClient()

    @property
    def origin(self) -> str:
        return f"http://{HOST}:{self.server_port}"


class SetupHandler(BaseHTTPRequestHandler):
    server: SetupServer

    def log_message(self, format_string: str, *args: object) -> None:
        # Request paths are safe to log. Bodies may contain a private key and
        # are deliberately never logged.
        sys.stderr.write("setup: " + format_string % args + "\n")

    def _headers(self, status: HTTPStatus, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'none'; connect-src 'self' https://api.github.com; "
            f"form-action https://github.com; script-src 'nonce-{self.server.csrf_token}'; "
            "style-src 'self' 'unsafe-inline'; img-src 'self' data:; base-uri 'none'; frame-ancestors 'none'",
        )

    def _json(self, status: HTTPStatus, payload: dict[str, object]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self._headers(status, "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict[str, object]:
        if self.headers.get("Origin") != self.server.origin:
            raise SetupError("Request origin was rejected.")
        if self.headers.get("X-Setup-Token") != self.server.csrf_token:
            raise SetupError("Setup session token was rejected.")
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise SetupError("Invalid request length.") from exc
        if length <= 0 or length > MAX_BODY_BYTES:
            raise SetupError("Request body is empty or too large.")
        try:
            value = json.loads(self.rfile.read(length))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SetupError("Invalid JSON request.") from exc
        if not isinstance(value, dict):
            raise SetupError("JSON request must be an object.")
        return value

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/":
            html = self.server.html_path.read_text(encoding="utf-8")
            html = html.replace("__SETUP_TOKEN__", self.server.csrf_token)
            html = html.replace("__DEFAULT_ORGANIZATION__", self.server.organization)
            html = html.replace("__DEFAULT_REPOSITORY__", self.server.repository)
            html = html.replace("__DEFAULT_RUN_ID__", self.server.run_id)
            body = html.encode("utf-8")
            self._headers(HTTPStatus.OK, "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self._json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "Not found."})

    def do_POST(self) -> None:  # noqa: N802
        try:
            payload = self._read_json()
            if self.path == "/api/status":
                organization = validate_org(payload.get("organization"))
                app_slug = validate_app_slug(payload.get("app_slug"))
                self._json(
                    HTTPStatus.OK,
                    {"ok": True, **self.server.gh.status(organization, app_slug)},
                )
                return
            if self.path == "/api/configure":
                organization = validate_org(payload.get("organization"))
                app_id, private_key = validate_credentials(payload.get("app_id"), payload.get("pem"))
                self.server.gh.configure(organization, app_id, private_key)
                self._json(
                    HTTPStatus.OK,
                    {
                        "ok": True,
                        "message": "Organization variable and secret configured for all repositories.",
                    },
                )
                return
            if self.path == "/api/rerun":
                if not self.server.repository or not self.server.run_id:
                    raise SetupError("This setup session was not started with a repository and run ID.")
                self.server.gh.rerun(self.server.repository, self.server.run_id)
                self._json(HTTPStatus.OK, {"ok": True, "message": "Failed jobs queued for re-run."})
                return
            self._json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "Not found."})
        except SetupError as exc:
            self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--organization", default="blackoutsecure")
    parser.add_argument("--repository", default="")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--no-browser", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    organization = validate_org(args.organization)
    repository, run_id = validate_repository_and_run_id(args.repository, args.run_id)
    html_path = Path(__file__).with_name("index.html")
    server = SetupServer(
        (HOST, args.port), html_path, organization, repository, run_id
    )
    print(f"Gatekeeper App setup is available at {server.origin}")
    print("The service is bound to loopback only. Press Ctrl+C when setup is complete.")
    if not args.no_browser:
        threading.Timer(0.4, webbrowser.open, args=(server.origin,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nSetup service stopped.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SetupError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
