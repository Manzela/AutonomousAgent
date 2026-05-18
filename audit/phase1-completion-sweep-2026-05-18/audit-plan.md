# Audit Plan — Phase 1 completion coordination plan, gap-closure

**Date:** 2026-05-18
**Companion to:** `findings.md` in this directory
**Status:** Pass 1 (codebase-only). Pass 2 enrichment in flight.
**Decision required:** which of the gaps below to absorb into the Phase 1 completion design spec before it's written.

---

## Severity legend

- 🔴 **P0 — must fix in design before any code lands.** Plan is wrong or incomplete in a way that will produce broken work.
- 🟠 **P1 — should fix in design.** Plan omits something material; absence will cost a session-cycle or two to discover.
- 🟡 **P2 — should at least surface as a known risk.** Plan can ship without addressing, but ignoring is a roll of the dice.

---

## P0 — Design corrections that must land before any code

### P0-A · Resolve the P1-6 / P1-2 out-of-order merge ⚠️

**Gap.** Per `audit/audit-plan.md` (pre-existing) §"P1-6":
> "scoring rubrics for each judge MUST reference the failure-matrix from P1-6 … **Blocks**: P1-2 (evaluators reference matrix), so P1-6 should land BEFORE or concurrent with P1-2."

P1-2 already merged. P1-6 has not. So either:
- (a) The judges in `lib/evaluators/judge.py` are scoring *without* the matrix (degraded behavior; need retrofit once P1-6 lands), or
- (b) The judges hardcode placeholder F-codes (Pass 2 should grep the merged code for this).

**What the design must do.** Add an explicit "**P1-6 follow-up: rewire judges to consume failure_matrix.py**" task to Phase α (after P1-6 lands). Until then, the judges' rejection categories are an unstable contract. Without this task, P1-6 lands but P1-2's scoring is silently still degraded.

**Effort.** 0.5–1 day inside Phase α P1-6 PR (or a small follow-up PR `chore(evaluators): wire judges to failure_matrix`).

---

### P0-B · Pick an integration vehicle for the second half of Phase 1

**Gap.** Per `docs/superpowers/session-coordination.md` §"When sessions converge" + §"Conflict-prevention rules" #2: parallel-session work must converge through a per-phase integration branch where the full acceptance protocol runs **before** main accepts the work. Phase 1's vehicle was `phase/1`, now a trap. The draft plan asked sessions c/d/e to PR directly to `main`, which departs from this convention with no justification.

**What the design must do.** Pick one and write it into the spec:
- **(Recommended) Option B-1**: Create `phase/1-completion` as the second-half integration branch. Sessions c/d/e merge into it; Phase γ runs acceptance on it; if pass, `--no-ff` PR to main + tag `phase1-accepted`.
- **Option B-2**: Direct-to-main per task. Acceptance runs against main HEAD after the last subsystem merges. Departs from convention. Simpler but riskier (failure in subsystem N may force revert of N-1 already on main).
- **Option B-3**: One bundled "phase-1-completion" PR with all subsystems squashed. Worst for review, best for atomicity.

Recommend B-1 as the convention-compliant choice.

---

### P0-C · Re-classify Phase γ as **partially human-driven**, not assistant-driven

**Gap.** Acceptance runbook step 1 requires the user to **send 10 specific Telegram messages from a phone**; step 4 requires manual Phoenix UI inspection. The draft plan said "Phase γ — this session runs Task 39." A Claude session cannot do these.

**What the design must do.**
- Split Phase γ into **γ-prep** (assistant: spin up stack, run smoke + pytest + integration tests, verify all cross-cutting concerns) and **γ-acceptance** (human-in-the-loop: send the 10 Telegram messages, inspect Phoenix, confirm spend tracking).
- γ-prep deliverable is a "ready-for-acceptance" checklist sign-off the human walks through.
- Spec should name the assistant's pre-flight checks explicitly so the user knows exactly what's automated vs. what they have to do live.

---

## P1 — Additions / corrections the design should fold in

### P1-A · Add Phase α-0: P0 unblocker triage from the pre-existing audit-plan

