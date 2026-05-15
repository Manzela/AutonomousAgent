# Recovery Procedures

## After a panic

1. Inspect Phoenix at http://localhost:6006 to find the offending trace
2. Inspect logs: `docker compose -f deploy/docker-compose.yml logs hermes-agent --tail=200`
3. If safe, resume: `docker compose -f deploy/docker-compose.yml unpause hermes-agent hermes-gateway`
4. If not, teardown: `./scripts/teardown.sh` (preserves data) or `./scripts/teardown.sh --remove-volumes` (destroys data)

## Restoring from a snapshot

```bash
TS=20260514-153012
COMPOSE="docker compose -f deploy/docker-compose.yml"
$COMPOSE down
docker volume rm autonomous-agent_hermes-data autonomous-agent_chroma-data autonomous-agent_honcho-db-data
$COMPOSE up -d --no-start
$COMPOSE start honcho-db
sleep 5
$COMPOSE exec -T honcho-db psql -U honcho honcho < snapshots/$TS/honcho.dump
docker run --rm -v autonomous-agent_hermes-data:/data -v "$(pwd)/snapshots/$TS":/snap alpine tar xzf /snap/hermes-data.tar.gz -C /data
docker run --rm -v autonomous-agent_chroma-data:/data -v "$(pwd)/snapshots/$TS":/snap alpine tar xzf /snap/chroma-data.tar.gz -C /data
$COMPOSE up -d
./scripts/smoke.sh
```

## Disaster: lost the age key

The encrypted secrets are useless without `~/.config/sops/age/keys.txt`.
- If you have the public key but not the private one, you cannot decrypt
- Restore from your password manager backup (you DID back it up, right?)
- Otherwise: regenerate Telegram bot token via BotFather, regenerate other secrets via `./scripts/decrypt-secrets.sh` after running the secret-generation steps from Task 29
