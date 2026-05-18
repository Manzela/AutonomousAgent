# Findings — Phase 1 completion coordination plan, sweep audit

**Date:** 2026-05-18
**Target of audit:** The Phase-1-completion-via-multi-session coordination plan I (assistant) drafted earlier in this conversation (Approach A: this session does pre-work + P1-6; sessions c/d/e do P1-3/P1-4/P1-5 in parallel; this session closes with Task 39).
**Audit purpose:** Verify nothing critical was overlooked before the coordination plan is written to a spec doc.
**Pass:** 1 (codebase-only; Pass 2 will enrich via parallel Explore subagents).

---

## 1. What the audit verified vs. what the draft plan assumed

| # | Draft-plan claim / assumption | Codebase reality | Severity |
|---|---|---|---|
| 1 | Smoke check is "9/9" (cited from HANDOFF + README + acceptance runbook) | `scripts/smoke.sh` actually contains **7 checks** (`Smoke test 1/7 … Smoke test 7/7`). Several docs are stale. | 🟡 doc drift |
| 2 | P1-6 (Durability) is a hard prerequisite for P1-3 (Checkpointing) and P1-4 (REJECTED.md) | Per `audit/audit-plan.md` P1-6 section: "**Blocks**: P1-2 (evaluators reference matrix), so P1-6 should land BEFORE or concurrent with P1-2." Real coupling is P1-6 → P1-2, not P1-6 → P1-3/4. **P1-2 has already merged WITHOUT P1-6.** | 🔴 **assumption wrong AND a real out-of-order violation exists on main** |
| 3 | The pre-work cleanup needs no prior-art lookup | An existing `audit/audit-plan.md` (511 lines) on `origin/main` predates this work and lists 5 P0 unblockers + the canonical P1-1..6 framing. My plan never referenced it. | 🟠 missed context |
| 4 | The system is "DOWN" per open issue #29 | Issue #29 is a healthchecks.io ping monitor (cron) that hasn't pinged. Containers are actually running (8 containers up, `litellm-proxy` healthy 32h+, `hermes-agent` up 20h). The healthcheck **cron** is the broken thing, not the agent. | 🟡 hidden Phase γ blocker |
| 5 | "Just prune ~10 stale worktrees" | All 20 worktrees inspected: 19 are clean. **`.worktrees/phase1` has 2 dirty files** (the untracked failure-matrix draft + 216-line session-coordination.md variant that HANDOFF §5 already called out). My plan said "verify dirty before prune" but did not specifically address these two. | 🟡 manual handling required |
| 6 | "Tasks 6 + 20b are tiny limits.yaml appends" | True for the YAML edit itself. But `lib/anchors/__init__.py` has **7 `TODO(P1-1 task 6)` markers** across functions (lines 18, 34, 40, 45, 56, 61, 71). These are the active plugin entry points; they currently return string stubs. Task 6 is not "just YAML" — it likely implies filling these stubs against the new limits keys. | 🟠 task scope larger than my draft acknowledged |
| 7 | "P1-5 is independent of all other subsystems" | `lib/anchors/__init__.py:55` has `TODO(P1-5): /cancel <id> handled by kanban plugin.` So P1-5 owns a cross-module touch in `lib/anchors/__init__.py` (the `/cancel` slash command). P1-5 is mostly independent but not 100%. | 🟡 minor cross-module touch |
| 8 | Direct-to-`main` PR flow for parallel sessions | `docs/superpowers/session-coordination.md` §"Conflict-prevention rules" point 2: "Shared-file edits go on the **track's integration PR**, not on individual task PRs." Phase 1's integration vehicle was `phase/1`, which the HANDOFF now flags as a trap. **There is no surviving integration vehicle for the second half of Phase 1.** My plan assumed direct-to-main without resolving this. | 🟠 protocol gap |
| 9 | Phase γ acceptance is one-script: `bash scripts/smoke.sh + pytest + integration test` | `docs/runbooks/phase1-acceptance.md` requires **10 manual Telegram messages from your phone**, plus a hands-on Phoenix UI inspection. The acceptance is human-in-the-loop, not scriptable. | 🟠 wall-clock + UX implication for Phase γ |
| 10 | Hermes-agent submodule is "already pinned, no concern" | Submodule pinned at `ddb8d8f`. Per `audit/audit-plan.md`, key reuse points are: `hermes-agent/toolsets.py:126` (clarify tool — used by P1-1), `hermes-agent/tools/delegate_tool.py:1909` (dispatch — used by P1-2), `hermes-agent/hermes_cli/kanban_db.py:559-673` (Kanban SQLite — to be used by P1-5). P1-3 wants Hermes' `batch_runner._load_checkpoint`. Pin freshness affects every remaining subsystem. | 🟡 pinned-version coupling |
| 11 | The hermes-agent container's `/app/skills` writable for autonomous skill creation | Acceptance step 2 requires `docker compose exec hermes-agent ls /app/skills` to show ≥1 autonomously-created skill. Whether the skill-extractor is wired (and Hermes is configured to persist into that path) is unverified — not in any P1 task. | 🟡 acceptance dep |
| 12 | The `lib/scrubber.py` is "wired into the live flow" | `lib/scrubber.py` exists and has tests, but acceptance step 5 expects `/data/secret-leak-attempts.log` to be created by the scrubber when it catches a leak. Whether the live pipeline routes through `lib/scrubber.py` (or relies on LiteLLM/Hermes built-ins) is unverified. | 🟡 acceptance dep |
| 13 | Pre-existing integration test scaffolds | 7 tests in `tests/integration/`: `budget_cap`, `chroma_outage`, `full_turn`, `p1_2_judge_panel`, `sandbox_isolation`, `secret_leak`, `skill_creation`. Only `p1_2_judge_panel` is confirmed PASS by HANDOFF. Status of the other 6 is unknown; some may be scaffolds-not-yet-passing that the missing subsystems should turn green. | 🟠 unknown baseline |
| 14 | `config/hermes/limits.yaml` (the P0-2 stale artifact) needs removal | **Already removed** — file does not exist (`ls config/hermes/` confirms). P0-2 is closed. | ✅ closed already |
| 15 | The HANDOFF's "pending list" is exhaustive | HANDOFF lists: Tasks 6 + 20b, P1-3, P1-4, P1-5, P1-6, Task 39. The existing `audit/audit-plan.md` adds: **P0-1 (Telegram round-trip re-verify), P0-3 (host pytest deps), P0-4 (OTel reach Phoenix), P0-5 (CHANGELOG cleanup)**. These are not in HANDOFF's pending list because the HANDOFF predates this audit being read. | 🟠 inherited gap |