**Gap.** The pre-existing `audit/audit-plan.md` enumerates 5 P0 unblockers. My draft plan ignored them.

**Disposition (verified during this sweep):**
- P0-1 (Telegram round-trip after `1a284de`): **unverified** — needs the user to confirm `@Manzelagent_bot` responds.
- P0-2 (empty `config/hermes/limits.yaml`): **closed** ✅ (file removed already).
- P0-3 (host pytest deps): **likely closed** (94 unit tests pass per HANDOFF).
- P0-4 (OTel traces reach Phoenix): **needs verification** (Phoenix is up; pipeline end-to-end unconfirmed).
- P0-5 (CHANGELOG duplicate bullet): **unverified** (Pass 2 task).

**What the design must do.** Add Phase α-0 (a single-PR prelude) that closes any remaining P0 items as confirmed open. Sequence: α-0 → α (limits.yaml appends + P1-6) → β (parallel subsystems) → γ.

---

### P1-B · Fix the smoke-doc drift

**Gap.** `scripts/smoke.sh` runs 7 checks. README, HANDOFF, and `docs/runbooks/phase1-acceptance.md` all say 9. Doc drift only — but the acceptance runbook precondition "smoke.sh passes all 9 checks" will fail-by-text even when the script passes.

**What the design must do.** Add a tiny doc-fix item to Phase α (single PR: `docs: smoke is 7 checks, not 9; sync README + acceptance runbook + HANDOFF`). 5-min PR. Without it, an honest acceptance report has to footnote the discrepancy.

---

### P1-C · Acknowledge cross-module touches (P1-5 ↔ anchors)

**Gap.** `lib/anchors/__init__.py:55` has `TODO(P1-5): /cancel <id> handled by kanban plugin.` So P1-5's session-e will touch `lib/anchors/__init__.py` for the `/cancel` slash command. Draft plan said "P1-5 is fully independent" — not quite.

**What the design must do.** Session-e's brief explicitly calls out that the `/cancel` slash-command wiring requires a small edit to `lib/anchors/__init__.py` (or, cleaner, a registration callback that anchors honors). Coordinate with whatever Phase α P1-6 work touches `__init__.py`.

---

### P1-D · Acknowledge the .worktrees/phase1 dirty state

**Gap.** Of the 20 worktrees on disk, `.worktrees/phase1` is the only one with uncommitted state (2 dirty files per `git -C ... status`). These are the untracked `failure-matrix.md` draft + 216-line `session-coordination.md` variant that HANDOFF §5 named. The draft plan said "prune ~10 worktrees" without naming the special case.

**What the design must do.** Phase α step 3 explicitly: for `.worktrees/phase1`, (a) decide whether the local 216-line `session-coordination.md` should replace the canonical 113-line version on main (probably no — HANDOFF chose to preserve it at a different path), (b) discard the failure-matrix local draft if it's stale vs. the version PR #31 committed, (c) THEN prune. Without this, pruning `.worktrees/phase1` loses the only on-disk copy of those drafts.

---

### P1-E · Add explicit cross-cutting acceptance preflight tasks to Phase γ-prep

**Gap.** Phase 1 acceptance presupposes that observability (OTel→Phoenix), secret scrubbing (live wiring of `lib/scrubber.py`), egress allowlist (sandbox isolation), and skill-extractor (autonomous skill creation in `/app/skills`) are all operational. None of these is owned by any pending Phase 1 task. If one is broken, acceptance fails.

**What the design must do.** Phase γ-prep includes a preflight checklist:
1. `pytest tests/integration/test_full_turn.py` — end-to-end turn works
2. `pytest tests/integration/test_secret_leak.py` — scrubber catches a planted secret
3. `pytest tests/integration/test_sandbox_isolation.py` — egress denied to sandbox
4. `pytest tests/integration/test_skill_creation.py` — skill-extractor fires
5. `pytest tests/integration/test_chroma_outage.py` — fail-soft on Chroma down (overlaps with P1-6 trichotomy)
6. `pytest tests/integration/test_budget_cap.py` — budget cap enforced at proxy
7. Curl Phoenix `localhost:6006`, verify traces present after a single live turn

