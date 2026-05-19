# Final Sweep — Audit Plan (2026-05-18, pre-acceptance)

**Companion to:** `findings.md` in this directory.
**Decision required:** which items to address BEFORE Phase 1 acceptance walk-through + promotion to `main`.

---

## Severity legend

- 🔴 **P0 — blocks Phase 1 acceptance integrity**: walk-through would pass but for the wrong reasons.
- 🟠 **P1 — should fix; risks confusion or follow-up noise**.
- 🟡 **P2 — known deferral; can ship without addressing**.
- 🟢 **P3 — cosmetic, do whenever**.

---

## P0 items (must address before promotion is meaningful)

### P0-α · Wire our `lib/*` into the hermes container so plugins actually load

**Gap.** Per finding #1: the hermes service in `deploy/docker-compose.yml` does NOT mount `./lib:/app/lib:ro`; the Dockerfile does NOT copy our `lib/`; PYTHONPATH does NOT include it; container logs show NO plugin load. The entire Phase 1 enhancement layer (P1-1 through P1-6) is inert.

**Fix options** (pick one):

**Option 1 — Mount + PYTHONPATH (minimal, matches escalation-watcher pattern):**
Edit `deploy/docker-compose.yml` hermes service:
```yaml
  hermes:
    # ... existing config ...
    volumes:
      # existing mounts plus:
      - ./lib:/app/lib:ro
      - ./scripts:/app/scripts:ro
    environment:
      PYTHONPATH: /app:/app/lib
      # ... other env ...
```
Then verify Hermes' plugin discovery picks up our modules. Per `hermes-agent/AGENTS.md:465-489`, Hermes auto-discovers plugins — verify the discovery path includes `/app/lib`.

Effort: 30-45 min including verification.

**Option 2 — Package lib/ as installable + install in Dockerfile:**
Add to repo root `pyproject.toml` an entry point for each plugin module, then in Dockerfile after the upstream Hermes install:
```dockerfile
COPY pyproject.toml /app/aa-plugins/pyproject.toml
COPY lib /app/aa-plugins/lib
RUN cd /app/aa-plugins && uv pip install --system --no-cache -e .
```

Effort: 1-2 hours.

**Option 3 — Hermes config-driven plugin list:**
If Hermes accepts a `plugins:` list in `config/hermes/cli-config.yaml`, add our module paths. Requires reading Hermes' config schema (need Pass 2 to confirm).

**Recommendation:** Option 1 (mount + PYTHONPATH). Mirrors the escalation-watcher pattern already in place; smallest diff; quickest to verify.

**Verification after fix:**
```bash
docker exec autonomous-agent-hermes-1 ls /app/lib   # should show our modules
docker exec autonomous-agent-hermes-1 python -c "from lib.durability import register; print(register)"   # should import
docker logs autonomous-agent-hermes-1 2>&1 | grep -iE "register|plugin"   # should show load activity
```

---

## P1 items (should fix; cheap wins)

### P1-α · Healthcheck cron PATH fix (closes spam from #38/#39 + prevents #40, #41, …)

**Gap.** Per finding #2: `scripts/healthcheck-ping.sh` line 32 calls `docker compose` but cron has empty PATH; script always fails with `docker: command not found`. PR #37 fixed the service name but not the PATH.

**Fix.** Add to `scripts/healthcheck-ping.sh` at the top (after shebang):
```bash
export PATH=/usr/local/bin:/usr/bin:/bin:$PATH
```

Effort: 5 min + 1 PR. Verify by re-running script in a stripped env: `env -i HOME=$HOME ./scripts/healthcheck-ping.sh`.

After fix, close issues #38 and #39 (similar root cause to #29).

### P1-β · P1-5 hook bodies — decide: fill, or formally defer?

**Gap.** Per finding #3: `lib/kanban/__init__.py:34-73` hook bodies are TODO stubs. Auto-card-on-message and status-change→Telegram don't work. Compounded by `lib/durability/escalation.py` `emit_escalation` being a `print()` stub.