---

## 2. Critical things my draft plan did **not** mention at all

### 2.1 The P1-6/P1-2 out-of-order merge

P1-2 (evaluators) shipped on `main` (commits `f9792b9`, `28c8779`, `e398034`, `1dca48f`) without P1-6 (failure matrix) in place. Per `audit/audit-plan.md`:

> "scoring rubrics for each judge MUST reference the failure-matrix from P1-6 (see 'Refined' note below — we don't vote blind)"

So the merged judges in `lib/evaluators/judge.py` either (a) skip the failure-matrix reference (degraded scoring) or (b) hardcode placeholder F-codes that the eventual `lib/durability/failure_matrix.py` must match exactly. **My plan didn't ask "does P1-6 require a follow-up rewire of P1-2?"** — a real possibility I overlooked.

### 2.2 The integration-branch vacuum

The coordination doc presumes each phase has an integration branch. Phase 1's was `phase/1`. That branch is now a trap. My draft plan implicitly asked sessions c/d/e to PR directly to `main`, but the project convention is to converge first into an integration branch and run the full acceptance there before promoting. **I never named or proposed a new integration vehicle.**

Options:
- (a) Create `phase/1-completion` as the second-half integration branch (cleanest; matches convention)
- (b) Allow direct-to-main per-task PRs (departs from convention; Phase 1 acceptance becomes a single "run it on main HEAD" step)
- (c) Use the next phase tag (`phase/1-2` or similar)

Plan needs to pick one and justify.

### 2.3 The healthcheck cron / open issue #29

