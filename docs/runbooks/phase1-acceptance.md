# Phase 1 Acceptance Protocol

## Prerequisites
- `./scripts/bootstrap.sh` completes cleanly
- `./scripts/smoke.sh` passes all 9 checks
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

Open http://localhost:6006. Filter for service.name=hermes-agent. Inspect at least one trace from your conversation; verify spans for `turn.start`, `model.call`, `tool.dispatch`.

### Step 5 — Verify no secret leaks

```bash
docker compose -f deploy/docker-compose.yml exec -T hermes-agent test -f /data/secret-leak-attempts.log && \
  cat /data/secret-leak-attempts.log
```

Expected: file does not exist OR is empty (no `[REDACTED:critical]` entries).

### Step 6 — Verify budget tracking

```bash
docker compose -f deploy/docker-compose.yml exec -T litellm-proxy curl -fsS \
  -H "Authorization: Bearer $(cat /run/secrets/litellm_master_key)" \
  http://localhost:4000/spend/calculate
```

Expected: JSON with non-zero `total_spend` reflecting your 10 messages, well under daily cap.

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
