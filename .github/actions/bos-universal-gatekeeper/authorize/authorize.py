"""Resolve whether a manual-dispatch actor may run the gatekeeper.

Checks, in order, are additive: the actor's org role, org team
memberships, and (optionally) enterprise-owner status are all resolved,
then evaluated against the configured policy. Any hard API failure is
reported as `error` so the caller can fail closed.

Standards basis: GitHub REST `orgs`/`teams` endpoints plus the GraphQL
`enterprise.ownerInfo.admins` connection, which is readable only by an
enterprise owner -- so a non-owner PAT degrades to `unknown` rather than
silently reporting `false`.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

API = os.environ.get("GITHUB_API_URL", "https://api.github.com").rstrip("/")
GRAPHQL = os.environ.get("GITHUB_GRAPHQL_URL", "https://api.github.com/graphql")
TOKEN = os.environ.get("AUTHZ_TOKEN", "")
# Enterprise ownership is not resolvable with a GitHub App installation token,
# so it gets its own credential and is skipped rather than failed when absent.
ENTERPRISE_TOKEN = os.environ.get("AUTHZ_ENTERPRISE_TOKEN", "") or TOKEN
ACTOR = os.environ.get("ACTOR", "")
ORG = os.environ.get("ORG", "")
ENTERPRISE = os.environ.get("ENTERPRISE_SLUG", "").strip()
REQUIRED_TEAMS = [t.strip() for t in os.environ.get("REQUIRED_TEAMS", "").split(",") if t.strip()]
ALLOW_ORG_ADMIN = os.environ.get("ALLOW_ORG_ADMIN", "true").lower() == "true"
REQUIRE_ENTERPRISE_OWNER = os.environ.get("REQUIRE_ENTERPRISE_OWNER", "false").lower() == "true"


def _request(url: str, *, data: bytes | None = None, token: str = "") -> tuple[int, dict | list | None]:
    """Return (status, parsed-json). Never raises on HTTP status."""
    req = urllib.request.Request(url, data=data)
    req.add_header("Authorization", f"Bearer {token or TOKEN}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    req.add_header("User-Agent", "bos-universal-gatekeeper")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8")
            return resp.status, (json.loads(body) if body else None)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            return exc.code, json.loads(body) if body else None
        except json.JSONDecodeError:
            return exc.code, None
    except (urllib.error.URLError, TimeoutError):
        return 0, None


def org_role() -> str:
    """`admin`, `member`, `outside`, or `unknown` on API failure."""
    status, payload = _request(f"{API}/orgs/{ORG}/memberships/{ACTOR}")
    if status == 200 and isinstance(payload, dict):
        if payload.get("state") != "active":
            return "outside"
        return str(payload.get("role", "member"))
    if status == 404:
        return "outside"
    return "unknown"


def team_memberships() -> tuple[list[str], bool]:
    """Return (teams the actor is an active member of, api_ok)."""
    found: list[str] = []
    ok = True
    for team in REQUIRED_TEAMS:
        status, payload = _request(f"{API}/orgs/{ORG}/teams/{team}/memberships/{ACTOR}")
        if status == 200 and isinstance(payload, dict):
            if payload.get("state") == "active":
                found.append(team)
        elif status != 404:
            ok = False
    return found, ok


def enterprise_owner() -> str:
    """`true`, `false`, or `unknown` when the PAT cannot read owner info."""
    if not ENTERPRISE:
        return "unknown"
    query = {
        "query": (
            "query($slug:String!){enterprise(slug:$slug)"
            "{ownerInfo{admins(first:100,role:OWNER){nodes{login}}}}}"
        ),
        "variables": {"slug": ENTERPRISE},
    }
    status, payload = _request(
        GRAPHQL, data=json.dumps(query).encode("utf-8"), token=ENTERPRISE_TOKEN
    )
    if status != 200 or not isinstance(payload, dict):
        return "unknown"
    if payload.get("errors"):
        return "unknown"
    try:
        nodes = payload["data"]["enterprise"]["ownerInfo"]["admins"]["nodes"]
    except (KeyError, TypeError):
        return "unknown"
    logins = {str(n.get("login", "")).lower() for n in nodes if isinstance(n, dict)}
    return "true" if ACTOR.lower() in logins else "false"


def emit(**kwargs: str) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as handle:
        for key, value in kwargs.items():
            handle.write(f"{key}={value}\n")


def main() -> int:
    if not TOKEN:
        emit(
            authorized="false",
            reason="No authorization token available. Configure the GATEKEEPER_AUTHZ_PAT secret.",
            org_role="unknown",
            teams="",
            enterprise_owner="unknown",
        )
        return 0
    if not ACTOR or not ORG:
        emit(authorized="false", reason="Missing actor or organization context.", org_role="unknown", teams="", enterprise_owner="unknown")
        return 0

    role = org_role()
    teams, teams_ok = team_memberships()
    owner = enterprise_owner() if ENTERPRISE else "unknown"

    reasons: list[str] = []
    authorized = False

    if role == "unknown" or not teams_ok:
        reasons.append("GitHub API did not return a conclusive membership answer.")
    elif role == "outside":
        reasons.append(f"@{ACTOR} is not an active member of {ORG}.")
    else:
        if REQUIRE_ENTERPRISE_OWNER:
            if owner == "true":
                authorized = True
                reasons.append(f"@{ACTOR} is an enterprise owner.")
            elif owner == "unknown":
                reasons.append(
                    "Enterprise-owner status could not be verified. The PAT must belong "
                    "to an enterprise owner and carry admin:enterprise."
                )
            else:
                reasons.append(f"@{ACTOR} is not an enterprise owner.")
        else:
            if owner == "true":
                authorized = True
                reasons.append(f"@{ACTOR} is an enterprise owner.")
            if not authorized and ALLOW_ORG_ADMIN and role == "admin":
                authorized = True
                reasons.append(f"@{ACTOR} is an owner of {ORG}.")
            if not authorized and REQUIRED_TEAMS:
                if teams:
                    authorized = True
                    reasons.append(f"@{ACTOR} is a member of: {', '.join(teams)}.")
                else:
                    reasons.append(
                        f"@{ACTOR} is not a member of any authorized team "
                        f"({', '.join(REQUIRED_TEAMS)})."
                    )
            if not authorized and not REQUIRED_TEAMS and not ALLOW_ORG_ADMIN:
                reasons.append("No authorization rule is enabled; nothing can satisfy this policy.")
            if not authorized and not reasons:
                reasons.append(f"@{ACTOR} does not satisfy the dispatch policy.")

    emit(
        authorized="true" if authorized else "false",
        reason=" ".join(reasons) or "Authorization evaluated.",
        org_role=role,
        teams=",".join(teams),
        enterprise_owner=owner,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