Issue #29 ("AutonomousAgent is DOWN") is a healthchecks.io cron monitor that stopped pinging on ~2026-05-16. `docs/runbooks/healthcheck-cron-setup.md` exists; `scripts/healthcheck-ping.sh` exists. The cron is not installed (or stopped firing). **Phase γ acceptance presumes "stack is operational"**; an open `DOWN` issue blocks an honest acceptance report. Plan must include: fix the cron OR close the issue with a "false-alarm" note OR mark the healthcheck as Phase 2 scope.

### 2.4 The hermes-agent submodule freshness check

P1-3, P1-5 (and to a lesser extent P1-4) all rely on Hermes upstream patterns:
- P1-3 wants `batch_runner._load_checkpoint` / `_save_checkpoint`
- P1-5 wants the `hermes_cli/kanban_db.py` SQLite Kanban (statuses, claim/heartbeat)

Pinned at `ddb8d8f`. **No task in the plan verifies the pin still has those symbols at the cited line numbers.** A bumped pin could rearrange code; a frozen pin could lag behind upstream fixes. My plan said "submodule pinned at ddb8d8f — fine" without checking.

### 2.5 The acceptance runbook is interactive

Acceptance step 1 requires **the human user to send 10 specific Telegram messages from a phone, one at a time, waiting for replies**. Step 4 requires the user to **open Phoenix UI in a browser and visually inspect spans**. Plan Phase γ said "this session runs Task 39" — but a Claude session cannot send Telegram messages from your phone. **Phase γ is partly a human-in-the-loop step, not an assistant-driven step.**

### 2.6 Cross-cutting concerns not owned by any Phase 1 task

Per `audit/audit-plan.md`, several systems are **assumed pre-existing or scaffolded outside P1**:
- OTel observability wiring (P0-4 covers verification, but no task covers end-to-end pipeline ownership)
- Secret-scrubbing live integration (lib exists; live wiring not in any P1 task)
- Egress allowlist (Docker compose level, not a P1 task)
- Sandbox tiering (Hermes-provided; no P1 task)
- Telegram bot configuration (assumed done at bootstrap; runbook exists)
- LiteLLM budget cap enforcement (test exists; live wiring at proxy level)
- Skill-extractor autonomous skill creation (Hermes-provided)

**Each is a Phase 1 acceptance dependency.** If any is broken, acceptance fails — but no task owns fixing it. Plan needs an explicit "Phase γ-0: acceptance preflight" step that confirms each cross-cutting concern is operational before the manual test run.

---

## 3. Items my draft plan got right (callouts for the record)

- **The pre-work prologue** (sync main, prune worktrees, decide audit dir) — correct in shape; only needed to add `.worktrees/phase1` special handling.
- **Tasks 6 + 20b being APPEND-only to `config/limits.yaml`** — correct per HANDOFF.
- **The 3-way parallel for the missing subsystems** — correct, **but** with the dependency re-shuffling from finding #2, the sequencing inside the parallel arm changes.
- **Naming `session-c`, `session-d`, `session-e`** — compliant with `docs/superpowers/session-coordination.md` §"Branch-name conformance."
- **APPEND-only `limits.yaml` discipline preventing conflicts** — correct.
- **Risk register categories** — directionally correct, just incomplete (this audit adds 5+ more risks).

---

## 4. Inventory of what's been verified live

- 8 containers running: `litellm-proxy` (healthy 32h), `hermes`, `phoenix`, `otel-collector`, `shell-sandbox`, `github-mcp`, + 2 ad-hoc github-mcp instances.
- 0 open PRs against `origin/main`.
- 1 open issue: #29 (healthcheck DOWN).
- 0 in-flight CI runs; last 10 runs all `success`.
- `origin/main` HEAD: `be9e544 docs(handoff): clarify Task 6 + 20b APPEND-only rule re user's 0b0cb06 commit (#32)`.
- Local `main` is 10 commits behind origin (still needs fast-forward, captured in plan Phase α step 1).
- 20 worktrees on disk, 19 clean, 1 dirty (`.worktrees/phase1` with 2 untracked files).

---

## 5. Pass 2 enrichment — verified against live stack + submodule + code

