#!/usr/bin/env bash
# Snapshot agent state. Phase 1: writes to local snapshots/ dir.
# Phase 2 will upload to GCS.
#
# What we back up (everything that's runtime state, not config-in-git):
#   - The full /data tree from the hermes container. This is the
#     hermes-data volume and contains:
#       * /data/checkpoints/                 (P1-3 durability)
#       * /data/MEMORY/REJECTED.md           (P1-4 institutional memory)
#       * /data/secret-leak-attempts.log     (scrubber audit log)
#       * any other runtime state Hermes writes to /data
#   - The Kanban SQLite DB from /root/.hermes/kanban/kanban.db
#     (the same hermes-data volume, mounted twice — once at /data,
#     once at /root/.hermes — so we capture it explicitly to keep
#     a separate, easy-to-restore artifact).
#   - The on-host logs/ directory.
#
# What we removed and why:
#   - chroma-data:  Chroma is cloud-managed in Phase 1; snapshots come
#                   from Chroma Cloud, not from a local volume.
#   - honcho.dump:  Honcho is disabled in Phase 1 (no honcho-db service).
#   - hermes-agent: service was renamed to `hermes` in 408459e
#                   (collapsed with hermes-gateway).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TS="$(date -u +%Y%m%d-%H%M%S)"
OUT="${SNAPSHOT_DIR:-$ROOT/snapshots/$TS}"
mkdir -p "$OUT"

COMPOSE="docker compose -f $ROOT/deploy/docker-compose.yml"

# Helper: only exec into the hermes container if it's actually running.
# Lets the script be invoked from a teardown / CI context without failing.
hermes_running() {
  $COMPOSE ps --status running --services 2>/dev/null | grep -qx hermes
}

if hermes_running; then
  echo "==> Snapshotting hermes-data volume (/data) → $OUT/hermes-data.tar.gz"
  $COMPOSE exec -T hermes tar czf - -C /data . > "$OUT/hermes-data.tar.gz"

  # The Kanban DB lives in the same volume, but mounted at /root/.hermes
  # (see kanban.db_path = /root/.hermes/kanban/kanban.db). Pull it out as
  # a discrete artifact so restoring just the board state is one command.
  echo "==> Snapshotting Kanban DB → $OUT/kanban.db"
  if $COMPOSE exec -T hermes test -f /root/.hermes/kanban/kanban.db; then
    $COMPOSE exec -T hermes cat /root/.hermes/kanban/kanban.db > "$OUT/kanban.db"
  else
    echo "    (no Kanban DB yet — fresh stack)"
  fi
else
  echo "==> hermes container not running; skipping in-container snapshots"
  echo "    (run \`docker compose -f deploy/docker-compose.yml up -d hermes\` to snapshot live state)"
fi

# Host-side artifacts — these don't need the container to be up.
if [ -d "$ROOT/logs" ]; then
  echo "==> Snapshotting host logs/ → $OUT/logs.tar.gz"
  tar czf "$OUT/logs.tar.gz" -C "$ROOT" logs
fi

# Convenience symlink so `snapshots/latest` always points at the newest
# snapshot. ln -sf is portable across BSD/GNU.
if [ -z "${SNAPSHOT_DIR:-}" ]; then
  ln -sfn "$TS" "$ROOT/snapshots/latest"
fi

echo
echo "Snapshot at $OUT"
ls -la "$OUT"

if [ -d "$ROOT/snapshots" ]; then
  echo
  echo "==> Cleaning snapshots older than 30 days"
  find "$ROOT/snapshots" -mindepth 1 -maxdepth 1 -type d -mtime +30 -exec rm -rf {} \;
fi
