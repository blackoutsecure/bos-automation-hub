# Purpose-scoped GitHub App setup

This loopback-only helper provisions organization-level GitHub App credentials
used by the Automation Hub. It combines GitHub's App manifest flow
with the authenticated GitHub CLI so the private key does not need to be copied
through repository settings by hand.

## Run it

From the repository root on Windows:

```powershell
.\scripts\start-gatekeeper-app-setup.ps1
```

Select the least-privilege profile for the capability being migrated:

```powershell
.\scripts\start-gatekeeper-app-setup.ps1 -Profile repository-admin
.\scripts\start-gatekeeper-app-setup.ps1 -Profile workflow-sync
.\scripts\start-gatekeeper-app-setup.ps1 -Profile release
.\scripts\start-gatekeeper-app-setup.ps1 -Profile security-audit
.\scripts\start-gatekeeper-app-setup.ps1 -Profile dispatch
.\scripts\start-gatekeeper-app-setup.ps1 -Profile upstream-read
```

The default `gatekeeper` profile maintains the existing read-only dispatcher
authorization App. Do not add repository write permissions to that App.

To offer a button that re-runs the failed authorization job after setup is
verified:

```powershell
.\scripts\start-gatekeeper-app-setup.ps1 `
  -Repository blackoutsecure/bos-code-scanning-kit `
  -RunId 33469205109
```

The launcher checks that `gh` and Python 3 are available, confirms `gh` is
authenticated, starts the service on `127.0.0.1:8765`, and opens the browser.
Use `-Port` to choose another loopback port or `-NoBrowser` to print the URL
without opening it.

The active GitHub CLI account must be an organization owner and its token must
be allowed to manage organization Actions variables and secrets. For `gh auth
login`, the commonly required OAuth scopes are `admin:org`, `repo`, and
`workflow`.

## What the helper automates

1. Detects an existing configured App by the selected profile's App ID variable,
   then checks its installation, permissions, and repository scope.
2. Recommends repairing the existing App instead of recreating it. The page
   links directly to App permission and installation access settings.
3. Supports private-key rotation by accepting a newly generated PEM and piping
   it directly to the organization secret without writing it to disk.
4. When replacement is intentional, builds a collision-resistant private
   GitHub App manifest requesting only the selected profile's permissions.
5. Sends that manifest directly to GitHub for owner review and confirmation.
6. Exchanges GitHub's one-time callback code directly from the browser.
7. Sends the returned App ID and private key only to the loopback service.
8. Sets the profile's organization App ID variable with all-repository visibility.
9. Pipes the key through standard input to `gh secret set` as the profile's
   organization private-key secret, also with all-repository visibility.
10. Opens the GitHub App installation page and verifies the installation,
   permissions, and repository coverage. Repository-capable profiles require
   all-repository coverage when replacing organization-wide PATs.
11. Optionally queues the failed jobs from a supplied workflow run.

GitHub App installation tokens are minted afresh by each workflow job and
expire automatically. There is no separate reauthentication operation. Repair
means restoring the installation scope, required permission, App ID variable,
or private-key secret; replacement is the last resort.

GitHub intentionally requires an organization owner to review App creation and
installation. Those two confirmations cannot and should not be bypassed.

## Security properties

- The HTTP service binds only to `127.0.0.1`, not the LAN.
- Mutating requests require a random per-process session token and matching
  browser origin.
- Responses are sent with `Cache-Control: no-store`, a restrictive Content
  Security Policy, frame denial, and MIME-sniffing protection.
- The private key is never printed, logged, stored in browser storage, or
  written to disk. It exists briefly in browser memory and is piped to `gh`
  over standard input.
- Organization-wide Actions variable and secret visibility is intentional
   because the managed workflows are shared across repositories. Install each App
   only as broadly as its profile requires.
- Stop the process with Ctrl+C after verification.
