# Google Antigravity Agent Briefing — Tasks B + C (Wave 1.5)
**Date:** 2026-05-25
**For:** Google Antigravity IDE (Gemini 3.1 Pro Preview)
**Priority:** HIGH — Task B unblocks Phase 0a soak monitoring; Task C unblocks SA5 Wave 3 e2e demo
**Collision boundary:** You own `terraform/phase-0a-gcp/` (GCP apply only) and `app/a2a_canary/` (new directory). Do NOT touch `lib/a2a/` (Claude SA4/SA5's territory).

---

## 1. Project Context

**AutonomousAgent** runs ~10 Docker containers on a GCE VM (`autonomousagent-vm`) in GCP project `autonomous-agent-2026`. The A2A (Agent-to-Agent) spike is in Wave 2 — server.py integration (Days 4-7) just landed. Wave 3 (Days 8-10, agent_card.py + e2e demo) launches next.

**Your two tasks are independent and can run in sequence (B first, then C).**

---

## 2. Task B — Apply Terraform Monitoring Module (~20 min)

### What and why

`terraform/phase-0a-gcp/monitoring.tf` is already written and committed. It creates:
- An email notification channel (`manzela@tngshopper.com`)
- A log-based metric `autonomousagent/watchdog_restart_triggered` — counts watchdog restart events from Cloud Logging (gcplogs driver captures all Docker container logs)
- An alert policy: fires on ANY watchdog restart event (threshold=0, fires immediately)
- An alert policy: fires if VM uptime drops to <1 (VM stopped or preempted) for 5 minutes

The VM has **no external IP**, so HTTP uptime checks are impossible. This log-based approach is the correct alternative.

### Context to read first

1. Run `cat terraform/phase-0a-gcp/monitoring.tf` — read the full file to understand what will be created
2. Run `gcloud monitoring notification-channels list --project=autonomous-agent-2026` — check if the email channel already exists (avoid duplicate)
3. Run `gcloud logging metrics list --project=autonomous-agent-2026` — check if `hermes-container-restarts` metric exists (was created manually in a prior session)
4. Run `terraform state list` from `terraform/phase-0a-gcp/` — see what's already in state

### Guiding questions to resolve before applying

1. **Does the email notification channel already exist?** If `gcloud monitoring notification-channels list` shows one with display name `autonomousagent-email-alert`, Terraform will import it. Otherwise it will create it.

2. **Does the log-based metric conflict with the manually created `hermes-container-restarts` metric?** The manually created metric (from the prior Gemini session) has a DIFFERENT name than what monitoring.tf creates (`autonomousagent/watchdog_restart_triggered`). Check: `gcloud logging metrics list --project=autonomous-agent-2026`. If both exist, that's fine — they're different metrics. The terraform-managed one is properly scoped.

3. **Is the `hermes_watchdog_restart_triggered` log message the right filter?** Check the watchdog script: `cat scripts/vm-bootstrap/hermes-watchdog.sh | grep "msg\|restart"`. The filter in monitoring.tf looks for `jsonPayload.msg="hermes_watchdog_restart_triggered"` — verify this matches what the watchdog actually emits.

4. **Does the google_project_service.enabled resource exist in state?** The monitoring.tf `depends_on = [google_project_service.enabled]`. Run `terraform state list | grep project_service` to confirm. If it doesn't exist, remove the `depends_on` line before applying (monitoring APIs are already enabled).

### Execution steps

```bash
cd "/Users/danielmanzela/RX-Research Project/AutonomousAgent/terraform/phase-0a-gcp"

# Step 1: Init (backend already configured to autonomous-agent-2026-tfstate)
terraform init -reconfigure

# Step 2: Plan — preview what will be created
terraform plan -var "project_id=autonomous-agent-2026" -out monitoring-tfplan 2>&1 | tee /tmp/monitoring-plan.txt

# Step 3: Review the plan output — expect 3-4 resources to be added
# (notification_channel, logging_metric, 2x alert_policy)
# If it shows resources to DESTROY, STOP and investigate before applying.

# Step 4: Apply
terraform apply monitoring-tfplan

# Step 5: Verify
gcloud monitoring notification-channels list --project=autonomous-agent-2026 --filter="displayName=autonomousagent-email-alert"
gcloud logging metrics describe "autonomousagent/watchdog_restart_triggered" --project=autonomous-agent-2026
gcloud alpha monitoring policies list --project=autonomous-agent-2026 --filter="displayName=autonomousagent-watchdog-restart"
gcloud alpha monitoring policies list --project=autonomous-agent-2026 --filter="displayName=autonomousagent-vm-down"
```

### Expected outcomes

| Resource | Verification command | Expected result |
|----------|---------------------|-----------------|
| Email notification channel | `gcloud monitoring notification-channels list --project=autonomous-agent-2026` | Shows `autonomousagent-email-alert` with type `email` |
| Log-based metric | `gcloud logging metrics describe autonomousagent/watchdog_restart_triggered --project=autonomous-agent-2026` | Returns metric descriptor |
| Watchdog restart alert | `gcloud alpha monitoring policies list --project=autonomous-agent-2026` | Shows `autonomousagent-watchdog-restart` ENABLED |
| VM down alert | same | Shows `autonomousagent-vm-down` ENABLED |

### What NOT to do

- Do NOT modify `monitoring.tf` unless the plan shows an error — the file is production-ready
- Do NOT run `terraform destroy`
- Do NOT apply any other terraform modules (root, model-armor, postgres) in this session — only `monitoring.tf` additions
- Do NOT create new GCP resources in `i-for-ai`

### Acceptance criteria

```bash
# All of these must succeed (exit 0):
gcloud logging metrics describe "autonomousagent/watchdog_restart_triggered" --project=autonomous-agent-2026 > /dev/null && echo "METRIC OK"
gcloud alpha monitoring policies list --project=autonomous-agent-2026 --format="value(displayName)" | grep "autonomousagent-watchdog-restart" && echo "ALERT OK"
gcloud alpha monitoring policies list --project=autonomous-agent-2026 --format="value(displayName)" | grep "autonomousagent-vm-down" && echo "VM ALERT OK"
```

---

## 3. Task C — Minimal Canary Peer FastAPI App (~45 min)

### What and why

`deploy/docker-compose.canary.yml` (created by AG2) starts a container with `HERMES_A2A_CANARY_MODE=true` using the hermes Docker image. But hermes doesn't implement canary mode — the env var is a stub. SA5 (Wave 3) needs a real canary peer that responds to A2A JSON-RPC calls for the Day 9-10 e2e demo.

**Create `app/a2a_canary/main.py`** — a minimal standalone FastAPI application (~80 lines) that implements the A2A JSON-RPC protocol methods the demo needs. This does NOT modify hermes; it's a separate tiny service.

Then update `deploy/docker-compose.canary.yml` to use this standalone app instead of the hermes image.

### Context to read first

1. `cat lib/a2a/server.py` — read the existing A2A server implementation to understand the JSON-RPC protocol shape (message/send, message/stream, tasks/get, tasks/cancel, /health endpoint)
2. `cat deploy/docker-compose.canary.yml` — read the current canary compose to understand port mapping and network
3. `cat audit/2026-05-21-a2a-spike-plan/spike-plan.md` — search for "Day 9" and "canary peer" to understand the acceptance criteria
4. `cat deploy/docker-compose.yml` — understand the network name used by the main stack

### Guiding questions

1. **What network should the canary join?** The main compose uses a network named `internal` (from `deploy/docker-compose.yml`). The canary compose references `deploy_internal` as `external: true`. Verify the network name matches: `docker network ls | grep internal`.

2. **What A2A methods must the canary implement?** From spike-plan.md Day 9 and the e2e demo: at minimum `message/send` (returns SUBMITTED task), `message/stream` (SSE with 3 events). `/health` endpoint is also required for the healthcheck.

3. **Should the canary be a Docker image or a Python script?** Use a `python:3.12-slim` base image with inline Dockerfile in the compose file (using `build: context:`). This avoids pushing to Artifact Registry and makes it self-contained.

4. **What should the echo behavior look like?** The canary receives a JSON-RPC `message/send` and returns a synthetic Task with status SUBMITTED. For `message/stream`, it emits 3 SSE events (WORKING, artifact_added, COMPLETED) with a 0.5s delay between them. This "echo+delay" behavior is enough for the e2e demo.

### Implementation

**Step 1: Create `app/a2a_canary/__init__.py`** (empty)

**Step 2: Create `app/a2a_canary/main.py`:**

```python
"""A2A canary peer — minimal echo+delay FastAPI app for Day 9 e2e demo.

Implements the A2A JSON-RPC 2.0 protocol surface needed by the spike:
  POST /         — JSON-RPC dispatcher (message/send)
  POST /stream   — SSE streaming (message/stream, tasks/subscribe)
  GET  /health   — liveness probe

Behavior: echo the inbound message back with a synthetic Task. SSE routes
emit 3 events with a 0.5s delay to simulate real agent processing.

Usage (via docker compose):
  docker compose -f deploy/docker-compose.canary.yml up -d
  curl -X POST http://localhost:9002/ -H "Content-Type: application/json" \
    -d '{"jsonrpc":"2.0","id":1,"method":"message/send",
         "params":{"message":{"role":"USER","parts":[{"text":"ping"}]}}}'
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

app = FastAPI(title="A2A Canary Peer", version="0.1.0-spike")


def _jsonrpc_result(req_id: Any, result: Any) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _jsonrpc_error(req_id: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "agent": "canary"}


@app.post("/")
async def jsonrpc_dispatch(request: Request) -> JSONResponse:
    """JSON-RPC 2.0 dispatcher — handles message/send, returns others as -32004."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(content=_jsonrpc_error(None, -32700, "Parse error"))

    req_id = body.get("id")
    method = body.get("method", "")
    params = body.get("params") or {}

    if method == "message/send":
        task_id = f"canary-task-{uuid.uuid4()}"
        return JSONResponse(content=_jsonrpc_result(req_id, {"id": task_id, "status": "SUBMITTED"}))

    if method in ("tasks/get",):
        task_id = params.get("id", f"canary-task-{uuid.uuid4()}")
        return JSONResponse(content=_jsonrpc_result(req_id, {"id": task_id, "status": "COMPLETED"}))

    if method in ("tasks/cancel",):
        task_id = params.get("id", "unknown")
        return JSONResponse(content=_jsonrpc_result(req_id, {"id": task_id, "status": "CANCELED"}))

    # message/stream and tasks/subscribe are handled via dedicated SSE routes
    return JSONResponse(
        content=_jsonrpc_error(req_id, -32004, f"Use /stream or /subscribe for {method}")
    )


async def _sse_events() -> Any:
    """Emit 3 SSE frames with 0.5s delay — simulates real agent processing."""
    events = [
        {"status": "WORKING"},
        {"artifact_added": True, "artifact": {"type": "text", "content": "canary echo"}},
        {"status": "COMPLETED"},
    ]
    for evt in events:
        yield f"data: {json.dumps(evt)}\n\n"
        await asyncio.sleep(0.5)


@app.post("/stream")
async def stream_endpoint(request: Request) -> StreamingResponse:
    """SSE streaming for message/stream — emits 3 events with 0.5s delay."""
    return StreamingResponse(
        _sse_events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/subscribe")
async def subscribe_endpoint(request: Request) -> StreamingResponse:
    """SSE streaming for tasks/subscribe — same 3 events."""
    return StreamingResponse(
        _sse_events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


if __name__ == "__main__":
    import uvicorn
    port = int(__import__("os").getenv("HERMES_A2A_PORT", "9001"))
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
```

**Step 3: Update `deploy/docker-compose.canary.yml`** to build the canary from `app/a2a_canary/` instead of pulling the hermes image:

Replace the entire file with:
```yaml
# deploy/docker-compose.canary.yml
# A2A Day 9 — minimal canary peer for end-to-end A2A testing.
#
# Builds from app/a2a_canary/main.py — a standalone FastAPI that implements
# message/send, message/stream, tasks/subscribe, /health.
# Does NOT use the hermes image; no HERMES_A2A_CANARY_MODE dependency.
#
# Usage (local):
#   docker compose -f deploy/docker-compose.yml up -d
#   docker compose -f deploy/docker-compose.canary.yml up -d
#
# Canary reachable from main hermes at http://agent-canary:9001/ (internal network)
# Reachable from host at http://localhost:9002/

services:
  agent-canary:
    build:
      context: ../
      dockerfile_inline: |
        FROM python:3.12-slim
        RUN pip install --no-cache-dir fastapi uvicorn
        COPY app/a2a_canary/ /app/
        WORKDIR /app
        CMD ["python", "main.py"]
    container_name: agent-canary
    ports:
      - "9002:9001"
    environment:
      HERMES_A2A_PORT: "9001"
      HERMES_LOG_LEVEL: "INFO"
    networks:
      - internal
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "python3", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:9001/health')"]
      interval: 15s
      timeout: 5s
      retries: 3
      start_period: 10s

networks:
  internal:
    external: true
    name: deploy_internal
```

**Step 4: Verify locally:**
```bash
cd "/Users/danielmanzela/RX-Research Project/AutonomousAgent"

# Verify compose config is valid
docker compose -f deploy/docker-compose.canary.yml config > /dev/null && echo "CONFIG VALID"

# Build the image (does not require running docker compose up)
docker compose -f deploy/docker-compose.canary.yml build && echo "BUILD OK"

# Quick smoke test (optional — requires docker compose main stack up)
# docker compose -f deploy/docker-compose.canary.yml up -d
# sleep 5 && curl -s http://localhost:9002/health && echo
# curl -s -X POST http://localhost:9002/ -H "Content-Type: application/json" \
#   -d '{"jsonrpc":"2.0","id":1,"method":"message/send","params":{"message":{"role":"USER","parts":[{"text":"ping"}]}}}' | python3 -m json.tool
# docker compose -f deploy/docker-compose.canary.yml down
```

**Step 5: Create PR:**
```bash
git checkout -b feat/a2a-canary-peer
git add app/a2a_canary/__init__.py app/a2a_canary/main.py deploy/docker-compose.canary.yml
git commit -m "feat(deploy): minimal A2A canary peer FastAPI app + update canary compose"
git push -u origin feat/a2a-canary-peer
gh pr create \
  --title "feat(deploy): minimal a2a canary peer — standalone FastAPI echo+delay stub" \
  --body "## Summary
- Creates app/a2a_canary/main.py: ~80-line FastAPI implementing message/send, message/stream (SSE, 3 events with 0.5s delay), /health
- Updates deploy/docker-compose.canary.yml to build from app/a2a_canary/ instead of pulling hermes image (removes HERMES_A2A_CANARY_MODE dependency)
- Canary reachable from main hermes at http://agent-canary:9001/ (deploy_internal network)
- Required by SA5 Wave 3 for Day 9-10 e2e canary demo

## Verification
- docker compose -f deploy/docker-compose.canary.yml config → valid
- docker compose -f deploy/docker-compose.canary.yml build → success

🤖 Generated with Google Antigravity (Gemini 3.1 Pro Preview)"
```

---

## 4. What NOT to do (Non-Negotiable)

| What | Why |
|------|-----|
| Do NOT touch `lib/a2a/` | Claude SA5 owns this for Wave 3 |
| Do NOT touch `terraform/phase-0a-gcp/model-armor/` or `postgres/` | Only apply monitoring additions |
| Do NOT commit plaintext secrets | `detect-secrets` + `gitleaks` block CI |
| Do NOT use `git add -A` | Stage specific files only |
| Do NOT force-push | Never |
| Branch naming | `feat/<desc>` — no dots in `<desc>` |
| PR title | `type(scope): lowercase subject after colon` |
| GCP project | Always `autonomous-agent-2026` — never `i-for-ai` |

---

## 5. Acceptance Criteria Summary

### Task B: Complete when ALL pass:
```bash
gcloud logging metrics describe "autonomousagent/watchdog_restart_triggered" --project=autonomous-agent-2026 && echo "B1 OK"
gcloud alpha monitoring policies list --project=autonomous-agent-2026 --format="value(displayName)" | grep "autonomousagent-watchdog-restart" && echo "B2 OK"
gcloud alpha monitoring policies list --project=autonomous-agent-2026 --format="value(displayName)" | grep "autonomousagent-vm-down" && echo "B3 OK"
```

### Task C: Complete when ALL pass:
```bash
test -f app/a2a_canary/main.py && echo "C1 OK"
docker compose -f deploy/docker-compose.canary.yml config > /dev/null && echo "C2 OK"
docker compose -f deploy/docker-compose.canary.yml build && echo "C3 OK"
curl -s http://localhost:9002/health 2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); assert d['status']=='ok'; print('C4 OK')" || echo "C4 SKIP (stack not running)"
```

---

## 6. Files to Read (in order)

1. `terraform/phase-0a-gcp/monitoring.tf` — Task B: the full HCL you'll apply
2. `scripts/vm-bootstrap/hermes-watchdog.sh` — Task B: verify log message filter
3. `lib/a2a/server.py` — Task C: understand the JSON-RPC protocol shape to mirror
4. `deploy/docker-compose.canary.yml` — Task C: current compose to update
5. `audit/2026-05-21-a2a-spike-plan/spike-plan.md` (§Day 9) — Task C: acceptance criteria for the canary demo
