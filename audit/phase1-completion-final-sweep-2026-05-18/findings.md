# Final Sweep — Findings (2026-05-18, pre-acceptance)

**Branch audited:** `phase/1-completion` @ `e403613`
**Date:** 2026-05-18
**Purpose:** Comprehensive sweep before Phase 1 acceptance walk-through + promotion to `main`. Verify nothing critical is unaddressed.

---

## 1. CRITICAL — Our `lib/*` plugins do NOT load in the running hermes container

**The single biggest finding of this sweep.** The Phase 1 enhanced agent — anchors (P1-1), evaluators (P1-2), durability (P1-6), checkpointing (P1-3), memory/REJECTED.md (P1-4), kanban (P1-5) — is **NOT actually running inside `autonomous-agent-hermes-1`**. The container has upstream `hermes-agent` 0.13.0 installed via editable pip; nothing else.

### Evidence

```bash
$ docker exec autonomous-agent-hermes-1 ls /app/lib
ls: cannot access '/app/lib': No such file or directory

$ docker exec autonomous-agent-hermes-1 bash -c 'echo PYTHONPATH=$PYTHONPATH; python -c "import sys; print(\"\n\".join(sys.path))"'
PYTHONPATH=
# (only /usr/local/lib/python3.11 paths + editable hermes_agent finder)

$ docker logs autonomous-agent-hermes-1 2>&1 | grep -iE "plugin|register|anchors|evaluators|durability|memory|kanban"
# (empty — no plugin load logs)

$ awk '/^  hermes:/,/^  [a-z][a-z-]*:/' deploy/docker-compose.yml | grep "volumes:"
# (empty — hermes service has NO `./lib:/app/lib` mount)
```

`deploy/Dockerfile.hermes` (full text reviewed) only:
1. Installs `hermes-agent` from the submodule editably.
2. Adds OTel SDK.
3. Creates `/data`, `/app/config`, `/app/skills`, `/app/secrets`.
4. Sets `ENTRYPOINT ["hermes"]`.

