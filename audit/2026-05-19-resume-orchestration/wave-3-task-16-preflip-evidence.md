# Wave 3 / Task 16 — Pre-flip Verified-Creator Evidence

**Date:** 2026-05-20
**Purpose:** Capture the Verified-Creator verification step required by PR #89's review
before the `allowed_actions=selected` API flip (CICD-SEC-05 hardening, P2-8).

## Background

PR #89 ([docs/security/allowed-actions-runbook.md](../../docs/security/allowed-actions-runbook.md))
documents the intended GitHub Actions allow-list. The spec review on #89 flagged a gap:
the runbook does not cite a source for the "Verified-Creator" status of the third-party
actions that are NOT in `patterns_allowed`. If `verified_allowed=true` is set and any of
those publishers turn out to be **un**verified, their workflows will be denied immediately
after the flip.

This file is the pre-flip evidence pass for Task 16.

## Actions in scope

Enumerated from `.github/workflows/` (Wave-3 audit, 2026-05-20):

```
actions/checkout                       # github-owned       (covered by github_owned_allowed=true)
actions/setup-python                   # github-owned       (covered by github_owned_allowed=true)
actions/upload-artifact                # github-owned       (covered by github_owned_allowed=true)
amannn/action-semantic-pull-request    # third-party        (covered by patterns_allowed)
anchore/sbom-action                    # third-party        (requires Verified-Creator)
aquasecurity/trivy-action              # third-party        (requires Verified-Creator)
astral-sh/setup-uv                     # third-party        (requires Verified-Creator)
hadolint/hadolint-action               # third-party        (covered by patterns_allowed)
sigstore/cosign-installer              # third-party        (requires Verified-Creator)
softprops/action-gh-release            # third-party        (covered by patterns_allowed)
```

## Verification results (2026-05-20, Marketplace)

| Publisher       | Marketplace listing                                   | Badge present? | Quoted attestation                                                                          |
|-----------------|--------------------------------------------------------|----------------|---------------------------------------------------------------------------------------------|
| anchore         | <https://github.com/marketplace/actions/anchore-sbom-action>   | **VERIFIED**  | "GitHub has manually verified the creator of the action as an official partner organization." |
| aquasecurity    | <https://github.com/marketplace/actions/aqua-security-trivy>   | **VERIFIED**  | "GitHub has manually verified the creator of the action as an official partner organization." |
| astral-sh       | <https://github.com/marketplace/actions/astral-sh-setup-uv>    | **VERIFIED**  | "GitHub has manually verified the creator of the action as an official partner organization." |
| sigstore        | <https://github.com/marketplace/actions/cosign-installer>      | **VERIFIED**  | "GitHub has manually verified the creator of the action as an official partner organization." |

## Conclusion

All four third-party publishers carry the Verified-Creator badge. Setting
`verified_allowed=true` will cover them without expansion of `patterns_allowed`.
The Task 16 flip is safe to proceed with the existing patterns from #89's runbook:

```
softprops/action-gh-release@*
amannn/action-semantic-pull-request@*
hadolint/hadolint-action@*
```

## API call sequence for the flip

```bash
# Step 1: switch from "all" to "selected"
gh api -X PUT /repos/Manzela/AutonomousAgent/actions/permissions \
  --field enabled=true \
  --field allowed_actions=selected

# Step 2: configure what "selected" means
gh api -X PUT /repos/Manzela/AutonomousAgent/actions/permissions/selected-actions \
  --field github_owned_allowed=true \
  --field verified_allowed=true \
  --field 'patterns_allowed[]=softprops/action-gh-release@*' \
  --field 'patterns_allowed[]=amannn/action-semantic-pull-request@*' \
  --field 'patterns_allowed[]=hadolint/hadolint-action@*'

# Step 3: re-read to confirm
gh api /repos/Manzela/AutonomousAgent/actions/permissions/selected-actions
```

## Post-flip smoke test

Trigger a workflow that uses each verified action and confirm green CI. The simplest
proof is the next push that lands on `main`: CI runs `actions/checkout`,
`actions/setup-python`, `astral-sh/setup-uv`, `aquasecurity/trivy-action`,
`anchore/sbom-action`, `sigstore/cosign-installer`, and the pattern-allowed
`hadolint/hadolint-action` + `softprops/action-gh-release` + `amannn/...`
on every PR pipeline.
