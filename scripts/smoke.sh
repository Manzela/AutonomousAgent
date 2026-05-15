#!/usr/bin/env bash
# Post-deploy smoke test. Exits non-zero on any failure.
#
# Verifies the Phase 1 stack (single `hermes` service + litellm + phoenix +
# otel-collector + shell-sandbox) is up and the critical paths work end-to-end.
# Removed checks vs the original spec: chroma local reachability (now Chroma
# Cloud, no internal endpoint), separate-agent endpoints (collapsed into
# the gateway).
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
COMPOSE=(docker compose -f "$ROOT/deploy/docker-compose.yml" -f "$ROOT/deploy/docker-compose.dev.yml")

failures=0
check() {
  local name="$1"
  shift
  if "$@" >/tmp/smoke.log 2>&1; then
    echo "✓ $name"
  else
    echo "✗ $name"
    sed 's/^/    /' /tmp/smoke.log
    failures=$((failures + 1))
  fi
}

echo "Smoke test 1/7: all expected containers running"
check "containers running" bash -c '
  expected=(hermes litellm-proxy phoenix otel-collector shell-sandbox)
  for svc in "${expected[@]}"; do
    if ! docker ps --filter "name=autonomous-agent-${svc}-" --format "{{.Names}}" | grep -q "${svc}"; then
      echo "missing: ${svc}"
      exit 1
    fi
  done
'

echo "Smoke test 2/7: litellm-proxy is healthy"
check "litellm-proxy healthy" bash -c '
  status=$(docker inspect autonomous-agent-litellm-proxy-1 --format "{{.State.Health.Status}}" 2>/dev/null)
  [ "$status" = "healthy" ] || { echo "status=$status"; exit 1; }
'

echo "Smoke test 3/7: real LLM round-trip via litellm → Vertex AI"
# Use Sonnet (claude-sonnet-4-6) for the smoke check — it shares the same
# integration path as Opus but has more headroom under the per-minute quota
# on the i-for-ai project. The smoke goal is "the chain works", not "Opus is
# unthrottled at this exact second". Opus is still wired and will be used by
# default by the agent for actual user turns.
check "real LLM call (vertex_ai/claude-sonnet-4-6)" bash -c '
  master_key=$(cat "'"$ROOT"'/secrets/litellm-master-key" 2>/dev/null)
  resp=$(curl -fsS -X POST http://localhost:4000/v1/chat/completions \
    -H "Authorization: Bearer ${master_key}" \
    -H "Content-Type: application/json" \
    -d "{\"model\":\"vertex_ai/claude-sonnet-4-6\",\"messages\":[{\"role\":\"user\",\"content\":\"Reply with just: pong\"}],\"max_tokens\":10}")
  echo "$resp" | grep -iq pong || { echo "no pong in: $resp"; exit 1; }
'

echo "Smoke test 4/7: Telegram bot reachable from gateway"
check "egress allowed (TG getMe)" bash -c '
  TG_TOKEN=$(grep -E "^TELEGRAM_BOT_TOKEN=" "'"$ROOT"'/secrets/telegram.env" | cut -d= -f2)
  curl -fsS "https://api.telegram.org/bot${TG_TOKEN}/getMe" | grep -q ok
'

echo "Smoke test 5/7: shell-sandbox cannot reach external network"
check "egress denied (shell-sandbox)" bash -c '
  ! docker exec autonomous-agent-shell-sandbox-1 \
    bash -c "curl -fsS --max-time 3 https://example.com >/dev/null 2>&1"
'

echo "Smoke test 6/7: limits.yaml validates against schema"
check "limits.yaml valid" bash -c '
  cd "'"$ROOT"'" && .venv/bin/python -m lib.limits_validator config/limits.yaml
'

echo "Smoke test 7/7: hermes container is running (gateway loop alive)"
check "hermes container alive" bash -c '
  status=$(docker inspect autonomous-agent-hermes-1 --format "{{.State.Status}}" 2>/dev/null)
  [ "$status" = "running" ] || { echo "status=$status"; exit 1; }
'

echo
if [ "$failures" -gt 0 ]; then
  echo "❌ $failures smoke check(s) failed"
  exit 1
fi
echo "✅ All 7 smoke checks passed"
