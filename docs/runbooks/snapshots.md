# GCS daily snapshots — provisioning & restore

The `snapshot-watchdog` sidecar (`scripts/snapshot_loop.py`) uploads a daily
`hermes-state.tar.gz` of `/home/hermes/.hermes` (kanban DB, plugins, runtime
state) to `gs://${GCS_SNAPSHOT_BUCKET}/hermes-snapshots/YYYY-MM-DD/` so the
stack can be reconstituted on a fresh host after total volume loss.

The sidecar is shipped in a no-op state: when `GCS_SNAPSHOT_BUCKET` is unset
(the default), the loop ticks, logs `skipped reason=bucket_not_configured`,
and uploads nothing. To enable, complete the four steps below — they're
all one-time per environment.

## Prerequisites

- `gcloud` CLI authenticated (`gcloud auth login`) against the GCP project
  you'll bill the bucket to.
- Owner or Storage Admin role on that project (needed to mint the SA key).
- A name that is **globally unique** on Cloud Storage — bucket names are
  not namespaced by project. The convention here is
  `autonomous-agent-snapshots-<short-project-id>`.

## Step 1 — create the bucket

```bash
PROJECT=autonomous-agent-2026
BUCKET=autonomous-agent-snapshots-${PROJECT}
LOCATION=US                                   # multi-region, cheapest for write-heavy DR
gcloud storage buckets create gs://${BUCKET} \
  --project=${PROJECT} \
  --location=${LOCATION} \
  --uniform-bucket-level-access \
  --public-access-prevention
```

Confirm:

```bash
gcloud storage buckets describe gs://${BUCKET} \
  --format='value(uniform_bucket_level_access.enabled,iam_configuration.public_access_prevention)'
# expect: True   enforced
```

## Step 2 — apply the 30-day retention policy

`config/limits.yaml → snapshots.gcs_retention_days` is the source of truth
(default `30`). Mirror it as a bucket lifecycle rule so old objects expire
automatically — the sidecar never deletes anything itself.

```bash
cat > /tmp/lifecycle.json <<'JSON'
{
  "lifecycle": {
    "rule": [
      {
        "action": {"type": "Delete"},
        "condition": {"age": 30, "matchesPrefix": ["hermes-snapshots/"]}
      }
    ]
  }
}
JSON
gcloud storage buckets update gs://${BUCKET} --lifecycle-file=/tmp/lifecycle.json
```

Confirm:

```bash
gcloud storage buckets describe gs://${BUCKET} --format='value(lifecycle)'
```

If you change `gcs_retention_days` in `limits.yaml`, re-run this step with
the new value — the YAML alone won't move the lifecycle rule.

## Step 3 — least-privilege service account

The sidecar only needs `objects.create` (write the tarball) and
`objects.list` (the "is today already done?" idempotency check). It does
**not** need `objects.delete` — lifecycle handles retention — or any
project-level role.

```bash
SA=autonomous-agent-snapshots
gcloud iam service-accounts create ${SA} \
  --project=${PROJECT} \
  --display-name="AutonomousAgent snapshot uploader"

SA_EMAIL=${SA}@${PROJECT}.iam.gserviceaccount.com

# Bind only the two needed permissions, at bucket scope.
gcloud storage buckets add-iam-policy-binding gs://${BUCKET} \
  --member=serviceAccount:${SA_EMAIL} \
  --role=roles/storage.objectCreator
gcloud storage buckets add-iam-policy-binding gs://${BUCKET} \
  --member=serviceAccount:${SA_EMAIL} \
  --role=roles/storage.objectViewer   # for list_blobs
```

Mint a JSON key and place it where the compose mount expects ADC:

```bash
mkdir -p ~/.config/gcloud
gcloud iam service-accounts keys create \
  ~/.config/gcloud/application_default_credentials.json \
  --iam-account=${SA_EMAIL}
chmod 600 ~/.config/gcloud/application_default_credentials.json
```

