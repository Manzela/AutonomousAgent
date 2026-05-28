# Rollback Procedures for Meta-Audit Remediation

This document registers the rollback steps for every Pre-condition (PC) and P0 task in the remediation checklist.

---

## PC-1: Snapshot Terraform state offsite
- **Reversal**: Read-only snapshot. No rollback needed.

## PC-2: Capture Memorystore AUTH state
- **Reversal**: Read-only snapshot. No rollback needed.

## PC-3: Preserve hermes-agent submodule local state
- **Reversal**: Pop stash from hermes-agent submodule:
  ```bash
  cd hermes-agent && git stash pop
  ```

## PC-4: Audit Antigravity brain pollution
- **Reversal**: Read-only check. No rollback needed.

## PC-5: Grep dead-code candidates across all worktrees
- **Reversal**: Read-only check. No rollback needed.

## PC-6: Catalog OTel attribute emitters
- **Reversal**: Read-only check. No rollback needed.

## PC-7: Document per-P0 rollback procedures
- **Reversal**: Delete this document:
  ```bash
  git rm audit/2026-05-28-meta-audit/rollback-procedures.md
  ```

## PC-8: Validate every finding against code
- **Reversal**: Read-only check. No rollback needed.

## PC-9: Force heterogeneous reviewer-model class
- **Reversal**: Revert changes to CLAUDE.md and PR template:
  ```bash
  git checkout HEAD -- CLAUDE.md .github/pull_request_template.md
  ```

## PC-10: CI guard against pytest.mark.skip
- **Reversal**: Delete the CI guard workflow:
  ```bash
  git rm .github/workflows/no-skip-on-remediation.yml
  ```

---

## P0-1: Revert firewall.tf mock-IP regression
- **Reversal**: Revert the firewall.tf commit or pull from backup tfstate:
  ```bash
  git checkout HEAD^ -- terraform/phase-0a-gcp/firewall.tf
  # Restore state from snapshot if needed:
  terraform -chdir=terraform/phase-0a-gcp state push "/Users/danielmanzela/audit-backups/2026-05-28-tfstate.json"
  ```

## P0-2: Roll back Memorystore AUTH
- **Reversal**: Restore the previous main.tf file:
  ```bash
  git checkout HEAD^ -- terraform/phase-0a-gcp/memorystore/main.tf
  # Push state from backup:
  terraform -chdir=terraform/phase-0a-gcp state push "/Users/danielmanzela/audit-backups/2026-05-28-tfstate.json"
  ```

## P0-3: Plaintext secret remediation
- **Reversal**:
  - Restoring secrets/hermes-provider.env: decrypt the SOPS file:
    ```bash
    sops --decrypt secrets/hermes-provider.env.sops > secrets/hermes-provider.env
    git rm -f secrets/hermes-provider.env.sops
    ```
  - Reverting the script and pre-commit hook changes:
    ```bash
    git revert <commit-sha>
    ```

## P0-4: Delete OR safely wire dead observability modules
- **Reversal**: Restore the deleted files from git history:
  ```bash
  git checkout HEAD^ -- lib/observability/ledger.py lib/observability/failure_detectors.py
  ```

## P0-5: Fix OTel redactionprocessor misconfig
- **Reversal**: Revert collector.prod.yaml and test file changes:
  ```bash
  git checkout HEAD^ -- deploy/otel/collector.prod.yaml tests/integration/test_otel_redaction.py
  # Restart collector on VM:
  ssh autonomousagent-vm 'docker compose -f /opt/autonomousagent/docker-compose.yml restart otel-collector'
  ```

## P0-6: Remove update_plan.py, freeze rubric, reset brain
- **Reversal**: Restore update_plan.py and restore brain write permission:
  ```bash
  git checkout HEAD^ -- update_plan.py CLAUDE.md .pre-commit-config.yaml
  chmod 644 "$HOME/.gemini/antigravity-ide/brain/8eed20e2-78ef-4992-b186-5ceaa76947dd/implementation_plan.md"
  ```

## P0-7: Add F37 dispatch to ship_trajectory
- **Reversal**: Revert shipper.py change and delete tests:
  ```bash
  git checkout HEAD^ -- lib/trajectory/shipper.py tests/integration/test_persistence_trap_ship_trajectory.py
  ```

## P0-8: Add environment: production + reviewer to deploy workflow
- **Reversal**: Revert phase-0a-deploy.yml and delete GitHub environment protection:
  ```bash
  git checkout HEAD^ -- .github/workflows/phase-0a-deploy.yml
  gh api -X DELETE /repos/Manzela/AutonomousAgent/environments/production
  ```

