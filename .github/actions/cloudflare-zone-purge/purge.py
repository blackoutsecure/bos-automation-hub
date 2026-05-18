#!/usr/bin/env python3
"""Resolve a Cloudflare zone ID (or accept one) and purge its edge cache.

Inputs (env):
    ZONE_ID, FALLBACK_ZONE_ID  Explicit 32-char hex IDs.
    SITE_URL                   Used when no ID is supplied.
    API_TOKEN                  Required.

Outputs (GITHUB_OUTPUT):
    zone_id, purged, http_code

Stdlib-only (urllib + json + re).
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

API_BASE = "https://api.cloudflare.com/client/v4"
USER_AGENT = "bos-automation-hub/cloudflare-zone-purge"
ID_RE = re.compile(r"^[0-9a-f]{32}$")
HOST_RE = re.compile(
    r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$"
)


def die(msg: str, *hints: str) -> "None":
    print(f"ERROR: {msg}", file=sys.stderr)
    for hint in hints:
        print(f"Hint: {hint}", file=sys.stderr)
    sys.exit(1)


def log(msg: str) -> None:
    print(msg, file=sys.stderr)


def write_outputs(pairs: dict[str, str]) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        die("GITHUB_OUTPUT is not set (running outside GitHub Actions?)")
    with open(path, "a", encoding="utf-8") as f:
        for key, value in pairs.items():
            if "\n" in value or "\r" in value:
                die(f"output {key!r} contains a newline")
            f.write(f"{key}={value}\n")


def require_token(token: str) -> str:
    if not token:
        die("API_TOKEN is required")
    if any(c.isspace() for c in token):
        die("API_TOKEN contains whitespace/newlines")
    return token


def verify_token(token: str) -> None:
    """Confirm the token is recognised and active. Surface a clear,
    permission-specific diagnostic when verify fails.

    Cloudflare's `/user/tokens/verify` endpoint only reports the token
    LIFECYCLE state (active / expired / disabled). It does NOT enumerate
    granted permissions, so a passing verify does not guarantee that
    `Zone:Cache Purge:Purge` is on the token. Verify is still useful
    because it distinguishes 'token is wrong / rotated / expired' from
    'token is fine but missing a specific permission'.
    """
    log("Verifying Cloudflare API token")
    status, payload = cf_request("GET", "/user/tokens/verify", token=token)
    if status == 200 and payload.get("success"):
        token_status = (payload.get("result") or {}).get("status") or "unknown"
        log(f"Token is valid (status={token_status})")
        return
    die(
        f"Cloudflare /user/tokens/verify returned HTTP {status} success={payload.get('success')}",
        "The token is invalid, expired, disabled, or has been rotated.",
        "Re-issue at https://dash.cloudflare.com/profile/api-tokens and update the workflow secret.",
        f"Response: {json.dumps(payload)[:400]}",
    )


def cf_request(
    method: str, path: str, *, token: str, query: dict[str, str] | None = None,
    body: bytes | None = None,
) -> tuple[int, dict]:
    url = f"{API_BASE}{path}"
    if query:
        url = f"{url}?{urllib.parse.urlencode(query)}"
    req = urllib.request.Request(url, method=method, data=body)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/json")
    req.add_header("User-Agent", USER_AGENT)
    if body is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        raw = exc.read() or b""
        try:
            return exc.code, json.loads(raw)
        except json.JSONDecodeError:
            return exc.code, {"_raw": raw.decode("utf-8", "replace")}


def derive_apex(site_url: str) -> str:
    host = site_url.split("://", 1)[-1].split("/", 1)[0].split(":", 1)[0].lower()
    if host.startswith("www."):
        host = host[len("www."):]
    if not HOST_RE.match(host):
        die(f"derived hostname {host!r} from site_url is not a valid domain")
    return host


def validate_id(value: str, kind: str) -> str:
    cleaned = "".join(value.split())
    if not ID_RE.match(cleaned):
        die(f"Cloudflare {kind} ID must be a 32-char lowercase hex string (got {cleaned!r})")
    return cleaned


def resolve_zone_from_site(site_url: str, token: str) -> str:
    apex = derive_apex(site_url)
    log(f"Looking up Cloudflare zone for {apex}")
    status, payload = cf_request(
        "GET", "/zones", token=token, query={"name": apex, "status": "active"},
    )
    if status != 200 or not payload.get("success"):
        die(
            f"Cloudflare /zones lookup for {apex!r} returned HTTP {status}",
            "Token needs Zone:Read in addition to Cache Purge for auto-resolve.",
            f"Response: {json.dumps(payload)[:400]}",
        )
    result = payload.get("result") or []
    if not result:
        die(
            f"no active Cloudflare zone matched name={apex!r}",
            "Confirm the apex domain is attached to the account this token authenticates, or pass zone_id explicitly.",
        )
    return validate_id(result[0].get("id", ""), "zone")


def purge_zone(zone_id: str, token: str) -> tuple[int, dict]:
    log(f"Purging cache for zone {zone_id}")
    return cf_request(
        "POST", f"/zones/{zone_id}/purge_cache",
        token=token, body=b'{"purge_everything":true}',
    )


def explain_purge_failure(zone_id: str, status: int, payload: dict) -> list[str]:
    """Map known Cloudflare error shapes to a specific remediation hint."""
    errors = payload.get("errors") or []
    codes = {err.get("code") for err in errors if isinstance(err, dict)}
    hints: list[str] = []
    if status in (401, 403) or 10000 in codes:
        hints.append(
            "Token authenticated but is not authorised to purge this zone.",
        )
        hints.append(
            f"Grant `Zone -> Cache Purge -> Purge` on zone {zone_id} "
            "(or 'All zones from an account' covering it) at "
            "https://dash.cloudflare.com/profile/api-tokens, then re-run.",
        )
        hints.append(
            "The token already has Zone:Read (the auto-resolve succeeded) "
            "but lacks Cache Purge; these are two separate permissions.",
        )
    elif status == 429:
        hints.append("Cloudflare rate-limited the purge. Retry after a short backoff.")
    elif status >= 500:
        hints.append("Cloudflare side error. Retry; if it persists, check https://www.cloudflarestatus.com/.")
    return hints


def main() -> int:
    token = require_token(os.environ.get("API_TOKEN", "").strip())
    verify_token(token)

    explicit = (os.environ.get("ZONE_ID") or os.environ.get("FALLBACK_ZONE_ID") or "").strip()
    site_url = os.environ.get("SITE_URL", "").strip()

    if explicit:
        zone_id = validate_id(explicit, "zone")
    elif site_url:
        zone_id = resolve_zone_from_site(site_url, token)
        log(f"Resolved zone ID: {zone_id}")
    else:
        die("no zone_id, fallback_zone_id, or site_url supplied")

    status, payload = purge_zone(zone_id, token)
    print(json.dumps(payload, indent=2))

    success = status == 200 and bool(payload.get("success"))
    write_outputs({
        "zone_id": zone_id,
        "purged": "true" if success else "false",
        "http_code": str(status),
    })

    if not success:
        die(
            f"Cloudflare purge_cache returned HTTP {status} success={payload.get('success')}",
            *explain_purge_failure(zone_id, status, payload),
        )
    log("Cache purge OK.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
