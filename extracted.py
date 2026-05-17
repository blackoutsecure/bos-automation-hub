          import json, os, re, sys, urllib.error, urllib.request

          token = (os.environ.get("API_TOKEN") or "").strip()
          if not token:
              sys.exit("ERROR: CLOUDFLARE_API_TOKEN is required for account auto-resolve")
          if any(c.isspace() for c in token):
              sys.exit("ERROR: CLOUDFLARE_API_TOKEN contains whitespace/newlines")

          req = urllib.request.Request(
              "https://api.cloudflare.com/client/v4/accounts",
              headers={
                  "Authorization": f"Bearer {token}",
                  "Accept": "application/json",
                  "User-Agent": "bos-automation-hub/deploy-cloudflare-pages",
              },
          )
          try:
              with urllib.request.urlopen(req, timeout=30) as resp:
                  status, payload = resp.status, json.loads(resp.read() or b"{}")
          except urllib.error.HTTPError as exc:
              status = exc.code
              payload = json.loads(exc.read() or b"{}") if exc.headers.get("Content-Type", "").startswith("application/json") else {"_raw": (exc.read() or b"").decode("utf-8", "replace")}

          if status != 200 or not payload.get("success"):
              print(f"ERROR: Cloudflare /accounts returned HTTP {status}", file=sys.stderr)
              print("Hint: token must be valid and have at least one account-scoped permission.", file=sys.stderr)
              print(json.dumps(payload)[:600], file=sys.stderr)
              sys.exit(1)

          result = payload.get("result") or []
          if not result:
              sys.exit("ERROR: token returned zero accounts (no account-scoped permissions).")
          if len(result) > 1:
              names = ", ".join(a.get("name", "?") for a in result)
              sys.exit(
                  f"ERROR: token can access {len(result)} accounts; set vars.CLOUDFLARE_ACCOUNT_ID "
                  f"(or the back-compat secret) explicitly to pick one. Accounts: {names}"
              )

          acct_id = (result[0].get("id") or "").strip()
          if not re.match(r"^[0-9a-f]{32}$", acct_id):
              sys.exit(f"ERROR: parsed account ID is not 32-char hex: {acct_id!r}")

          print(f"Auto-resolved Cloudflare account ID: {acct_id}", file=sys.stderr)
          with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as f:
              f.write(f"value={acct_id}\n")
          PY

      - name: Resolve & validate Cloudflare account ID
        id: account
        uses: blackoutsecure/bos-automation-hub/.github/actions/shared/cloudflare-resolve-id@main
        with:
          value: ${{ env.ACCOUNT_ID || steps.account_autoresolve.outputs.value }}
          kind: account

      - name: Preflight validation
        env:
          # PROJECT_NAME, SITE_URL, PUBLIC_DIR, DEPLOY_DIR,
          # WORKING_DIRECTORY, BRANCH, DEPLOY_OVERRIDE, WRANGLER_VERSION,
          # DEPLOYMENT_ENVIRONMENT inherited from job-level env.
          GENERATE_SITEMAP: ${{ inputs.generate_sitemap }}
          GENERATE_ROBOTS: ${{ inputs.generate_robots }}
          GENERATE_SECURITY_TXT: ${{ inputs.generate_security_txt }}
          GENERATE_MANIFEST: ${{ inputs.generate_manifest }}
          SECURITY_CONTACT: ${{ inputs.security_contact }}
          MANIFEST_NAME: ${{ inputs.manifest_name }}
          MANIFEST_THEME_COLOR: ${{ inputs.manifest_theme_color }}
          MANIFEST_BACKGROUND_COLOR: ${{ inputs.manifest_background_color }}
          MANIFEST_DIR: ${{ inputs.manifest_dir }}
          MANIFEST_LANG: ${{ inputs.manifest_lang }}
          MANIFEST_ORIENTATION: ${{ inputs.manifest_orientation }}
          CLOUDFLARE_API_TOKEN: ${{ secrets.CLOUDFLARE_API_TOKEN }}
        run: |
          set -euo pipefail
          ERR=0
          fail() { echo "ERROR: $*" >&2; ERR=1; }

          # A stray newline in a secret silently truncates GITHUB_OUTPUT
          # for any downstream step, so reject it explicitly.
          check_singleline() {
            local name="$1" val="$2"
            [ -n "${val}" ] || { fail "${name} is missing"; return; }
            case "${val}" in
              *[$'\n\r\t ']*) fail "${name} contains whitespace/newlines" ;;
            esac
          }

          # Repo-relative path: no leading slash, no '..' segment, no NUL.
          check_relpath() {
            local name="$1" val="$2"
            [ -n "${val}" ] || { fail "${name} is empty"; return; }
            case "${val}" in
              /*)        fail "${name} must be a repo-relative path (got '${val}')" ;;
              *..*)      fail "${name} must not contain '..' (got '${val}')" ;;
              *[$'\n\r']*) fail "${name} must not contain newlines" ;;
            esac
          }

          # Cloudflare Pages project names: lowercase a-z, 0-9 and '-',
          # 1-58 chars, can't start or end with '-'.
          if ! printf '%s' "${PROJECT_NAME}" \
            | grep -Eq '^[a-z0-9]([a-z0-9-]{0,56}[a-z0-9])?$'; then
            fail "input 'cloudflare_project_name' is not a valid Pages project name: '${PROJECT_NAME}'"
          fi

          check_singleline "secret 'CLOUDFLARE_API_TOKEN'"  "${CLOUDFLARE_API_TOKEN}"

          # GitHub Environment names: 1–255 chars, no control chars or '/'.
          if [ -n "${DEPLOYMENT_ENVIRONMENT}" ]; then
            check_singleline "input 'deployment_environment'" "${DEPLOYMENT_ENVIRONMENT}"
            if ! printf '%s' "${DEPLOYMENT_ENVIRONMENT}" \
              | grep -Eq '^[A-Za-z0-9][A-Za-z0-9._-]{0,254}$'; then
              fail "input 'deployment_environment' is not a valid GitHub Environment name: '${DEPLOYMENT_ENVIRONMENT}'"
            fi
          fi

          check_relpath "input 'public_dir'" "${PUBLIC_DIR}"
          check_relpath "input 'deploy_dir'" "${DEPLOY_DIR}"
          if [ -n "${WORKING_DIRECTORY}" ]; then
            check_relpath "input 'working_directory'" "${WORKING_DIRECTORY}"
          fi

          if [ -n "${BRANCH}" ]; then
            check_singleline "input 'branch'" "${BRANCH}"
            # Allow git-ref shape: letters, digits, '/', '-', '_', '.'.
            if ! printf '%s' "${BRANCH}" | grep -Eq '^[A-Za-z0-9][A-Za-z0-9._/-]{0,254}$'; then
              fail "input 'branch' is not a valid branch name: '${BRANCH}'"
            fi
          fi

          if [ -n "${DEPLOY_OVERRIDE}" ]; then
            case "${DEPLOY_OVERRIDE}" in
              true|false) ;;
              *) fail "input 'deploy' must be empty, 'true', or 'false' (got '${DEPLOY_OVERRIDE}')" ;;
            esac
          fi

          if [ -n "${WRANGLER_VERSION}" ]; then
            check_singleline "input 'wrangler_version'" "${WRANGLER_VERSION}"
          fi

          # Anything that gets baked into static asset URLs must be a
          # well-formed absolute http(s) URL.
          needs_url=false
          [ "${GENERATE_SITEMAP}"       = "true" ] && needs_url=true
          [ "${GENERATE_ROBOTS}"        = "true" ] && needs_url=true
          [ "${GENERATE_SECURITY_TXT}"  = "true" ] && needs_url=true
          if [ "${needs_url}" = "true" ]; then
            [ -n "${SITE_URL}" ] || fail "input 'site_url' is required when any generator is enabled"
            if [ -n "${SITE_URL}" ] && ! printf '%s' "${SITE_URL}" \
              | grep -Eq '^https?://[A-Za-z0-9.-]+(:[0-9]+)?(/[^[:space:]]*)?$'; then
              fail "input 'site_url' must be an http(s) URL (got '${SITE_URL}')"
            fi
          elif [ -n "${SITE_URL}" ]; then
            # Still validate when supplied.
            if ! printf '%s' "${SITE_URL}" \
              | grep -Eq '^https?://[A-Za-z0-9.-]+(:[0-9]+)?(/[^[:space:]]*)?$'; then
              fail "input 'site_url' must be an http(s) URL (got '${SITE_URL}')"
            fi
          fi

          if [ "${GENERATE_SECURITY_TXT}" = "true" ]; then
            [ -n "${SECURITY_CONTACT}" ] \
              || fail "input 'security_contact' is required when 'generate_security_txt' is true"
            if [ -n "${SECURITY_CONTACT}" ] && ! printf '%s' "${SECURITY_CONTACT}" \
              | grep -Eq '^(mailto:[^[:space:]@]+@[^[:space:]@]+|https?://[^[:space:]]+|[^[:space:]@]+@[^[:space:]@]+)$'; then
              fail "input 'security_contact' must be an email or http(s) URL (got '${SECURITY_CONTACT}')"
            fi
          fi

          if [ "${GENERATE_MANIFEST}" = "true" ]; then
            [ -n "${MANIFEST_NAME}" ] \
              || fail "input 'manifest_name' is required when 'generate_manifest' is true"

            for color_name in MANIFEST_THEME_COLOR MANIFEST_BACKGROUND_COLOR; do
              eval "color_val=\${$color_name}"
              if [ -n "${color_val}" ] \
                && ! printf '%s' "${color_val}" | grep -Eq '^#([0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$'; then
                fail "input '$(echo "${color_name}" | tr '[:upper:]' '[:lower:]')' must be a CSS hex colour (got '${color_val}')"
              fi
            done

            if [ -n "${MANIFEST_DIR}" ]; then
              case "${MANIFEST_DIR}" in
                ltr|rtl|auto) ;;
                *) fail "input 'manifest_dir' must be 'ltr', 'rtl', or 'auto' (got '${MANIFEST_DIR}')" ;;
              esac
            fi

            if [ -n "${MANIFEST_LANG}" ] && ! printf '%s' "${MANIFEST_LANG}" \
              | grep -Eq '^[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})*$'; then
              fail "input 'manifest_lang' is not a BCP 47 tag (got '${MANIFEST_LANG}')"
            fi

            if [ -n "${MANIFEST_ORIENTATION}" ]; then
              case "${MANIFEST_ORIENTATION}" in
                any|natural|landscape|landscape-primary|landscape-secondary|portrait|portrait-primary|portrait-secondary) ;;
                *) fail "input 'manifest_orientation' is not a valid PWA orientation (got '${MANIFEST_ORIENTATION}')" ;;
              esac
            fi
          fi

          [ "${ERR}" -eq 0 ] || { echo "Preflight validation failed." >&2; exit 1; }
          echo "Preflight OK."

      - name: Resolve deploy gate
        id: gate
        env:
          # DEPLOY_OVERRIDE inherited from job-level env.
          EVENT_NAME: ${{ github.event_name }}
          REF: ${{ github.ref }}
          DEFAULT_BRANCH: ${{ github.event.repository.default_branch }}
        run: |
          set -euo pipefail
          if [ -n "${DEPLOY_OVERRIDE}" ]; then
            DECISION="${DEPLOY_OVERRIDE}"
          elif [ "${EVENT_NAME}" = "push" ] && [ "${REF}" = "refs/heads/${DEFAULT_BRANCH}" ]; then
            DECISION="true"
          else
            DECISION="false"
          fi
          echo "deploy=${DECISION}" >> "${GITHUB_OUTPUT}"
          echo "deploy=${DECISION}"

      - name: Run prebuild command
        if: inputs.prebuild_command != ''
        env:
          PREBUILD_COMMAND: ${{ inputs.prebuild_command }}
          # WORKING_DIRECTORY inherited from job-level env.
        run: |
          set -euo pipefail
          if [ -n "${WORKING_DIRECTORY}" ]; then
            cd "${WORKING_DIRECTORY}"
          fi
          # ⚠️ SECURITY: the caller-provided command is executed verbatim under
          # bash. The caller workflow is responsible for ensuring nothing
          # untrusted (PR titles, issue bodies, github.event.* strings,
          # repository_dispatch payloads, etc.) is interpolated into
          # `inputs.prebuild_command` — doing so would be a shell-injection
          # sink. See the input description for the full warning.
          bash -eo pipefail -c "${PREBUILD_COMMAND}"

      - name: Stage deploy directory
        uses: blackoutsecure/bos-automation-hub/.github/actions/shared/stage-deploy-dir@main
        with:
          public_dir: ${{ inputs.public_dir }}
          deploy_dir: ${{ inputs.deploy_dir }}
          clean: ${{ inputs.clean_deploy_dir }}
          copy_files: ${{ inputs.copy_files }}
          copy_dirs: ${{ inputs.copy_dirs }}

      # ──────────────────────────────────────────────────────────────
      # Generators (each opt-in). Pinned by SHA — Dependabot bumps
      # them together with the rest of the actions in this repo.
      # ──────────────────────────────────────────────────────────────

      - name: Generate sitemap
        if: inputs.generate_sitemap
        uses: blackoutsecure/bos-sitemap-generator@5ec7fbce9a419a47c2e7d58bcb0ae28c02c84cda # v1.0.1
        with:
          site_url: ${{ inputs.site_url }}
          public_dir: ${{ inputs.deploy_dir }}

      - name: Generate Web App Manifest
        if: inputs.generate_manifest
        uses: blackoutsecure/bos-web-application-manifest-generator@80a4378040f636d8ce2941026f47aad22bb75256 # v1.0.8
        with:
          name: ${{ inputs.manifest_name }}
          short_name: ${{ inputs.manifest_short_name }}
          description: ${{ inputs.manifest_description }}
          orientation: ${{ inputs.manifest_orientation }}
          theme_color: ${{ inputs.manifest_theme_color }}
          background_color: ${{ inputs.manifest_background_color }}
          lang: ${{ inputs.manifest_lang }}
          dir: ${{ inputs.manifest_dir }}
          categories: ${{ inputs.manifest_categories }}
          public_dir: ${{ inputs.deploy_dir }}
          icons_dir: ${{ inputs.manifest_icons_dir }}

      - name: Generate robots.txt
        if: inputs.generate_robots
        uses: blackoutsecure/bos-robotstxt-generator@0a4523ab7fd22579799d5406247f7724b6728293 # v1.2.0
        with:
          site_url: ${{ inputs.site_url }}
          public_dir: ${{ inputs.deploy_dir }}

      - name: Generate security.txt
        if: inputs.generate_security_txt
        uses: blackoutsecure/bos-securitytxt-generator@7ca9d802b9ff2fff07e4e514c72cc8f241b2494f # v1.2.0
        with:
          security_contact: ${{ inputs.security_contact }}
          site_url: ${{ inputs.site_url }}
          public_dir: ${{ inputs.deploy_dir }}

      - name: Compose wrangler command
        id: cmd
        if: steps.gate.outputs.deploy == 'true'
        uses: blackoutsecure/bos-automation-hub/.github/actions/cloudflare-pages-compose-command@main
        with:
          deploy_dir: ${{ inputs.deploy_dir }}
          project_name: ${{ inputs.cloudflare_project_name }}
          branch: ${{ inputs.branch }}
          commit_message: ${{ inputs.commit_message }}
          extra_args: ${{ inputs.extra_wrangler_args }}

      - name: Deploy to Cloudflare Pages
        id: deploy
        if: steps.gate.outputs.deploy == 'true'
        uses: cloudflare/wrangler-action@ebbaa1584979971c8614a24965b4405ff95890e0 # v4.0.0
        with:
          apiToken: ${{ secrets.CLOUDFLARE_API_TOKEN }}
          accountId: ${{ steps.account.outputs.value }}
          wranglerVersion: ${{ inputs.wrangler_version }}
          workingDirectory: ${{ inputs.working_directory }}
          gitHubToken: ${{ secrets.GITHUB_TOKEN }}
          command: ${{ steps.cmd.outputs.command }}

      - name: Resolve cache-purge gate
        id: purge_gate
        if: always()
        env:
          DEPLOY_GATE: ${{ steps.gate.outputs.deploy }}
          DEPLOY_OUTCOME: ${{ steps.deploy.outcome }}
          PURGE_REQUESTED: ${{ inputs.purge_cache }}
          # ZONE_ID inherited from job-level env (vars.CLOUDFLARE_ZONE_ID
          # || secrets.CLOUDFLARE_ZONE_ID, prefer vars).
          SITE_URL: ${{ inputs.site_url }}
        run: |
          set -euo pipefail
          decide() {
            echo "purge=$1"  >> "${GITHUB_OUTPUT}"
            echo "reason=$2" >> "${GITHUB_OUTPUT}"
            echo "purge=$1 reason=$2"
          }
          if [ "${DEPLOY_GATE}" != "true" ]; then
            decide false "deploy-skipped"
            exit 0
          fi
          if [ "${DEPLOY_OUTCOME}" != "success" ]; then
            decide false "deploy-${DEPLOY_OUTCOME:-unknown}"
            exit 0
          fi
          if [ "${PURGE_REQUESTED}" != "true" ]; then
            decide false "opt-out"
            exit 0
          fi
          ZONE_ID_CLEAN="$(printf '%s' "${ZONE_ID:-}" | tr -d '[:space:]')"
          if [ -n "${ZONE_ID_CLEAN}" ]; then
            # Shape (`^[0-9a-f]{32}$`) is validated inside the purge
            # action via shared/cloudflare-resolve-id — a malformed
            # value fails loudly there.
            decide true "explicit-zone-id"
            exit 0
          fi
          if [ -n "${SITE_URL}" ]; then
            # The action will auto-resolve via the Cloudflare API. The
            # token needs `Zone:Read` in addition to `Cache Purge` for
            # this path to work.
            decide true "autoresolve-from-site-url"
            exit 0
          fi
          echo "::notice::purge_cache=true but no zone source available (set vars.CLOUDFLARE_ZONE_ID or pass site_url). Skipping cache purge."
          decide false "no-zone-source"

      - name: Purge Cloudflare zone cache
        id: purge
        if: steps.purge_gate.outputs.purge == 'true'
        uses: blackoutsecure/bos-automation-hub/.github/actions/cloudflare-zone-purge@main
        with:
          zone_id: ${{ env.ZONE_ID }}
          site_url: ${{ inputs.site_url }}
          api_token: ${{ secrets.CLOUDFLARE_API_TOKEN }}

      - name: Job summary
        if: always()
        env:
          DEPLOYED: ${{ steps.gate.outputs.deploy }}
          # PROJECT_NAME, DEPLOY_DIR, BRANCH, SITE_URL,
          # DEPLOYMENT_ENVIRONMENT inherited from job-level env.
          DEPLOYMENT_URL: ${{ steps.deploy.outputs.deployment-url }}
          DEPLOYMENT_ID: ${{ steps.deploy.outputs.pages-deployment-id }}
          DEPLOYMENT_ALIAS: ${{ steps.deploy.outputs.pages-deployment-alias-url }}
          PAGES_ENVIRONMENT: ${{ steps.deploy.outputs.pages-environment }}
          PURGE_DECISION: ${{ steps.purge_gate.outputs.purge }}
          PURGE_REASON: ${{ steps.purge_gate.outputs.reason }}
          PURGED: ${{ steps.purge.outputs.purged }}
          PURGE_HTTP: ${{ steps.purge.outputs.http_code }}
        run: |
          {
            echo "## Cloudflare Pages Deploy"
            echo ""
            echo "| Field | Value |"
            echo "|-------|-------|"
            echo "| Project          | \`${PROJECT_NAME}\` |"
            echo "| Deployed         | \`${DEPLOYED}\` |"
            echo "| Deploy dir       | \`${DEPLOY_DIR}\` |"
            echo "| Branch           | \`${BRANCH:-<wrangler default>}\` |"
            echo "| GitHub env       | \`${DEPLOYMENT_ENVIRONMENT:-<none>}\` |"
            echo "| Pages env        | \`${PAGES_ENVIRONMENT:-n/a}\` |"
            echo "| Site URL         | ${SITE_URL:-n/a} |"
            echo "| Deployment URL   | ${DEPLOYMENT_URL:-n/a} |"
            echo "| Alias URL        | ${DEPLOYMENT_ALIAS:-n/a} |"
            echo "| Deployment ID    | \`${DEPLOYMENT_ID:-n/a}\` |"
            echo "| Cache purge      | \`${PURGED:-${PURGE_DECISION:-n/a}}\` (reason: \`${PURGE_REASON:-n/a}\`${PURGE_HTTP:+, HTTP \`${PURGE_HTTP}\`}) |"
