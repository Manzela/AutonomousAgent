# Observability + Reliability Audit — Phase 1 → Phase 2 Readiness

**Target:** AutonomousAgent @ `main` `85512a3`, tagged `phase1-accepted`
**Date:** 2026-05-19
**Method:** Static read of `lib/`, `hermes-agent/`, `deploy/`, `scripts/`, `config/`; live `docker logs` + `docker ps` inspection; git history check.
**Scope:** Observability (OTel coverage, metrics, structured logs, alerting, dashboards, error tracking) + Reliability (graceful degradation, backoff/retry, circuit breakers, idempotency, checkpointing/resume, DR, rate limits, healthchecks, session reset).

---

## TL;DR — Brutal Honesty

Phase 1 looks shippable on paper. In running containers it is **observability-positive, reliability-aspirational**.

- Five Hermes app-spans (`turn.start`, `tool.dispatch`, `model.call`) ARE emitted and reach Phoenix.
- Almost every other reliability subsystem advertised in `lib/durability/` is **dead-on-arrival** in the running container due to a signature/contract mismatch between our plugin callbacks and Hermes' `invoke_hook(**kwargs)` dispatcher. The proof is in `docker logs autonomous-agent-hermes-1` itself.
- Multiple "alert" paths (escalation, kanban) are `print()`/`logger.info()` stubs that never reach Telegram.
- `scripts/snapshot.sh`, `scripts/panic.sh`, `docs/runbooks/recovery.md` reference services and volumes that no longer exist (chroma, honcho-db, hermes-agent, hermes-gateway). DR is paper-only.
- `escalation-watcher` sidecar is **not running** in the live stack even though it is defined in `deploy/docker-compose.yml`.

Going to cloud-prod in this state ships a system where the user can see *what happened* (Phoenix traces) but the agent will not actually self-heal, retry with backoff, escalate on stuck Kanban cards, resume from checkpoints, or persist its own state across the daily 4 a.m. session reset.

---

## 1. Critical Findings (must-fix before Phase 2)

### 1.1 P1-3 / P1-4 / P1-6 hooks are dead-on-arrival (TypeError, swallowed)

**Severity:** S0
**Evidence (live container, 2026-05-19):**

```
$ docker logs autonomous-agent-hermes-1 2>&1 | grep "Hook.*raised" | sort -u
WARNING hermes_cli.plugins: Hook 'on_session_start' callback _p1_3_resume_session raised:
  _p1_3_resume_session() got an unexpected keyword argument 'session_id'
WARNING hermes_cli.plugins: Hook 'on_session_start' callback _p1_4_inject_rejected raised:
  _p1_4_inject_rejected() got an unexpected keyword argument 'session_id'
WARNING hermes_cli.plugins: Hook 'pre_tool_call' callback before_tool_call raised:
  before_tool_call() got an unexpected keyword argument 'tool_name'
```

**Root cause** — Hermes calls hooks as `cb(**kwargs)` (`hermes-agent/hermes_cli/plugins.py:1277`), passing kwargs like `session_id`, `tool_name`, `tool_call_id`, `args`, `result`, `duration_ms`. Our callbacks are written with the wrong signature:

| Callback | File:Line | Signature | Hermes passes |
| --- | --- | --- | --- |
| `_p1_3_resume_session` | `lib/durability/__init__.py:21` | `(ctx)` | `session_id=…` |
| `_p1_4_inject_rejected` | `lib/durability/__init__.py:35` | `(ctx)` | `session_id=…` |
| `trichotomy.before_tool_call` | `lib/durability/trichotomy.py:68` | `(ctx, tool_call)` | `tool_name=…, args=…, …` |
| `trichotomy.after_tool_call` | `lib/durability/trichotomy.py:73` | `(ctx, tool_call, result_or_error)` | `tool_name=…, result=…, duration_ms=…` |

**Hermes catches the TypeError** at `plugins.py:1280-1286` and logs a WARNING but never invokes the body. So:

- **P1-3 checkpoint resume**: never runs. `resume.rehydrate_latest_for_session` is unreachable from live flow.
- **P1-4 REJECTED-inject**: never runs. `Past failed approaches…` system message is never injected.
- **P1-6 trichotomy classify**: never runs. The 33-mode F-matrix is unreachable.
- **`backoff_delay()`**: defined at `lib/durability/trichotomy.py:53`, *zero* callers in `lib/` or `hermes-agent/`. (`grep -rn "backoff_delay" lib/ hermes-agent/` → 1 hit, the definition itself.)
- **`durability.classify` OTel span** (`trichotomy.py:82`): never emitted; the function it lives inside is dead.

**Why the unit tests passed** — `tests/unit/test_durability_plugin.py:43-44` calls `_p1_3_resume_session(ctx)` positionally, never with `session_id=…`. The test verifies the wrong contract.

**Why this matters for Phase 2** — *every* durability story we tell about Phase 1 (auto-resume, REJECTED memory, classified retries) is fiction in the running system. Issue: the failing hook is a no-op without surfacing to ops; the user reads green checks, the system silently drops retries.

### 1.2 `Checkpoint.maybe_write` has zero call sites in live code

**Severity:** S0
**Evidence:** `grep -rn "Checkpoint\|maybe_write" hermes-agent/ deploy/ scripts/` finds zero matches outside `lib/durability/checkpoint.py` itself and `tests/`. The class is well-tested in isolation but never *instantiated by anything that runs in production*.

Hermes' `model_tools.py` does not call into our checkpoint writer. The only "checkpoints" Hermes itself maintains are conversation transcripts at `~/.hermes/checkpoints/` via `hermes-agent/hermes_cli/checkpoints.py` (a different beast — transcript snapshots, not per-step state for P1-3 resume).

Combined with finding 1.1 (resume hook is dead), the entire P1-3 checkpointing subsystem is observably absent from runtime. **A container restart loses all in-flight state.**

### 1.3 `Telegram` alerting is `print()` / `logger.info()` stubs

**Severity:** S0
**Evidence:**

- `lib/durability/escalation.py:38-41` — `emit_escalation` is a `print(f"[ESCALATION F32] …")` with `# TODO(P1-5): replace with telegram_bridge.send_alert(...)`.
- `lib/kanban/telegram_bridge.py:128-140` — `send_alert` is a `logger.info(...)`. The docstring says "the actual HTTP send lives in the gateway. The bridge can be wired into the gateway's outbound queue once the gateway-side hook is in place (P1-5 task 38)." That wiring did not land in PR #49 / `phase1-accepted`.

**Effect:** When a Kanban card transitions `running → blocked`, when an evaluator panel rejects 3-strike, when the 24h-silence watcher fires F32 — **no message ever reaches the user's Telegram**. They go into Phoenix only if the surrounding code happens to emit a span (which most of these paths don't).

### 1.4 `escalation-watcher` sidecar is not running

**Severity:** S1
**Evidence:** `docker ps -a --filter "name=escalation-watcher"` returns empty. `docker compose -f deploy/docker-compose.yml config --services` lists `escalation-watcher` as a defined service, but the container is absent from the live stack.

Even if it were running, finding 1.3 above means F32 escalations would print to stdout, not Telegram.

### 1.5 DR scripts and runbook reference services that no longer exist

**Severity:** S1
**Evidence:**

- `scripts/snapshot.sh:14-19` calls `docker compose exec hermes-agent`, `chroma`, `honcho-db pg_dump`. None of those services exist in the current compose file (`hermes-agent` renamed to `hermes`; `chroma` switched to Chroma Cloud; `honcho-db` disabled in Phase 1).
- `scripts/panic.sh:9` pauses `hermes-agent hermes-gateway` — neither exists.
- `docs/runbooks/recovery.md:16` references volumes `autonomous-agent_chroma-data`, `autonomous-agent_honcho-db-data` — removed.
- No `snapshots/` directory exists on disk. The script has never produced output.

**RTO/RPO not stated anywhere.** No documented procedure for restoring Chroma Cloud state (which is now external SaaS) or LiteLLM proxy spend data.

### 1.6 OTel collector → Phoenix exporter is flaky