If any fail, that's a Phase γ-prep blocker — fix in a small chore PR before scheduling the human γ-acceptance walk-through.

---

### P1-F · Decide healthcheck cron disposition

**Gap.** Issue #29 ("AutonomousAgent is DOWN") is open because `scripts/healthcheck-ping.sh` hasn't pinged healthchecks.io for a day. The runbook `docs/runbooks/healthcheck-cron-setup.md` exists but the cron may not be installed.

**Options:**
- (a) Install the cron now (`crontab -e` + the line from the runbook), close issue #29 once the next ping succeeds.
- (b) Defer healthcheck to Phase 2 (cloud-prod), close issue #29 with "Phase 2 scope" comment.
- (c) Leave it open; acceptance report footnotes the false-alarm.

**What the design must do.** Pick one; spec should state which. Recommend (a) — 10 minutes of work, removes a noisy open issue from the Phase 1 acceptance picture.

---

### P1-G · Verify hermes-agent submodule pin against cited symbols

**Gap.** P1-3 (checkpoint) and P1-5 (kanban) rely on Hermes upstream APIs cited at specific file:line in `audit/audit-plan.md`. The submodule is pinned at `ddb8d8f`. **No task in the plan verifies those symbols still exist at the pin.**

**What the design must do.** Add to Phase α-0 (or to each affected session brief): a single grep verification that `hermes-agent/tools/delegate_tool.py:1909` (delegate_task), `hermes-agent/hermes_cli/kanban_db.py:559-673` (kanban schema), and `hermes-agent/batch_runner.py` (`_load_checkpoint`/`_save_checkpoint`) exist at the pin. If they've moved, update the line refs in the session briefs.

---

## P2 — Risks to surface but not necessarily address now

### P2-A · P1-2 may have shipped without OTel span emission

The handoff says "94/94 unit tests pass + live integration test PASS." But does `lib/evaluators/judge.py` emit `evaluator.dispatch` / `evaluator.consensus` spans to OTel? Phase 1 acceptance step 4 requires "spans for `turn.start`, `model.call`, `tool.dispatch`." Evaluator dispatch may or may not be in that span family. Not a blocker, but Pass 2 should grep `lib/evaluators/` for any OTel SDK use.

### P2-B · The Hermes Kanban schema doesn't match the project's column names

Per `audit/audit-plan.md` P1-5 note: "Our column names (`BACKLOG`/`BRIEFING`/...) DON'T match Hermes' fixed enum (`triage`/`todo`/...). Either accept Hermes' names OR add a presentation-layer mapping." Session-e's brief should pick (default: accept Hermes' names per pre-existing recommendation).

### P2-C · The 24h Telegram escalation watcher requires a cron

P1-6 Task 10 (`lib/durability/escalation.py`) is a periodic watcher. Whether it runs as a cron, as a Hermes background task, or as a docker-compose sidecar is unspecified. Session-c (P1-6 owner if Phase α) needs to pick. Recommend docker-compose sidecar (matches the rest of the stack).

### P2-D · `tests/integration/conftest.py` may presume the full stack is up

If sessions c/d/e want to write integration tests, they need to know what fixtures `conftest.py` provides and what the test prerequisites are. Each session brief should include a 1-line "how to run integration tests in your subsystem" note.

### P2-E · The first session to merge their `config/limits.yaml` append wins; others rebase

With three parallel sessions appending different top-level keys (`durability:`, `memory:`, `kanban:`), git auto-merges trivially as long as the appends are at end-of-file with newlines between. The order of merges is non-deterministic; the last-merger rebases. Spec should call this out so session briefs include "if your PR fails the limits-validator check after a rebase, re-append to the new EOF."

---

## P3 — Items already closed / not actionable

| Audit-plan P0 item | Status |
|---|---|
| P0-2 — delete empty `config/hermes/limits.yaml` | ✅ Closed (file does not exist) |
| P0-3 — host pytest deps | ✅ Likely closed (94 unit tests pass per HANDOFF) |
| P1-2 (`lib/evaluators/`) | ✅ Merged to main (caveat: P0-A above re: missing failure-matrix retrofit) |
| P1-1 (`lib/anchors/`) | ✅ Merged to main (caveat: Task 6 stubs still TODO per finding 6) |

