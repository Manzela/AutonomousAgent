# Execution Strategy — 2026-05-21 outstanding-threads roadmap

> **Approved approach:** Approach 3 — triple-worktree, per-plan branches, gate-synchronized waves.
> **Owner:** Orchestrator (this session).
> **REQUIRED SUB-SKILL for execution:** `superpowers:subagent-driven-development` (fresh subagent per task, two-stage review, model tiering).
> **Source spec:** `docs/superpowers/specs/2026-05-21-outstanding-threads-roadmap-design.md` (`ce3ee40`).

## Goal

Land Threads #1–#5 from the outstanding-threads roadmap with maximum safe parallelism, atomic per-plan PRs, and Iron-Law verification at every task boundary.

## Industry standards applied

- **Trunk-based development** — short-lived feature branches, atomic PRs (target <50 files each).
- **Critical chain protection** — A2A (10-day chain, "Google production MUST" priority) drives wall-clock; Plans A and C fit inside its envelope.
- **Theory of Constraints** — user attention is the bottleneck; batched into two gate windows (G1, G2).
- **Per-track filesystem isolation** — worktree-per-track prevents accidental cross-contamination.
- **TDD red-green-refactor** — preserved per-task from source plans; not re-derived here.
- **Iron Law (verification-before-completion)** — embedded at every task boundary; no completion claims without fresh verification evidence in the commit message.
- **Model tiering** — Sonnet implementer/reviewer default · Haiku mechanical lookups · Opus reserved for 3 architecture decisions (Day 5 auth, Day 8 AgentCard JCS, Task 9 J1 atomic flip).
- **Two-stage review** — implementer subagent + reviewer subagent per task (per `superpowers:subagent-driven-development`).

## Topology

### Worktrees + branches

| Track | Worktree path | Branch | Base | Status |
|---|---|---|---|---|
| T-A | `wt-framing-2` | `feat/framing-2-bolt-on` | (existing, 36 commits ahead of main) | EXISTS |
| T-B | `wt-a2a-spike` | `feat/a2a-spike-v0.1` | `main` | TO CREATE |
| T-C | `wt-j1-launch` | `feat/j1-launch-sequence` | `feat/phase-0a-h-plus` | TO CREATE |

Path prefix for all worktrees: `/Users/danielmanzela/RX-Research Project/`.

### Worktree creation commands

```bash
git -C "/Users/danielmanzela/RX-Research Project/wt-framing-2" worktree add \
  -b feat/a2a-spike-v0.1 \
  "/Users/danielmanzela/RX-Research Project/wt-a2a-spike" \
  main

git -C "/Users/danielmanzela/RX-Research Project/wt-framing-2" worktree add \
  -b feat/j1-launch-sequence \
  "/Users/danielmanzela/RX-Research Project/wt-j1-launch" \
  feat/phase-0a-h-plus
```

### Rationale for branch bases

- **T-B off `main`** — keeps A2A spike PR diff scoped to `lib/a2a/`, `tests/integration/a2a/`, `deploy/docker-compose.canary.yml` only. Reviewable independent of framing-2 cleanup.
- **T-C off `feat/phase-0a-h-plus` + merge `feat/framing-2-bolt-on`** — inherits in-flight phase-0a terraform context that Gemini Stream A is building on (smoke/chaos/acceptance scripts, runbook, deploy CI, otel fix). Merges framing-2 to also inherit the Persistence Trap foundation (`a847f1a` J3 shipper, `38856f2` contract tests, `47bbb45` spec, `0911028` Model Armor regional fix, `8cf3270` Postgres sub-module) which Plan C depends on. Wave 1A T-C Task 0 discovered this dependency was missing from h-plus-alone; spec amended 2026-05-21.

### Plan file location

The 3 plan files live on `feat/framing-2-bolt-on` (orchestration branch). Subagents in T-B and T-C worktrees read them by absolute path:

- Plan A: `/Users/danielmanzela/RX-Research Project/wt-framing-2/docs/superpowers/plans/2026-05-21-pr-merge-and-docker-skip-guard.md`
- Plan B: `/Users/danielmanzela/RX-Research Project/wt-framing-2/docs/superpowers/plans/2026-05-21-a2a-spike-day-0-10.md`
- Plan C: `/Users/danielmanzela/RX-Research Project/wt-framing-2/docs/superpowers/plans/2026-05-21-j1-unblock-sequence.md`

Cross-worktree reads are authorized per CLAUDE.md `additionalDirectories` config.

## Wave model

### Wave 1 — pre-gate fan-out

Intra-wave sequencing:

