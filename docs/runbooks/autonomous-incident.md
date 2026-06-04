# Autonomous Agent — Incident Response Runbook

**Audience**: On-call operator.
**Scope**: Runaway loops, bad merges, overspend, stuck fan-out.
**Kill-switch SLO**: Halt + token revocation within **30 seconds** of the /panic trigger.

---

## Trigger the kill-switch

### Via shell (preferred — no network dependency)

```bash
bash scripts/panic.sh "reason: <short description>"
```

Writes the HALT sentinel, sweeps in-flight WorkspaceSessions, revokes the GitHub App
installation token, and pauses the hermes container.

### Via Telegram (operator-only, requires bot configured)

Send `/panic` to the bot chat.  The FastAPI webhook routes it to `KillSwitch.trigger()`.
Same effect as `panic.sh`.

---

## Incident scenarios

### 1. Runaway loop

**Symptoms**: Same tool call repeated N times (F34 LoopDetector should auto-trip, but
escalate here if it hasn't).

**Immediate actions**:
1. `bash scripts/panic.sh "runaway loop: <thread_id>"`
2. Check `logs/panic.log` — sentinel timestamp recorded.
3. Inspect `git worktree list` — all `aa-ws-*` entries should be gone (reaper swept them).
4. Confirm GitHub App token revoked: check Splunk/OTel for new `gh` spans — should be 0.

**Recovery**:
1. Investigate the loop root cause in the graph trace (Phoenix/Langfuse).
2. `ks.clear()` or `rm /tmp/aa-kill-switch` to re-enable the process.
3. File a post-mortem (see `docs/postmortem_template.md`).

---

### 2. Bad merge / agent wrote dangerous code to a PR

**Symptoms**: CI passes on a PR that shouldn't — eval gate miss, or agent opened a PR
that is not safe to merge.

**Immediate actions**:
1. Close the PR on GitHub: `gh pr close <number> --comment "Emergency close — under review"`
2. If already merged: `gh pr create --base main --head <bad-sha>^1` with a revert.
3. If agent is actively pushing: `bash scripts/panic.sh "bad merge: PR <number>"`

**Recovery**:
1. Verify the reverted commit reaches the required checks.
2. Root-cause: was the eval gate (SP-06) configured correctly?  Was the `required_status_checks`
   list complete?

---

### 3. Overspend

**Symptoms**: `cost_accumulator` exceeds `SPINE_BUDGET_USD`; or LiteLLM F21 daily watchdog
wrote `/data/HALT_F21`.

**Immediate actions**:
1. Check `SPINE_BUDGET_USD` env var — if set, the per-graph admission check should have blocked.
   If it didn't, `bash scripts/panic.sh "overspend: cost exceeded budget"`.
2. Check `/data/HALT_F21` exists → daily budget hit.  Rotate the cap before resuming.
3. Check LiteLLM proxy logs: `docker compose logs litellm-proxy | grep budget`.

**Recovery**:
1. Confirm root cause: which model/graph consumed the spend?
2. Raise `SPINE_BUDGET_USD` or reduce model tier for affected tasks.
3. Clear sentinel: `rm /tmp/aa-kill-switch` (and `/data/HALT_F21` if needed).

---

### 4. Stuck fan-out

**Symptoms**: Fan-out wave dispatched N tasks; fewer than N completed; SP-27 mid-flight monitor
hasn't fired; operator is waiting.

**Immediate actions**:
1. Check `git worktree list | grep aa-ws-` — list in-flight nodes.
2. Check graph state: `OTEL_TRACES_EXPORTER=none uv run --extra dev python -c "
   from app.adapters.inmemory.checkpointer import InMemoryCheckpointer
   from app.core.spine_runner import SpineRunner
   r = SpineRunner(InMemoryCheckpointer())
   print(r.get_state('<thread_id>'))"`
3. If hanging mid-fan-out: `bash scripts/panic.sh "stuck fan-out: <thread_id>"`
   The reaper sweeps all `aa-ws-*` worktrees.

**Recovery**:
1. Investigate which node hung (check OTel spans for the last tool call).
2. Resume the graph after fixing the stuck node:
   ```bash
   uv run --extra dev python -c "
   import asyncio
   from app.adapters.inmemory.checkpointer import InMemoryCheckpointer
   from app.core.spine_runner import SpineRunner
   r = SpineRunner(InMemoryCheckpointer())
   asyncio.run(r.resume(thread_id='<t>', interrupt_id='<iid>', decision='abort'))"
   ```

---

## Post-panic recovery checklist

- [ ] HALT sentinel cleared: `rm /tmp/aa-kill-switch`
- [ ] All `aa-ws-*` worktrees removed: `git worktree list | grep aa-ws-`
- [ ] GitHub App token re-issued (installation token auto-rotates in ≤1 hr, or revoke+re-auth)
- [ ] Hermes container resumed: `docker compose unpause hermes`
- [ ] Panic log entry recorded: `cat logs/panic.log`
- [ ] Post-mortem filed

---

## Measuring the SLO

`panic.sh` records start + end timestamps to `logs/panic.log`. The SLO is ≤30 s from
trigger to sentinel written + reaper sweep + token revoked.

Hermetic test: `pytest tests/unit/test_sp_ir1_kill_switch.py::TestSLO`
