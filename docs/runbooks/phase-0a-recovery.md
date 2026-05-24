# Phase 0a Recovery Runbook — Rebuild VM from PD Snapshot

**Use when:** VM is unrecoverable (corrupted boot, accidental delete, zone outage).
**RTO:** ~30 minutes.
**RPO:** up to 24h (last daily snapshot).

## Steps

### 1. Identify latest snapshot
```bash
gcloud compute snapshots list --project=autonomous-agent-2026 \
  --filter="sourceDisk:autonomousagent-vm-data" \
  --sort-by=~creationTimestamp --limit=3
```

### 2. Restore data disk from snapshot
```bash
LATEST_SNAP=$(gcloud compute snapshots list --project=autonomous-agent-2026 \
  --filter="sourceDisk:autonomousagent-vm-data" --sort-by=~creationTimestamp --limit=1 \
  --format="value(name)")

gcloud compute disks create autonomousagent-vm-data-recovered \
  --source-snapshot="$LATEST_SNAP" \
  --zone=us-central1-a \
  --type=pd-balanced
```

### 3. Update Terraform state to use new disk
```bash
cd terraform/phase-0a-gcp
terraform import google_compute_disk.data \
  projects/autonomous-agent-2026/zones/us-central1-a/disks/autonomousagent-vm-data-recovered
# Then update compute.tf if disk name changed
```

### 4. Recreate VM
```bash
terraform apply -replace=google_compute_instance.autonomousagent
```

### 5. Wait for bootstrap; verify
```bash
gcloud compute instances get-serial-port-output autonomousagent-vm --zone=us-central1-a | tail -100
bash tests/phase_0a/smoke.sh autonomousagent-vm us-central1-a
bash tests/phase_0a/acceptance.sh autonomousagent-vm us-central1-a
```

### 6. Cleanup old disk after 7 days of stable operation
```bash
gcloud compute disks delete autonomousagent-vm-data --zone=us-central1-a
```
