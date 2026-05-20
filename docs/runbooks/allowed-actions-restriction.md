# Allowed-actions restriction — repository-level allowlist

The audit P2-8 finding closes the OWASP **CICD-SEC-05** ("Insufficient
[restriction of] Pipeline-Based Access Controls" / "Restrict action usage")
gap by switching the repository from `allowed_actions=all` (any action on the
public marketplace can run) to `allowed_actions=selected` (GitHub-owned +
verified-publisher + an explicit pattern allowlist). This runbook is the
companion to that flip — it owns the inventory, the API procedure, the
rollback, and the smoke-test.

The flip itself is **API-only** (no repo files change); the orchestrator
applies it after this PR merges. Keep this runbook in sync any time a
workflow adds a new `uses:` line.

## Why

OWASP Top-10 CI/CD Security Risks lists CICD-SEC-05 ("Insufficient PBAC"):
in the Actions context, the canonical mitigation is to disable the
default `allowed_actions=all` and only permit GitHub-owned actions, verified
publishers, and a hand-curated allowlist of third-party actions. With
`all`, a freshly compromised marketplace action (or a typo-squat) can run on
the next push to `main` with `GITHUB_TOKEN` and the full secrets store. With
`selected`, an attacker must additionally compromise an admin to extend the
allowlist before they can ship.

Related controls already in place in this repo:

* every third-party action is **SHA-pinned** (`@<40-char-sha>  # vX.Y.Z`)
  — see `.github/workflows/*.yml`
* SHA-pinning is **enforced in CI** via `scripts/check-sha-pinning.sh`,
  wired into `ci.yml → enforce-sha-pinning`. A PR that introduces a
  tag-pinned `uses:` fails the required check (audit Task 24).
* `step-security/harden-runner` is **not** yet wired in (tracked separately)
* SLSA Source L3 branch-protection is being closed in parallel (P1-3 runbook)

### Server-side `sha_pinning_required` (additional layer, optional)

GitHub's REST API now exposes `sha_pinning_required` on `PUT
/repos/{owner}/{repo}/actions/permissions` (verified 2026-05-20 against
`docs.github.com/en/rest/actions/permissions`; the field is also
present on the org-level endpoint). When enabled, GitHub rejects
workflow runs whose `uses:` refs are not 40-char SHAs **before** the
runner picks them up. This is strictly additive to the in-repo
`enforce-sha-pinning` CI job:

* CI job catches the regression at PR time with a precise file:line
  pointer and a `gh api` fix-hint — fast feedback loop.
* `sha_pinning_required` is a server-side belt that blocks the run
  even on a force-push to `main` or a workflow that somehow slipped
  past the CI gate.

Apply (operator, gh authenticated as repo admin):

```bash
# Requires allowed_actions=selected to already be in place (see above).
gh api -X PUT /repos/Manzela/AutonomousAgent/actions/permissions \
  --field enabled=true \
  --field allowed_actions=selected \
  --field sha_pinning_required=true

# Verify
gh api /repos/Manzela/AutonomousAgent/actions/permissions \
  --jq '{enabled, allowed_actions, sha_pinning_required}'
# Expect: {"enabled": true, "allowed_actions": "selected", "sha_pinning_required": true}
```

Rollback (only if a workflow that must use a tag-ref is blocked and
the SHA-pin fix cannot ship within SLA):

```bash
gh api -X PUT /repos/Manzela/AutonomousAgent/actions/permissions \
  --field enabled=true \
  --field allowed_actions=selected \
  --field sha_pinning_required=false
```

If the server-side flag is flipped on, the CI job below stays — it
provides the better developer-feedback loop. Disabling the CI check
is **not** an acceptable substitute for the server flag because the
CI job runs against the PR's workflow definitions, but a manually
re-run job on `main` would bypass it; the server flag covers both.

## What changes

| Field | Before | After |
| --- | --- | --- |
| `allowed_actions` | `all` | `selected` |
| `github_owned_allowed` | n/a | `true` |
| `verified_allowed` | n/a | `true` |
| `patterns_allowed` | n/a | hand-curated allowlist (below) |

Nothing in the repo changes. No workflow rewrites. The change is applied
once via the Actions Permissions REST API and persists on the repository
settings object until explicitly reverted.

## Categorized inventory (as of 2026-05-20)

Scanned all `.github/workflows/*.yml`. Every unique `uses:` reference is
listed below, categorized by how it's allowed:

| Action | Pin | Category | Used by |
| --- | --- | --- | --- |
| `actions/checkout` | `de0fac2e4500dabe0009e67214ff5f5447ce83dd  # v6` | GitHub-owned | ci, codeql, release, sbom-cosign, secret-scan, trivy |
| `actions/setup-python` | `a309ff8b426b58ec0e2a45f0f869d46889d02405  # v6` | GitHub-owned | secret-scan |
| `actions/upload-artifact` | `043fb46d1a93c77aae656e7c1c64a875d1fc6a0a  # v7` | GitHub-owned | ci |
| `github/codeql-action/init` | `458d36d7d4f47d0dd16ca424c1d3cda0060f1360  # v3` | GitHub-owned | codeql |
| `github/codeql-action/analyze` | `458d36d7d4f47d0dd16ca424c1d3cda0060f1360  # v3` | GitHub-owned | codeql |
| `github/codeql-action/upload-sarif` | `458d36d7d4f47d0dd16ca424c1d3cda0060f1360  # v3` | GitHub-owned | secret-scan, trivy |
| `astral-sh/setup-uv` | `37802adc94f370d6bfd71619e3f0bf239e1f3b78  # v7` | Verified (astral-sh) | ci |
| `anchore/sbom-action` | `e22c389904149dbc22b58101806040fa8d37a610  # v0` | Verified (anchore) | sbom-cosign |
| `sigstore/cosign-installer` | `f713795cb21599bc4e5c4b58cbad1da852d7eeb9  # v3` | Verified (sigstore) | sbom-cosign |
| `aquasecurity/trivy-action` | `a9c7b0f06e461e9d4b4d1711f154ee024b8d7ab8  # v0.36.0` | Verified (aquasecurity) | trivy |
| `softprops/action-gh-release` | `b4309332981a82ec1c5618f44dd2e27cc8bfbfda  # v3` | **Unverified — pattern** | release, sbom-cosign |
| `amannn/action-semantic-pull-request` | `48f256284bd46cdaab1048c3721360e808335d50  # v6` | **Unverified — pattern** | pr-validation |
| `hadolint/hadolint-action` | `2332a7b74a6de0dda2e2221d575162eba76ba5e5  # v3.3.0` | **Unverified — pattern** | ci |
| `google-github-actions/auth` | `71f986410dfbc7added4569d411d040a91dc6935  # v2.1.8` | **Unverified — pattern** | phase-0a-deploy |
| `docker/setup-buildx-action` | `b5ca514318bd6ebac0fb2aedd5d36ec1b5c232a2  # v3.10.0` | **Unverified — pattern** | phase-0a-deploy |
| `docker/login-action` | `74a5d142397b4f367a81961eba4e8cd7edddf772  # v3.4.0` | **Unverified — pattern** | phase-0a-deploy |
| `docker/build-push-action` | `471d1dc4e07e5cdedd4c2171150001c434f0b7a4  # v6.15.0` | **Unverified — pattern** | phase-0a-deploy |

Counts: **6 GitHub-owned**, **4 verified publisher**, **7 unverified
(SHA-pinned)**. All 17 distinct `uses:` references covered. All
third-party actions (verified or not) are SHA-pinned at the call site —
the patterns_allowed entries below are deliberately written as `@*`
because GitHub Actions Permissions evaluates the pattern against the
ref, and a strict pattern match would refuse updates without an
operator rerun of this runbook. The SHA pin in the workflow file is
what enforces immutability of the version actually executed.

### patterns_allowed

```json
{
  "patterns_allowed": [
    "softprops/action-gh-release@*",
    "amannn/action-semantic-pull-request@*",
    "hadolint/hadolint-action@*",
    "anchore/sbom-action@*",
    "aquasecurity/trivy-action@*",
    "astral-sh/setup-uv@*",
    "sigstore/cosign-installer@*",
    "google-github-actions/auth@*",
    "docker/setup-buildx-action@*",
    "docker/login-action@*",
    "docker/build-push-action@*"
  ]
}
```

If a new third-party action lands in `.github/workflows/`, update this
runbook (table + JSON block) **and** re-run the API call below in the
same PR, or the next CI run on that workflow will hard-fail with
`Resource not accessible by integration` / `This action is not allowed
to run on this repository`.

## Operator procedure (apply)

Run from a shell with `gh` authenticated as a repo admin.

```bash
# 1. Flip the top-level allowed_actions to "selected".
gh api -X PUT /repos/Manzela/AutonomousAgent/actions/permissions \
  --field enabled=true \
  --field allowed_actions=selected

# 2. PUT the selected-actions payload (GitHub-owned + verified + patterns).
gh api -X PUT /repos/Manzela/AutonomousAgent/actions/permissions/selected-actions \
  --field github_owned_allowed=true \
  --field verified_allowed=true \
  --raw-field 'patterns_allowed=["softprops/action-gh-release@*","amannn/action-semantic-pull-request@*","hadolint/hadolint-action@*"]'
```

(Equivalent single-shot using a JSON payload, if the shell-flag form
trips on quoting:)

```bash
gh api -X PUT /repos/Manzela/AutonomousAgent/actions/permissions/selected-actions \
  --input - <<'JSON'
{
  "github_owned_allowed": true,
  "verified_allowed": true,
  "patterns_allowed": [
    "softprops/action-gh-release@*",
    "amannn/action-semantic-pull-request@*",
    "hadolint/hadolint-action@*"
  ]
}
JSON
```

## Verification (post-apply)

```bash
gh api /repos/Manzela/AutonomousAgent/actions/permissions
# Expect: {"enabled": true, "allowed_actions": "selected", "selected_actions_url": "..."}

gh api /repos/Manzela/AutonomousAgent/actions/permissions/selected-actions
# Expect: {
#   "github_owned_allowed": true,
#   "verified_allowed": true,
#   "patterns_allowed": ["softprops/action-gh-release@*", "amannn/action-semantic-pull-request@*", "hadolint/hadolint-action@*"]
# }
```

## Smoke-test

Trigger one workflow that exercises each category and confirm all stay
green. The fastest coverage path:

1. Open a no-op PR against `main` (e.g. whitespace tweak in a markdown
   file). This fires:
   * `ci.yml` → covers `actions/checkout`, `actions/setup-python`,
     `actions/upload-artifact`, `astral-sh/setup-uv` (verified),
     `hadolint/hadolint-action` (unverified pattern).
   * `pr-validation.yml` → covers
     `amannn/action-semantic-pull-request` (unverified pattern).
   * `codeql.yml` → covers `github/codeql-action/*` (GitHub-owned).
   * `secret-scan.yml` → covers `actions/setup-python` and
     `github/codeql-action/upload-sarif` (GitHub-owned).
   * `trivy.yml` → covers `aquasecurity/trivy-action` (verified) and
     `github/codeql-action/upload-sarif` (GitHub-owned).
2. Watch `gh pr checks <num> --watch` until all required checks resolve.
3. Cut a throwaway tag (e.g. `v0.0.0-smoke`) and delete it within five
   minutes — this exercises `release.yml` (`softprops/action-gh-release`)
   and `sbom-cosign.yml` (`anchore/sbom-action`, `sigstore/cosign-installer`,
   `softprops/action-gh-release`). Skip this step in the smoke-test if a
   real release is queued within 24h; the next real release covers the
   gap.

Any **policy denial** surfaces in the workflow run as:

```
This workflow contains an action that is not allowed to be used in this repository.
```

If you see that string, either (a) the action needs to be added to
`patterns_allowed` here and the operator procedure re-run, or (b) the
publisher's verified-creator status changed upstream and a new entry
in `patterns_allowed` is the workaround.

## Rollback

Reverts the repository to "any marketplace action allowed" — same posture as
before this PR. Use only if a policy denial blocks a critical workflow and
the right patch (adding the missing action above) cannot ship within the
SLA.

```bash
gh api -X PUT /repos/Manzela/AutonomousAgent/actions/permissions \
  --field enabled=true \
  --field allowed_actions=all
```

After rollback, open a follow-up issue to triage the missing action and
re-apply the restriction. Do not leave `allowed_actions=all` in place — it
reopens CICD-SEC-05.

## Related

* OWASP CICD-SEC-05: <https://owasp.org/www-project-top-10-ci-cd-security-risks/CICD-SEC-05>
* GitHub Docs — "Managing GitHub Actions settings for a repository":
  <https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/enabling-features-for-your-repository/managing-github-actions-settings-for-a-repository>
* GitHub REST API — Actions Permissions:
  <https://docs.github.com/en/rest/actions/permissions>
* Companion runbook: `docs/runbooks/branch-protection.md` (SLSA Source L3
  gap-closure, paired effort).
