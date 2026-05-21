# Runbook — Atomic J1 Launch (J3 Trajectory Shipper Enable)

**Audience:** the operator (human) executing the J1 launch flip.
**Pre-requisites — ALL must be true before this runbook runs:**

1. `tests/integration/test_persistence_trap.py` — 8/8 passing on current HEAD.
2. `tests/integration/test_run_trajectory_shipper.py` — 3/3 passing on current HEAD.
3. `audit/2026-05-21-persistence-trap-12c/USER-APPROVAL.md` — committed, contains the verbatim user approval phrase.
4. `terraform/phase-0a-gcp/gcs.tf` — `google_storage_bucket.j3_trajectories` **APPLIED** (`gsutil ls -b gs://i-for-ai-autonomousagent-j3-trajectories` returns 0).
5. `terraform/phase-0a-gcp/model-armor/` — **APPLIED**: `gcloud model-armor floorsettings describe --project=i-for-ai --location=global` returns a settings block with `enforcement = true`, AND `gcloud model-armor templates describe j1-trajectory-shipper --project=i-for-ai --location=us-central1` returns a template referencing the regional InspectTemplate.
6. `terraform/phase-0a-gcp/secret_manager.tf` — `google_secret_manager_secret.j3_shipper_config` **APPLIED**: `gcloud secrets describe autonomousagent-j3-shipper-config --project=i-for-ai` returns the secret. Version 1 has `feature_flag_enabled = false`.

**Rollback strategy — read this BEFORE the flip:** Secret Manager versioning is the rollback vehicle. The flip writes secret version 2 with `feature_flag_enabled = true`. Rollback is a single command to disable version 2 (instant; version 1 remains the served value):

```bash
gcloud secrets versions disable 2 --secret=autonomousagent-j3-shipper-config --project=i-for-ai
```

Do NOT plan a rollback by editing version 2 in place. Version immutability is the safety mechanism.

---

## Stage A — Stage the new secret version (NO flip yet)

Delegated via gemini-gcp skill. Verbatim command:

```bash
# Build the new secret payload with feature_flag_enabled=true
cat > /tmp/j3-shipper-config-v2.json <<'JSON'
{
  "bucket_name": "i-for-ai-autonomousagent-j3-trajectories",
  "model_armor_template_resource": "projects/i-for-ai/locations/us-central1/templates/j1-trajectory-shipper",
  "feature_flag_enabled": true
}
JSON

# Stage as a NEW version (version 2). Until the systemd unit restarts,
# the shipper still sees version 1's feature_flag_enabled=false. This step
# is reversible by `gcloud secrets versions disable 2 ...`.
gcloud secrets versions add autonomousagent-j3-shipper-config \
  --data-file=/tmp/j3-shipper-config-v2.json \
  --project=i-for-ai

# Verify version 2 is present and 1 still exists
gcloud secrets versions list autonomousagent-j3-shipper-config --project=i-for-ai
```

Expected: versions list shows both `1` and `2`, both `ENABLED`.

---

## Stage B — Canary-record smoke (BEFORE wiring the timer)

Delegated via gemini-gcp skill. SSH to the Hermes VM and run a one-shot `--ship-once` exercise that uploads a single record containing a known canary token, then verify the GCS object has the canary REDACTED.

**The canary tokens (from `audit/2026-05-21-persistence-trap-12c/findings.md`):**
- email: `canary+persistencetrap@example.test`
- SSN: `999-88-7777`
- PAN: `4111-1111-1111-1111`
- phone: `(555) 010-1234`

```bash
# On the VM:
ssh autonomousagent-vm -- bash -lc '
  cd /opt/autonomousagent
  export HERMES_J3_SHIPPER_CONFIG_SECRET="projects/i-for-ai/secrets/autonomousagent-j3-shipper-config/versions/2"  # pragma: allowlist secret
  export HERMES_LOG_LEVEL=DEBUG
  uv run --extra dev python scripts/run_trajectory_shipper.py --dry-run
'
```

Expected stdout includes: `j3-shipper: dry-run OK — bucket=i-for-ai-autonomousagent-j3-trajectories, template=...`. If it instead says `feature_flag_enabled=false`, the VM still has the old secret version cached — re-run after rebuilding the in-memory cache.

Then the actual canary-record ship (still --ship-once mode wired to a one-shot input):

```bash
# Construct a one-record JSONL with all four canary tokens
ssh autonomousagent-vm -- bash -lc '
  cat > /tmp/canary-judge-event.jsonl <<JSON
{"schema_version": 1, "verdict": "approved", "consensus": true, "tool_call_id": "canary-001", "tool_name": "lookup_user", "args": {"email": "canary+persistencetrap@example.test", "ssn": "999-88-7777", "card": "4111-1111-1111-1111", "phone": "(555) 010-1234"}, "result_preview": "User found.", "judge_responses": []}
JSON
'
```