Pass 2 dispatched 3 parallel agents: (i) live-state probes, (ii) lib/+tests/ code analysis, (iii) Hermes submodule symbol verification. Their findings update the table in §1 with new live-stack defects and resolve every "unknown" from the prior Pass 1 list.

### 5.1 NEW live-stack blockers (none caught by Pass 1)

| # | Defect | Evidence | Severity | Owner |
|---|---|---|---|---|
| B1 | **OTel collector → Phoenix URL is double-prefixed `/v1/traces/v1/traces` → HTTP 405; 0 traces in Phoenix** | `deploy/otel/collector.dev.yaml:21` sets endpoint `http://phoenix:6006/v1/traces`; OTLP HTTP exporter auto-appends `/v1/traces` again. Collector logs: `error exporting items, request to http://phoenix:6006/v1/traces/v1/traces responded with HTTP Status Code 405`. Phoenix `/v1/projects` returns `{"data":[{"name":"default","traceCount":0}]}`. | 🔴 P0 — acceptance step 4 will fail | Pre-work |
| B2 | **Phoenix ports `4317` + `6006` are NOT published to host** | `docker inspect autonomous-agent-phoenix-1` → both ports map to `[]`. `curl localhost:6006` → connection refused. The dev override at `deploy/docker-compose.dev.yml:5-9` would publish them, but the running stack came up with `docker-compose.yml` only. | 🔴 P0 — acceptance step 4 cannot run | Pre-work |
| B3 | **Hermes container has no DNS resolution to Telegram → bot non-functional** | Logs: `Telegram network error: Name or service not known`. Hermes is on `internal` network only; `egress` network is not attached. Token may be valid but the gateway can't resolve `api.telegram.org`. | 🔴 P0 — acceptance step 1 cannot run | Pre-work |
| B4 | **Healthcheck-ping cron always reports failure** (root cause of open issue #29) | Cron IS installed (`*/5 * * * * .../healthcheck-ping.sh >> logs/healthcheck.log 2>&1`). `logs/` dir does not exist. Script line 29 looks for service `hermes-agent`; actual compose service is `hermes` → grep never matches → script pings `/fail` every run. Additionally, hermes service has no `healthcheck:` block in compose, so `"Health"` field would never appear anyway. | 🟠 P1 — script + compose both wrong | Pre-work |
| B5 | **`lib/scrubber.py` is NOT wired into the live pipeline** | `deploy/litellm/config.yaml` has `callbacks: ["otel"]` only — no custom callback referencing `lib.scrubber`, no `guardrails:` block. Only the patterns YAML is mounted into LiteLLM (`deploy/docker-compose.yml:217`); the Python module isn't. Hermes side: no `import lib.scrubber` anywhere. Zero writers of `/data/secret-leak-attempts.log` in production code. | 🟠 P1 — acceptance step 5 will "pass" trivially because nothing writes the file; this is a false-positive pass | Phase γ-prep |

### 5.2 Pass 1 unknowns — now resolved

| Pass-1 question | Resolution |
|---|---|
| (1) Does `lib/evaluators/judge.py` need failure-matrix retrofit? | **NO**. `judge.py` is matrix-agnostic; the only F-code references are 2 cosmetic string literals in `lib/evaluators/consensus.py:90, :123` (`rationale="F60 -> escalate to 5th judge"`). They are not load-bearing — P0-A in audit-plan.md is **downgraded** to optional cosmetic cleanup. |
| (2) Status of the 7 integration tests | **6 of 7 likely-fail today.** Only `test_p1_2_judge_panel.py` cleanly guards with `@pytest.mark.skipif(not _proxy_reachable())`. The other 6 (`full_turn`, `budget_cap`, `chroma_outage`, `secret_leak`, `sandbox_isolation`, `skill_creation`) error rather than skip when their prerequisites aren't met — and most require endpoints/hooks that are **not implemented in P1 code** (`/v1/admin/limits`, `degraded[]` response field, `_test_inject_response`, `/v1/nudges/skill_extractor/run`). These are scaffolds for cross-cutting acceptance concerns; some won't go green until features land that no current P1 task owns. |
| (3) Hermes submodule pin `ddb8d8f` symbols | **All 8 cited symbols verified.** `delegate_task` at line 1909 ✓; `Task` schema at 559-673 with all 10 fields ✓; `VALID_STATUSES` at line 93 ✓; SQLite WAL+CAS at 61-68 ✓; `_load_checkpoint` at 688, `_save_checkpoint` at 715, `--resume` flag ✓; `AGENTS.md:325` `checkpoints` config section ✓ (no longer marked "not yet implemented"); `AGENTS.md:465-489` register(ctx) contract with hooks `pre_tool_call/post_tool_call/pre_llm_call/post_llm_call/on_session_start/on_session_end` ✓; `toolsets.py` clarify tool ✓. **P1-G from audit-plan.md is CLOSED.** |
| (4) Live scrubber wiring | **NOT wired** (see B5 above). |
| (5) OTel pipeline | **BROKEN with new defect** (see B1, B2 above). Original P0-4 (4317/4318 mismatch) is resolved; new defect (double `/v1/traces`) takes its place. |
| (6) Telegram bot live-status | **Token in container, but no DNS — non-functional** (see B3 above). |
| (7) Skill-extractor config | **Configured.** `config/hermes/cli-config.yaml:34-38` declares `skills.enabled: true, skills_dir: /app/skills, extractor.min_turns: 10, min_distinct_tools: 3`. Whether the upstream extractor actually fires is a runtime question; config is in place. |
| (8) CHANGELOG P0-5 duplicate | **CLOSED.** No duplicate "Worktree-per-phase" bullet in `[Unreleased]/Added` — only one occurrence in `[0.0.1-phase1.merge]` Added. P0-5 already resolved. |
| (9) Bootstrap dry-run | **No dry-run mode.** 6 steps: verify-prereqs → decrypt-secrets → limits-validator → compose pull+build → compose up + sleep 10 → smoke.sh. Inspection-only is safe. |
| (10) Issue #29 root cause | **Healthcheck script bug, not a real outage.** Cron is installed and firing; script always misclassifies service-not-healthy. See B4. |

### 5.3 NEW architectural finding from Pass 2

🟠 **P1-3 + P1-4 may share `register()` in a single `lib/durability/__init__.py`**, per spec `docs/superpowers/specs/2026-05-15-phase1-design-alignment.md:332` as cited by Pass 2 Agent 2: "P1-3/P1-4 share that single `register()` (per spec L332) — only one `on_session_start` registration per package, with internal call ordering controlling resume→inject sequence (since Hermes does not guarantee hook iteration order)."

**If this is correct**, two parallel sessions cannot own P1-3 and P1-4 simultaneously without coordinating on `__init__.py`. **Audit Pass 3 must verify this against the spec** before the coordination design locks in.

Two possible interpretations to verify:
- (a) P1-3 lives in `lib/durability/` and P1-4 lives in `lib/memory/` (separate packages → separate register()s) — then no coupling, parallel safe
- (b) P1-3 + P1-4 both live in `lib/durability/` (or one shared package) — then coupling is real and the parallel plan needs revision

The handoff doc and the audit-plan paths suggest (a): `lib/durability/checkpoint.py` for P1-3, `lib/memory/rejected.py` for P1-4. But the Pass 2 finding contradicts this. Resolution: read spec line 332 directly before finalizing the design.

### 5.4 NEW dependency finding: P1-6 → P1-2 follow-up is COSMETIC ONLY

🟢 **DOWNGRADE.** Pass 1 P0-A (failure-matrix retrofit) is no longer P0. `lib/evaluators/judge.py` doesn't reference the matrix at all; only `consensus.py:90, :123` have hardcoded F60 string literals in `rationale=` fields. When P1-6's `failure_matrix.py` lands, the only follow-up is an optional `chore(evaluators): replace hardcoded F60 strings with matrix lookup` PR (~30 min). No retrofit blocker.

### 5.5 The integration vehicle question is even more urgent

Given B1, B2, B3 above, the second-half work needs an integration branch where the OTel + Telegram + scrubber fixes can compose with the new subsystems before main accepts. P0-B from audit-plan.md (pick `phase/1-completion`) is **strengthened** — it's not just a convention concern, it's a real merge-isolation need now that we know main has 3 live-stack defects.
