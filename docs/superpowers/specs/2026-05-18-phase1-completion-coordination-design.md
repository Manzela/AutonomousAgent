---
title: "Phase 1 completion — coordination design"
created: 2026-05-18
authors: ["Daniel Manzela", "Claude Opus 4.7"]
status: draft (awaiting review)
applies_to: "AutonomousAgent Phase 1 — second half (post-2026-05-17 handoff) through acceptance"
supersedes: none
references:
  - docs/superpowers/HANDOFF-2026-05-17.md
  - docs/superpowers/plans/2026-05-15-phase1-10x-implementation.md
  - docs/superpowers/specs/2026-05-15-phase1-design-alignment.md
  - docs/superpowers/session-coordination.md
  - docs/runbooks/phase1-acceptance.md
  - audit/audit-plan.md (pre-existing 10× audit)
  - audit/phase1-completion-sweep-2026-05-18/{findings.md, audit-plan.md} (this design's audit)
---

# Phase 1 completion — coordination design

## 0. TL;DR

Drive AutonomousAgent Phase 1 to **Task 39 acceptance PASS** by combining (a) three live-stack defect fixes that block acceptance today, (b) the two short `config/limits.yaml` appends already documented as pending, (c) the four missing subsystems (P1-3 / P1-4 / P1-5 / P1-6) implemented across three parallel sessions, and (d) a split closeout where the assistant runs preflight checks then hands off to the human for the manual acceptance walk-through.

Topology: **Phase α-0 → Phase α → Phase β → Phase γ-prep → Phase γ-acceptance.** All work converges on a `phase/1-completion` integration branch before promoting to `main` via a single `--no-ff` PR and `phase1-accepted` tag.

## 1. Goal & success criteria

### 1.1 Goal

Phase 1 is "done" when `docs/runbooks/phase1-acceptance.md` passes all 7 acceptance criteria on a `phase1-accepted`-tagged commit on `main`:

1. All 10 manual Telegram messages get coherent replies
2. ≥3 distinct tools invoked across the 10 messages
3. ≥1 skill autonomously created (visible in `/app/skills`)
4. State persists across hermes-agent container restart
5. Traces visible in Phoenix UI at `localhost:6006`
6. No critical entries in `/data/secret-leak-attempts.log` (or file absent; see §6.5 for caveat)
7. Daily spend recorded in LiteLLM, well under $500 cap

### 1.2 Non-goals (out of scope)

- Phase 2 cloud-prod migration (GCP VM, Secret Manager, Cloud Trace) — separate plan.
- Phase 3 Multi-LLM Specialization Mesh (self-hosted Qwen on A100) — separate plan.
- Phase 4 Atropos trajectory pipeline + RL training — separate plan.
- The cosmetic F60 string-literal cleanup in `lib/evaluators/consensus.py:90, :123` (downgraded by audit Pass 2; optional ~30-min follow-up after P1-6 lands).
- Wiring `lib/scrubber.py` as a live LiteLLM callback (audit B5; flagged as a known false-positive on acceptance step 5; defer to Phase 2 or as a separate hardening PR).

### 1.3 Done-criteria for this design (i.e., what makes the spec ready to convert to an implementation plan)

- All 5 audit-discovered P0 items absorbed (P0-B integration vehicle, P0-C γ split, P0-D OTel URL fix, P0-E Phoenix ports, P0-F Telegram DNS, P0-G integration-test triage matrix).
- All 6 P1 items absorbed (P1-A α-0 prelude, P1-B smoke doc, P1-C `/cancel` cross-module note, P1-D `.worktrees/phase1` handling, P1-H P1-3/P1-4 coupling resolved, P1-I healthcheck dual-fix).
- P2 risks surfaced in §9 risk register.
- Sessions c/d/e have unambiguous briefs sufficient for a fresh Claude Code session to claim and execute a track.

## 2. Session topology

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│  Phase α-0  THIS session         Live-stack defect fixes (3 small PRs)          │
│              → unblocks Phase γ acceptance ahead of time                        │
├─────────────────────────────────────────────────────────────────────────────────┤
│  Phase α    THIS session         Pre-work cleanup + Tasks 6/20b + P1-6          │
│              → ends with `lib/durability/__init__.py` scaffolded register()     │
│                 wiring P1-6 hooks (real) + P1-3 + P1-4 hooks (stubs)            │
├─────────────────────────────────────────────────────────────────────────────────┤
│  Phase β    THREE fresh sessions, concurrent against `phase/1-completion`:      │
│                                                                                 │
│              session-c  ──→  P1-3  Per-step checkpoint + resume                 │
│                              (Tasks 22-28; fills resume hook stub from Phase α) │
│                                                                                 │
│              session-d  ──→  P1-4  REJECTED.md institutional memory             │
│                              (Tasks 29-33; fills inject hook stub from Phase α) │
│                                                                                 │
│              session-e  ──→  P1-5  Kanban → Telegram bridge                     │
│                              (Tasks 34-38; new `lib/kanban/` package)           │
├─────────────────────────────────────────────────────────────────────────────────┤
│  Phase γ-prep         THIS session    Acceptance preflight                      │
│                                       (smoke + pytest + 6-test triage matrix)   │
├─────────────────────────────────────────────────────────────────────────────────┤
│  Phase γ-acceptance   THE HUMAN       10 manual Telegram messages + Phoenix UI  │
│                                       inspection per acceptance runbook         │
├─────────────────────────────────────────────────────────────────────────────────┤
│  Promotion            THIS session    `phase/1-completion` → `main` via --no-ff │
│                                       PR + `phase1-accepted` tag                │
└─────────────────────────────────────────────────────────────────────────────────┘
```

### 2.1 Why this shape

- **Phase α-0 first**: live-stack defects are independent of subsystem work, can ship as 3 small isolated PRs, and unblock Phase γ-acceptance to run *at all*. Doing them last would mean discovering them during the human walk-through.
- **Phase α before β**: P1-6 declares the failure-matrix classifier interface and lands the `lib/durability/__init__.py` register() scaffold that sessions c + d will extend. Without α, β can't start (sessions would race on the same file).
- **Phase β truly parallel**: sessions c, d, e each touch disjoint files except for the pre-scaffolded `lib/durability/__init__.py` (where c fills a stub, d fills a different stub) and `config/limits.yaml` (APPEND-only, three distinct top-level keys).
- **Phase γ split**: acceptance is partly automatable (preflight) and partly human (Telegram + browser). Pretending otherwise creates a "ready" signal that isn't real.

## 3. Phase α-0 — Live-stack defect fixes (this session)

Three independent PRs against `phase/1-completion`. Each is a small fix with focused commit + verification.

### 3.1 PR α-0.1 · OTel collector → Phoenix URL fix

**Defect.** `deploy/otel/collector.dev.yaml:21` configures exporter endpoint as `http://phoenix:6006/v1/traces`. The OTLP HTTP exporter SDK auto-appends `/v1/traces` to the configured endpoint, producing `http://phoenix:6006/v1/traces/v1/traces` → HTTP 405. Result: Phoenix has 0 traces; acceptance step 4 cannot pass.

**Fix.** Edit `deploy/otel/collector.dev.yaml:21` — change `endpoint: http://phoenix:6006/v1/traces` to `endpoint: http://phoenix:6006`. Same fix for `collector.prod.yaml` if equivalent line exists.

**Verification.** `docker compose -f deploy/docker-compose.yml -f deploy/docker-compose.dev.yml restart otel-collector`; tail collector logs for absence of `405`; send one live agent turn; curl Phoenix `/v1/projects` and confirm `traceCount > 0`.

**Commit.** `fix(otel): correct collector endpoint to avoid double /v1/traces suffix`

### 3.2 PR α-0.2 · Phoenix port publishing

**Defect.** Running stack came up with `docker-compose.yml` only. The dev override at `docker-compose.dev.yml:5-9` publishes Phoenix's `4317` and `6006` ports to the host, but it isn't applied. `docker inspect autonomous-agent-phoenix-1` confirms both port maps are empty. `curl localhost:6006` → connection refused. Acceptance step 4 requires the human to open `localhost:6006` in a browser — impossible today.

**Fix decision (pick one in PR):**
- **(Recommended)** Move both Phoenix port publishings (`4317:4317` OTLP gRPC + `6006:6006` UI + OTLP HTTP) into base `docker-compose.yml` (acceptance is a Phase 1 requirement, not a dev-only feature). Remove the now-redundant entries from `docker-compose.dev.yml`.
- Alternative: bring stack up with `-f deploy/docker-compose.dev.yml` and update bootstrap.sh + acceptance runbook to require the override.

**Verification.** After PR + stack restart: `docker port autonomous-agent-phoenix-1` shows host bindings; `curl http://localhost:6006/` returns Phoenix UI shell HTML (HTTP 200).

**Commit.** `fix(deploy): publish phoenix ports in base compose so acceptance can reach UI`

### 3.3 PR α-0.3 · Hermes container DNS egress

**Defect.** `hermes` service in `deploy/docker-compose.yml` is attached only to the `internal` network. The `egress` network exists in the compose file but is not attached to the hermes service. Container logs show `Telegram network error: Name or service not known` — DNS resolution to `api.telegram.org` fails. Acceptance step 1 (the 10-message Telegram walk-through) cannot run.

**Fix decision (pick one in PR):**
- **(Recommended)** Attach `egress` network to hermes service in `deploy/docker-compose.yml` (matches the existing architecture pattern — the network exists for exactly this purpose).
- Alternative: configure `dns:` block on hermes service with explicit DNS resolvers.

**Verification.** After restart: `docker exec autonomous-agent-hermes-1 nslookup api.telegram.org` resolves; hermes logs no longer show DNS errors; one test Telegram message round-trips end-to-end.

**Commit.** `fix(deploy): attach egress network to hermes so bot can reach api.telegram.org`

### 3.4 PR α-0.4 · Healthcheck cron dual-fix (closes issue #29)

**Defect (compound).** (a) `scripts/healthcheck-ping.sh:29` looks for compose service `hermes-agent`; actual service name is `hermes`. The grep never matches, so the script always pings `${URL}/fail`. (b) The hermes service in `docker-compose.yml` has no `healthcheck:` block, so `"Health"` field never appears in `docker inspect` JSON regardless of service name. (c) `logs/` directory does not exist at repo root; the cron's `>> logs/healthcheck.log` redirect silently no-ops.

**Fix.**
- `scripts/healthcheck-ping.sh:29` — replace `hermes-agent` with `hermes`.
- `deploy/docker-compose.yml` — add `healthcheck:` block to hermes service (curl localhost gateway endpoint or `pgrep` for hermes-cli process).
- `.gitignore` — add `logs/` if not already.
- One-line `mkdir -p logs` in `scripts/healthcheck-ping.sh` prelude (idempotent).

**Verification.** `./scripts/healthcheck-ping.sh` exits 0 with the stack healthy; cron tick produces a non-empty `logs/healthcheck.log` line within 5 minutes; issue #29 closed manually after first successful ping.

**Commit.** `fix(healthcheck): correct service name + add compose healthcheck + ensure logs dir`

### 3.5 Phase α-0 acceptance

All four PRs merged into `phase/1-completion`. Live stack restart. Smoke-check 7/7 PASS. Phoenix has ≥1 trace. Telegram bot replies to `/start`. Issue #29 closed.

## 4. Phase α — Pre-work cleanup + Tasks 6/20b + P1-6 (this session)

### 4.1 Pre-work prelude (4 sub-steps; can be one PR or four)

1. **Sync local main + create integration branch.**
   ```bash
   git checkout main && git pull --ff-only origin main
   git checkout -b phase/1-completion origin/main
   git push -u origin phase/1-completion
   ```
2. **Archive the local pre-existing `audit/phase1-unblock-2026-05-15/` directory** (user's decision per prior conversation: archive outside repo). Move to `~/RX-Research Project/AutonomousAgent-archives/phase1-unblock-2026-05-15/`.
3. **`.worktrees/phase1` special-case handling** (P1-D from audit).
   - Inspect the 2 dirty files: `docs/architecture/failure-matrix.md` (compare against the version PR #31 committed; if identical, the local draft is stale and can be discarded), `docs/superpowers/session-coordination.md` (216-line variant; HANDOFF §5 chose to preserve via `SESSION-LOG-2026-05-15-to-17.md` at a different path — local 216-line variant is OBSOLETE).
   - With user confirmation, discard both untracked files in `.worktrees/phase1`, then `git worktree remove .worktrees/phase1`.
4. **Prune remaining 19 worktrees** (per HANDOFF §5 script; all confirmed clean per audit Pass 1).
5. **Smoke-doc drift fix** (P1-B). Single doc PR: README + HANDOFF + acceptance runbook all updated from "9 checks" to "7 checks". Title: `docs: align smoke check count (7, not 9) across README, HANDOFF, acceptance runbook`.

### 4.2 PR α.1 · Tasks 6 + 20b — APPEND `anchors:` and `evaluators:` sections to `config/limits.yaml`

**Scope.** Two APPEND-only sections, one PR.

**`anchors:` section** (Task 6 — keys from implementation plan):
```yaml
anchors:
  max_clarification_questions: 6
  lock_confidence_threshold: 0.85
  draft_silence_lock_h: 12
  draft_locked_silence_escalate_h: 24
  spec_storage_dir: /data/specs
```

**`evaluators:` section** (Task 20b — keys from implementation plan; note `vertex_ai/gemini-3.1-pro-preview` model id with `-preview` suffix):
```yaml
evaluators:
  axes: [code-correctness, safety, scope-fit, completeness]
  consensus:
    accept_threshold: 3   # of 4 judges
    reject_threshold: 3
    on_split: escalate_to_5th_judge
    fifth_judge_model: vertex_ai/claude-opus-4-7
  rejection_repeat_threshold: 3
  judge_timeout_s: 120
  parallel_judges_max: 4
  per_axis_model:
    code-correctness: vertex_ai/claude-sonnet-4-6
    safety: vertex_ai/claude-opus-4-7
    scope-fit: vertex_ai/claude-sonnet-4-6
    completeness: vertex_ai/gemini-3.1-pro-preview
```

**APPEND-only discipline.** Preserve user's existing keys (`budget.daily_usd_cap: 500`, `agent.dynamic_guardrails: true`, `agent.telegram_escalation_timeout_h: 24`). Append the two new top-level keys at end-of-file with a blank line separator.

**Verification.** `python lib/limits_validator.py config/limits.yaml` passes; smoke check 6 (`limits.yaml valid`) passes; `lib/anchors/__init__.py` and `lib/evaluators/__init__.py` can import these keys at runtime (verify with a unit test or REPL).

**Commit.** `feat(config): append anchors + evaluators sections to limits.yaml`

### 4.3 PR α.2 · P1-6 Durability subsystem (bundled, single PR)

**Scope.** All of P1-6 (Tasks 7-12) in one PR — internally coupled, easier to review as a unit.

**Files added/modified.**
- `lib/durability/failure_matrix.py` — Python lookup table mapping all 33 F-codes (F1-F33 per `docs/architecture/failure-matrix.md`) to trichotomy class (`FAIL_LOUD` / `FAIL_SOFT` / `SELF_HEAL`) + handler reference. Add 17 more modes (F17-F33) to bring the file from the initial 16 modes to 33.
- `lib/durability/trichotomy.py` — exception classifier (maps exception type/error message → F-code) + retry policy with exponential backoff/jitter (consumes `limits.yaml retries.self_heal.*`).
- `lib/durability/escalation.py` — 24h Telegram silence watcher. Cron-style periodic run (every 5 min). Reads Kanban DB for cards in `blocked` status with `last_heartbeat_at` older than `agent.telegram_escalation_timeout_h` hours; emits Telegram alert.
- `lib/durability/__init__.py` — **the scaffolded register() that sessions c + d will extend** (see §4.4 below).
- `docs/architecture/failure-matrix.md` — extend the existing 16-mode draft to 33 modes per the `audit/audit-plan.md` AA-Atelier sweep reference.
- `tests/unit/test_failure_matrix.py` — assert all 33 F-codes map to valid trichotomy classes; no duplicates; round-trip codes.
- `tests/unit/test_trichotomy.py` — classifier accuracy against 10+ exception types; retry-policy backoff timing within tolerance.
- `tests/integration/test_p1_6_failure_matrix.py` — exercise 5 representative modes end-to-end (rate-limit, timeout, OOM, parse-error, escalation) against the live LiteLLM proxy.
- **OPTIONAL within same PR**: `chore(evaluators): replace hardcoded F60 strings in consensus.py with matrix lookup` — closes the cosmetic P0-A downgrade.

**Hermes integration.** Periodic escalation runs as a docker-compose sidecar (preferred over host cron — matches the rest of the stack). Add `escalation-watcher` service to `docker-compose.yml`.

**Commit.** `feat(durability): add P1-6 failure matrix + trichotomy + escalation watcher`

### 4.4 The scaffolded `lib/durability/__init__.py` register() — the P1-H resolution

Per `docs/superpowers/specs/2026-05-15-phase1-design-alignment.md:332`, P1-3's resume hook AND P1-4's REJECTED-inject hook both register inside `lib/durability/__init__.py`'s single `register(ctx)` so call sequence controls ordering (resume → inject, since Hermes does not guarantee hook iteration order).

This file is therefore a **shared contract surface** between sessions c (P1-3) and d (P1-4). Phase α lands it once with explicit stubs:

```python
"""Durability plugin: failure-matrix-driven retry policy, checkpoint-resume, and REJECTED-inject."""
from lib.durability import failure_matrix, trichotomy, escalation

def register(ctx):
    # P1-6 hooks (real implementations from this PR)
    ctx.register_hook("pre_tool_call",  trichotomy.before_tool_call)
    ctx.register_hook("post_tool_call", trichotomy.after_tool_call)

    # P1-3 + P1-4 hooks (stubs; sessions c + d fill in)
    # ORDER MATTERS: resume must run first so REJECTED-inject can read active TaskSpec
    ctx.register_hook("on_session_start", _p1_3_resume_session)   # session-c fills
    ctx.register_hook("on_session_start", _p1_4_inject_rejected)  # session-d fills


def _p1_3_resume_session(ctx):
    """TODO(P1-3 session-c): on container start, scan /data/checkpoints/ for incomplete sessions
    and rehydrate the latest checkpoint per session. See lib/durability/checkpoint.py."""
    return None


def _p1_4_inject_rejected(ctx):
    """TODO(P1-4 session-d): read active TaskSpec.intent_category, load matching unexpired
    REJECTED.md entries, inject as system message: 'Past failed approaches for this kind of
    task — DO NOT repeat:'. See lib/memory/rejected.py."""
    return None
```

This scaffold means:
- Session-c's PR is `feat(durability): implement P1-3 checkpoint + resume` — touches `lib/durability/checkpoint.py`, `lib/durability/resume.py` (or fills the body of `_p1_3_resume_session` in-place if simpler), and `lib/durability/__init__.py` to swap the stub for a real call.
- Session-d's PR is `feat(memory): implement P1-4 REJECTED.md institutional memory` — touches `lib/memory/__init__.py`, `lib/memory/rejected.py`, and `lib/durability/__init__.py` to swap the stub for a real call.
- Both sessions edit `lib/durability/__init__.py` but in non-overlapping function bodies. Git auto-merges as long as both edit only their respective stub function.

### 4.5 Phase α acceptance

All α PRs merged into `phase/1-completion`. `pytest tests/unit/` and `pytest tests/integration/test_p1_6_failure_matrix.py` PASS. The scaffolded `lib/durability/__init__.py` exists with hook order correct. Phase β can launch.

## 5. Phase β — Three parallel sessions

### 5.1 Launch protocol

For each fresh session:
1. User launches Claude Code in a new terminal: `claude --add-dir "/Users/danielmanzela/Professional Profile"` (already authorized).
2. User provides the session brief (a per-session markdown file written by this session in Phase α; see §5.4 below).
3. New session reads brief, claims the ledger entry in `docs/superpowers/session-coordination.md`, creates branch + worktree per the project convention, and works from there.

### 5.2 Session briefs

This session writes three briefs in Phase α, one per parallel session:

- `docs/superpowers/SESSION-BRIEF-c-2026-05-18.md` — for P1-3 owner
- `docs/superpowers/SESSION-BRIEF-d-2026-05-18.md` — for P1-4 owner
- `docs/superpowers/SESSION-BRIEF-e-2026-05-18.md` — for P1-5 owner

Each brief contains:
- The subsystem's purpose + plan-section pointer (e.g., "P1-3 = `docs/superpowers/plans/2026-05-15-phase1-10x-implementation.md` §P1-3 tasks 22-28")
- Branch naming (e.g., `session-c/p1-3-task-NN-<slug>`) and worktree path
- The integration branch (`phase/1-completion`) to PR against
- Files this session owns (greenfield) vs. files it must touch shared (esp. `lib/durability/__init__.py` for c+d)
- The exact `register()` stub to fill (verbatim function name from §4.4)
- Hermes upstream symbols this session will consume (file:line at the pinned `ddb8d8f`, verified by audit Pass 2)
- The "do not touch" list (e.g., `lib/anchors/`, `lib/evaluators/`, `lib/durability/{failure_matrix,trichotomy,escalation}.py` — those are settled)
- Test scaffolds the session can promote from xfail/skip to passing (if any in the 6 broken integration tests)
- Commit convention reminders + branch-name regex
- The completion signal: when the session's last PR merges into `phase/1-completion`, the session updates the ledger entry to ✅ done

### 5.3 Session ownership map

| Session | Subsystem | New packages | Shared-file edits | Integration tests it should green |
|---|---|---|---|---|
| session-c | P1-3 Checkpointing | `lib/durability/checkpoint.py`, `lib/durability/resume.py` | `lib/durability/__init__.py` (fill `_p1_3_resume_session` stub), `config/limits.yaml` (APPEND `durability:` section) | `tests/integration/test_chroma_outage.py` (fail-soft on Chroma down — uses checkpoint to skip stale memory; needs P1-6 trichotomy + P1-3 resume) |
| session-d | P1-4 REJECTED.md | `lib/memory/__init__.py`, `lib/memory/rejected.py`, `lib/memory/intent_classifier.py` | `lib/durability/__init__.py` (fill `_p1_4_inject_rejected` stub), `lib/evaluators/consensus.py` (wire `lib/memory/rejected.append_entry(...)` call when `consecutive_rejections >= 3`, per design-alignment spec L333), `config/limits.yaml` (APPEND `memory:` section) | none directly |
| session-e | P1-5 Kanban→Telegram | `lib/kanban/__init__.py`, `lib/kanban/telegram_bridge.py`, `lib/kanban/notification_policy.py` | `lib/anchors/__init__.py:55` (replace `TODO(P1-5)` stub with real `/cancel <id>` handler), `config/limits.yaml` (APPEND `kanban:` section), `deploy/docker-compose.yml` (mount `hermes-data:/root/.hermes/kanban` for SQLite persistence) | optionally `tests/integration/test_full_turn.py` (a successful turn now creates a Kanban card + Telegram notification) |

### 5.4 Conflict-prevention rules (carried from `session-coordination.md`)

- **Disjoint files first.** Each session's primary work is in its own new package (`lib/durability/`, `lib/memory/`, `lib/kanban/`).
- **Shared-file edits documented in the brief.** Every shared-file touch is named explicitly so the session expects it (no surprise rebases).
- **APPEND-only `config/limits.yaml`.** Three distinct top-level keys (`durability:`, `memory:`, `kanban:`). First-merger wins; subsequent rebases re-append after current HEAD. If a session's PR fails limits-validator after a rebase, re-append the section after the new EOF.
- **`lib/durability/__init__.py` edits limited to stub bodies.** Sessions c + d both edit this file; each edits only their respective `_p1_3_resume_session` or `_p1_4_inject_rejected` function body. Git auto-merges; if conflict, the second session resolves by accepting both edits.
- **Update the ledger before claiming.** Each session adds its row to `docs/superpowers/session-coordination.md` §"Active sessions (Phase 1 completion)" before starting work, and marks ✅ when done.

### 5.5 Phase β acceptance

All three sessions' PRs merged into `phase/1-completion`. `pytest tests/unit/` PASS (94 + the new ones from P1-3/4/5/6). Phase γ-prep can run.

## 6. Phase γ-prep — Acceptance preflight (this session)

### 6.1 Sequence

Run on `phase/1-completion` HEAD after Phase β merges complete:

1. **Smoke test.** `bash scripts/smoke.sh` — all 7 checks PASS (note: P1-B already fixed the doc claim to 7).
2. **Unit tests.** `pytest tests/unit/` — all PASS.
3. **Integration test triage matrix (P0-G).** Of the 7 integration tests, apply the per-test disposition decided in Phase α:
   - `test_p1_2_judge_panel.py` — already passes (cleanly skip-guards when proxy unreachable; PASS when reachable).
   - `test_full_turn.py` — should now PASS (P1-5 created the Kanban-card-on-turn flow).
   - `test_chroma_outage.py` — should now PASS (P1-3 resume + P1-6 trichotomy handle Chroma fail-soft).
   - `test_budget_cap.py` — **DEFERRED** (needs `/v1/admin/limits` endpoint; mark `@pytest.mark.skip(reason="P2 — admin endpoint")` and document).
   - `test_secret_leak.py` — **DEFERRED** (needs `_test_inject_response` hook + live scrubber wiring; mark `@pytest.mark.skip(reason="P2 — live scrubber wiring + test hook")`).
   - `test_sandbox_isolation.py` — should now PASS (no new code needed; smoke check 5 already validates equivalent behavior).
   - `test_skill_creation.py` — **DEFERRED** (needs `/v1/nudges/skill_extractor/run` endpoint; mark `@pytest.mark.skip(reason="P2 — skill-extractor manual nudge endpoint")`). Note: autonomous skill creation will still be exercised by acceptance step 2 via the human walk-through.
4. **OTel/Phoenix.** Curl `http://localhost:6006/v1/projects` → `traceCount > 0`. Send one live agent turn (curl the gateway). Re-curl → `traceCount` incremented. Open Phoenix UI in browser; confirm at least one trace visible.
5. **Telegram preflight.** Send `/start` to `@Manzelagent_bot` from phone. Confirm bot replies. (DNS from α-0.3 confirmed.)
6. **LiteLLM spend tracking.** `docker compose exec litellm-proxy curl -fsS -H "Authorization: Bearer $(cat /run/secrets/litellm_master_key)" http://localhost:4000/spend/calculate` → non-null JSON.
7. **Skills dir exists, writable.** `docker compose exec hermes-agent ls -la /app/skills` → directory exists.

### 6.2 Preflight pass criteria

All 7 above PASS. If any fail, fix in a small `chore/p1-prep-fixes` PR against `phase/1-completion` before scheduling the human γ-acceptance walk-through.

### 6.3 Phase γ-prep deliverable

This session writes a one-page "ready-for-acceptance" report (`docs/runbooks/phase1-acceptance-prep-2026-05-NN.md`) listing each preflight check's status with timestamp, log snippet, and "GO / NO-GO" verdict. Then notifies the user with: "Stack is green for acceptance. Walk the runbook when you have ~30 minutes uninterrupted with your phone."

## 7. Phase γ-acceptance — Human walk-through

### 7.1 Pre-conditions

- Phase γ-prep reports GO.
- User has ~30 min uninterrupted.
- Phone has Telegram with `@Manzelagent_bot` reachable.
- Laptop browser open to `http://localhost:6006` (Phoenix UI).

### 7.2 The walk-through

Per `docs/runbooks/phase1-acceptance.md`:
1. Send the 10 specific Telegram messages (one at a time, wait for full reply).
2. Verify ≥3 distinct tools invoked across messages 2-6.
3. Verify autonomous skill creation: `docker compose exec hermes-agent ls /app/skills` shows ≥1 directory.
4. Verify state persistence: restart hermes container; from Telegram: "What did we just talk about?" → coherent summary.
5. Verify traces in Phoenix UI: filter `service.name=hermes-agent`, inspect one trace, confirm spans `turn.start`, `model.call`, `tool.dispatch`.
6. Verify no critical secret-leak entries: `docker compose exec hermes-agent test -f /data/secret-leak-attempts.log && cat …` → file absent OR empty. (NOTE: this is a known false-positive pass per audit B5 — `lib/scrubber.py` is not wired. Acceptance report will footnote this.)
7. Verify spend tracking: LiteLLM `/spend/calculate` shows non-zero spend, well under $500 cap.

### 7.3 Promotion

On all 7 pass:
1. `git checkout phase/1-completion && git pull`
2. Open promotion PR: `chore(phase1): accept Phase 1 — close out subsystems P1-3/4/5/6 + acceptance` (against `main`, `--no-ff` because branch protection allows squash only — adapt to squash with detailed body).
3. After merge, tag `phase1-accepted` on the resulting main HEAD; push tag; release-notes workflow auto-fires.
4. Close issue #29 if not already (α-0.4 should have).
5. Clean up: delete `phase/1-completion` branch on origin (kept local); prune any session-c/d/e worktrees from this round.

## 8. Coordination protocol summary

### 8.1 Branch + PR naming

Per `docs/superpowers/session-coordination.md`:
- Branch: `session-<letter>/<phase-tag>-task-<NN>-<slug>` (e.g., `session-c/p1-3-task-22-checkpoint-write`).
- Worktree: `.worktrees/session-<letter>-task-<NN>/`.
- PR title: Conventional Commits (`feat: …`, `fix: …`, `chore: …`).
- Branch-name regex compliance (per PR validation): `session-<letter>/<path>` is allowed.

### 8.2 PR base

All PRs base against `phase/1-completion`, NOT `main`. The integration branch is the convergence point.

### 8.3 Session ledger

`docs/superpowers/session-coordination.md` §"Active sessions" gains a Phase-1-completion table:

```markdown
### Active sessions (Phase 1 completion)

| Session | Track | Owner-since | Status | Branch | Notes |
|---|---|---|---|---|---|
| C | P1-3 (checkpointing) | YYYY-MM-DD | in-flight / done | session-c/p1-3-* | Fills _p1_3_resume_session stub |
| D | P1-4 (REJECTED.md) | YYYY-MM-DD | in-flight / done | session-d/p1-4-* | Fills _p1_4_inject_rejected stub |
| E | P1-5 (Kanban→Telegram) | YYYY-MM-DD | in-flight / done | session-e/p1-5-* | Replaces TODO(P1-5) in lib/anchors/__init__.py:55 |
```

Each session updates its row at start (in-flight) and at last-PR-merge (done).

### 8.4 Hermes-agent submodule pin

Pin stays at `ddb8d8f` for the duration of Phase 1 completion. All cited upstream symbols (`delegate_task@1909`, `Task@559-673`, `_load_checkpoint@688`, etc.) verified present at this pin by audit Pass 2. Sessions consume them as-is. Pin bump is Phase 2 scope.

## 9. Risk register

| Risk | Mitigation |
|---|---|
| **R1 · OTel pipeline still broken post-α-0.1** despite the URL fix (Phoenix has other defects) | α-0 acceptance includes "1 live turn → trace appears." If absent, escalate before launching β. |
| **R2 · Telegram DNS fix doesn't actually fix the connection** (deeper firewall issue) | α-0.3 acceptance includes a real `/start` message round-trip. If absent, escalate. |
| **R3 · Sessions c + d race on `lib/durability/__init__.py`** despite scaffolded stubs | First-merger wins; second session rebases and edits only their own stub. Conflict resolution doc in briefs. |
| **R4 · P1-3/P1-4 `on_session_start` hook ordering misbehaves** at runtime (Hermes ignores call sequence) | Integration test `tests/integration/test_p1_3_resume_then_p1_4_inject.py` asserts ordering via observable side-effects (capture log lines, assert sequence). |
| **R5 · The 3 deferred integration tests need to land in Phase 2, blocking Phase 2 acceptance** | Document in `docs/superpowers/specs/` as Phase 2 prerequisites; do not silently lose them. |
| **R6 · OTel span emission in `lib/evaluators/judge.py` is absent** so acceptance step 4 doesn't show evaluator spans (audit P2-A) | Pass-2-deferred: grep evaluators for OTel use in Phase γ-prep; if absent, add minimal `span.start_as_current("evaluator.dispatch")` instrumentation as a chore PR. |
| **R7 · Hermes Kanban status names mismatch project conventions** (audit P2-B: triage/todo/… vs BACKLOG/BRIEFING/…) | Session-e brief picks: accept Hermes' names verbatim (recommended; no fork). Add a presentation-layer mapping in the Telegram bridge if user-facing labels need different words. |
| **R8 · 24h escalation watcher hosting decision** (audit P2-C: cron vs sidecar vs Hermes background) | Spec §4.3 picks: docker-compose sidecar `escalation-watcher`. Documented in §4.3. |
| **R9 · `tests/integration/conftest.py` fixture assumptions** trip new sessions writing integration tests (audit P2-D) | Each session brief includes a "how to run integration tests in your subsystem" 1-liner referencing the conftest. |
| **R10 · `config/limits.yaml` merge order for three concurrent APPEND-only sessions** (audit P2-E) | APPEND-only at EOF with newline separator; first-merger wins; on rebase, re-append at new EOF. Documented in briefs. |
| **R11 · Scrubber `lib/scrubber.py` not wired** means acceptance step 5 passes trivially (audit B5) | Acceptance report footnotes the false-positive; wiring is Phase 2 hardening, captured in §1.2 non-goals. |
| **R12 · The 6 deferred integration tests need to stay xfail/skip with documented reasons** so future readers don't think they're already passing | Each `@pytest.mark.skip` has `reason="P2 — …"` argument; CI does not count them as failures. |

## 10. Open items requiring user input before implementation plan

None blocking. Two cosmetic preferences:

- **Q1 · Bundle vs. granular for P1-6 PRs.** Spec recommends bundling P1-6 (Tasks 7-12) into one PR for review coherence. User can override to one PR per task.
- **Q2 · Phase α-0 PR granularity.** Spec presents 4 distinct PRs (one per defect) for clean revert isolation. User can override to a single `fix(stack): live-stack defects` PR.

## 11. Estimated effort (for plan calibration)

| Phase | Wall-clock (single assistant session) | PRs |
|---|---|---|
| α-0 | 1.5-2 hr | 4 |
| α (pre-work + Tasks 6/20b + P1-6) | 4-6 hr | 3-4 |
| β (each parallel session, in their own time) | 4-8 hr per session | 1-2 per session (3-6 total) |
| γ-prep | 30-60 min | 0-1 (only if preflight fixes needed) |
| γ-acceptance | 30-45 min (human walk-through) | 1 (promotion to main) |

If sessions c/d/e run truly concurrently AND no rework is needed: end-to-end ~1-2 working days from α-0 start to `phase1-accepted` tag. Realistic estimate including 1-2 rounds of code-review iterations per subsystem: 3-5 working days.

## 12. Glossary

- **`phase/1-completion`** — the integration branch for this design's work. New; not to be confused with the deprecated `phase/1`.
- **Scaffolded register()** — a function file with real wiring for some hooks and TODO-stub bodies for others, designed to be filled in by later parallel sessions without merge conflicts.
- **P1-H** — the audit's name for the "P1-3 + P1-4 share register()" coupling, resolved here via §4.4.
- **F-code** — failure-mode identifier in `docs/architecture/failure-matrix.md` (F1-F33).
- **γ-prep vs γ-acceptance** — the split between assistant-driven preflight (`pytest`, `curl`, smoke checks) and human-driven acceptance (10 Telegram messages, Phoenix UI inspection).