---

## Recommended absorption order into the design spec

Highest-leverage first. If the user only wants to absorb a subset, take the P0s + P1-A + P1-B + P1-E.

1. **P0-A** — failure-matrix retrofit (avoids shipping degraded scoring permanently).
2. **P0-B** — integration vehicle picked (`phase/1-completion` recommended).
3. **P0-C** — Phase γ split into γ-prep (assistant) + γ-acceptance (human).
4. **P1-A** — Phase α-0 added for the 3 unverified P0 items from the pre-existing audit.
5. **P1-E** — Phase γ-prep preflight checklist for cross-cutting concerns.
6. **P1-B** — smoke-doc drift one-line PR.
7. **P1-D** — `.worktrees/phase1` special-case handling.
8. **P1-C** — session-e brief mentions the `/cancel` cross-module touch.
9. **P1-F** — healthcheck cron disposition.
10. **P1-G** — submodule pin symbol verification.
11. **P2-A..E** — surface in the design's risk register.

---

## Changes from Pass 1

Pass 2 dispatched 3 parallel agents (live-state probes, code analysis, submodule verification). Below is the diff against Pass 1.

### Promoted to P0 (newly discovered live-stack defects)

- **P0-D · OTel collector→Phoenix double `/v1/traces` URL bug.** `deploy/otel/collector.dev.yaml:21` produces `…/v1/traces/v1/traces` after the SDK auto-appends, yielding HTTP 405 every push. Phoenix has 0 traces. Acceptance step 4 cannot pass. Fix: drop the trailing `/v1/traces` from the endpoint config. Effort: 5 min + 1 PR.
- **P0-E · Phoenix ports not published to host.** `docker inspect` confirms `4317` and `6006` map to `[]`. The running stack came up with `docker-compose.yml` only, not the `-f deploy/docker-compose.dev.yml` override that would publish them. Acceptance step 4 (human opens `localhost:6006`) cannot run. Fix: bring stack up with the dev override OR add port publish to base compose. Effort: 10 min.
- **P0-F · Telegram bot has no DNS egress.** Hermes container is on `internal` network only; `egress` network not attached. Logs show `Telegram network error: Name or service not known`. Acceptance step 1 (10 manual Telegram messages) cannot run. Fix: attach `egress` network to hermes service in `docker-compose.yml`, OR add `dns:` block, OR restructure networking. Effort: 30 min including verify.
- **P0-G · 6 of 7 integration tests will fail today.** Pass 2 confirmed all but `test_p1_2_judge_panel.py` will error on `pytest tests/integration/`. They depend on endpoints/hooks not implemented in P1 code (e.g., `/v1/admin/limits`, `degraded[]` response field, `_test_inject_response`, `/v1/nudges/skill_extractor/run`). Phase γ-prep checklist depends on these — needs explicit triage: which to make pass via new code, which to mark `@pytest.mark.skip` with a documented reason, which require backend that's out of P1 scope.

### Promoted to P1 (new architectural risk)

- **P1-H · Verify P1-3/P1-4 register() coupling.** Pass 2 Agent 2 cited spec `docs/superpowers/specs/2026-05-15-phase1-design-alignment.md:332` claiming `P1-3 + P1-4 share that single register() (per spec L332)`. If true, sessions c/d (P1-3 / P1-4) cannot proceed fully in parallel — they share a file. Audit Pass 3 must read L332 verbatim and resolve. If shared, the coordination plan must either (a) bundle P1-3 + P1-4 into one session, OR (b) have one session land a stub register() first that the other extends.
- **P1-I · Healthcheck script + compose dual bug.** Issue #29's root cause is a script bug (`scripts/healthcheck-ping.sh:29` looks for service `hermes-agent`; actual is `hermes`) PLUS the hermes service in compose has no `healthcheck:` block. Both must be fixed for the cron to ever succeed. Effort: 15 min single PR `fix(healthcheck): correct service name + add compose healthcheck block`.