**Options:**
- (a) Fill the hook bodies now (requires P1-1 session-metadata API access — likely 2-3 hours).
- (b) Formally defer to Phase 2 with a tracked issue. Update GO report footnote to make this explicit ("auto-card flow deferred to Phase 2").
- (c) If fix to P0-α also resolves session-metadata API exposure (Hermes' ctx now reaches our code), filling becomes trivial — recheck.

**Recommendation:** (b) defer with tracking. Phase 1 acceptance criteria don't require auto-cards.

---

## P2 items (known deferrals; flag in tracking)

### P2-α · R4 integration test (hook ordering at runtime)

**Gap.** `tests/integration/test_p1_3_resume_then_p1_4_inject.py` was specified in spec §9 R4 mitigation but never created.

**Fix.** After P0-α is resolved (so the hooks actually run in the container), add this integration test. Until P0-α is resolved, the test would be a no-op anyway (hooks don't run).

### P2-β · OTel spans in `lib/evaluators/judge.py`

**Gap.** Per finding #5: 0 hits for tracer/span/trace in judge.py. Spec §9 R6 deferred this.

**Fix.** Add `span.start_as_current("evaluator.dispatch")` around judge calls. ~10 lines. Skip until P0-α resolved (same logic as above).

### P2-γ · consensus.py 3-strike tracker integration

**Gap.** Per finding #7: `record_rejection_for_fingerprint` exists but no caller. Skip until P0-α resolved + evaluator orchestration wired.

---

## P3 items (cosmetic)

### P3-α · Session-coordination ledger status

**Gap.** Per finding #6: rows for C/D/E still say `in-flight`. Should be `done`.

**Fix.** Edit `docs/superpowers/session-coordination.md` lines 50-56; flip status field. 1 PR, 3 lines. Effort: 5 min.

---

## Disposition matrix — decisions needed from user

| Item | Severity | Recommended action | Time |
|---|---|---|---|
| P0-α (lib/* not loading) | 🔴 | FIX before promotion (Option 1 — mount + PYTHONPATH) | 30-45 min |
| P1-α (healthcheck PATH) | 🟠 | Fix in a small PR | 5 min |
| P1-β (P1-5 hook stubs) | 🟠 | Formally defer to Phase 2 with tracked issue | 10 min (doc + issue) |
| P2-α (R4 runtime test) | 🟡 | Defer; add post-P0-α | — |
| P2-β (judge.py OTel spans) | 🟡 | Defer (already documented) | — |
| P2-γ (3-strike tracker caller) | 🟡 | Defer (depends on P0-α) | — |
| P3-α (ledger status) | 🟢 | Bundle with any other doc PR | 5 min |

---

## Critical question for the user

**Promoting to `main` + tagging `phase1-accepted` is now contingent on P0-α.** Without resolving the plugin-loading gap, the tag would certify a system that runs upstream Hermes with our 12 PRs of code attached but never executed. The acceptance walk-through would superficially PASS but for the wrong reasons.

Three paths forward:

1. **Resolve P0-α first** (~30-45 min), then re-verify (smoke + restart hermes + check plugin loads), then re-issue GO report, then user walks acceptance. Clean Phase 1.
2. **Promote as-is + acknowledge limitation**: tag as `phase1-accepted` but explicitly document in release notes that "plugin loading remains a Phase 2 task; current acceptance verifies infrastructure not behavior." Faster but misleading.
3. **Defer promotion**: keep `phase/1-completion` open; treat finding P0-α as Phase 1.5 work; revisit after.

Recommendation: **Path 1**.

---

## Changes from Pass 1

Pass 2 dispatched one Opus subagent to investigate the Hermes plugin discovery mechanism (the critical unknown from Pass 1 P0-α). The answer is now fully pinned down — and reshapes the recommended fix.

### Hermes plugin discovery — definitive mechanism (verified via `hermes-agent/hermes_cli/plugins.py`)

Hermes' `PluginManager` scans **four sources** in order at startup (line 747-905):
1. **Bundled** — `<hermes_repo>/plugins/<name>/` (env-overridable via `HERMES_BUNDLED_PLUGINS`)
2. **User** — `~/.hermes/plugins/<name>/`
3. **Project** — `./.hermes/plugins/<name>/` (requires `HERMES_ENABLE_PROJECT_PLUGINS=1`)
4. **Pip entry points** — packages exposing the `hermes_agent.plugins` entry-point group

**Hard requirements per plugin** (line 967-1014, 1141-1147):
- A `plugin.yaml` manifest in the plugin's directory
- An `__init__.py` exposing a callable `register(ctx)`

**Critical opt-in gate** (line 826-905): even after discovery, standalone plugins ONLY load when their key appears in `plugins.enabled:` in `config.yaml`. There is NO PYTHONPATH/extra_modules shortcut.

### What our `lib/*` is missing — 3 things, in order

| # | Missing | Where |
|---|---|---|
| 1 | `/lib/*` directories not mounted to a Hermes-recognized scan path | `deploy/docker-compose.yml` hermes service |
| 2 | `plugin.yaml` manifest file per module | `lib/{anchors,evaluators,durability,memory,kanban}/plugin.yaml` |
| 3 | `plugins.enabled:` allow-list | `config/hermes/cli-config.yaml` (currently no `plugins:` section) |

### REVISED P0-α — replaces the Pass 1 "Option 1" recommendation

**Option A (compose-only env override)** — REJECTED in Pass 2: `HERMES_BUNDLED_PLUGINS` would replace the upstream bundled root and lose every Hermes-shipped plugin.

**Option B (user-plugins mount + manifests + allow-list)** — RECOMMENDED:

Step 1. Create 5 `plugin.yaml` manifest files:
```yaml
# lib/durability/plugin.yaml (analogous for anchors, evaluators, memory, kanban)
name: durability
version: 0.1.0
description: "Failure-matrix retry, checkpoint-resume, REJECTED inject"
kind: standalone
provides_hooks: [pre_tool_call, post_tool_call, on_session_start]
```

Step 2. Add 5 volume mounts to hermes service in `deploy/docker-compose.yml`:
```yaml
  hermes:
    # ... existing config ...
    volumes:
      # ... existing mounts plus:
      - ../lib/anchors:/root/.hermes/plugins/anchors:ro
      - ../lib/evaluators:/root/.hermes/plugins/evaluators:ro
      - ../lib/durability:/root/.hermes/plugins/durability:ro
      - ../lib/memory:/root/.hermes/plugins/memory:ro
      - ../lib/kanban:/root/.hermes/plugins/kanban:ro
```
(Paths are `../lib/...` because the compose file lives in `deploy/`.)

Step 3. Add `plugins:` block to `config/hermes/cli-config.yaml`:
```yaml
plugins:
  enabled: [anchors, evaluators, durability, memory, kanban]
```

Step 4. Optionally set `HERMES_PLUGINS_DEBUG=1` env in the hermes service for the first verification (line 909 prints discovery summary).

**Option C (Dockerfile + entry-points)** — requires rebuild + adding `[project.entry-points."hermes_agent.plugins"]` to `pyproject.toml`. Heavier; saved for if Option B turns out insufficient.

### Verification after Option B

```bash
docker compose -f deploy/docker-compose.yml up -d --force-recreate hermes
sleep 20
docker logs autonomous-agent-hermes-1 2>&1 | grep -iE "plugin discovery|register|enabled"
# Expected: "Plugin discovery complete: 5 found, 5 enabled" (per plugins.py:909)
# If HERMES_PLUGINS_DEBUG=1, per-plugin load trace + register() invocation logs.

docker exec autonomous-agent-hermes-1 ls /root/.hermes/plugins/
# Expected: anchors evaluators durability memory kanban
```

### Effort estimate (revised)

- Manifests (5 files): 15 min — content is mostly identical per module
- Compose edits: 5 min
- cli-config.yaml: 2 min
- Verify in running container: 10-20 min
- **Total: ~45-60 min** for one fix PR

### Secondary concern not in Pass 1

Pass 2 also noted: `lib/memory/__init__.py` mentions memory-related concepts. Hermes auto-coerces plugin `kind` to `exclusive` if `__init__.py` references `MemoryProvider` / `register_memory_provider` (line 1036-1050). Our `lib/memory/__init__.py` registers `/forget` + `/rejections` slash commands — should NOT trigger this coercion, but verify the manifest's `kind: standalone` is respected.

### Still STILL OPEN

🔴 P0-α is now precisely scoped — recommended fix is one ~45-60 min PR with the Option B steps above. All other findings (P1-α through P3-α) unchanged.

### Even after P0-α: acceptance walk-through limitation

The current `docs/runbooks/phase1-acceptance.md` 7-step walk-through does NOT explicitly verify that our specific enhancements (clarification loop, judge panel, checkpointing, REJECTED.md inject, Kanban-card-on-message) are running. A passing acceptance after the fix would prove "the agent works" but not "our enhancements work." Adding 2-3 enhancement-specific verification steps to the acceptance runbook would close that gap — but is itself Phase 2 work.
