# Marketplace repo enforcement bootstrap scripts

This directory contains the **one-time platform setup** required to
enforce GitHub Marketplace Action publishing compliance across one or
more repos in the `blackoutsecure` org.

> **What this enforces:** GitHub Marketplace publishing requires that
> the **default branch of an Action repo contain zero workflow files
> under `.github/workflows/`**. The rule is enforced by Marketplace at
> publish time; if it is violated, the publish silently fails (or the
> repo silently drops out of the Marketplace listing on the next
> publish attempt). Source:
> <https://docs.github.com/en/actions/how-tos/create-and-publish-actions/publish-in-github-marketplace>.

The hub's runtime defenses (the [`bos-marketplace-kit`](https://github.com/marketplace/actions/blackout-secure-marketplace-kit)
[`promote`](https://github.com/blackoutsecure/bos-marketplace-kit/tree/main/.github/actions/promote)
+ [`guard`](https://github.com/blackoutsecure/bos-marketplace-kit/tree/main/.github/actions/guard)
composite Actions, invoked from
[`release-promote.yml`](../../.github/workflows/release-promote.yml)
and
[`marketplace-repo-guard.yml`](../../.github/workflows/marketplace-repo-guard.yml))
prevent the *automation* from doing the wrong thing. The scripts here
prevent **humans, bots without bypass, and other workflows** from doing
it by enforcing the rule at the **GitHub platform** layer — the only
layer that catches direct pushes from a maintainer's laptop, force
pushes, merges from a UI button, and merges via the GraphQL API.

## Files

| File                              | Purpose                                                                 |
|-----------------------------------|-------------------------------------------------------------------------|
| `main-protection-ruleset.json`    | Org-level ruleset template: blocks `.github/workflows/**` on default branch. |
| `bootstrap-ruleset.sh`            | Apply / update the org ruleset via `gh api` (RECOMMENDED).              |
| `bootstrap-branch-protection.sh`  | Per-repo branch protection alternative when no org admin is available.  |

## Which one do I use?

```
                          Do you have org-admin
                          access for the
                          `blackoutsecure` org?
                                   |
                          +--------+--------+
                         YES               NO
                          |                 |
              bootstrap-ruleset.sh   bootstrap-branch-protection.sh
            (preferred: one ruleset    (one run per Action repo,
             covers ALL listed repos    requires repo-admin only)
             at once)
```

The ruleset path is preferred because (a) one source of truth covers
every Marketplace Action repo in the org, (b) ruleset bypass actors
are easier to audit than branch-protection PR exceptions, and (c) new
Action repos can be added by editing one JSON file rather than
running a script per repo.

## What gets blocked

Both paths configure the same enforcement:

1. **Path block:** Any push to the default branch that adds, modifies,
   or renames a file under `.github/workflows/` is rejected by GitHub
   before the ref is updated. The error is surfaced in the merge UI
   and in the `git push` output.
2. **Pull-request requirement:** Direct pushes to the default branch
   are forbidden; all changes must arrive via PR.
3. **Bypass actor:** A named actor (e.g. an org-owned GitHub App used
   by `release-promote.yml`) is permitted to push around the path
   block. Without a bypass actor, the legitimate `dev -> main`
   promotion path is also blocked, which defeats the purpose.

## Prerequisites

* **`gh` CLI** authenticated as someone with the necessary scopes:
  * Org ruleset path → `admin:org` + `repo`.
  * Per-repo branch protection path → repo admin on each target repo.
* **A bypass actor identity** — typically a GitHub App installed on
  the org (preferred) or a dedicated service account with a
  fine-grained PAT. Capture its actor ID before running the
  bootstrap; the JSON template uses a placeholder you must replace.

  To find a GitHub App's actor ID:

  ```bash
  gh api '/orgs/{org}/installations' \
    --jq '.installations[] | select(.app_slug=="<your-app>") | .app_id'
  ```

## Running

### Org ruleset (preferred)

1. Edit `main-protection-ruleset.json` and fill in:
   * `"include"` repository list under `conditions.repository_name.include`.
   * `bypass_actors[0].actor_id` with the bypass App / Bot ID.
2. Run:

   ```bash
   ./bootstrap-ruleset.sh blackoutsecure ./main-protection-ruleset.json
   ```

3. Verify by attempting a violating push from a fork:

   ```bash
   # On a feature branch, add a workflow file and push.
   # Expect: "GH013: Repository rule violations found ... file path restriction".
   ```

### Per-repo branch protection (fallback)

```bash
./bootstrap-branch-protection.sh blackoutsecure/bos-sitemap-generator main
./bootstrap-branch-protection.sh blackoutsecure/bos-upstream-watcher main
./bootstrap-branch-protection.sh blackoutsecure/bos-nginx-config-validator main
```

Note: GitHub Branch Protection rules do **not** include the
`file_path_restriction` rule (that's ruleset-only). The fallback
configures the surrounding policies (PR-required, no direct push,
restricted pushers) and relies on the in-PR
`marketplace-repo-guard.yml` workflow plus the
[`bos-marketplace-kit` `promote` Action](https://github.com/blackoutsecure/bos-marketplace-kit/tree/main/.github/actions/promote)
to enforce the path block. This
is **defense-in-depth without platform enforcement** — slightly weaker
but still effective for the normal PR flow.

## Default branch must be `main`

Both paths assume the default branch is `main`. If you set the
default branch to something else, update `target.ref` in the JSON
template (`refs/heads/<your-default>`) and the second argument to
`bootstrap-branch-protection.sh`.

## What is NOT covered here

* **Setting the default branch to `main`** — UI-only operation
  (Settings → Branches → default branch). Required once per repo
  before the bootstrap.
* **Creating the `dev` branch** — one-time `git push origin main:dev`.
* **Initial Marketplace publish acceptance** — first publish requires
  a maintainer to tick "Publish this Action to the GitHub
  Marketplace" in the Release UI. Subsequent releases auto-publish.