- **Wave 1A (parallel, ~6 agents)** — fires immediately.
- **Wave 1B (parallel, ~3 agents)** — fires after T-C Task 0 returns.
- **Wave 1C (1 agent)** — fires after T-C Tasks 1-3 return.

| Sub-wave | Track | Task | Model | Notes |
|---|---|---|---|---|
| 1A | T-A | Plan A Task 1 (Docker skip-guard mirror) | Sonnet | TDD red-green |
| 1A | T-A | Plan A Task 2 (synthesis row-1 root-cause addendum) | Haiku | Audit doc append |
| 1A | T-A | Plan A Task 3 — REVISED (record PR #112 already merged) | Haiku | 1-line audit entry |
| 1A | T-B | Plan B Day 0.1 (hermes submodule verify) | Haiku | `git submodule status` |
| 1A | T-B | Plan B Day 0.2 prep (draft sponsor 12-Q sign-off packet) | Sonnet | Output goes to G1 batch |
| 1A | T-C | Plan C Task 0 (re-verify foundation) | Sonnet | `Explore` subagent type |
| 1B | T-C | Plan C Task 1 (terraform GCS bucket append) | Sonnet | After Task 0 |
| 1B | T-C | Plan C Task 2 (terraform SM j3_shipper_config append) | Sonnet | After Task 0 |
| 1B | T-C | Plan C Task 3 (TDD shipper script + wiring tests) | Sonnet | After Task 0 |
| 1C | T-C | Plan C Task 4 (j1-launch-flip.md runbook draft) | Sonnet | After Tasks 1-3 |

Each implementer subagent commits its work on its track's branch with Iron-Law verification evidence in the commit message. A reviewer subagent (separate dispatch) verifies acceptance criteria per task.

### Gate batch G1 — user attention (one consolidated message)

Three asks bundled:

1. **Framing-2 PR open auth** — Plan A Task 5 prerequisite. Verbatim required: "GO to open PR for `feat/framing-2-bolt-on` → `main`."
2. **A2A 12-Q sponsor sign-off facilitation** — Plan B Day 0.2 prerequisite. The 12 questions packaged for sponsor review (user facilitates with sponsor, returns answers).
3. **Persistence Trap contract approval** — Plan C G1 prerequisite. Verbatim required: "Persistence Trap contract approved."

### Wave 2 — post-G1 fan-out

| Track | Tasks | Model |
|---|---|---|
| T-A | Plan A Task 5 (open framing-2 PR via `gh pr create`) | Sonnet |
| T-A | Plan A Task 6 (squash-merge framing-2 PR after CI green) | Sonnet |
| T-B | Plan B Day 1 (scaffold A2A package) | Sonnet |
| T-B | Plan B Day 2 (JSON-RPC dispatch via FastAPI) | Sonnet |
| T-B | Plan B Day 3 (TaskSpec ↔ A2A bridge skeleton) | Sonnet |
| T-B | Plan B Day 4 (SSE streaming — KILL-CRITERION GATE) | Sonnet |
| T-C | Plan C Task 5 (USER-APPROVAL.md memo writeup) | Sonnet |

Intra-wave: T-B Days 1→2→3→4 are sequential (each depends on prior). T-A Tasks 5→6 sequential (CI must pass before merge). T-C Task 5 independent.

### Gate G2 — user attention

**Stream A apply auth + Postgres $1,580/mo trigger acknowledgement.**

Verbatim required: "GO for Stream A apply via Gemini-CLI — acknowledge Postgres $1,580/mo cost trigger on RUNNABLE."

### Wave 3 — post-G2 fan-out

| Track | Tasks | Model | Notes |
|---|---|---|---|
| T-B | Plan B Day 5 (JWT auth + AgentIdentity + replay cache + audit log) | **Opus** | Architecture decision |
| T-B | Plan B Day 6 (OTel traceparent + dual-emit) | Sonnet | After Day 5 |
| T-B | Plan B Day 7 (TaskSpec ↔ A2A state-machine mapping) | Sonnet | After Day 6 |
| T-B | Plan B Day 8 (AgentCard JCS canonicalize + JWS sign) | **Opus** | Architecture decision |
| T-B | Plan B Day 9 (scrubber integration + canary compose) | Sonnet | After Day 8 |
| T-C | Plan C Task 6 (GCS bucket apply via Gemini-CLI — $0) | Sonnet | Delegates to Gemini-CLI |
| T-C | Plan C Task 7 (Model Armor re-apply via Gemini-CLI — ~$31/mo) | Sonnet | After Task 6 |
| T-C | Plan C Task 8 (Postgres apply via Gemini-CLI — $1,580/mo on RUNNABLE) | Sonnet | After Task 7 — cost-ascending strict sequence |
| T-C | Plan C Task 9 (J1 atomic flip + canary smoke + 4-token grep + systemd timer + evidence) | **Opus** | After Task 8 |
| T-C | Plan C Task 10 (memory closeout + MEMORY.md update + closeout audit memo) | Sonnet | After Task 9 |

T-C Tasks 6→7→8 strictly sequential per cost-ascending apply ladder (memory: `persistence_trap_contract.md`). T-B Days 5→6→7→8→9 sequential per A2A spike dependencies.

T-B and T-C tracks themselves run in parallel within Wave 3.

### Final — Plan B Day 10

| Track | Task | Model |
|---|---|---|
| T-B | Plan B Day 10 (docs/a2a-spike-handoff.md + sponsor sign-off + local tag `spike/a2a-v0.1`) | Sonnet |

## Authorization gates summary

| Gate | When | Verbatim text required | Track |
|---|---|---|---|
| G1.a | Pre-Wave-2 | "GO to open PR for `feat/framing-2-bolt-on` → `main`." | T-A |
| G1.b | Pre-Wave-2 | (Sponsor 12-Q sign-off — user-facilitated, not verbatim) | T-B |
| G1.c | Pre-Wave-2 | "Persistence Trap contract approved." | T-C |
| G2 | Pre-Wave-3 (T-C only) | "GO for Stream A apply via Gemini-CLI — acknowledge Postgres $1,580/mo cost trigger on RUNNABLE." | T-C |
| G3 | Post-Day-10 | "GO to open A2A spike PR" / "GO to push spike/a2a-v0.1 tag" | T-B (deferred — separate session) |

T-B can proceed Wave 2 Days 1-4 in parallel to G1.b being collected, since Days 1-4 don't depend on sponsor answers. Days 5+ may depend on sponsor positions — Day 5 dispatch checks for sponsor answers first.

### Standing prohibitions enforced across all tracks

- No `git push` to origin without explicit user-message auth
- No `gh pr create` without explicit user-message auth
- No `terraform destroy` / `gcloud delete` / VM teardown / Secret Manager rotation / IAM role removal
- No skipping hooks (`--no-verify` / `--no-gpg-sign`)
- No `git commit --amend` after hook failure (per hook-failure protocol: fix, re-stage, NEW commit)
- No commits of plaintext secrets (gitleaks + detect-secrets enforce in CI; pre-commit catches the obvious patterns)
- GCP applies delegated to Gemini-CLI (3.1 Pro Preview) via `gemini-gcp` skill — no direct `terraform apply` or `gcloud` invocations from Claude subagents

## Subagent dispatch model

Per `superpowers:subagent-driven-development`:

- **Fresh subagent per task** — no cross-task context bleed.
- **Two-stage review** — implementer subagent produces work + commit; reviewer subagent (separate dispatch, different prompt) verifies against task acceptance criteria + Iron Law evidence.
- **Model tiering** — table above; Opus reserved for 3 architecture decisions only.
- **Verification evidence in commit message** — every commit cites the exact command + relevant output snippet proving the claim per Iron Law.
- **Hook failure protocol** — on pre-commit failure, fix underlying issue, re-stage, NEW commit (never `--amend`).
- **Stream-of-record per track** — each track's commits form its audit trail; closeout memo at `audit/2026-05-21-execution-strategy-closeout.md` aggregates at end.
- **No background mode for these dispatches** — orchestrator needs subagent results to advance the wave, so dispatches run in foreground.

### Subagent briefing template

Every implementer subagent receives:

1. The plan file path + the exact task number(s) to execute (verbatim copy of the task block).
2. The worktree path to `cd` into / operate from.
3. The branch name expected (so subagent verifies before committing).
4. Explicit Iron Law instruction with the verification command(s) to run.
5. Authorization constraints (which gates are open for the task; which must be deferred).
6. Commit message format requirement (conventional commit + Iron Law evidence + Co-Authored-By trailer).
7. Hook failure protocol reminder.

## Plan A revision (inline)

Plan A Tasks 3 + 4 (verify PR #112 status + conditional squash-merge) are obsolete:

- PR #112 merged 2026-05-20T17:20:36Z (verified via `gh pr view 112 --json state` → "MERGED").
- Task 3 collapses to a 1-line audit log entry recording the merge.
- Task 4 is deleted.
- Tasks 5 + 6 (open + merge framing-2 PR) remain unchanged.

Plan A document update: append a note at the top of `docs/superpowers/plans/2026-05-21-pr-merge-and-docker-skip-guard.md` referencing this spec and noting Tasks 3-4 obsolescence.

## Verification checkpoints (Iron Law)

At every wave boundary, the orchestrator runs:

1. **Per-track branch state** — `git -C <worktree> log --oneline <base>..HEAD`
2. **Per-track working tree clean** — `git -C <worktree> status --short` returns empty
3. **Test suite green** — `uv run --extra dev pytest -q` on each track's worktree (for tracks that touched code)
4. **Lint clean** — `uv run --extra dev ruff check .`
5. **Format clean (authoritative)** — `pre-commit run ruff-format --all-files`
6. **Terraform validate** (T-C only) — `terraform validate` for `terraform/phase-0a-gcp/` root + sub-modules
7. **Subagent report cross-check** — agent summary claims verified against actual `git diff` and tool outputs

No wave advances without all applicable checks passing on completed tasks. Failures trigger remediation, not progression.

## Risk register

| ID | Risk | Mitigation |
|---|---|---|
| R-EX-1 | Subagents drift on long-running tasks (>1h wall-clock) | Time-box subagent dispatches; abort + dispatch fresh if no commit in 60 min |
| R-EX-2 | User-gate messages overlap and confuse user | Batch into G1 single message; G2 separate (cost-trigger needs isolation) |
| R-EX-3 | T-C terraform apply fails mid-ladder | Strict sequencing; Tasks 7 + 8 each preceded by `terraform plan` verification via Gemini-CLI |
| R-EX-4 | T-B Day 4 SSE kill-criterion fails | Plan B Day 4 has explicit halt + replan logic; honor it; do not auto-advance |
| R-EX-5 | T-A framing-2 PR CI fails (e.g., Docker skip-guard not effective) | Plan A Task 1 includes regression test; CI failure surfaces here, not at merge |
| R-EX-6 | Cross-track filesystem race (two subagents writing same file) | File-level disjoint analysis confirmed; worktree isolation enforces it |
| R-EX-7 | Gemini-CLI delegation fails (Stream A branch in flux) | T-C subagent verifies `feat/phase-0a-h-plus` HEAD + Stream A activity status before launching `terraform apply` |
| R-EX-8 | Iron Law violation accusation | Verification evidence in commit message + audit trail per track + reviewer subagent cross-check |
| R-EX-9 | Branch base drift — `main` or `feat/phase-0a-h-plus` advances mid-execution | Each subagent records base SHA at dispatch; rebase warnings surfaced at wave boundaries |
| R-EX-10 | Plan A revision incomplete — Tasks 3-4 still referenced by Tasks 5-6 | Plan A revision adds a top-banner note + audits Task 5-6 references explicitly |

## End-of-spec checklist (orchestrator-owned)

- [ ] Plan A revision banner written + committed
- [ ] 3 plan files + this spec committed on `feat/framing-2-bolt-on`
- [ ] T-B worktree created on `feat/a2a-spike-v0.1` off `main`
- [ ] T-C worktree created on `feat/j1-launch-sequence` off `feat/phase-0a-h-plus`
- [ ] Wave 1A dispatched (single multi-Agent call, 6 agents parallel)
- [ ] Wave 1A reviewer subagents dispatched per task
- [ ] Wave 1B dispatched (T-C Tasks 1-3 parallel post-Task-0)
- [ ] Wave 1C dispatched (T-C Task 4)
- [ ] G1 batched gate message sent to user
- [ ] G1 user response recorded
- [ ] Wave 2 dispatched
- [ ] Wave 2 reviewers dispatched
- [ ] G2 gate message sent to user
- [ ] G2 user response recorded
- [ ] Wave 3 dispatched (T-B Days 5-9 + T-C Tasks 6-10, with intra-track sequencing)
- [ ] Wave 3 reviewers dispatched
- [ ] Final Plan B Day 10 dispatched
- [ ] Closeout audit memo at `audit/2026-05-21-execution-strategy-closeout.md`
- [ ] Memory updates per memory protocol (project state + new branch tracker)

## File map

| Artifact | Path |
|---|---|
| This spec | `docs/superpowers/specs/2026-05-21-execution-strategy-design.md` |
| Source roadmap spec | `docs/superpowers/specs/2026-05-21-outstanding-threads-roadmap-design.md` |
| Plan A | `docs/superpowers/plans/2026-05-21-pr-merge-and-docker-skip-guard.md` |
| Plan B | `docs/superpowers/plans/2026-05-21-a2a-spike-day-0-10.md` |
| Plan C | `docs/superpowers/plans/2026-05-21-j1-unblock-sequence.md` |
| Closeout memo (to write at end) | `audit/2026-05-21-execution-strategy-closeout.md` |
