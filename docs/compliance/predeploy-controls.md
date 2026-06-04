# Predeploy Compliance Map — AutonomousAgent (SP-24)

Each control checked by `scripts/predeploy_gate.sh` before any production
deploy to `autonomous-agent-2026`. The gate exits non-zero if any check fails,
blocking the deploy.

---

## Controls

| Check name | Script check | PRD control | Implementation | Evidence |
|---|---|---|---|---|
| `eval-gate` | `gh run list` — latest CI run on HEAD has `conclusion=success` | SP-06 eval gate must pass before deploy | `.github/workflows/sp06-eval-gate.yml` | CI run ID in deploy log |
| `dead-code-c4` | `dead_code_gate.py --diff` — zero unreachable public symbols on the diff | C4 dead-code reachability gate | `scripts/ci/dead_code_gate.py` + `config/dead_code_entrypoints.txt` | gate exit code + output |
| `cosign-verify` | `cosign verify-commit HEAD` — HEAD commit has a valid cosign signature | SP-00d cosign provenance: every merged commit must be OIDC-signed by the GitHub Actions workflow | `scripts/ci/cosign_sign.sh` + Fulcio OIDC | `cosign verify` output |
| `submodule-clean` | `git submodule status` — no `+` (dirty) or `-` (unregistered) submodule | No unregistered or dirty submodule at deploy time | `.gitmodules` + `scripts/ci/submodule_pin_check.sh` | `git submodule status` output |
| `secrets-encrypted` | `find secrets/ ! -name "*.sops*"` — no plaintext secrets under `secrets/` | C12 secrets must be SOPS-encrypted at rest; no plaintext in the repo | `secrets/` directory + `.pre-commit-config.yaml` `block-plaintext-in-secrets` hook | `find` output |
| `terraform-plan-clean` | `terraform plan -detailed-exitcode` — exit code 0 (no changes) only; exit 2 (planned changes present) = FAIL (drift must be remediated before deploy) | SP-23 GCP substrate — no unplanned drift in `terraform/phase-0a-gcp` | `terraform/phase-0a-gcp/` | `terraform plan` exit code |

---

## Gate invocation

```bash
# Dry-run all checks with hermetic overrides (CI fixture):
PREDEPLOY_EVAL_RESULT=0 \
PREDEPLOY_C4_RESULT=0 \
PREDEPLOY_COSIGN_RESULT=0 \
PREDEPLOY_SUBMODULE_RESULT=0 \
PREDEPLOY_SECRETS_RESULT=0 \
PREDEPLOY_TERRAFORM_RESULT=0 \
  bash scripts/predeploy_gate.sh

# Real pre-deploy run (requires GH CLI, cosign, terraform, GCP auth):
bash scripts/predeploy_gate.sh
```

---

## Compliance references

| PRD control | Where enforced |
|---|---|
| SP-00d (cosign provenance) | `cosign-verify` check |
| SP-06 (eval gate) | `eval-gate` check |
| C4 (dead-code reachability) | `dead-code-c4` check |
| C12 (secrets encrypted) | `secrets-encrypted` check |
| SP-23 (GCP substrate no-drift) | `terraform-plan-clean` check |
| Submodule integrity | `submodule-clean` check |
