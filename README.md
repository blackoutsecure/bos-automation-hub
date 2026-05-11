# platform-automation

Reusable GitHub Actions workflows and composite actions shared across
**blackoutsecure** repositories.

This repo is public so any repository (including forks) can call these
workflows directly without needing a token.

### Configuration model

- **`secrets:`** — things that grant access (Docker Hub token, Balena
  API token, optional `ACTIONS_REPO_TOKEN`).
- **`vars:`** — things that identify *what* you're publishing (image
  name, namespace, block name). Set them once on the repository or
  organisation and the caller workflow stays free of literals.

The Docker Hub namespace is **public information** (it appears in every
`docker pull` URL), so it's modelled as `vars.DOCKERHUB_NAMESPACE` /
`inputs.dockerhub_namespace` rather than a secret. The legacy
`secrets.DOCKERHUB_NAMESPACE` is still accepted for back-compat.

---

## Contents

| Path | Kind | Purpose |
|------|------|---------|
| [.github/workflows/docker-build-push.yml](.github/workflows/docker-build-push.yml) | Reusable workflow | Multi-arch Docker build, push-by-digest, and single-manifest publish to Docker Hub. |
| [.github/workflows/balena-block-publish.yml](.github/workflows/balena-block-publish.yml) | Reusable workflow | Resolve a block version, optionally sync `balena.yml`, and publish via `balena-io/deploy-to-balena-action`. |
| [.github/workflows/github-release.yml](.github/workflows/github-release.yml) | Reusable workflow | Render Markdown release notes from a template + structured inputs and create/update a GitHub Release via `softprops/action-gh-release`. |
| [.github/workflows/monitor-upstream-release.yml](.github/workflows/monitor-upstream-release.yml) | Reusable workflow | Poll an upstream repo's `latest` release, dispatch downstream workflows on change, and commit a tracking file. |
| [.github/workflows/deploy-cloudflare-pages.yml](.github/workflows/deploy-cloudflare-pages.yml) | Reusable workflow | Stage a static-site build, optionally generate `sitemap.xml` / `robots.txt` / `security.txt` / Web App Manifest, and deploy to Cloudflare Pages via `cloudflare/wrangler-action`. |
| [.github/actions/shared/resolve-docker-image-tags/action.yml](.github/actions/shared/resolve-docker-image-tags/action.yml) | Composite action | Resolves an image version from a Dockerfile `ARG`, version file, git tag, or commit SHA and emits a deduplicated tag list. |
| [.github/actions/shared/resolve-release-context/action.yml](.github/actions/shared/resolve-release-context/action.yml) | Composite action | Shared "publish-on-default-branch" gate + version/`build_date` selection used by both reusable workflows. |
| [.github/actions/sync-dockerhub-description/action.yml](.github/actions/sync-dockerhub-description/action.yml) | Composite action | Validates inputs and pushes a repo's README + short description to Docker Hub via `peter-evans/dockerhub-description`. |
| [.github/actions/render-release-notes/action.yml](.github/actions/render-release-notes/action.yml) | Composite action | Renders Markdown release notes from a template with safe `{{ key }}` substitution — no shell or template-engine execution against user values. |
| [.github/workflows/lint.yml](.github/workflows/lint.yml) | Workflow | Runs `actionlint` + `shellcheck` on this repo's workflows and actions. |
| [.github/workflows/openwrt-readsb-wiedehopf-bump.yml](.github/workflows/openwrt-readsb-wiedehopf-bump.yml) | Scheduled automation | Tracks new `wiedehopf/readsb` releases and proposes them upstream as a cross-repo PR to `openwrt/packages` (bumps `PKG_VERSION`/`PKG_HASH`, resets `PKG_RELEASE`) via a bot-owned fork. |

---

## `docker-build-push.yml` — reusable workflow

Build a multi-arch Docker image (amd64 + arm64 by default), push each
architecture by digest, and assemble a single OCI manifest with the
resolved version tag (plus optional `:latest` and extra tags).

### Minimal caller