### Downgraded / closed

- **P0-A (failure-matrix retrofit) → cosmetic only.** Pass 2 confirmed `lib/evaluators/judge.py` has zero matrix references. Only 2 hardcoded F60 string literals in `consensus.py:90, :123` (rationale text). No degraded-scoring problem on main today. Follow-up after P1-6 lands is an optional ~30 min PR, not a blocker.
- **P1-G (submodule pin symbol verification) → CLOSED.** All 8 cited symbols verified at `ddb8d8f`: `delegate_task@1909`, `Task@559-673`, `VALID_STATUSES@93`, WAL+CAS@61-68, `_load_checkpoint@688`, `_save_checkpoint@715`, `AGENTS.md:325` checkpoints section, `AGENTS.md:465-489` register(ctx) contract, `toolsets.py` clarify tool. No new files in `hermes-agent/` suggesting upstream added the missing subsystems. Pin is healthy.
- **P0-2 (empty `config/hermes/limits.yaml`) → CLOSED** (file absent).
- **P0-5 (CHANGELOG duplicate bullet) → CLOSED** (no duplicate in current `[Unreleased]/Added`).

### Pre-existing audit P0 items — final disposition

| Item | Status | Source |
|---|---|---|
| P0-1 — Telegram round-trip after `1a284de` | **STILL OPEN — different root cause than thought.** Container env has the token; bot is trying to connect; DNS resolution fails (P0-F above). Original `1a284de` fix was for LiteLLM auth, not networking. | Pass 2 Agent 1 |
| P0-2 — empty `config/hermes/limits.yaml` | CLOSED ✅ | Pass 1 ls check |
| P0-3 — host pytest deps | CLOSED ✅ (94 unit tests pass per HANDOFF) | Pass 1 |
| P0-4 — OTel reach Phoenix (port 4317↔4318) | **REPLACED.** Original mismatch resolved; new defect P0-D + P0-E above. | Pass 2 Agent 1 |
| P0-5 — CHANGELOG cleanup | CLOSED ✅ | Pass 2 Agent 2 |

### Updated recommended absorption order into design spec

Highest-leverage first. P0 items are non-negotiable for the spec; P1 items strongly recommended.

1. **P0-D** — OTel double-URL fix (acceptance dep)
2. **P0-E** — Phoenix port publishing (acceptance dep)
3. **P0-F** — Telegram DNS egress (acceptance dep)
4. **P0-B** — integration vehicle picked (`phase/1-completion` recommended) — now strengthened because main has live-stack defects to isolate from
5. **P0-C** — Phase γ split into γ-prep (assistant) + γ-acceptance (human)
6. **P0-G** — 6 broken integration tests: triage matrix (own / skip-with-reason / out-of-scope)
7. **P1-A** — Phase α-0 prelude (closes any remaining pre-existing audit P0 items: now just P0-1 = telegram, replaced by P0-F)
8. **P1-E** — Phase γ-prep cross-cutting preflight checklist (now references the triaged integration tests from P0-G)
9. **P1-H** — verify and resolve P1-3/P1-4 register() coupling
10. **P1-I** — healthcheck script + compose dual fix (closes issue #29)
11. **P1-B** — smoke-doc drift one-line PR (7 not 9)
12. **P1-D** — `.worktrees/phase1` special-case handling
13. **P1-C** — session-e brief mentions `/cancel` cross-module touch
14. **P1-F** — healthcheck disposition — now subsumed by P1-I
15. **P0-A** — failure-matrix cosmetic cleanup (downgraded; do after P1-6 lands or skip)
16. **P2-A..E** — risks in design's risk register

### Summary of severity

- 🔴 **5 P0 items** (must land in design): P0-B, P0-C, P0-D, P0-E, P0-F, plus the P0-G triage matrix
- 🟠 **6 P1 items** (should land): P1-A, P1-B, P1-C, P1-D, P1-E, P1-H, P1-I
- 🟢 **3 closed** since Pass 1: P0-A downgrade, P1-G close, P0-2/P0-3/P0-4/P0-5 confirmed already-closed
