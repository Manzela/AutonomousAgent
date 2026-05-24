# Phase 0a Rollback Runbook — GCP → Laptop

**Use when:** cutover failed acceptance OR a regression appeared in the 72h soak window.
**RTO:** ~20 minutes.

## Decision criteria
Invoke rollback if ANY of:
- `acceptance.sh` shows FAIL >0 for criteria 2, 4, 9 (containers / watchdog / secrets)
- `litellm-proxy /health` returns non-200 for >15min
- Watchdog alert fires >3 times in 1h
- Data corruption suspected on VM data disk

## Steps

### 1. Stop VM stack
```bash
gcloud compute ssh autonomousagent-vm --zone=us-central1-a --tunnel-through-iap --command="
  sudo systemctl stop docker-compose-hermes.service hermes-watchdog.service
"
```

### 2. Extract VM state to local
```bash
gcloud compute ssh autonomousagent-vm --zone=us-central1-a --tunnel-through-iap --command="
  sudo tar -czf /tmp/vm-hermes-data-$(date +%F).tar.gz -C /opt/hermes/data .
"
gcloud compute scp autonomousagent-vm:/tmp/vm-hermes-data-*.tar.gz ./ \
  --zone=us-central1-a --tunnel-through-iap
```

### 3. Restore laptop volume
```bash
docker volume create autonomousagent_hermes-data || true
docker run --rm -v autonomousagent_hermes-data:/data -v $(pwd):/backup \
  alpine sh -c "cd /data && tar -xzf /backup/vm-hermes-data-*.tar.gz"
```

### 4. Restart laptop stack
```bash
docker compose -f deploy/docker-compose.yml up -d
sleep 60
docker compose -f deploy/docker-compose.yml ps
```

### 5. Verify
```bash
curl -fsS http://localhost:4000/health
docker compose -f deploy/docker-compose.yml logs hermes --tail 50
```

### 6. Disable CI deploys to GCP (prevent re-deploy)
```bash
gh workflow disable "Phase 0a Deploy"
```

### 7. Post-rollback
- Open a P0 incident issue in GitHub
- Stop the GCP VM (do not delete — preserve forensics): `gcloud compute instances stop autonomousagent-vm --zone=us-central1-a`
- Triage root cause; re-execute cutover only after fix is verified