## P0-9: OSV-Scanner / pip-audit on PR CI
- **Reversal**: Delete the workflow file:
  ```bash
  git rm -f .github/workflows/osv-scanner.yml
  ```

## P0-10: Trivy on PR CI (not just on tags)
- **Reversal**: Revert the workflow file:
  ```bash
  git checkout HEAD^ -- .github/workflows/trivy.yml
  ```

## P0-11: Cloud Audit Logs DATA_READ/WRITE for SM/GCS/IAM
- **Reversal**: Destroy the audit config resources and remove the file:
  ```bash
  terraform -chdir=terraform/phase-0a-gcp destroy -target=google_project_iam_audit_config.sm -target=google_project_iam_audit_config.gcs -target=google_project_iam_audit_config.iam
  git rm -f terraform/phase-0a-gcp/audit_config.tf
  ```

## P0-12: Signed commits + multi-CODEOWNER + branch protection
- **Reversal**: Revert CODEOWNERS, CLAUDE.md, and destroy branch protection:
  ```bash
  terraform -chdir=terraform/phase-0a-gcp destroy -target=github_branch_protection.main
  git rm -f .github/CODEOWNERS terraform/phase-0a-gcp/branch_protection.tf
  git checkout HEAD -- CLAUDE.md
  ```

## P0-13: Build provenance attestation verification
- **Reversal**: Revert deploy workflow modification:
  ```bash
  git checkout HEAD^ -- .github/workflows/phase-0a-deploy.yml
  ```

## P0-14: AppArmor/Seccomp for ALL containers
- **Reversal**: Remove seccomp profiles and revert docker-compose.yml:
  ```bash
  git checkout HEAD^ -- deploy/docker-compose.yml
  git rm -f deploy/sandboxes/seccomp-hermes.json deploy/sandboxes/seccomp-sidecar.json
  ```

## P0-15: GCP Billing Budget + cost anomaly detection
- **Reversal**: Destroy the monthly budget resources and remove the file:
  ```bash
  terraform -chdir=terraform/phase-0a-gcp destroy -target=google_billing_budget.monthly -target=google_monitoring_alert_policy.hourly_spend_spike
  git rm -f terraform/phase-0a-gcp/billing.tf
  ```

## P0-16: DR RPO/RTO targets + quarterly drill
- **Reversal**: Revert limits.yaml and delete quarterly drill workflow:
  ```bash
  git checkout HEAD^ -- config/limits.yaml
  git rm -f .github/workflows/quarterly-dr-drill.yml docs/architecture/dr-runbook.md
  ```

## P0-17: Retention lifecycle + GDPR right-to-deletion
- **Reversal**: Revert buckets.tf, docker-compose.yml, and delete deletion.py:
  ```bash
  git checkout HEAD^ -- terraform/phase-0a-gcp/buckets.tf deploy/docker-compose.yml
  git rm -f compliance/data-classification.yaml lib/privacy/deletion.py
  ```

## P0-18: CI gate for collected-but-skipped tests
- **Reversal**: Revert ci.yml modification and delete SKIPS.yaml:
  ```bash
  git checkout HEAD^ -- .github/workflows/ci.yml
  git rm -f tests/integration/SKIPS.yaml
  ```

## P0-19: Tracking — F-A.3 priority mismatch
- **Reversal**: Delete tracking file:
  ```bash
  git rm -f audit/2026-05-28-meta-audit/tracking/P0-19.md
  ```

## P0-20: Terraform state bucket with CMEK
- **Reversal**: Clear GCS bucket default encryption key:
  ```bash
  gcloud storage buckets update gs://autonomousagent-tfstate --clear-default-encryption-key
  ```

## P0-21: VPC Flow Logs + Firewall Rule logging
- **Reversal**: Revert networking.tf and firewall.tf:
  ```bash
  git checkout HEAD^ -- terraform/phase-0a-gcp/networking.tf terraform/phase-0a-gcp/firewall.tf
  ```

## P0-22: PagerDuty integration
- **Reversal**: Destroy the notification channel and remove variables:
  ```bash
  terraform -chdir=terraform/phase-0a-gcp destroy -target=google_monitoring_notification_channel.pagerduty
  git rm -f terraform/phase-0a-gcp/notification.tf secrets/pagerduty-routing-key.sops
  ```

## P0-23: Auto-rollback on smoke-check failure
- **Reversal**: Revert phase-0a-deploy.yml deploy workflow:
  ```bash
  git checkout HEAD^ -- .github/workflows/phase-0a-deploy.yml
  ```