```yaml
name: Build image

on:
  push:
    branches: [main]
  pull_request:

permissions:
  contents: read

jobs:
  docker:
    uses: blackoutsecure/platform-automation/.github/workflows/docker-build-push.yml@main
    with:
      image_name: my-service
      dockerhub_namespace: ${{ vars.DOCKERHUB_NAMESPACE }}
    secrets:
      DOCKERHUB_USERNAME: ${{ secrets.DOCKERHUB_USERNAME }}
      DOCKERHUB_TOKEN:    ${{ secrets.DOCKERHUB_TOKEN }}
```

### Default behaviour

- **Push trigger:** the workflow pushes only when the caller event is a
  `push` to the default branch. Override with the `push` input.
- **Version resolution:** `auto` cascade → Dockerfile `ARG APP_VERSION` →
  `VERSION` file → annotated git tag → commit SHA.
- **Platforms:** `linux/amd64` + `linux/arm64`. Set `multi_arch: false`
  for amd64-only.
- **Tagging:** `:<version>`, `:latest` (toggle with `latest`), plus any
  caller-supplied `extra_tags`.
- **Labels:** standard OCI labels (`created`, `version`, `revision`,
  `source`) are attached to every build.

### Inputs

| Input | Type | Default | Description |
|-------|------|---------|-------------|
| `image_name` | string | **required** | Image name (no registry/namespace). |
| `dockerhub_namespace` | string | `''` | Docker Hub namespace. Set via `vars.DOCKERHUB_NAMESPACE`. Falls back to `secrets.DOCKERHUB_NAMESPACE` for back-compat. |
| `dockerfile` | string | `./Dockerfile` | Path to the Dockerfile. |
| `build_context` | string | `.` | Docker build context. |
| `registry` | string | `docker.io` | Registry hostname. |
| `push` | string | `''` | `'true'`/`'false'` to force push; empty = push on default branch only. |
| `image_version` | string | `''` | Explicit version; overrides auto-resolution. |
| `extra_tags` | string | `''` | Newline-separated extra tags. |
| `latest` | boolean | `true` | Also tag `:latest` when pushing. |
| `multi_arch` | boolean | `true` | Build amd64 + arm64. |
| `platforms` | string | `''` | Override platform list (comma-separated). |
| `build_args` | string | `''` | Newline-separated `KEY=VALUE` build args. |
| `version_build_arg` | string | `APP_VERSION` | Dockerfile `ARG` to read/populate with the version. |
| `inject_oci_build_args` | boolean | `true` | Inject `BUILD_DATE`, `VCS_REF`, `VCS_URL` build args. |
| `auto_resolve_version` | boolean | `true` | Run the composite resolver. |
| `version_source` | string | `auto` | `auto` \| `dockerfile` \| `file` \| `git_tag` \| `sha`. |
| `version_file` | string | `VERSION` | Plain-text version file path. |
| `distro` | string | `''` | Variant suffix (e.g. `alpine`). |
| `build_date` | string | `''` | Override ISO-8601 build date. |
| `manifest_retries` | number | `3` | Retry attempts for manifest creation. |
| `manifest_retry_delay` | number | `15` | Seconds between manifest retries. |
| `actions_repo` | string | `blackoutsecure/platform-automation` | Repo hosting the composite action. |
| `resolver_ref` | string | `main` | Git ref of `actions_repo` to check out. |
| `runner_type` | string | `cloud` | `cloud` (GitHub-hosted) or `self-hosted`. |
| `update_description` | boolean | `true` | Sync the repo README + short description to Docker Hub after a successful manifest push. No-op when not pushing. |
| `readme_filepath` | string | `./README.md` | Path to the README uploaded as the Docker Hub full description. |
| `short_description` | string | `''` | Docker Hub short description (max 100 chars). Empty leaves the existing one untouched. |
| `enable_url_completion` | boolean | `true` | Convert relative URLs in the README to absolute GitHub URLs. |

### Secrets

