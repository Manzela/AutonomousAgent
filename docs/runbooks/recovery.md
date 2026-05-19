# Recovery Procedures

Operating manual for restoring the Hermes Agent stack after a fault.

The current Phase 1 stack consists of these compose services:

- `hermes`               — the agent runtime (was `hermes-agent`; collapsed with `hermes-gateway` in commit 408459e)
- `litellm-proxy`        — model gateway
- `otel-collector` + `phoenix` — observability
- `shell-sandbox` + `github-mcp` — tool sandboxes
- `escalation-watcher`   — Telegram bridge

Chroma is **cloud-managed** (api.trychroma.com), and Honcho is **disabled** in
Phase 1; neither has a local compose service or volume to restore. See
`deploy/docker-compose.yml` for the canonical service list.

The only stateful volume is `hermes-data` (mounted at both `/data` and
`/home/hermes/.hermes` inside the `hermes` container; the container runs
as the non-root `hermes` user after the α-5 security-hardening PR).

---

## Step 1 — identify the failed subsystem

```bash
COMPOSE="docker compose -f deploy/docker-compose.yml"
$COMPOSE ps                          # which services are down / unhealthy?
$COMPOSE logs hermes --tail=200      # primary agent logs
$COMPOSE logs litellm-proxy --tail=100
$COMPOSE logs escalation-watcher --tail=50
```

Phoenix UI: <http://localhost:6006> — look at the most recent traces for the
last successful tool call before failure.

## Step 2 — snapshot current state

Always snapshot before changing anything; the broken state is often the most
informative artifact you'll have.

```bash
./scripts/snapshot.sh
# → snapshots/<TS>/{hermes-data.tar.gz, kanban.db, logs.tar.gz}
# → snapshots/latest symlink updated
```

## Step 3 — targeted restart

For most faults, restarting the affected service is enough:

```bash
$COMPOSE restart hermes              # most common
$COMPOSE restart litellm-proxy       # if model calls are failing
$COMPOSE restart otel-collector      # if traces stopped appearing
```

P1-3 durability means `hermes` will rehydrate from
`/data/checkpoints/<session_id>/step-*.json` on restart, so an in-flight
48 h run survives a restart of just the `hermes` container.

## Step 4 — verify

```bash
./scripts/smoke.sh
$COMPOSE exec hermes hermes --help    # CLI reachable from inside container
```

## Step 5 — full disaster restore (from snapshot)

Use only when targeted restart is insufficient (corrupt volume, lost data,
moved to a new host). Requires a valid snapshot in `snapshots/<TS>/` —
ideally one taken in step 2, or the most recent `snapshots/latest`.

```bash
TS=20260519-153012                   # or: TS=$(readlink snapshots/latest)
COMPOSE="docker compose -f deploy/docker-compose.yml"

# Stop the stack and destroy the volume we're about to restore.
$COMPOSE down
docker volume rm autonomous-agent_hermes-data

# Bring services back up without starting hermes, so we can populate
# the freshly recreated hermes-data volume before hermes opens it.
$COMPOSE up -d --no-start
$COMPOSE start litellm-proxy otel-collector phoenix

# Restore the /data tree (checkpoints, MEMORY/REJECTED.md, scrubber log).
docker run --rm \
  -v autonomous-agent_hermes-data:/data \
  -v "$(pwd)/snapshots/$TS":/snap \
  alpine tar xzf /snap/hermes-data.tar.gz -C /data

# Restore the Kanban DB if a discrete copy was captured.
if [ -f "snapshots/$TS/kanban.db" ]; then
  docker run --rm \
    -v autonomous-agent_hermes-data:/vol \
    -v "$(pwd)/snapshots/$TS":/snap \
    alpine sh -c 'mkdir -p /vol/kanban && cp /snap/kanban.db /vol/kanban/kanban.db'
fi

# Now start hermes against the restored volume.
$COMPOSE up -d hermes
./scripts/smoke.sh
```

## After a panic (`scripts/panic.sh`)

`panic.sh` pauses `hermes`, leaving every other service running so you can
inspect traces and logs without the agent issuing more tool calls.

1. Inspect Phoenix at <http://localhost:6006> to find the offending trace.
2. Inspect logs: `docker compose -f deploy/docker-compose.yml logs hermes --tail=200`.
3. If safe to resume: `docker compose -f deploy/docker-compose.yml unpause hermes`.
4. If not safe: `./scripts/teardown.sh` (preserves data) or
   `./scripts/teardown.sh --remove-volumes` (destroys data).

## Disaster: lost the age key

The encrypted secrets are useless without `~/.config/sops/age/keys.txt`.

- If you have only the public key, decryption is impossible.
- Restore from your password-manager backup of `keys.txt`.
- Otherwise: regenerate every credential (Telegram bot token via BotFather,
  Chroma Cloud API key in the dashboard, Healthchecks UUID, GitHub PAT, etc.)
  and re-run the secret-generation steps from the Phase 0 bootstrap, then
  `./scripts/decrypt-secrets.sh` will produce the decrypted env files the
  compose stack expects.
