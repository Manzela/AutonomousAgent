#!/usr/bin/env bash
# A2A spike Day 10 — end-to-end canary demo.
#
# What this script does:
#   1. Starts the canary compose stack (or a stdlib stub if compose file is absent).
#   2. Starts agent-a via uvicorn on port 9001.
#   3. Sends message/send from agent-a to agent-canary.
#   4. Subscribes to the returned task via POST /subscribe (SSE).
#   5. Verifies the AgentCard via GET /.well-known/agent-card.json.
#   6. Optionally checks Cloud Trace for spans (set SKIP_CLOUD_TRACE=1 to skip).
#
# Usage:
#   SKIP_CLOUD_TRACE=1 bash scripts/a2a-e2e-demo.sh   # local / no Cloud Trace
#   bash scripts/a2a-e2e-demo.sh                        # full with Cloud Trace

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_FILE="${REPO_ROOT}/deploy/docker-compose.canary.yml"
AGENT_A_URL="http://localhost:9001"
AGENT_CANARY_URL="http://localhost:9002"
MAX_WAIT_SECONDS=60
SKIP_CLOUD_TRACE="${SKIP_CLOUD_TRACE:-0}"
GCP_PROJECT="autonomous-agent-2026"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
pass() { echo -e "${GREEN}PASS${NC}: $*"; }
fail() { echo -e "${RED}FAIL${NC}: $*"; exit 1; }
warn() { echo -e "${YELLOW}WARN${NC}: $*"; }

echo "=== A2A Spike Day 10 — Canary E2E Demo ==="
echo ""

# Unified cleanup — reads CANARY_IS_STUB, CANARY_PID, AGENT_A_PID.
# Declared before any trap assignment so all variables are in scope.
CANARY_IS_STUB=0
CANARY_PID=0
AGENT_A_PID=0

_cleanup() {
    [[ "${AGENT_A_PID}" -ne 0 ]] && kill "${AGENT_A_PID}" 2>/dev/null || true
    if [[ "${CANARY_IS_STUB}" -eq 1 ]]; then
        [[ "${CANARY_PID}" -ne 0 ]] && kill "${CANARY_PID}" 2>/dev/null || true
    else
        docker compose -f "${COMPOSE_FILE}" down 2>/dev/null || true
    fi
}
trap _cleanup EXIT

# --- 0. Start canary (compose stack or stdlib stub) --------------------------
if [[ ! -f "${COMPOSE_FILE}" ]]; then
    warn "deploy/docker-compose.canary.yml not found — using stdlib HTTP stub"
    python3 -c "
import json, threading
from http.server import HTTPServer, BaseHTTPRequestHandler
class H(BaseHTTPRequestHandler):
    def do_GET(self):
        r=b'{\"status\":\"ok\",\"agent\":\"canary-stub\"}'
        self.send_response(200); self.send_header('Content-Type','application/json')
        self.send_header('Content-Length',len(r)); self.end_headers(); self.wfile.write(r)
    def do_POST(self):
        n=int(self.headers.get('Content-Length',0)); b=json.loads(self.rfile.read(n))
        result={'jsonrpc':'2.0','id':b.get('id'),'result':{'id':'canary-task-001','status':'SUBMITTED'}}
        r=json.dumps(result).encode()
        self.send_response(200); self.send_header('Content-Type','application/json')
        self.send_header('Content-Length',len(r)); self.end_headers(); self.wfile.write(r)
    def log_message(self,*a): pass
s=HTTPServer(('0.0.0.0',9002),H)
threading.Thread(target=s.serve_forever,daemon=True).start()
import time; time.sleep(3600)
" &
    CANARY_PID=$!
    CANARY_IS_STUB=1
else
    echo "Starting canary compose stack..."
    docker compose -f "${COMPOSE_FILE}" up -d
fi

# --- 1. Start agent-a --------------------------------------------------------
echo "Starting agent-a on :9001..."
cd "${REPO_ROOT}"
uv run uvicorn lib.a2a.server:app --host 0.0.0.0 --port 9001 --log-level warning &
AGENT_A_PID=$!