There is **no `COPY lib/`, no `pip install -e .` of our project root, no `ENV PYTHONPATH=/app/lib:$PYTHONPATH`** anywhere. The `escalation-watcher` sidecar (added in PR #41) DOES mount `./lib:/app/lib:ro` for its own use, but the hermes service does not.

### Impact

Every `register(ctx)` we wrote — in `lib/anchors`, `lib/evaluators`, `lib/durability`, `lib/memory`, `lib/kanban` — never executes. The 162 unit tests pass against host Python. The acceptance runbook walk-through will SUPERFICIALLY pass because:
- Acceptance step 1 (Telegram messages): upstream Hermes Telegram already handles message↔reply
- Step 2 (skills): upstream Hermes' built-in skill extractor
- Step 3 (state persistence): upstream Hermes
- Step 4 (Phoenix traces): OTel via the proxy + collector — agent code irrelevant
- Step 5 (no secret-leak file): trivially passes because nothing writes the file
- Step 6 (spend tracking): LiteLLM
- Step 7 (budget cap): LiteLLM

But **the agent is running stock upstream behavior**, not the Phase 1 enhanced behavior. The PR series #34-#47 added a non-functional layer.

### Severity

🔴 **P0 / CRITICAL** for the *integrity* of Phase 1 acceptance. The walk-through can technically PASS, but it would certify a system that does not include any of our code. Promoting to `main` + tagging `phase1-accepted` based on such an acceptance would be misleading.

---

## 2. CRITICAL — Healthcheck cron still failing (`#38`, `#39` opened today, post-fix)

**PR #37 corrected the SERVICE NAME (`hermes-agent → hermes`) but did NOT fix the PATH issue.** The cron context does not have `/usr/local/bin` in PATH, so `docker` is not resolvable when the script runs from cron. `logs/healthcheck.log` shows repeated:

```
./scripts/healthcheck-ping.sh: line 32: docker: command not found
Reported failure
```

Two new "AutonomousAgent is DOWN" issues opened today (post-merge of PR #37):
- `#38` at 2026-05-18 18:15:01Z — 4 failure pings
- `#39` at 2026-05-18 18:20:03Z — 7 failure pings

The original `#29` is closed but the underlying cron defect persists; new noise generated.

### Fix

One of:
- (a) Edit `scripts/healthcheck-ping.sh` prelude to set `export PATH=/usr/local/bin:/usr/bin:/bin:$PATH`.
- (b) Update the crontab line itself: `*/5 * * * * PATH=/usr/local/bin:/usr/bin:/bin cd '.../AutonomousAgent' && ./scripts/healthcheck-ping.sh >> logs/healthcheck.log 2>&1`.
- (a) is more portable.

### Severity

🟠 **P1 / IMPORTANT but not blocking acceptance**. The agent itself works; only the external monitor is wrong. But the steady stream of `#38, #39, #40, …` from healthchecks.io will be noisy until resolved.

---

## 3. P1-5 hook bodies are stubbed (auto-card-on-message does NOT work)

`lib/kanban/__init__.py:34-73` — both `_on_pre_tool_call` and `_on_post_tool_call` hook bodies are `TODO(P1-5 follow-up)` no-ops. The bridge surface (`telegram_msg_to_card`, `cancel_card`, `send_alert`) is real and tested, and the `/cancel <id>` slash command works. But:
- A new Telegram message does NOT automatically create a Kanban card.
- A status transition does NOT automatically emit a Telegram notification.

### Severity

🟠 **P1 — documented in GO report footnote**. Acceptance step 1 (10 Telegram messages) still passes because Telegram works without auto-cards. But the "Kanban → Telegram bridge" subsystem is not delivering its core promise.

Compounding: `lib/durability/escalation.py` `emit_escalation` is also a `print()` stub (also TODO(P1-5)) — it never calls `telegram_bridge.send_alert`. So even if the 24h-blocked escalation triggers, the user gets only a stdout line in the sidecar log.

---

## 4. R4 integration test missing — hook ordering not asserted at runtime

The design spec §9 R4 mitigation said:
> "Integration test `tests/integration/test_p1_3_resume_then_p1_4_inject.py` asserts ordering via observable side-effects."

This file does not exist. The unit test `tests/unit/test_durability_plugin.py::test_register_wires_on_session_start_in_correct_order` asserts the order in the *register() call sequence*, but not at runtime — the assumption is "Hermes will call them in call-order, but Hermes' docs don't guarantee this." Without a runtime test, an upstream Hermes change to hook dispatch could silently reorder these and break the `resume → inject` ordering invariant.

### Severity

🟡 **P2 — risk register flagged this but mitigation incomplete**. Defer to follow-up.

---

## 5. R6 OTel spans in `lib/evaluators/judge.py` absent

`grep "tracer\|span\|trace" lib/evaluators/judge.py` → 0 hits. The evaluator dispatch produces NO OTel spans. Acceptance step 4 requires spans for `turn.start`, `model.call`, `tool.dispatch` — those come from upstream Hermes/LiteLLM. Evaluator spans are nice-to-have but not in the acceptance criteria.

### Severity

🟡 **P2 — explicitly deferred** in the GO report and design spec §9 R6.

---

## 6. Session-coordination ledger status is stale

`docs/superpowers/session-coordination.md:50-56` rows for C/D/E still show status `in-flight`. All 3 PRs (#43, #44, #45) merged. Per `docs/superpowers/session-coordination.md` §"How sessions converge", a session's row should be marked `done` when their last PR merges. None of the Phase β subagents updated this.

### Severity

🟢 **P3 — purely cosmetic doc hygiene**. Not a blocker.

---

## 7. consensus.py 3-strike tracker is wired but never invoked

`lib/evaluators/consensus.py:169-229` adds a public `record_rejection_for_fingerprint(...)` API. The actual evaluator dispatch path (`decide_consensus`) does NOT call it — it would need to be invoked by the orchestrator wrapping the consensus loop (P1-2 territory). Since the consensus and orchestrator are upstream concerns, AND since the broader plugin-loading issue (finding #1) means none of this runs anyway, this is doubly inert.

### Severity

🟡 **P2 — secondary to finding #1**. Wiring this is moot until the plugin-loading issue is resolved.

---

## 8. What IS verified working (the positives)

- `phase/1-completion` branch healthy: 12 PRs merged cleanly, no conflicts, smoke 8/8 PASS on the running stack
- 162 unit tests pass against host Python `.venv`
- 8 integration tests pass; 6 skipped with documented P2-deferred reasons
- Live integration test (`test_p1_2_judge_panel`) hits real Vertex AI / Gemini 3.1 Pro Preview successfully
- OTel pipeline ALIVE: Phoenix has traces from live LiteLLM calls (despite our evaluator code not running, the proxy itself emits spans)
- All 5 audit P0 items from the prior sweep are RESOLVED
- All 6 P1 items from the prior sweep are RESOLVED
- Issue #29 closed
- Hermes container egress network attached; DNS to api.telegram.org resolves
- The 33-mode failure matrix file + Python lookup is internally consistent
- Test coverage is thorough at the unit level (108 → 162 = +54 new tests across Phase α+β)

---

## 9. To enrich in Pass 2

Pass 2 should answer:
1. **Is the lib/* loading gap a doc gap (we missed wiring a Hermes config) or an architectural gap (Hermes doesn't expose plugin discovery to external Python modules)?** Investigate `hermes-agent/AGENTS.md` lines 465-489 (the register() contract docs) AND any `plugins:` or `extra_modules:` config keys in `config/hermes/cli-config.yaml`.
2. **What's the minimum-effort path to make our plugins load?** Three candidates:
   - (a) Mount `./lib:/app/lib:ro` on hermes service + add `ENV PYTHONPATH=/app/lib:$PYTHONPATH` + ensure Hermes' plugin discovery scans there.
   - (b) Package our `lib/` as an installable pyproject + `pip install` it in the Dockerfile.
   - (c) Add a hermes config entry pointing at our plugin modules by name.
3. **Should P1-5 hook bodies be filled before acceptance?** Or is the documented "P1-5 follow-up" stance OK for Phase 1?
4. **Is the healthcheck cron PATH fix in-scope for Phase 1 completion, or P2?**
