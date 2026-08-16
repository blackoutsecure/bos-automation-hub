"""Reconcile structured AI remediation recommendations with GitHub PRs."""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

API = "https://api.github.com"
MODES = {"notify", "draft_pr", "pr"}
MARKER = "<!-- blackout-secure-remediation:{key} -->"


def fail(message: str) -> None:
    print(f"::error title=AI remediation::{message}")
    raise SystemExit(1)


def request(path: str, token: str, method: str = "GET", payload: dict | None = None):
    body = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(
        f"{API}{path}",
        data=body,
        method=method,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            return json.loads(response.read().decode()) if response.readable() else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        fail(f"GitHub API {method} {path} failed ({exc.code}): {detail[:400]}")
    except (OSError, ValueError) as exc:
        fail(f"GitHub API {method} {path} failed: {exc}")


def run(*args: str) -> None:
    subprocess.run(args, check=True, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def slug(value: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return value[:40] or "recommendation"


def recommendation_hash(item: dict) -> str:
    raw = item.get("recommendation_hash") or item.get("patch") or item.get("recommendation") or ""
    return hashlib.sha256(str(raw).encode()).hexdigest()[:16]


def main() -> None:
    path = Path(os.environ["REMEDIATION_RECOMMENDATIONS"])
    mode = (os.environ.get("REMEDIATION_MODE") or "notify").strip().lower()
    token = os.environ.get("REMEDIATION_TOKEN", "").strip()
    repo = os.environ["REMEDIATION_REPO"]
    base = os.environ.get("REMEDIATION_BASE", "").strip()
    labels = [x.strip() for x in os.environ.get("REMEDIATION_LABELS", "").split(",") if x.strip()]
    if mode not in MODES:
        fail(f"mode must be one of {sorted(MODES)}")
    if not path.is_file():
        fail(f"recommendations file not found: {path}")
    try:
        recommendations = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        fail(f"recommendations must be valid JSON: {exc}")
    if not isinstance(recommendations, list):
        fail("recommendations must be a JSON array")
    if not recommendations:
        report = "[]"
        output = os.environ.get("GITHUB_OUTPUT")
        if output:
            with open(output, "a", encoding="utf-8") as handle:
                handle.write(f"report<<__BOS_REMEDIATION__\n{report}\n__BOS_REMEDIATION__\n")
        print(report)
        return
    if not token:
        fail("token is required to reconcile existing PRs")

    open_prs = request(f"/repos/{repo}/pulls?state=open&per_page=100", token)
    by_key = {}
    for pr in open_prs:
        body = pr.get("body") or ""
        match = re.search(r"blackout-secure-remediation:([^ ]+) -->", body)
        if match:
            by_key[match.group(1)] = pr

    results = []
    for index, item in enumerate(recommendations):
        if not isinstance(item, dict):
            fail(f"recommendations[{index}] must be an object")
        key = str(item.get("finding_key") or item.get("id") or "").strip()
        title = str(item.get("title") or item.get("recommendation") or "Remediation recommendation").strip()
        if not key:
            fail(f"recommendations[{index}] is missing finding_key")
        current_hash = recommendation_hash(item)
        existing = by_key.get(key)
        if existing:
            body = existing.get("body") or ""
            old_hash = re.search(r"recommendation_hash: ([0-9a-f]+)", body)
            changed = not old_hash or old_hash.group(1) != current_hash
            if changed and mode != "notify":
                request(
                    f"/repos/{repo}/issues/{existing['number']}/comments",
                    token,
                    "POST",
                    {"body": f"Updated recommendation ({current_hash}):\n\n{item.get('recommendation', title)}"},
                )
                status = "existing_pr_updated"
            else:
                status = "existing_pr_linked" if not changed else "no_update_needed"
            results.append({"finding_key": key, "status": status, "pr": existing["html_url"]})
            continue

        patch = item.get("patch")
        if mode == "notify" or not isinstance(patch, str) or not patch.strip():
            results.append({
                "finding_key": key,
                "status": "no_pr",
                "reason": "notify mode or no machine-applicable patch",
            })
            continue

        branch = f"blackout-secure/remediation/{slug(key)}"
        body = (
            f"{MARKER.format(key=key)}\n"
            "## AI remediation\n\n"
            f"{item.get('recommendation', title)}\n\n"
            f"- Confidence: `{item.get('confidence', 'unknown')}`\n"
            f"- Rule: `{item.get('rule_id', 'unspecified')}`\n"
            f"- Recommendation hash: `{current_hash}`\n"
            "\nGenerated as a draft for human review."
        )
        run("git", "fetch", "origin", base)
        run("git", "switch", "-C", branch, f"origin/{base}")
        patch_path = Path(".github-ai-remediation.patch")
        patch_path.write_text(patch, encoding="utf-8")
        try:
            run("git", "apply", "--check", str(patch_path))
            run("git", "apply", str(patch_path))
            patch_path.unlink()
            run("git", "config", "user.name", "github-actions[bot]")
            run("git", "config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com")
            run("git", "add", "--all")
            run("git", "commit", "-m", f"fix: {title[:60]}")
            run("git", "push", "--force-with-lease", "origin", branch)
        except subprocess.CalledProcessError as exc:
            patch_path.unlink(missing_ok=True)
            results.append({"finding_key": key, "status": "blocked", "reason": f"patch validation failed: {exc}"})
            continue
        finally:
            run("git", "switch", "--detach", "origin/" + base)
        pr = request(f"/repos/{repo}/pulls", token, "POST", {
            "title": title,
            "head": branch,
            "base": base,
            "body": body,
            "draft": mode == "draft_pr",
        })
        for label in labels:
            request(f"/repos/{repo}/issues/{pr['number']}/labels", token, "POST", {"labels": [label]})
        results.append({"finding_key": key, "status": "pr_created", "pr": pr["html_url"]})

    report = json.dumps(results, separators=(",", ":"))
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as handle:
            handle.write(f"report<<__BOS_REMEDIATION__\n{report}\n__BOS_REMEDIATION__\n")
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
