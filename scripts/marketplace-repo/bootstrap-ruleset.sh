#!/usr/bin/env bash
# bootstrap-ruleset.sh — apply (or update) the Marketplace-Action
# default-branch ruleset on a GitHub organization.
#
# Usage:
#   ./bootstrap-ruleset.sh <org> [<ruleset-json>]
#
# Behavior:
#   * Looks up an existing ruleset with the same `name` on the org.
#   * If found, updates it via PUT (preserving the ruleset ID).
#   * If not, creates it via POST.
#   * Both operations are idempotent — re-running with an unchanged
#     JSON file is a safe no-op (apart from a server-side update
#     timestamp bump).
#
# Requires: `gh` CLI authenticated with `admin:org` + `repo` scopes
#           `jq` for response parsing
#
# See scripts/marketplace-repo/README.md for the full enrollment flow.

set -euo pipefail

die() {
  printf 'bootstrap-ruleset: %s\n' "$*" >&2
  exit 1
}

# ---- Arg parsing -------------------------------------------------------------
ORG="${1:-}"
RULESET_PATH="${2:-$(dirname "$0")/main-protection-ruleset.json}"

[ -n "${ORG}" ] || die "usage: $0 <org> [<ruleset-json>]"
[ -f "${RULESET_PATH}" ] || die "ruleset JSON not found: ${RULESET_PATH}"

# ---- Tool checks -------------------------------------------------------------
command -v gh >/dev/null 2>&1 || die "'gh' CLI not on PATH"
command -v jq >/dev/null 2>&1 || die "'jq' not on PATH"

# Confirm gh is authenticated.
gh auth status >/dev/null 2>&1 || die "gh CLI not authenticated. Run: gh auth login"

# ---- Sanity-check the JSON ---------------------------------------------------
RULESET_NAME="$(jq -r '.name // empty' "${RULESET_PATH}")"
[ -n "${RULESET_NAME}" ] || die "ruleset JSON is missing top-level 'name'"

# Refuse to ship the placeholder bypass_actors entry — it would create
# a ruleset that blocks the legitimate release bot.
PLACEHOLDER_HITS="$(jq -r '
  [.bypass_actors[]?.actor_id]
  | map(select(tostring | startswith("BYPASS_ACTOR_ID_PLACEHOLDER")))
  | length
' "${RULESET_PATH}")"
if [ "${PLACEHOLDER_HITS}" != "0" ]; then
  die "ruleset JSON still contains BYPASS_ACTOR_ID_PLACEHOLDER — replace with the bypass actor's integration / App ID before running. See scripts/marketplace-repo/README.md."
fi

# Refuse to ship if no repos are listed (would silently apply to nothing).
REPO_COUNT="$(jq -r '.conditions.repository_name.include | length' "${RULESET_PATH}")"
[ "${REPO_COUNT}" -gt 0 ] || die "ruleset JSON has empty conditions.repository_name.include — add at least one repo name."

# ---- Find existing ruleset ---------------------------------------------------
echo "==> Looking up existing ruleset '${RULESET_NAME}' on org '${ORG}'..."

# `gh api --paginate` so we don't miss it on orgs with >30 rulesets.
EXISTING_ID="$(
  gh api --paginate "/orgs/${ORG}/rulesets" \
    --jq ".[] | select(.name == \"${RULESET_NAME}\") | .id" || true
)"

# ---- Build the payload (strip our `_comment_*` keys; the API rejects them) ---
PAYLOAD="$(jq 'with_entries(select(.key | startswith("_comment_") | not))' "${RULESET_PATH}")"

if [ -n "${EXISTING_ID}" ]; then
  echo "==> Updating ruleset ID ${EXISTING_ID}..."
  RESPONSE="$(printf '%s' "${PAYLOAD}" | gh api \
    -X PUT \
    -H 'Accept: application/vnd.github+json' \
    --input - \
    "/orgs/${ORG}/rulesets/${EXISTING_ID}")"
  ACTION="updated"
else
  echo "==> Creating new ruleset..."
  RESPONSE="$(printf '%s' "${PAYLOAD}" | gh api \
    -X POST \
    -H 'Accept: application/vnd.github+json' \
    --input - \
    "/orgs/${ORG}/rulesets")"
  ACTION="created"
fi

RULESET_ID="$(printf '%s' "${RESPONSE}" | jq -r '.id // empty')"
[ -n "${RULESET_ID}" ] || die "ruleset ${ACTION} did not return an ID. Raw response:\n${RESPONSE}"

echo "==> ${ACTION} ruleset '${RULESET_NAME}' (ID ${RULESET_ID}) on org ${ORG}."
echo "    https://github.com/organizations/${ORG}/settings/rules/${RULESET_ID}"
echo ""
echo "Next: verify by attempting a violating push from a feature branch on a covered repo;"
echo "expect 'GH013: Repository rule violations found ... file path restriction'."