**Severity:** S2
**Evidence:** `docker logs autonomous-agent-otel-collector-1` shows intermittent `connection reset by peer` and `EOF` errors from the `otlphttp/phoenix` exporter (4 occurrences in the last 24h). Spans are being retried but the warning rate suggests Phoenix occasionally closes connections mid-batch. Phoenix has no healthcheck, so the collector can't gate on Phoenix readiness.

---

## 2. Observability Maturity Table

| Capability | State | Where | Grade | Phase 2 gap |
| --- | --- | --- | --- | --- |
| **OTel SDK init in Hermes** | Works | `lib/observability/otel_setup.py:34` | A | OK |
| **App-level spans** (`turn.start`, `tool.dispatch`, `model.call`) | Emitted | `lib/observability/__init__.py:99-225` | A- | Open-Inference attrs missing per issue #53 |
| **`durability.classify` span** | **DEAD** (host fn never invoked, see 1.1) | `lib/durability/trichotomy.py:82` | F | Fix hook sig |
| **Spans from `lib/anchors/`** (TaskSpec lock, clarification loop) | None | n/a | F | Need spans on `decide_next_action`, `lock_spec`, `intent_classifier.classify` |
| **Spans from `lib/evaluators/`** (judge panel, consensus) | None | n/a | F | Need spans per judge call, consensus result, rejection-streak inc |
| **Spans from `lib/memory/`** (REJECTED inject, /forget) | None | n/a | F | Need spans on `load_active_entries`, `append_entry` |
| **Spans from `lib/kanban/`** (telegram bridge, notification policy) | None | n/a | F | Need spans on `telegram_msg_to_card`, `send_alert` |
| **Spans from `lib/healthcheck.py`** | None | n/a | D | Low priority but useful |
| **Spans from `lib/scrubber.py`** | None | n/a | C | Per-pattern hit-rate spans would be valuable |
| **Spans from `lib/durability/checkpoint`** | None (and class never instantiated) | n/a | F | Critical for 48h-run observability |
| **Metrics** (Prometheus / OTel metrics SDK) | **Zero** | n/a | F | Need request rate, error rate, latency p95/p99, budget consumed, queue depth |
| **Structured logging (JSON)** | Hermes: NO; LiteLLM: YES | `cli-config.yaml:90` declares `logs.format: json` but Hermes ignores it (live logs are `WARNING hermes_cli.plugins: …` plaintext) | D | Required for log aggregation |
| **Log level discipline** | OK on first glance | Most modules use `logger.debug` for fail-open paths, `logger.warning` for hook errors | B | OK |
| **Log aggregation destination** | Docker `json-file` driver, max-size 100m, 5 files = 500MB total per service. Local-only. | `deploy/docker-compose.yml:6-11` | C | Phase 2 needs Cloud Logging / Loki / Datadog. No shipper exists. |
| **Healthchecks.io ping** | Works | `scripts/healthcheck-ping.sh`, cron every 5 min | B | Single check (hermes only); doesn't gate on phoenix/otel/litellm health |
| **Telegram escalation** | **Stub only** (1.3) | `lib/durability/escalation.py:38` | F | Wire to real send |
| **PagerDuty / Opsgenie / Slack** | None | n/a | F | Required for cloud-prod |
| **Phoenix dashboards** | UI works; queries work | `http://localhost:6006` accessible | B | Phoenix UI alone isn't multi-user; Phase 2 needs Cloud Trace + Grafana |
| **Grafana / Cloud Monitoring** | None | n/a | F | Phase 2 essential |
| **Error tracking (Sentry/Rollbar)** | **Zero** | n/a | F | Hermes exceptions are logged, never aggregated for triage |
| **Spans actually reaching Phoenix** | Yes, with intermittent drops (see 1.6) | `docker logs autonomous-agent-otel-collector-1` shows ~5 EOF/reset events / 24h | C | Phase 2 should use Cloud Trace (more stable than Phoenix) |

**Observability grade overall: C-** — traces flow for the happy path; everything else (metrics, structured logs, alerting, error tracking, dashboards) is either missing or stubbed.

---

## 3. Reliability Findings by Area

### 3.1 Graceful degradation