| Secret | Required | Description |
|--------|:--------:|-------------|
| `DOCKERHUB_USERNAME` | ✔ | Docker Hub username. |
| `DOCKERHUB_TOKEN` | ✔ | Docker Hub access token (not your password). |
| `DOCKERHUB_NAMESPACE` | ✖ | **Deprecated.** Pass `dockerhub_namespace:` (or set `vars.DOCKERHUB_NAMESPACE`) instead. Still accepted for back-compat. |
| `ACTIONS_REPO_TOKEN` | ✖ | Only needed if `actions_repo` is private. Falls back to `GITHUB_TOKEN`. |

### Outputs

| Output | Description |
|--------|-------------|
| `image` | Fully qualified image reference (`registry/namespace/name`). |
| `image_version` | Resolved version tag. |
| `namespace` | Effective Docker Hub namespace. |

### Self-hosted runners

Set `runner_type: self-hosted`. The workflow expects runners labelled:

- amd64: `self-hosted, Linux, x64`
- arm64: `self-hosted, Linux, ARM64`

---

## `resolve-docker-image-tags` — composite action

Standalone version/tag resolver. Usable outside the reusable workflow.

```yaml
- uses: blackoutsecure/platform-automation/.github/actions/shared/resolve-docker-image-tags@main
  id: tags
  with:
    version_source: auto            # auto | dockerfile | file | git_tag | sha
    dockerfile: ./Dockerfile
    dockerfile_arg: APP_VERSION
    version_file: VERSION
    distro: alpine                  # optional
    extra_tags: |
      stable
      prod
- run: echo "version=${{ steps.tags.outputs.version }}"
```

### Outputs

- `version` — resolved version tag
- `short_sha` — shortened commit SHA
- `build_date` — ISO-8601 UTC timestamp
- `source` — which resolver path produced the version
- `extra_tags` — newline-separated deduplicated tag list

See [action.yml](.github/actions/shared/resolve-docker-image-tags/action.yml)
for the full input list.

---

## `sync-dockerhub-description` — composite action

