#!/usr/bin/env bash
# Snapshot agent state. Phase 1: writes to local snapshots/ dir.
# Phase 2 will upload to GCS.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TS="$(date -u +%Y%m%d-%H%M%S)"
OUT="$ROOT/snapshots/$TS"
mkdir -p "$OUT"

COMPOSE="docker compose -f $ROOT/deploy/docker-compose.yml"

echo "Snapshotting hermes-data → $OUT/hermes-data.tar.gz"
$COMPOSE exec -T hermes-agent tar czf - -C /data . > "$OUT/hermes-data.tar.gz"

echo "Snapshotting chroma-data → $OUT/chroma-data.tar.gz"
$COMPOSE exec -T chroma tar czf - -C /chroma/chroma . > "$OUT/chroma-data.tar.gz"

echo "Snapshotting honcho-db → $OUT/honcho.dump"
$COMPOSE exec -T honcho-db pg_dump -U honcho honcho > "$OUT/honcho.dump"

echo "✓ Snapshot at $OUT"
echo "Cleaning snapshots older than 30 days..."
find "$ROOT/snapshots" -mindepth 1 -maxdepth 1 -type d -mtime +30 -exec rm -rf {} \;