**Evidence:** `lib/durability/failure_matrix.py` defines 33 F-codes with `class` ∈ {SELF_HEAL, FAIL_LOUD, FAIL_SOFT} and a `handler` string like `"retry_with_backoff"`, `"restart_sandbox_and_retry"`. **The `handler` string is metadata only** — `grep -rn "def retry_with_backoff\|def fail_loud\|def fail_soft" lib/` returns zero. The handlers are descriptive labels, not callable references.

Combined with finding 1.1 (trichotomy hook never invoked) and finding 1.2 (checkpointing never triggered), the runtime executes **no per-failure-mode policy**. Every failure either:

1. Comes back as a JSON error string to the model (Hermes' default tool error path, `model_tools.py:783-789`), which the LLM may or may not retry by re-issuing the tool; or
2. Bubbles up unhandled and crashes the worker (with no checkpoint to resume from — see 1.2).

**Grade: D** — the static matrix is comprehensive (33 modes is great work) but nothing wires it to runtime behavior.

### 3.2 Backoff + retry

- `backoff_delay()` defined at `lib/durability/trichotomy.py:53`. **Zero callers** outside its own definition and one unit-test file. Dead code.
- No `tenacity`, no `backoff`, no `@retry` decorators in `lib/` or `hermes-agent/` (per `grep`).
- LiteLLM has its own internal retry config (`config/limits.yaml:8-19` sets `litellm_max_attempts: 5`, etc.) — that's the *only* effective retry path for LLM calls, and it's enforced by LiteLLM, not by our code.
- MCP server connection failures retry 3 times via Hermes' own `tools.mcp_tool` (live log evidence: `MCP server 'github' initial connection failed (attempt 1/3)…`) before "giving up" — also not our code.

**Grade: D** — LiteLLM handles its own retries; everything else has no retry policy because our retry primitive is unwired.

### 3.3 Circuit breakers

**Evidence:** `grep -rn "circuit_breaker\|CircuitBreaker\|pybreaker" lib/ hermes-agent/` returns one hit — a docstring in `lib/anchors/clarification_loop.py:3` that calls the clarification loop "a hybrid circuit-breaker", but it's an in-domain state machine for question budgets / silence timers, not a circuit breaker for downstream services.

There is **no circuit breaker** between Hermes and LiteLLM proxy, Chroma Cloud, GitHub MCP, or Context7. If any of those goes into a degraded state, Hermes will keep firing requests indefinitely.

Live evidence of this risk: `docker logs autonomous-agent-hermes-1` shows GitHub MCP returning HTTP 401 (PAT auth bad) and Context7 returning "Session terminated" — both have been failing for 12+ hours; Hermes keeps trying.

**Grade: F.**

### 3.4 Idempotency

- `scripts/bootstrap.sh` — manually inspected; uses `set -euo pipefail`, only `mkdir -p` and idempotent docker compose ops. Mostly OK.
- `scripts/decrypt-secrets.sh`, `scripts/teardown.sh`, `scripts/smoke.sh` — not deeply inspected; reasonable to trust the patterns.
- `scripts/snapshot.sh` — broken (1.5), so idempotency irrelevant.
- `scripts/panic.sh` — broken (1.5).

**Grade: B-** on the live scripts; **F** on the broken ones.

### 3.5 Checkpointing + resume

Covered in 1.1 and 1.2. Class is well-tested in isolation (`tests/unit/test_checkpoint.py`, 26 tests); class is never instantiated by anything live; resume hook signature is wrong and dies on every call. **Grade: F.**

### 3.6 Disaster recovery

Covered in 1.5. `snapshot.sh` cannot run. `recovery.md` references nonexistent volumes. No tested restore. No RTO/RPO documented. `snapshots/` directory has never been created. **Grade: F.**

### 3.7 Capacity / rate limits

- `config/limits.yaml:32` declares `agent.max_concurrent_tasks: 3`. **Nothing enforces this** — `grep -rn "max_concurrent_tasks" lib/ hermes-agent/` finds no enforcement code. The limit is documentation.
- `config/limits.yaml:2` declares `budget.daily_usd_cap: 500`. **Nothing enforces this** — `grep -rn "daily_usd_cap" lib/ hermes-agent/` finds nothing. F21 ("daily_budget_exceeded") is a classifier pattern (`trichotomy.py:27`) but nothing emits a matching error.
- `config/limits.yaml:73` declares `kanban.notification_rate_limit_per_minute: 6`. **Not enforced** in `lib/kanban/telegram_bridge.py` — there's no token bucket / sliding window. (Moot since the send is a stub — see 1.3.)
- Sandbox limits ARE enforced — `deploy/docker-compose.yml:148-150` sets `mem_limit: 1g`, `cpus: 1.0`, `pids_limit: 200`. Real and effective.

**Grade: D** — Docker-level limits work; app-level limits are documentation.

### 3.8 Healthchecks

| Service | Healthcheck? | Where |
| --- | --- | --- |
| `litellm-proxy` | Yes (python urllib probe) | `deploy/docker-compose.yml:84-91` |
| `hermes` | Yes (trivial — `python -c 'import sys; sys.exit(0)'`) | `deploy/docker-compose.yml:279-284` |
| `otel-collector` | No (distroless, no probe binaries; comment acknowledges) | `deploy/docker-compose.yml:108-111` |
| `phoenix` | No | `deploy/docker-compose.yml:122-131` |
| `github-mcp` | No (distroless, no probe binaries) | `deploy/docker-compose.yml:174-177` |
| `shell-sandbox` | No | `deploy/docker-compose.yml:135-151` |
| `escalation-watcher` | No (and not running per 1.4) | `deploy/docker-compose.yml:302-317` |

The Hermes healthcheck is **trivial** (`python -c "import sys; sys.exit(0)"`) — it verifies the Python interpreter starts. It does not verify the agent loop is alive, that the gateway is polling Telegram, that the OTel SDK is initialized, or that any of the lib/* plugins loaded. **A Hermes that fails to load every plugin still passes this healthcheck.**

`lib/healthcheck.py` defines a richer `HealthReport` (Status.OK / DEGRADED / DOWN per dependency) but **no service is wired to expose it** — it's a library, not a server endpoint.

`scripts/healthcheck-ping.sh` does work (per `logs/healthcheck.log`) after PR #37/#48 fixed the cron PATH issue.

**Grade: C** for the trivial healthcheck, **D** for the missing rich endpoint and missing per-service probes.

### 3.9 Memory + Honcho (issue #54)

Already tracked as a known gap. Honcho is disabled in Phase 1 (`cli-config.yaml:48`). Issue #54 is sufficient; no additional concerns to add.

### 3.10 Hermes session daily-reset

**Evidence:** `hermes-agent/gateway/config.py:248-251` defaults to:

```python
mode: str = "both"           # daily AND idle reset
at_hour: int = 4             # 4 a.m. local
idle_minutes: int = 1440     # 24 hours
```

Our `config/hermes/cli-config.yaml` does **not** override `default_reset_policy`. So every day at 4 a.m. local, every active session is reset (context lost, fresh greeting), and sessions idle 24h+ are also reset.

For a system targeting 48-hour autonomous runs, this is hostile by default. Combined with finding 1.2 (P1-3 checkpoint resume is dead-on-arrival), **a 48h task that crosses a 4 a.m. boundary loses all state**.

**Action:** Add `default_reset_policy.mode: "none"` (or `"idle"` with `idle_minutes: 4320` = 72h) to `cli-config.yaml`. Pair with fixing 1.1/1.2 so checkpoint resume actually works as a safety net.

**Grade: D** — known knob, wrong default for our use case.

---

## 4. Per-Area Grades

| Area | Grade |
| --- | --- |
| OTel coverage map | C+ |
| Metrics emission | F |
| Structured logging | D (Hermes ignores its own JSON setting) |
| Alerting infrastructure | F (stubs only) |
| Dashboards | C (Phoenix works; no Grafana) |
| Error tracking | F (none) |
| Graceful degradation | D (matrix is documentation) |
| Backoff + retry | D (LiteLLM only) |
| Circuit breakers | F |
| Idempotency | B- on working scripts; F on broken ones |
| Checkpoint + resume | F |
| Disaster recovery | F |
| Capacity / rate limits | D (docker enforces; app doesn't) |
| Healthchecks | C |
| Memory / Honcho | (deferred — issue #54) |
| Session reset policy | D |
| **OVERALL** | **D** |

---

## 5. Phase 2 Pre-Requisite List (ordered by blocking risk)

**Must-fix before any Phase 2 prod traffic:**

1. **Fix hook signatures** in `lib/durability/__init__.py` and `lib/durability/trichotomy.py` to accept `**kwargs`. Add integration tests that call hooks the way Hermes does (`cb(session_id="…", …)`), not positionally.
2. **Wire `Checkpoint`** to either `post_tool_call` (write every N tool calls) or a Hermes scheduler hook. Pair with `resume.rehydrate_latest_for_session` — currently both halves are independent dead code.
3. **Wire `telegram_bridge.send_alert`** to actual Telegram Bot API (today's `logger.info` is a stub). Resolve the "gateway-side hook (P1-5 task 38)" debt.
4. **Wire `emit_escalation`** to call `telegram_bridge.send_alert` instead of `print()`.
5. **Restart and verify** `escalation-watcher` sidecar; or make Hermes-startup verify its absence.
6. **Override `default_reset_policy.mode`** to `"none"` or `"idle"` with a long timeout in `config/hermes/cli-config.yaml`.

**Must-fix before Phase 2 GA:**

7. **Rewrite `scripts/snapshot.sh`** to match current services (hermes only locally; export Chroma Cloud collections via API; remove honcho/chroma-volume lines).
8. **Rewrite `docs/runbooks/recovery.md`** to match current architecture. Document RTO/RPO. Test the restore procedure end-to-end.
9. **Implement budget enforcement** for `budget.daily_usd_cap`. Read LiteLLM `/spend` (issue #55 blocks this) and emit F21 when exceeded.
10. **Implement `agent.max_concurrent_tasks`** in the gateway dispatcher (or document that it's unenforced).
11. **Switch Hermes logs to JSON** as the cli-config already requests. (Likely requires upstream Hermes patch — file an upstream issue.)
12. **Add OTel metrics SDK** alongside the tracer. Emit: request rate, error rate, latency p50/p95/p99, in-flight tool count, budget consumed today, Kanban queue depth.
13. **Add OTel spans to** `lib/anchors/`, `lib/evaluators/`, `lib/memory/`, `lib/kanban/`. (Issue #53 only covers attrs on existing spans, not new spans.)
14. **Add circuit breakers** between Hermes and LiteLLM / Chroma Cloud / GitHub MCP / Context7. `pybreaker` is the smallest library that does this; or implement using `tenacity.retry` with `stop_after_attempt` + global counter.
15. **Add healthchecks** to `phoenix` (HTTP probe to `/v1/projects`), `github-mcp` (TCP probe to `:8003`). Replace the trivial Hermes check with one that calls `lib.healthcheck.run_checks()`.
16. **Stand up an error tracker** (Sentry/Cloud Error Reporting). Hermes-level exceptions today only land in stdout.
17. **Move from Phoenix-only to Cloud Trace** for the prod tracing sink. Phoenix is fine for dev; the OTel collector → Phoenix exporter is dropping spans intermittently (1.6).
18. **Replace `docker logs` with a log shipper** (Fluent Bit / Vector / GCP Ops Agent → Cloud Logging) so the structured logs are queryable / alertable.

---

## 6. Strengths (don't lose these in Phase 2)

- The five Hermes app-spans (`turn.start`, `tool.dispatch`, `model.call`) ARE wired correctly and DO reach Phoenix. PR #52 is solid work — the SDK init, the atexit flush, the orphaned-post fallback, the absorb-unknown-kwargs `**_:` pattern. This is exactly the right shape.
- The 33-mode failure matrix (`lib/durability/failure_matrix.py`) is comprehensive, well-organized, and well-tested in isolation. It's a great foundation — it just needs the runtime to actually use it.
- `lib/durability/checkpoint.py` does atomic writes correctly (write `.tmp`, fsync, `os.replace`, fsync parent dir) with retention. The class is production-quality; the bug is the absence of callers, not the class itself.
- `scripts/healthcheck-ping.sh` ships failure → success state transitions correctly via Healthchecks.io after PR #37/#48 fix.
- `lib/scrubber.py` and `config/scrubber-patterns.yaml` give us a real secret redaction layer (not in this audit's scope, but worth noting).
- Docker-level resource limits on `shell-sandbox` (mem, cpu, pids, read-only fs, no network) are correct and effective.

---

## 7. Citations index

All claims above are anchored at:

- `lib/observability/__init__.py:99-225` (the 5 spans)
- `lib/observability/otel_setup.py:34-108` (SDK init)
- `lib/durability/__init__.py:10-32` (broken hook registrations)
- `lib/durability/trichotomy.py:53,68,73,82` (dead `backoff_delay`, broken `before/after_tool_call`, dead `durability.classify` span)
- `lib/durability/checkpoint.py:43-156` (`Checkpoint` class, no live callers)
- `lib/durability/resume.py:117` (`rehydrate_latest_for_session`, called only by dead hook)
- `lib/durability/escalation.py:38-41` (`emit_escalation` = `print()` stub)
- `lib/kanban/telegram_bridge.py:128-140` (`send_alert` = `logger.info` stub)
- `lib/durability/failure_matrix.py:18-191` (33-mode matrix; `handler` field is metadata)
- `hermes-agent/hermes_cli/plugins.py:1277,1280-1286` (kwargs-style dispatch + WARNING on TypeError)
- `hermes-agent/model_tools.py:740-802` (real Hermes kwargs: `tool_name`, `args`, `result`, `task_id`, `session_id`, `tool_call_id`, `duration_ms`)
- `hermes-agent/gateway/config.py:238-277` (`SessionResetPolicy` defaults: `mode="both"`, `at_hour=4`)
- `config/limits.yaml:2,32,73,166-173` (budget, concurrency, kanban rate-limit, durability cfg — most unenforced)
- `config/hermes/cli-config.yaml:88-91,108-115` (logs.format=json ignored; plugin enable list)
- `deploy/docker-compose.yml:101-317` (services + healthchecks)
- `deploy/otel/collector.dev.yaml:1-43` (collector pipelines)
- `scripts/snapshot.sh:14-19`, `scripts/panic.sh:9`, `docs/runbooks/recovery.md:16-20` (DR drift)
- `docker logs autonomous-agent-hermes-1` (live evidence of hook TypeErrors)
- `docker logs autonomous-agent-otel-collector-1` (live evidence of Phoenix exporter EOFs)
- `docker compose ps` (live evidence of missing escalation-watcher)
- `tests/unit/test_durability_plugin.py:43-44` (wrong-oracle test that hid 1.1)

---

## 8. Summary verdict for Phase 1 → Phase 2 promotion

**Promotion-blocking findings: 6** (sections 1.1 through 1.6 above).

The codebase has the *shape* of a reliability-conscious system but not its *behavior*. The plugin contract mismatch between our `lib/durability` callbacks and Hermes' `invoke_hook(**kwargs)` dispatcher means the entire P1-3/P1-4/P1-6 acceptance content runs but doesn't *do* anything in production. The unit tests do not catch this because they call the callbacks the wrong way.

Phase 1 was tagged accepted in good faith — the spans show up in Phoenix, the smoke tests pass, the limits.yaml validates. But the live `docker logs` reveal that almost every reliability subsystem we ship is silently failing on every session start and every tool call.

Going to cloud-prod in this state means: when something breaks at 03:00 — the wrong region, a stuck Kanban card, a runaway budget, a corrupted checkpoint — the user will not be paged, the agent will not retry intelligently, the failure will not be classified, and there is no snapshot to restore from. The *only* signal that anything went wrong is whatever spans happen to have been emitted before the crash, and there is no alerting on top of those spans.

**Recommendation:** Block Phase 2 promotion until at minimum items 1-6 in §5 are fixed and verified by an integration test that calls every registered hook the way Hermes actually calls it (kwargs-style, with the exact kwargs from `hermes-agent/model_tools.py:740-802` and the `on_session_start` call site).