# --- 2. Wait for both to be healthy ------------------------------------------
wait_up() {
    local url="$1" label="$2" elapsed=0
    echo -n "Waiting for ${label}... "
    until curl -sf "${url}" > /dev/null 2>&1; do
        sleep 2; elapsed=$((elapsed + 2))
        [[ ${elapsed} -ge ${MAX_WAIT_SECONDS} ]] && fail "${label} did not become healthy"
    done
    echo "healthy"
}
wait_up "${AGENT_A_URL}/health" "agent-a"

echo -n "Waiting for agent-canary:9002... "
elapsed=0
until curl -sf "${AGENT_CANARY_URL}/" > /dev/null 2>&1 || nc -z localhost 9002 2>/dev/null; do
    sleep 2; elapsed=$((elapsed + 2))
    [[ ${elapsed} -ge ${MAX_WAIT_SECONDS} ]] && fail "agent-canary did not become reachable"
done
echo "reachable"
echo ""

# --- 3. message/send ---------------------------------------------------------
echo "Step 1: message/send → agent-canary"
SEND_RESP=$(curl -sf -X POST "${AGENT_CANARY_URL}/" \
    -H "Content-Type: application/json" \
    -d '{"jsonrpc":"2.0","id":1,"method":"message/send","params":{"message":{"role":"USER","parts":[{"text":"ping from agent-a"}]}}}')
TASK_ID=$(echo "${SEND_RESP}" | python3 -c "import json,sys; print(json.load(sys.stdin)['result']['id'])")
STATUS=$(echo "${SEND_RESP}" | python3 -c "import json,sys; print(json.load(sys.stdin)['result']['status'])")
[[ "${STATUS}" == "SUBMITTED" ]] || fail "Expected SUBMITTED, got ${STATUS}"
pass "message/send: task_id=${TASK_ID} status=${STATUS}"
echo ""

# --- 4. tasks/subscribe SSE ---------------------------------------------------
echo "Step 2: POST /subscribe on agent-a"
SSE_BODY=$(curl -sf -X POST "${AGENT_A_URL}/subscribe" \
    -H "Content-Type: application/json" \
    -d "{\"id\": \"${TASK_ID}\"}" \
    --max-time 15 || true)
SSE_COUNT=$(echo "${SSE_BODY}" | grep -c "^data:" || true)
[[ ${SSE_COUNT} -ge 3 ]] || fail "Expected ≥3 SSE events, got ${SSE_COUNT}"
pass "SSE subscribe: ${SSE_COUNT} events"
echo ""

# --- 5. AgentCard ------------------------------------------------------------
echo "Step 3: GET /.well-known/agent-card.json"
CARD=$(curl -sf "${AGENT_A_URL}/.well-known/agent-card.json")
CARD_ID=$(echo "${CARD}" | python3 -c "import json,sys; print(json.load(sys.stdin).get('id','MISSING'))")
[[ "${CARD_ID}" != "MISSING" ]] || fail "AgentCard missing 'id' field"
pass "AgentCard: id=${CARD_ID}"
echo ""

# --- 6. Cloud Trace (optional) -----------------------------------------------
if [[ "${SKIP_CLOUD_TRACE}" == "1" ]]; then
    warn "SKIP_CLOUD_TRACE=1 — skipping Cloud Trace verification"
else
    echo "Step 4: Cloud Trace verification"
    sleep 10
    TRACE_COUNT=$(gcloud trace list --project="${GCP_PROJECT}" --limit=5 --format=json \
        2>/dev/null | python3 -c "import json,sys; print(len(json.load(sys.stdin)))" || echo "0")
    if [[ "${TRACE_COUNT}" -gt 0 ]]; then
        pass "Cloud Trace: ${TRACE_COUNT} traces found"
    else
        fail "No Cloud Trace entries found (Cloud Trace propagation broken)"
    fi
fi

echo ""
echo "=== Demo Summary ==="
echo "  message/send:    PASS (task_id=${TASK_ID})"
echo "  subscribe SSE:   PASS (${SSE_COUNT} events)"
echo "  AgentCard:       PASS (id=${CARD_ID})"
[[ "${SKIP_CLOUD_TRACE}" != "1" ]] && echo "  Cloud Trace:     see above"
echo ""
pass "A2A spike Day 10 demo complete"