(Tail-watcher loop for production goes here — out of scope for Phase 0a runbook; the canary path uses a manual `gsutil cp` after `TrajectoryShipper.ship_one` returns its sanitized payload. Detailed sub-procedure lives in `audit/2026-05-21-gemini-delegation/j1-flip-canary-smoke.md` once Task 9 records it.)

Then verify the GCS object has the canary REDACTED:

```bash
# Download the most recent uploaded object and grep for canary tokens
gsutil cp gs://i-for-ai-autonomousagent-j3-trajectories/$(gsutil ls gs://i-for-ai-autonomousagent-j3-trajectories | tail -1 | xargs -n1 basename) /tmp/canary-uploaded.jsonl

# The Persistence Trap holds IFF all four canary patterns are absent
for token in 'canary+persistencetrap@example.test' '999-88-7777' '4111-1111-1111-1111' '(555) 010-1234'; do
  if grep -q "$token" /tmp/canary-uploaded.jsonl; then
    echo "PERSISTENCE TRAP VIOLATED — canary token leaked: $token"
    exit 1
  fi
done
echo "Persistence Trap holds — all four canary tokens redacted in the uploaded object"
```

If any canary token survives, HALT IMMEDIATELY:

```bash
# Roll back the flip
gcloud secrets versions disable 2 --secret=autonomousagent-j3-shipper-config --project=i-for-ai
```

…and open a P0 incident: the J3 shipper has shipped un-redacted PII to GCS. Purge the offending object, file a Persistence Trap regression in `audit/`, and do NOT re-enable until root-caused.

---

## Stage C — Wire the systemd timer (production cadence)

Delegated via gemini-gcp skill. This is the long-running loop (the canary smoke in Stage B was --ship-once).

```bash
# On the VM, install the systemd timer that runs the shipper every 5 minutes
sudo tee /etc/systemd/system/autonomousagent-trajectory-shipper.service <<UNIT
[Unit]
Description=AutonomousAgent J3 trajectory shipper
After=network.target

[Service]
Type=oneshot
User=autonomousagent
WorkingDirectory=/opt/autonomousagent
Environment=HERMES_LOG_LEVEL=INFO
ExecStart=/usr/bin/env uv run --extra prod python scripts/run_trajectory_shipper.py --ship-once
UNIT

sudo tee /etc/systemd/system/autonomousagent-trajectory-shipper.timer <<TIMER
[Unit]
Description=Run J3 trajectory shipper every 5 minutes
[Timer]
OnBootSec=2min
OnUnitActiveSec=5min
[Install]
WantedBy=timers.target
TIMER

sudo systemctl daemon-reload
sudo systemctl enable --now autonomousagent-trajectory-shipper.timer

# Verify
systemctl status autonomousagent-trajectory-shipper.timer
journalctl -u autonomousagent-trajectory-shipper.service -n 50
```

Expected: timer is `active (waiting)`; first service run within 2 minutes; journal shows clean exits.

---

## Stage D — Capture flip evidence

After Stage C confirms green:

1. Save the full `gcloud secrets versions list` + `gsutil ls` + `journalctl` output to `audit/2026-05-21-gemini-delegation/j1-flip-canary-smoke.output`.
2. Write the narrative summary to `audit/2026-05-21-gemini-delegation/j1-flip-canary-smoke.md` (timeline + commands + verification).
3. Tag `j1-launched` on the current HEAD (do NOT push without the G4 auth phrase from the plan):

```bash
git tag -a j1-launched -m "J1 launch flip executed: J3 shipper writing redacted trajectories to gs://i-for-ai-autonomousagent-j3-trajectories"
```

---

## Failure modes (read before, not during, an incident)

| Mode | Symptom | Recovery |
|------|---------|----------|
| Secret v2 staged but VM serves v1 | `journalctl` shows `feature_flag_enabled=false` after Stage A | Cache TTL — wait 60s OR restart the timer to force re-read |
| Canary leak (Persistence Trap violation) | grep finds canary in uploaded object | DISABLE v2 + purge object + P0 incident |
| F37 dispatch in journal | `journalctl` shows `dispatch("F37")` line | Model Armor sanitize is unavailable — shipper has HALTed by design; check Model Armor service status before re-enabling |
| Bucket IAM denied | `403` in journal at upload | Re-apply `google_storage_bucket_iam_member.j3_trajectories_vm_writer` |
| Template-mismatch error | `INVALID_SDP_TEMPLATE` in Model Armor response | Cross-region template drift — apply `terraform/phase-0a-gcp/model-armor/` to refresh |

---

## What this runbook does NOT cover

- The tail-and-ship watcher (the long-running tailer that feeds `--ship-once` from continuously appended JSONL). That is a Phase 0a follow-up — once shipped, the systemd unit above becomes a daemon-mode service rather than a 5-minute timer.
- Phase 4 RL training-data ingest (which reads from this bucket). Not yet built.
- Cross-region replication of `i-for-ai-autonomousagent-j3-trajectories`. Phase 0a is single-region by design; cross-region is a Phase 4 concern when training compute moves.
