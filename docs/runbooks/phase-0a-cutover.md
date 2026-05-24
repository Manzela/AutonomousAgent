# Phase 0a Cutover Runbook — Laptop → GCP

**Audience:** operator executing the cutover from local docker-compose to GCP VM.
**Estimated duration:** 60-90 minutes including verification.
**Prerequisites:**
- Phase A through H tasks complete; `acceptance.sh` FAIL=0
- VM `autonomousagent-vm` exists in zone `us-central1-a` and is healthy
- Latest hermes image pushed to Artifact Registry

## Cutover sequence

### T-24h: Announce + pre-cutover snapshot
1. Take a fresh snapshot of the local `hermes-data` volume:
   ```bash
   docker run --rm -v autonomousagent_hermes-data:/data -v $(pwd):/backup \
     alpine tar -czf /backup/laptop-hermes-data-$(date +%F).tar.gz -C /data .
   ```
2. Upload the tarball to `gs://autonomous-agent-2026-snapshots/laptop-state/`.
   This is the rollback safety net.

### T-0: Cutover
1. **Stop laptop stack** (do NOT remove volumes):
   ```bash
   docker compose -f deploy/docker-compose.yml stop
   ```
2. **Restore laptop state onto VM data disk:**
   ```bash
   gcloud compute scp ./laptop-hermes-data-*.tar.gz \
     autonomousagent-vm:/tmp/ --zone=us-central1-a --tunnel-through-iap
   gcloud compute ssh autonomousagent-vm --zone=us-central1-a --tunnel-through-iap --command="
     sudo systemctl stop docker-compose-hermes.service
     sudo tar -xzf /tmp/laptop-hermes-data-*.tar.gz -C /opt/hermes/data/
     sudo systemctl start docker-compose-hermes.service
   "
   ```
3. **Smoke + acceptance:**
   ```bash
   bash tests/phase_0a/smoke.sh autonomousagent-vm us-central1-a
   bash tests/phase_0a/acceptance.sh autonomousagent-vm us-central1-a
   ```
4. **Update external pointers** (any client app pointing at laptop): switch DNS / config to VM internal IP via IAP tunnel.

### T+24h: Soak
1. Watch Cloud Monitoring uptime check + watchdog metric for 24h.
2. If green: proceed to T+72h cleanup.
3. If red: invoke `docs/runbooks/phase-0a-rollback.md`.

### T+72h: Cleanup
1. Confirm 72h continuous green.
2. Tag the commit: `git tag phase-0a-cutover-stable && git push --tags`.
3. Keep laptop stack down but DO NOT delete the laptop `hermes-data` volume for 7 days (rollback window).

## Acceptance criteria
All 10 items in spec section 11 pass; deferred items (3, 5, 6, 10) tracked separately.