Uploads a repository README and short description to Docker Hub. Wraps
[`peter-evans/dockerhub-description`](https://github.com/peter-evans/dockerhub-description)
with preflight validation (README exists, credentials/repository slug
are non-empty and single-line, short description fits Docker Hub's
100-char cap) so failures surface with a clear message instead of a
401 from the registry API.

It is invoked automatically by the `update-description` job in
[`docker-build-push.yml`](.github/workflows/docker-build-push.yml) after
a successful manifest push, and can also be used standalone:

```yaml
- uses: blackoutsecure/platform-automation/.github/actions/sync-dockerhub-description@main
  with:
    repository: ${{ vars.DOCKERHUB_NAMESPACE }}/my-service
    username: ${{ secrets.DOCKERHUB_USERNAME }}
    password: ${{ secrets.DOCKERHUB_TOKEN }}
    readme_filepath: ./README.md
    short_description: A short tagline shown on the Docker Hub repo page.
```

See [action.yml](.github/actions/sync-dockerhub-description/action.yml)
for the full input list.

---

## `balena-block-publish.yml` — reusable workflow

Resolve a block version (same logic as the Docker workflow), optionally
sync the version back into `balena.yml`, and publish to balenaCloud via
[`balena-io/deploy-to-balena-action`](https://github.com/balena-io/deploy-to-balena-action).

### Required configuration on the caller repo

| Kind     | Name                  | Used for |
|----------|-----------------------|----------|
| Variable | `BALENA_BLOCK_NAME`   | Block slug (without namespace). |
| Variable | `BALENA_NAMESPACE`    | Balena user/org that owns the block. |
| Variable | `DOCKERHUB_NAMESPACE` | Docker Hub namespace for the image. |
| Secret   | `BALENA_API_TOKEN`    | balenaCloud API token (forwarded via `secrets:`). |

### Default behaviour

- **Publish trigger:** publishes only on `push` to the default branch.
  Override with the `publish` input (or via `workflow_dispatch`).
- **Version resolution:** `auto` cascade → Dockerfile `ARG APP_VERSION` →
  `VERSION` file → annotated git tag → commit SHA.
- **`balena.yml` sync:** when `sync_balena_yml: true` (default) and the
  workflow is publishing on the default branch, the top-level `version:`
  is rewritten and committed back. Requires `contents: write` on the
  caller job (set in the template).
- **Concurrency:** balenaCloud rejects concurrent pushes to the same
  block; in-progress runs are never cancelled.

See [.github/workflows/balena-block-publish.yml](.github/workflows/balena-block-publish.yml)
for the full input/output list.

---

## `github-release.yml` — reusable workflow

Create or update a GitHub Release with a rendered Markdown body that
includes `docker pull` commands, supported architectures, and any
caller-supplied context. Wraps
[`softprops/action-gh-release`](https://github.com/softprops/action-gh-release)
(SHA-pinned) and the [`render-release-notes`](.github/actions/render-release-notes/action.yml)
composite action.

### Default behaviour

- **Trigger-agnostic.** The caller passes `tag_name:` explicitly, so
  the workflow works on `release` events, tag pushes, or manual
  dispatches without hard-coding event assumptions.
- **Pre-release auto-detection.** Tags shaped like `vX.Y.Z-<suffix>`
  (e.g. `v1.2.3-rc1`) are marked as pre-releases automatically. Override
  with `prerelease: 'true'` or `prerelease: 'false'`.
- **Update-in-place.** Re-running against the same `tag_name:`
  patches the existing release body instead of erroring.
- **Auto-generated notes.** GitHub's PR/contributor notes are appended
  after the rendered template body when `generate_release_notes: true`
  (the default).
- **Least-privilege.** Workflow defaults to `contents: read`; the
  publish job opts into `contents: write` itself — the caller doesn't
  need to grant it.

### Template substitution

The bundled template at
[.github/actions/render-release-notes/default-template.md](.github/actions/render-release-notes/default-template.md)
supports these placeholders out of the box:

| Placeholder | Source |
|-------------|--------|
| `{{ release_name }}` | `release_name` input (defaults to `tag_name`). |
| `{{ tag_name }}`     | `tag_name` input. |
| `{{ version }}`      | `version` input (defaults to `tag_name` minus leading `v`). |
| `{{ short_sha }}`    | First 12 chars of `github.sha`. |
| `{{ commit_url }}`   | Auto-built from `github.server_url` + `github.repository`. |
| `{{ build_date }}`   | ISO-8601 UTC timestamp at render time. |
| `{{ image_section }}`     | Rendered when `image_ref:` is set. |
| `{{ platforms_section }}` | Rendered when `platforms:` is set. |
| `{{ extra_section }}`     | Bullet list built from `extra_context:` `KEY=VALUE` pairs. |

Keys passed via `extra_context:` are also available as `{{ KEY }}` for
custom templates. Substitution is regex-based on a fixed key shape
(`^[A-Z][A-Z0-9_]*$`) — there is no template-engine evaluation, so
user-supplied values cannot inject runner commands.

See [.github/workflows/github-release.yml](.github/workflows/github-release.yml)
for the full input/output list.

---

## `monitor-upstream-release.yml` — reusable workflow

Poll a public GitHub repo on a schedule and react when its `latest`
release tag changes. Designed for downstream repos that wrap an upstream
project (e.g. building a custom Docker image of `actions/runner`,
`balena-io/balena-cli`, `k3s-io/k3s`) and want their pipeline to
rebuild whenever upstream cuts a release — without polling logic
living in every caller repo.

### Default behaviour

- **Tracking file.** A small JSON file at `inputs.track_file` (default
  `.github/upstream/tracked-release.json`) records the last-seen `repo`,
  `tag`, `version`, and resolved `commit` SHA. The file is committed
  back to the default branch when the upstream changes; subsequent runs
  diff against it to detect change.
- **Real commit SHA.** The release's `target_commitish` field is often
  a branch name (`main`), and the Git Refs API returns the *tag-object*
  SHA for annotated tags — neither is the commit you want. The workflow
  resolves the actual commit SHA via `repos/<owner>/<repo>/commits/<tag>`,
  which handles lightweight and annotated tags uniformly.
- **Dispatch-then-commit ordering.** Downstream workflows are
  dispatched **before** the tracking file is committed — if dispatch
  fails, the marker is unchanged and the next scheduled run retries.
- **Concurrent-push safety.** `git push` retries up to `push_retries`
  attempts with a `git pull --rebase` between tries.
- **No false positives.** `published_at` is deliberately excluded from
  the tracking file — GitHub re-publishing a release with a tweaked
  timestamp must not look like a new version.
- **Least-privilege.** Workflow defaults to `contents: read`; the
  monitor job opts into `contents: write` (for the marker commit) and
  `actions: write` (for `gh workflow run`).

### Optional `DISPATCH_TOKEN` secret

The default `GITHUB_TOKEN` cannot trigger further `workflow_run` /
`workflow_dispatch` chains. If your dispatched workflow itself needs
to trigger another workflow, pass a fine-scoped PAT or GitHub App
token via the `DISPATCH_TOKEN` secret.

See [.github/workflows/monitor-upstream-release.yml](.github/workflows/monitor-upstream-release.yml)
for the full input/output list.

---

## `deploy-cloudflare-pages.yml` — reusable workflow

Stage a static-site build into a deploy directory, optionally generate
SEO/PWA companion files (`sitemap.xml`, `robots.txt`,
`/.well-known/security.txt`, Web App Manifest), and publish to
[Cloudflare Pages](https://developers.cloudflare.com/pages/) via
[`cloudflare/wrangler-action`](https://github.com/cloudflare/wrangler-action).

### Minimal caller (organisation-shared pattern)

Configure the project name and account ID once at the org or repo
level, then any caller stays free of literals:

```yaml
name: Deploy site

on:
  push:
    branches: [main]
  workflow_dispatch:

permissions:
  contents: read

jobs:
  pages:
    uses: blackoutsecure/platform-automation/.github/workflows/deploy-cloudflare-pages.yml@main
    with:
      cloudflare_project_name: ${{ vars.CLOUDFLARE_PROJECT_NAME }}
      cloudflare_account_id:   ${{ vars.CLOUDFLARE_ACCOUNT_ID }}
      copy_files: |
        index.html
        favicon.ico
      copy_dirs: |
        assets
    secrets:
      CLOUDFLARE_API_TOKEN: ${{ secrets.CLOUDFLARE_API_TOKEN }}
```

Already storing the account ID as `secrets.CLOUDFLARE_ACCOUNT_ID`?
Pass it through the `secrets:` block instead — `inputs.cloudflare_account_id`
falls back to the secret for back-compat.

### Default behaviour

- **Deploy trigger:** the workflow deploys only on `push` events to the
  repository's default branch. Override with `deploy: 'true'` /
  `deploy: 'false'`, or branch-target via `branch:`.
- **Staging model:** files listed in `copy_files` and directories
  listed in `copy_dirs` are copied from `public_dir` (default `.`) into
  `deploy_dir` (default `./dist`), which is wiped first when
  `clean_deploy_dir: true`. A `prebuild_command` may run beforehand
  (e.g. `npm ci && npm run build`).
- **Generators are opt-in.** Each of `generate_sitemap`,
  `generate_robots`, `generate_security_txt`, and `generate_manifest`
  defaults to `false`. Enabling any of the first three requires
  `site_url:` to be set; enabling `generate_security_txt` additionally
  requires `security_contact:`; enabling `generate_manifest` requires
  `manifest_name:`.
- **Concurrency:** Cloudflare Pages serialises deploys per project, so
  in-progress runs are never cancelled.
- **Production vs preview:** wrangler treats the deploy as a production
  release when `branch:` matches the project's production branch in
  Cloudflare Pages; otherwise it's a preview. The resolved environment
  is reported as the `environment` output and in the job summary.
- **Production gating via GitHub Environments.** Set
  `deployment_environment: production` (or any environment name) to
  bind the deploy job to a [GitHub Environment](https://docs.github.com/en/actions/deployment/targeting-different-environments/using-environments-for-deployment)
  and inherit its required-reviewer rules, wait timer, branch policy,
  and environment-scoped secrets. Required reviewers approve from the
  Actions UI before wrangler runs.

### Caller-side configuration

The same `vars:` vs `secrets:` model used by the Docker workflow
applies here:

| Kind         | Name                       | Used for |
|--------------|----------------------------|----------|
| **Variable** | `CLOUDFLARE_PROJECT_NAME`  | Pages project name. Pass through `inputs.cloudflare_project_name`. |
| **Variable** | `CLOUDFLARE_ACCOUNT_ID`    | 32-char hex account ID (appears in dashboard URLs — not secret). Pass through `inputs.cloudflare_account_id`. |
| **Secret**   | `CLOUDFLARE_API_TOKEN`     | Cloudflare API token with `Account → Cloudflare Pages → Edit` on the project. |
| Secret       | `CLOUDFLARE_ACCOUNT_ID`    | **Optional / back-compat.** Used only if `inputs.cloudflare_account_id` is empty. Prefer the `vars.` form. |

Set `vars.CLOUDFLARE_PROJECT_NAME` and `vars.CLOUDFLARE_ACCOUNT_ID`
once at the **organisation** level for shared defaults, override at
the repo level when a project differs.

### Inputs

| Input | Type | Default | Description |
|-------|------|---------|-------------|
| `cloudflare_project_name` | string | **required** | Pages project name (lowercase, digits, `-`; 1–58 chars). |
| `cloudflare_account_id` | string | `''` | 32-char hex account ID. Falls back to `secrets.CLOUDFLARE_ACCOUNT_ID`. |
| `deployment_environment` | string | `''` | GitHub Environment name to bind the deploy job to (production gates, env-scoped secrets). |
| `site_url` | string | `''` | Canonical site URL. Required when any generator is enabled. |
| `public_dir` | string | `.` | Source root in the caller repo. |
| `deploy_dir` | string | `./dist` | Staging directory uploaded to Pages. |
| `clean_deploy_dir` | boolean | `true` | Remove `deploy_dir` before staging. |
| `copy_files` | string | `''` | Newline-separated files/globs (relative to `public_dir`) copied to `deploy_dir/`. |
| `copy_dirs` | string | `''` | Newline-separated `SRC[:DEST]` directory entries (relative to `public_dir`). |
| `prebuild_command` | string | `''` | Shell command run from `working_directory` before staging. |
| `working_directory` | string | `''` | Working dir for `prebuild_command` and wrangler. |
| `branch` | string | `''` | Override the deploy branch passed to `wrangler pages deploy`. |
| `commit_message` | string | `''` | Override the Pages deploy commit message. |
| `wrangler_version` | string | `''` | Pin wrangler (e.g. `4`, `^4.0.0`, `latest`). |
| `extra_wrangler_args` | string | `''` | Extra args appended to `wrangler pages deploy` (newlines = spaces). |
| `deploy` | string | `''` | `'true'`/`'false'` to force; empty deploys only on default-branch pushes. |
| `runs_on` | string | `ubuntu-latest` | Runner label. |
| `checkout_fetch_depth` | number | `0` | `fetch-depth` for `actions/checkout`. |
| `generate_sitemap` | boolean | `false` | Run `bos-sitemap-generator`. |
| `generate_robots` | boolean | `false` | Run `bos-robotstxt-generator`. |
| `generate_security_txt` | boolean | `false` | Run `bos-securitytxt-generator`. |
| `security_contact` | string | `''` | Email or `https://` URL written to `security.txt`. |
| `generate_manifest` | boolean | `false` | Run `bos-web-application-manifest-generator`. |
| `manifest_name` | string | `''` | PWA manifest `name` (required when generating). |
| `manifest_short_name` | string | `''` | PWA manifest `short_name`. |
| `manifest_description` | string | `''` | PWA manifest `description`. |
| `manifest_orientation` | string | `''` | PWA manifest `orientation`. |
| `manifest_theme_color` | string | `''` | PWA manifest `theme_color` (CSS hex). |
| `manifest_background_color` | string | `''` | PWA manifest `background_color` (CSS hex). |
| `manifest_lang` | string | `''` | PWA manifest `lang` (BCP 47 tag). |
| `manifest_dir` | string | `''` | PWA manifest `dir` (`ltr`/`rtl`/`auto`). |
| `manifest_categories` | string | `''` | PWA manifest `categories` (comma-separated). |
| `manifest_icons_dir` | string | `''` | Directory inside `deploy_dir` to scan for icons. |

### Outputs

| Output | Description |
|--------|-------------|
| `deployed` | `true` when wrangler pushed a deploy. |
| `deployment_url` | Primary deployment URL. |
| `deployment_id` | Cloudflare Pages deployment ID. |
| `deployment_alias_url` | Deployment alias URL (preview/branch deploys). |
| `environment` | `production` or `preview`. |
| `account_id` | Resolved Cloudflare account ID (input or fallback secret). |

### Production gating with GitHub Environments

Bind the deploy to a protected environment so production pushes
require human approval, and store the API token + account ID at the
**environment** scope so they're only readable when that environment
is targeted:

```yaml
jobs:
  pages:
    uses: blackoutsecure/platform-automation/.github/workflows/deploy-cloudflare-pages.yml@main
    with:
      cloudflare_project_name: ${{ vars.CLOUDFLARE_PROJECT_NAME }}
      cloudflare_account_id:   ${{ vars.CLOUDFLARE_ACCOUNT_ID }}
      deployment_environment:  production
      branch: main
    secrets:
      CLOUDFLARE_API_TOKEN: ${{ secrets.CLOUDFLARE_API_TOKEN }}
```

The deploy job inherits the `production` environment's required
reviewers, wait timer, and branch policy — wrangler doesn't run until
the gate is satisfied.

### Composite actions consumed by this workflow

Three steps are split out into local composite actions so they can be
audited independently and reused on their own:

| Action | Path | Purpose |
|--------|------|---------|
| `cloudflare-pages-resolve-account-id` | [.github/actions/cloudflare-pages-resolve-account-id/action.yml](.github/actions/cloudflare-pages-resolve-account-id/action.yml) | Picks the account ID from the input or fallback secret, validates the 32-char hex shape, and registers `::add-mask::` so the value is redacted from later logs. |
| `stage-deploy-dir` | [.github/actions/shared/stage-deploy-dir/action.yml](.github/actions/shared/stage-deploy-dir/action.yml) | Generic deploy-directory stager (`copy_files` + `copy_dirs` with `SRC:DEST` rewrite, glob expansion, and path-traversal rejection). Lives under `shared/` because it's not Cloudflare-specific — any static-site deploy can reuse it. |
| `cloudflare-pages-compose-command` | [.github/actions/cloudflare-pages-compose-command/action.yml](.github/actions/cloudflare-pages-compose-command/action.yml) | Builds the `wrangler pages deploy` argv as a properly shell-quoted string for `cloudflare/wrangler-action`'s `command:` input. |

Use them directly from any workflow when you don't need the full
reusable workflow — for example, the resolver works with any wrangler
command, not just `pages deploy`:

```yaml
- uses: actions/checkout@v4
  with: { persist-credentials: false }
- id: account
  uses: blackoutsecure/platform-automation/.github/actions/cloudflare-pages-resolve-account-id@main
  with:
    account_id:          ${{ vars.CLOUDFLARE_ACCOUNT_ID }}
    fallback_account_id: ${{ secrets.CLOUDFLARE_ACCOUNT_ID }}
- uses: cloudflare/wrangler-action@v3
  with:
    apiToken:  ${{ secrets.CLOUDFLARE_API_TOKEN }}
    accountId: ${{ steps.account.outputs.value }}
    command:   r2 object list my-bucket
```

### Security notes specific to this workflow

- All third-party actions (including the `blackoutsecure/bos-*`
  generators and `cloudflare/wrangler-action`) are SHA-pinned with the
  resolved tag in a trailing comment; Dependabot rolls them weekly.
- The three composite actions consumed by this workflow live in this
  repository (referenced as `./.github/actions/...`) so they're
  versioned together with the workflow and require no extra trust
  boundary.
- `public_dir`, `deploy_dir`, `working_directory`, and every entry in
  `copy_files`/`copy_dirs` are rejected if they're absolute, empty,
  contain `..`, or contain newlines — so a misconfigured caller cannot
  exfiltrate files from outside the workspace into the deploy bundle.
  The check runs in both the preflight step and the staging composite
  action (defence in depth).
- `cloudflare_project_name` is regex-validated *twice* (once at
  preflight, once again inside the compose-command action right before
  the wrangler argv is built) so a runtime change cannot smuggle shell
  metacharacters through.
- `cloudflare_account_id` (or its `CLOUDFLARE_ACCOUNT_ID` fallback) is
  required to be a 32-char hex string. The resolver registers it with
  `::add-mask::` so it never appears verbatim in subsequent logs.
- `CLOUDFLARE_API_TOKEN` is rejected if it contains whitespace
  (newlines in a secret silently truncate `GITHUB_OUTPUT`).
- `deployment_environment` is regex-validated against GitHub's
  environment-name shape before being bound to the job.
- The wrangler command is assembled as a properly shell-quoted argv
  string from validated inputs — no caller value reaches `bash -c`
  unquoted. The argv round-trip through `eval set --` is covered by
  smoke tests in CI.
- Every `${{ … }}` template expansion is funnelled through an `env:`
  block (job-level for hot inputs like `PROJECT_NAME` and `DEPLOY_DIR`,
  step-level for the rest) — caller values never appear inline in any
  `run:` body, eliminating the script-injection class of bug.

See [.github/workflows/deploy-cloudflare-pages.yml](.github/workflows/deploy-cloudflare-pages.yml)
for the full input/output list.

---

## Security notes

This repository is public and these workflows run in downstream repos
with access to their secrets, so they follow GitHub's
[security hardening guidance](https://docs.github.com/en/actions/security-guides/security-hardening-for-github-actions):

- **Pinned actions.** Every `uses:` reference is pinned to a 40-char
  commit SHA, with the human-readable version in a trailing comment.
  [Dependabot](.github/dependabot.yml) opens grouped PRs weekly to bump
  the SHAs as new releases ship.
- **Injection-safe scripts.** All user-controlled inputs reach shell via
  `env:` — no `${{ ... }}` expansion happens inside `run:` bodies,
  eliminating the
  [script-injection class of vulnerability](https://securitylab.github.com/research/github-actions-untrusted-input/).
- **Least-privilege tokens.** `permissions: contents: read` at the
  workflow level; jobs that need `contents: write` (only the
  `sync-balena-yml` job) opt in explicitly.
- **No credential persistence.** Every checkout uses
  `persist-credentials: false`, except the one job that has to push a
  commit back to the default branch.
- **Strict input validation.** Tags, identifiers, image names, slugs,
  Balena block names, and `actions_repo` overrides are all regex-checked
  before use; secrets are rejected if they contain stray whitespace.
- **Concurrency safety.** Publish jobs use `cancel-in-progress: false`
  to avoid leaving partial releases behind.
- **No `pull_request_target`.** Untrusted PR code is never checked out
  with elevated privileges.

See [SECURITY.md](SECURITY.md) for the vulnerability-disclosure process.

---

## Contributing

1. Fork and create a feature branch.
2. Edit workflows/actions under `.github/`.
3. Open a PR — [lint.yml](.github/workflows/lint.yml) runs `actionlint`
   and `shellcheck` automatically.
4. Test end-to-end by calling the reusable workflow from a downstream
   repo with `@<your-branch>`.

When adding a new third-party action, pin it to a commit SHA and append
the version in a trailing comment, e.g.:

```yaml
- uses: owner/action@1234567890abcdef1234567890abcdef12345678 # v1.2.3
```

### Local linting

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/rhysd/actionlint/main/scripts/download-actionlint.bash)
./actionlint -color
```