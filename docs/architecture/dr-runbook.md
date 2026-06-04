# Disaster Recovery Runbook

## RPO/RTO Targets

Disaster Recovery targets are defined in `config/limits.yaml`:
- **Recovery Point Objective (RPO)**: 24 hours (maximum age of restored data).
- **Recovery Time Objective (RTO)**: 4 hours (maximum time allowed to restore the stack and confirm health).
- **Drill Schedule**: Quarterly.

## Restore Steps

The automated quarterly DR drill restores the production state to the staging environment:
1. Authenticate to GCP via Workload Identity Federation (WIF).
2. Retrieve the latest backup ID of the production Cloud SQL database (`autonomousagent-honcho-prod` in project `autonomous-agent-2026`).
3. Restore that backup to the staging database instance (`autonomousagent-honcho-staging-drill` in project `autonomousagent-staging-2026`).
4. Retrieve the latest disk snapshot for the production GCE VM (`autonomousagent-vm-data` in project `autonomous-agent-2026`).
5. Create a new staging disk (`autonomousagent-drill-data` in zone `us-central1-a` in project `autonomousagent-staging-2026`) from the snapshot.

## Smoke Test Contents

The smoke test is executed via `scripts/smoke-staging.sh`. It performs the following checks:
1. Verifies that the restored staging Cloud SQL database instance state is `RUNNABLE`.
2. Verifies that the restored staging compute disk status is `READY`.

## Escalation Contacts

In the event of a real disaster or if the automated drill fails, escalate to:
- **Lead DevOps/SRE**: DevOps Team (on-call page via PagerDuty)
- **Security Lead**: Security Engineering Team
- **Production Owner**: @Manzela
