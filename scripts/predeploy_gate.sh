#!/usr/bin/env bash
# SP-24 — Predeploy gate for AutonomousAgent.
#
# Runs 6 explicit checks before any production deploy. Exits 0 only if ALL
# checks pass. Failing checks are reported to stdout.
#
# Each check can be overridden via env vars for hermetic CI fixtures:
#
#   PREDEPLOY_EVAL_RESULT=0|1       — eval-gate (0=pass, 1=fail)
#   PREDEPLOY_C4_RESULT=0|1         — dead-code gate (C4)
#   PREDEPLOY_COSIGN_RESULT=0|1     — cosign verify
#   PREDEPLOY_SUBMODULE_RESULT=0|1  — submodule clean
#   PREDEPLOY_SECRETS_RESULT=0|1    — secrets encrypted
#   PREDEPLOY_TERRAFORM_RESULT=0|1  — terraform plan clean
#
# Override values MUST be exactly "0" (pass) or "1" (fail). An empty or
# absent value is NOT treated as "pass": absent → run the real check;
# empty → treated as fail to prevent silent bypass via unset env vars.
#
# When no override is set the real check is executed (requires GH CLI,
# cosign, terraform, and a configured GCP project).

set -euo pipefail

FAILURES=()

# ---------- helper ----------

# _check <display-name> <OVERRIDE_VAR> [<cmd> <args...>]
# Appends display-name to FAILURES if the check fails.
#
# Override semantics:
#   - var UNSET:   run the real command
#   - var == "0":  treat as pass (skip real command)
#   - var == "1" or any other value including "": treat as fail
_check() {
    local name="$1"
    local override_var="$2"
    local result

    if [[ -n "${!override_var+x}" ]]; then
        # Var is SET (possibly empty). Require exactly "0" to pass.
        local raw="${!override_var}"
        if [[ "$raw" == "0" ]]; then
            result=0
        else
            result=1
        fi
    else
        shift 2
        if "$@" >/dev/null 2>&1; then
            result=0
        else
            result=1
        fi
    fi

    if [[ "$result" -ne 0 ]]; then
        FAILURES+=("$name")
        echo "FAIL: $name"
    else
        echo "PASS: $name"
    fi
}

# ---------- checks ----------

# 1. CI eval gate (SP-06): latest CI run on HEAD must be green.
_check "eval-gate" "PREDEPLOY_EVAL_RESULT" \
    bash -c 'gh run list --branch "$(git branch --show-current)" --limit 1 \
             --json conclusion --jq ".[0].conclusion == \"success\"" | grep -q true'

# 2. Dead-code gate (C4): no unreachable public symbols on this diff.
_check "dead-code-c4" "PREDEPLOY_C4_RESULT" \
    bash -c 'git diff origin/main...HEAD | \
             python scripts/ci/dead_code_gate.py --diff -'

# 3. Cosign verify (SP-00d): current HEAD commit is cosign-signed.
_check "cosign-verify" "PREDEPLOY_COSIGN_RESULT" \
    bash -c 'cosign verify-commit HEAD \
             --certificate-identity-regexp ".*" \
             --certificate-oidc-issuer-regexp ".*"'

# 4. Submodule clean: no submodule at a dirty (+) or unregistered (-) ref.
_check "submodule-clean" "PREDEPLOY_SUBMODULE_RESULT" \
    bash -c '! git submodule status | grep -qE "^[+\-]"'

# 5. Secrets encrypted: no git-tracked plaintext (non-.sops) files under
#    secrets/. Uses git ls-files (not find) so untracked/ignored decrypted
#    copies are excluded. The pattern *.sops is a strict suffix — a file
#    named "token.sops.evil" does NOT match and will fail the check.
_check "secrets-encrypted" "PREDEPLOY_SECRETS_RESULT" \
    bash -c '
failed=0
while IFS= read -r f; do
    case "$f" in
        secrets/README.md|secrets/.gitignore) continue ;;
        *.sops) continue ;;
        *) echo "plaintext tracked file in secrets/: $f"; failed=1 ;;
    esac
done < <(git ls-files secrets/)
exit $failed
'

# 6. Terraform plan clean: no config drift in terraform/phase-0a-gcp.
#    terraform plan -detailed-exitcode: 0=no changes (PASS), 1=error (FAIL),
#    2=changes present (FAIL — drift must be applied before deploy).
_check "terraform-plan-clean" "PREDEPLOY_TERRAFORM_RESULT" \
    bash -c 'terraform -chdir=terraform/phase-0a-gcp plan -detailed-exitcode \
             -out=/dev/null -lock=false 2>&1'

# ---------- verdict ----------

if [[ ${#FAILURES[@]} -gt 0 ]]; then
    echo ""
    printf 'FAILED CHECK: %s\n' "${FAILURES[@]}"
    echo "predeploy-gate: ${#FAILURES[@]} check(s) failed — deploy blocked"
    exit 1
fi

echo ""
echo "predeploy-gate: ALL CHECKS PASSED — deploy approved"
