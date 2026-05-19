# Branch protection — recommended settings

The audit P1-3 supply-chain bundle ships CI workflows (CodeQL, Trivy, SBOM +
cosign keyless) and SHA-pins every third-party action. To close the
**SLSA Source L3** gap, the remote branch-protection rule on `main` also
needs two flips. These cannot live in repo — they're GitHub settings, so
the owner has to apply them manually with the `gh` CLI.

## Current state (as of 2026-05-19)

```text
enforce_admins:               false   ← admins (including the owner) can bypass required checks
required_approving_review_count: 0    ← CODEOWNERS-only enforcement
required_status_checks:       13      ← already comprehensive
linear_history:               false
signature_required:           false
```

## Recommended state

| Setting | Current | Recommended | Why |
| --- | --- | --- | --- |
| `enforce_admins` | false | **true** | Required by SLSA Source L3. Without it, an attacker who compromises an admin account (or coerces one via social engineering) can push straight to `main`. |
| `required_approving_review_count` | 0 | **1** | CODEOWNERS-only fails open when the touched paths don't map to any owner. Requiring one approving review forces every PR through review. |
| `linear_history` | false | true (optional) | Already effectively enforced by squash-only merge config, but flipping makes it a hard guarantee. |
| `required_signatures` | false | false (intentional) | Not a hard requirement — squash merges break verification anyway because GitHub re-signs the squash commit. |

## How to apply

```bash
# 1. Flip enforce_admins to true.
gh api -X PUT /repos/Manzela/AutonomousAgent/branches/main/protection/enforce_admins

# 2. Require at least 1 approving review on every PR.
#    (Run inside the existing protection rule — pass the full payload.)
gh api -X PATCH /repos/Manzela/AutonomousAgent/branches/main/protection \
  -f required_pull_request_reviews.required_approving_review_count=1 \
  -f required_pull_request_reviews.dismiss_stale_reviews=true

# 3. (Optional) Enforce linear history.
gh api -X PUT /repos/Manzela/AutonomousAgent/branches/main/protection \
  --input - <<'JSON'
{
  "required_linear_history": true
}
JSON
```

## Verification

```bash
gh api /repos/Manzela/AutonomousAgent/branches/main/protection \
  | jq '{enforce_admins: .enforce_admins.enabled,
         required_approvals: .required_pull_request_reviews.required_approving_review_count,
         linear_history: .required_linear_history.enabled}'
```

Expected output after the flip:

```json
{
  "enforce_admins": true,
  "required_approvals": 1,
  "linear_history": true
}
```

## Rollback

If a hot-fix needs to land while CI is broken (e.g. a dependency-mirror
outage), temporarily disable enforce_admins, push the fix, and re-enable:

```bash
gh api -X DELETE /repos/Manzela/AutonomousAgent/branches/main/protection/enforce_admins
# ... push hot-fix ...
gh api -X POST /repos/Manzela/AutonomousAgent/branches/main/protection/enforce_admins
```

Never leave `enforce_admins: false` in steady state.
