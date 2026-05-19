# Runbooks

Operational procedures for running and recovering AutonomousAgent.

## Index

| Runbook | When to use |
|---|---|
| [telegram-bot-setup.md](telegram-bot-setup.md) | One-time: create the Telegram bot for the messaging gateway |
| [healthcheck-cron-setup.md](healthcheck-cron-setup.md) | One-time per host: register the 5-minute Healthchecks.io ping cron |
| [phase1-acceptance.md](phase1-acceptance.md) | End of Phase 1: validate the local deployment works end-to-end |
| [recovery.md](recovery.md) | Stack is broken, panic was invoked, or you need to restore from a snapshot |
| [snapshots.md](snapshots.md) | One-time per environment: provision the GCS bucket + SA so the daily snapshot sidecar can upload (off-host DR) |
| [branch-protection.md](branch-protection.md) | One-time per repo: flip `enforce_admins` and require approvals on `main` (SLSA Source L3) |

## Conventions

Every runbook:
- States its prerequisites at the top
- Lists steps in order with expected output for each
- Has a clear "pass criteria" or "expected end state"
- Says what to do if a step fails
