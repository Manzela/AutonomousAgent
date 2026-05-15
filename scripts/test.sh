#!/usr/bin/env bash
# Run all tests: unit + integration.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

source .venv/bin/activate

echo "==> Unit tests"
pytest tests/unit/ -v --tb=short

echo
echo "==> Integration tests (mocked LLM)"
docker compose -f deploy/docker-compose.yml -f deploy/docker-compose.test.yml up -d --wait
trap "docker compose -f deploy/docker-compose.yml -f deploy/docker-compose.test.yml down -v" EXIT
pytest tests/integration/ -v --tb=short

echo
echo "✅ All tests passed"