`deploy/docker-compose.yml` already mounts `${HOME}/.config/gcloud` into
the sidecar read-only — no compose edit needed.

## Step 4 — flip the feature flag

```bash
# Persist for future shells.
echo "export GCS_SNAPSHOT_BUCKET=${BUCKET}" >> ~/.zshrc

# Restart just the sidecar so it picks up the env var.
export GCS_SNAPSHOT_BUCKET=${BUCKET}
docker compose -f deploy/docker-compose.yml up -d --force-recreate snapshot-watchdog
```

Verify within 30 minutes (one loop tick):

```bash
docker logs autonomous-agent-snapshot-watchdog-1 --tail 20 | grep snapshot_loop
# expect either:
#   snapshot_loop uploaded object=hermes-snapshots/YYYY-MM-DD/hermes-state.tar.gz bytes=N
#   snapshot_loop skipped reason=before_snapshot_hour_utc=4   (if it's before 04:00 UTC)
#   snapshot_loop skipped reason=today_already_uploaded       (if today's snap is in)
```

And on the bucket side:

```bash
gcloud storage ls gs://${BUCKET}/hermes-snapshots/$(date -u +%Y-%m-%d)/
# expect: gs://.../hermes-snapshots/YYYY-MM-DD/hermes-state.tar.gz
```

## Restore from a GCS snapshot

GCS snapshots integrate with [recovery.md](recovery.md) — the off-host
copy is functionally identical to a local `snapshots/<TS>/` directory.

```bash
# 1. Download yesterday's snapshot to the local snapshots/ tree.
TS=$(date -u +%Y-%m-%d)
mkdir -p snapshots/${TS}
gcloud storage cp \
  gs://${GCS_SNAPSHOT_BUCKET}/hermes-snapshots/${TS}/hermes-state.tar.gz \
  snapshots/${TS}/hermes-data.tar.gz

# 2. Hand off to the existing recovery flow (step 5 onward).
#    See recovery.md → "full disaster restore (from snapshot)".
```

The tar's internal `arcname` is `hermes/...`, so the extract command in
recovery.md step 5 (`tar xzf .../hermes-data.tar.gz -C /data`) writes
files into `/data/hermes/...`. If you're restoring into a fresh
`hermes-data` volume where the runtime expects `/home/hermes/.hermes/...`,
strip the prefix:

```bash
tar xzf snapshots/${TS}/hermes-data.tar.gz --strip-components=1 -C /data
```

## Alerting

The sidecar fails **soft**: a failed upload emits a Telegram alert via
`lib.kanban.telegram_bridge.send_alert("snapshot", ...)` and returns,
so the loop keeps ticking. Operator response:

1. Check the alert text — it includes the bucket, object name, and the
   underlying SDK exception (typical: `403 Forbidden`, `404 Not Found`,
   `quota exceeded`).
2. Verify IAM (Step 3) and bucket existence (Step 1).
3. The watchdog's idempotency check means a transient failure on hour
   N will auto-retry on hour N+1 once you've fixed the root cause — no
   manual re-trigger needed.

If alerts are missing entirely but uploads also aren't happening, check
`docker logs autonomous-agent-snapshot-watchdog-1` directly. A common
cause is `ImportError: google.cloud.storage not installed` in a fresh
image build — `deploy/Dockerfile.hermes` installs it, so this only fires
on a stale image; rebuild with `docker compose build hermes`.

## Pass criteria

- `gcloud storage ls gs://${BUCKET}/hermes-snapshots/$(date -u +%Y-%m-%d)/`
  shows the day's `hermes-state.tar.gz` within a few hours of 04:00 UTC.
- `docker logs autonomous-agent-snapshot-watchdog-1` cycles between
  `uploaded` and `skipped reason=today_already_uploaded` with no
  `tick error=` lines.
- The bucket's `lifecycle` describes the 30-day deletion rule.
