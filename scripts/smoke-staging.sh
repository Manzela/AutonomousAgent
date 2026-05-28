#!/usr/bin/env bash
# Staging smoke test for DR drill.
set -euo pipefail

echo "Checking Cloud SQL Postgres instance on staging..."
# Check that the restored instance exists and is running
gcloud sql instances describe autonomousagent-honcho-staging-drill \
  --project=autonomousagent-staging-2026 \
  --format="value(state)" | grep -q "RUNNABLE"

echo "Checking GCE disk on staging..."
# Check that the restored GCE disk exists
gcloud compute disks describe autonomousagent-drill-data \
  --project=autonomousagent-staging-2026 \
  --zone=us-central1-a \
  --format="value(status)" | grep -q "READY"

echo "✓ DR Staging Smoke Test passed!"
