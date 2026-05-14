#!/usr/bin/env bash
# Post-deploy smoke test. Exits non-zero on any failure.
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
COMPOSE="docker compose -f $ROOT/deploy/docker-compose.yml -f $ROOT/deploy/docker-compose.dev.yml"

failures=0
check() {
  local name="$1"
  shift
  if "$@" >/tmp/smoke.log 2>&1; then
    echo "✓ $name"
  else
    echo "✗ $name"
    cat /tmp/smoke.log | sed 's/^/    /'
    failures=$((failures+1))
  fi
}

echo "Smoke test 1/9: all containers healthy"
check "containers running" $COMPOSE ps --status running --quiet

echo "Smoke test 2/9: hermes-agent → chroma reachable"
check "agent → chroma" $COMPOSE exec -T hermes-agent \
  python -c "import httpx; r=httpx.get('http://chroma:8000/api/v2/heartbeat', timeout=5); assert r.status_code==200"

echo "Smoke test 3/9: hermes-agent → litellm reachable"
check "agent → litellm" $COMPOSE exec -T hermes-agent \
  python -c "import httpx; r=httpx.get('http://litellm-proxy:4000/health/liveliness', timeout=5); assert r.status_code==200"

echo "Smoke test 4/9: egress allowlist works (Telegram)"
TG_TOKEN=$(grep TELEGRAM_BOT_TOKEN "$ROOT/secrets/telegram.env" | cut -d= -f2)
check "egress allowed (TG getMe)" $COMPOSE exec -T hermes-gateway \
  curl -fsS "https://api.telegram.org/bot${TG_TOKEN}/getMe"

echo "Smoke test 5/9: shell-sandbox cannot reach external network"
check "egress denied (shell-sandbox)" bash -c "
  ! docker exec \$($COMPOSE ps -q shell-sandbox) curl -fsS --max-time 3 https://example.com 2>/dev/null
"

echo "Smoke test 6/9: real LLM round-trip"
check "real LLM call via litellm" $COMPOSE exec -T hermes-agent \
  python -c "
import httpx, os
master_key = open('/run/secrets/litellm_master_key').read().strip()
r = httpx.post('http://litellm-proxy:4000/v1/chat/completions',
               headers={'Authorization': f'Bearer {master_key}'},
               json={'model': 'vertex_ai/claude-opus-4-7',
                     'messages': [{'role':'user','content':'Reply with the single word: pong'}],
                     'max_tokens': 10},
               timeout=30)
assert r.status_code == 200, r.text
out = r.json()['choices'][0]['message']['content']
print('LLM said:', out)
assert 'pong' in out.lower(), f'Expected pong, got: {out}'
"

echo "Smoke test 7/9: memory write persists across container restart"
$COMPOSE exec -T hermes-agent bash -c "echo 'TEST_TOKEN_$(date +%s)' > /data/.smoke-test-marker"
$COMPOSE restart hermes-agent
sleep 5
check "data persists across restart" $COMPOSE exec -T hermes-agent \
  bash -c "test -f /data/.smoke-test-marker && grep -q TEST_TOKEN /data/.smoke-test-marker"
$COMPOSE exec -T hermes-agent rm /data/.smoke-test-marker

echo "Smoke test 8/9: OTel traces visible in Phoenix within 30s"
check "trace visible in Phoenix" bash -c "
  for i in {1..6}; do
    if curl -fsS http://localhost:6006/v1/traces 2>/dev/null | grep -q hermes-agent; then exit 0; fi
    sleep 5
  done
  exit 1
"

echo "Smoke test 9/9: limits.yaml validates"
check "limits.yaml valid" bash -c "cd $ROOT && source .venv/bin/activate && python -m lib.limits_validator config/limits.yaml"

echo
if [ "$failures" -gt 0 ]; then
  echo "❌ $failures smoke check(s) failed"
  exit 1
fi
echo "✅ All 9 smoke checks passed"
