# Phase 1 Acceptance Protocol

## Prerequisites
- `./scripts/bootstrap.sh` completes cleanly
- `./scripts/smoke.sh` passes all checks
- Phoenix at http://localhost:6006 is reachable
- Telegram bot reachable (you can `/start` it)

## Acceptance steps

### Step 1 — Send 10 real Telegram messages spanning ≥3 distinct task types

Send these from your phone, one at a time, waiting for full reply each time:

1. "What can you do?"
2. "Search for files containing 'TODO' in the workspace"
3. "What's the latest open PR in NousResearch/hermes-agent?"
4. "Run `df -h` and tell me how much disk is free"
5. "Read the README.md in this project and summarize it in 2 sentences"
6. "Look up the Vite 5 documentation for environment variables and explain how to set one"
7. "List your installed skills"
8. "Tell me my MEMORY.md contents"
9. "Summarize what we've talked about so far"
10. "Create a quick reference for how to deploy a Cloud Run service"

**Tasks 2–6 should each invoke distinct tools** (file search, github MCP, shell sandbox, file read, context7 MCP).

### Step 2 — Verify autonomous skill creation

```bash
docker compose -f deploy/docker-compose.yml exec -T hermes-agent ls /app/skills
```

Expected: At least one skill directory autonomously created from the conversations above (likely from message #10 which is a "create a procedure" prompt).

### Step 3 — Verify state persists across container restart

```bash
docker compose -f deploy/docker-compose.yml restart hermes-agent
sleep 10
```

From Telegram: "What did we just talk about?"

Expected: Bot summarizes the prior 10-message conversation.

### Step 4 — Verify traces visible in Phoenix

Open http://localhost:6006. The default Phoenix project is named `default` —
all spans land there (collector → Phoenix is single-tenant; per-service
projects are a Phase 2 routing-exporter task).

Inspect at least one recent trace from your conversation; verify the
following span names appear with their expected attributes:

- **`turn.start`** — emitted by the `observability` plugin (`lib/observability/__init__.py`)
  on `on_session_start`. Attributes: `model`, `platform`, `session.id`.
- **`model.call`** — emitted on `pre_llm_call` -> `post_llm_call`.
  Attributes: `session.id`, `model`, `platform`, `is_first_turn`,
  `response.length`.
- **`tool.dispatch`** — emitted on `pre_tool_call` -> `post_tool_call`
  (when the LLM invokes a tool). Attributes: `tool.name`, `session.id`,
  `duration_ms`, and `error.type` if the tool raised.
- LiteLLM-emitted spans: `Received Proxy Server Request`, `proxy_pre_call`,
  `router`, `raw_gen_ai_request`, `self`. These come from the proxy
  callback (`callbacks: ["otel"]` in `deploy/litellm/config.yaml`) and
  cover the LLM API leg.

The OTel resource attribute `service.name` is set to `hermes-agent` for
spans emitted by the agent (configured in
`lib/observability/otel_setup.py`); LiteLLM-emitted spans carry their own
service name. Phoenix does not surface resource attrs directly in its
default span listing — to confirm, click any span to open the detail
panel and look under "Resource".

Quick scriptable check (returns the unique span names in the most recent 200):

```bash
curl -sS -X POST http://localhost:6006/graphql \
  -H "Content-Type: application/json" \
  -d '{"query":"{ projects { edges { node { spans(first:200, sort:{col:startTime, dir:desc}) { edges { node { name } } } } } } }"}' \
  | python3 -c "
import json, sys
spans = json.load(sys.stdin)['data']['projects']['edges'][0]['node']['spans']['edges']
print('Unique span names:', sorted(set(s['node']['name'] for s in spans)))
"
```

Expected output to include `turn.start`, `model.call`, and (if the
conversation invoked tools) `tool.dispatch`.

### Step 5 — Verify no secret leaks

```bash
docker compose -f deploy/docker-compose.yml exec -T hermes-agent test -f /data/secret-leak-attempts.log && \
  cat /data/secret-leak-attempts.log
```

Expected: file does not exist OR is empty (no `[REDACTED:critical]` entries).

### Step 6 — Verify budget tracking

The LiteLLM proxy is backed by a Postgres sidecar (`litellm-db`) that
persists spend history (issue #55 wired this up). Query the
`/global/spend/report` endpoint for a per-day total:

```bash
START="$(date -u +%Y-%m-%d)"
END="$(date -u -v+1d +%Y-%m-%d 2>/dev/null || date -u -d '+1 day' +%Y-%m-%d)"
docker compose -f deploy/docker-compose.yml exec -T litellm-proxy curl -fsS \
  -H "Authorization: Bearer $(cat /run/secrets/litellm_master_key)" \
  "http://localhost:4000/global/spend/report?start_date=${START}&end_date=${END}&group_by=team"
```

Expected: JSON array with non-zero `spend` reflecting your 10 messages,
well under the daily cap.

> Note: the older runbook called `GET /spend/calculate`, which is the
> wrong endpoint AND the wrong method. `/spend/calculate` is `POST` and
> computes the *hypothetical* cost of a single request payload (model +
> messages), not the *historical* total — useful for pre-flight cost
> estimates, not acceptance verification. `/global/spend/report` is the
> endpoint that aggregates `LiteLLM_SpendLogs` over a date range.
> For raw per-request logs you can also call
> `GET /spend/logs?start_date=…&end_date=…`.

## Pass criteria

ALL of the following must be true:

- [ ] All 10 messages got coherent replies
- [ ] At least 3 distinct tools were invoked across the 10 messages
- [ ] At least 1 skill was autonomously created
- [ ] State persisted across hermes-agent restart
- [ ] Traces visible in Phoenix
- [ ] No critical entries in secret-leak-attempts.log
- [ ] Daily spend recorded in LiteLLM, well under $100 cap

If all pass: **Phase 1 ACCEPTED**. Ready to begin Phase 2 plan.
If any fail: open `docs/runbooks/recovery.md` and debug; re-run after fix.
