# Findings — Resume + Orchestration Audit (2026-05-19)

> **Scope.** Where AutonomousAgent (the Hermes deployment) actually stands today, what
> orchestration surface is available *right now* for long-running autonomous goals, and
> what can go wrong if we run unattended for hours / days. Pass-1 (codebase-only). Pass-2
> reference enrichment to follow before the approval gate.

---

## §1 — Authoritative state (verified live, 2026-05-19)

### 1.1 Git
- `main` HEAD = `c180892` (`feat(litellm): attach Postgres for spend tracking + fix runbook step 6 (#71)`).
- `git log origin/main..HEAD` = **empty** → fully in sync with remote.
- Latest tag = `phase1.0.1-accepted` (the rolled-up Phase-1 hotfix series, PRs #56–#63).
- **0 open issues**, **0 open PRs** on `github.com/Manzela/AutonomousAgent`. Everything in flight has shipped.

### 1.2 Containers (`docker ps`, 2026-05-19)
All 7 hermes services up `About an hour (healthy)`:

| Container | Status | Role |
|---|---|---|
| `autonomous-agent-hermes-1` | Up healthy | The agent loop itself |
| `autonomous-agent-litellm-proxy-1` | Up healthy | LLM gateway → Vertex AI |
| `autonomous-agent-github-mcp-1` | Up | Github MCP sidecar |
| `autonomous-agent-escalation-watcher-1` | Up | 24h Telegram silence → F32 escalation |
| `autonomous-agent-phoenix-1` | Up | OTel trace viewer (`localhost:6006`) |
| `autonomous-agent-shell-sandbox-1` | Up | Tool sandbox for agent shells |
| `autonomous-agent-otel-collector-1` | Up | Span / metric collector |

Three unrelated stray containers (`friendly_wilbur`, `admiring_payne`, `wonderful_chatelet`) are present but unowned by this compose stack — cosmetic only.

> **Caveat on count.** `README.md:55` still calls the stack "twelve services". Reality is 8 defined (`deploy/docker-compose.yml`) and 7 long-running. Doc has not been corrected; carried over from the prior audit as an unresolved P2.

### 1.3 Phase 1.1 closed-out (issues #53, #54, #55 — all CLOSED today)
- **#53 → #70**: `feat(observability): emit OpenInference attributes on Phoenix spans`. Verified in `lib/observability/__init__.py:18,83,239,328` — `openinference.span.kind` is emitted for both LLM and TOOL spans, matching the OpenInference semantic-conventions spec.
- **#54 → #69**: `feat(memory): wire hosted Honcho persistent memory provider`. `cli-config.yaml:37` activates `provider: honcho`; secrets are sourced from `secrets/honcho.env.sops`.
- **#55 → #71**: `feat(litellm): attach Postgres for spend tracking + fix runbook step 6`. The LiteLLM `/spend/*` endpoints now have a backing DB.

### 1.4 Latest 6 merged PRs (this calendar day, 2026-05-19)
`#71` (litellm spend DB) → `#70` (Phoenix OI attrs) → `#69` (Honcho wire) → `#68` (Honcho secret) → `#67` (handoff doc corrections, closes #50) → `#66` (Phase-1 spec drift notice).

---

## §2 — Drift from memory + prior handoff

### 2.1 Memory file `project_state_2026-05-17.md` is stale
| Field | Memory (2026-05-17) | Reality (2026-05-19) |
|---|---|---|
| Main HEAD | `b7738f9` | `c180892` |
| Phase 1.1 issues | "P1-3/4/5/6 pending" | All 3 (#53/#54/#55) CLOSED via #69/#70/#71 |
| Latest tag | `phase1-accepted` | `phase1.0.1-accepted` (+10 hotfix PRs) |

**Action.** Pass-2 should refresh `project_state_*` memory to a 2026-05-19 snapshot (see plan §P2.1).

### 2.2 Handoff `docs/superpowers/HANDOFF-2026-05-19.md` is mostly current but has 3 open follow-ups
The prior audit (`audit/handoff-doc-2026-05-19-review/`) found:
- **DEFECT-1 (resolved)** — missing master audit dir → rescued via PR #65.
- **DEFECT-2 (resolved)** — wrong issue list in handoff → corrected via PR #67.
- **DEFECT-3 (NOT resolved)** — `docs/superpowers/HANDOFF-2026-05-19.md` §7.3 still describes Phase-1 compose layout that was simplified during 1.0.1. No PR has touched §7.3 since. **Action: include in P2 cleanup pass.**

Four additional spec/code drifts were flagged by the deep re-verification pass of that same audit. Per `audit/handoff-doc-2026-05-19-review/findings.md`, none of those four have been actioned yet either.

---

## §3 — Uncommitted work in the working tree

```
 M config/hermes/cli-config.yaml         (+5 lines)
 M lib/kanban/telegram_bridge.py         (+139 / -16 lines)
 M tests/unit/test_kanban_telegram_bridge.py  (+75 lines)
?? .claude/                              (Claude Code local state — DO NOT commit)
?? audit/handoff-doc-2026-05-19-review/  (prior audit deliverables, unshipped)
?? uv.lock                               (Python lockfile — verify intent before commit)
```

### 3.1 `config/hermes/cli-config.yaml:121-123` — session-reset override
```yaml
# Overrides default 4 AM daily reset to preserve context for long-running autonomous goals.
session_reset:
  mode: none
```
**Why this matters.** Closes the §5.2 "ghost reset" finding from the prior audit by *explicitly opting out* of the implicit 04:00 reset. Without this commit on `main`, every container restart re-introduces the silent context drop that was masquerading as a feature. Ship as part of P0.

### 3.2 `lib/kanban/telegram_bridge.py` — `update_card_status` substantive rewrite
Adds:
- `_row_to_dict(row)` — sqlite Row → dict helper.
- `_find_task_by_session_or_task_id(conn, session_id, task_id)` — single-query lookup.
- `update_card_status(session_id, status)` rewritten to:
  1. Retrieve current card state.
  2. **Touch heartbeat** if status unchanged (no spurious Telegram alert).
  3. Transition via proper helpers (`block_task` / `complete_task`) when the status maps to a known terminal state; fall back to a single transaction otherwise.
  4. Fetch updated card and emit `send_alert()` only on real transitions.
  5. Fail-open exception handling so bridge failures never crash the agent loop.

Three matching unit tests in `tests/unit/test_kanban_telegram_bridge.py`:
- `test_update_card_status_same_status_touches_heartbeat`
- `test_update_card_status_transition_sends_alert`
- `test_update_card_status_uses_block_task_helper`

**Why this matters.** This is the missing piece that makes F32 (24h Telegram silence) accurate — without heartbeat updates, the escalation watcher fires false positives on cards that are alive but idle on the same status. **Ship as part of P0.**

### 3.3 `uv.lock` — needs intent verification
Could be a routine dep refresh or could be a side-effect of a sub-agent run. Before committing, `git diff uv.lock` should be reviewed for *which* package versions moved, against the SLSA / SCA requirement that we know our dep tree.

### 3.4 `.claude/` and `audit/handoff-doc-2026-05-19-review/`
- `.claude/` = Claude Code session state. Must remain `.gitignore`d (already is, via the repo-level ignore). No action.
- `audit/handoff-doc-2026-05-19-review/` = prior-audit deliverables (`audit-plan.md`, `findings.md`). Per repo convention (`audit/` is committed), these *should* be shipped as a small `chore(audit): ...` PR so the audit trail is durable. **P1.**

---

## §4 — Orchestration capability surface (what's actually available)

To answer "can you become a super-orchestrator?" — yes, and the surface is rich. Layered from inner to outer:

### Layer 0 — In-session reasoning loop (Claude Code itself)
- **Skill tool** — invokes user-installed skills (`superpowers:*`, `audit`, etc.). Skills encode HOW to do tasks (TDD, brainstorming, executing-plans, verification-before-completion, writing-plans, dispatching-parallel-agents).
- **Task tracker** — `TaskCreate` / `TaskUpdate` / `TaskList` for in-session work breakdown; survives compaction. Used for short-horizon (single-conversation) decomposition.
- **EnterPlanMode + ExitPlanMode** — get explicit user sign-off on multi-file changes before touching code.
- **AskUserQuestion** — structured multi-option prompts when a decision is irreversible.
- **Context compaction** — automatic when nearing the window; messages summarized and reinjected so work survives long conversations.

### Layer 1 — Parallel & background work inside one session
- **`Agent` tool** — dispatch sub-agents. Specialized types: `Explore` (read-only search), `general-purpose` (catch-all), `Plan` (architecture planning), `claude-code-guide` (Claude Code / SDK / API help), `claude` (general). Multiple in one message → runs in parallel. `run_in_background=true` → notified on completion; no polling needed.
- **`Bash` with `run_in_background=true`** — long-running shell jobs; output captured to a file; you're notified on completion. Perfect for builds, `docker compose up`, test suites.
- **`CronCreate` + `ScheduleWakeup`** — durable (`durable: true` writes to `.claude/scheduled_tasks.json`) or session-only cron / one-shot wakeups. Survives Claude Code restarts when durable.
- **`EnterWorktree`** — isolated git worktrees so a sub-agent can safely commit without disturbing the user's working tree. Auto-cleaned if no changes.

### Layer 2 — Cross-terminal coordination (multiple Claude Code instances)
- Each Claude Code terminal is independent — they share filesystem & git, not memory.
- **Coordination contract today**: a shared `MEMORY.md`-style file or a Kanban (already exists at `lib/kanban/` + sqlite at `/home/hermes/.hermes/kanban.db`). Two terminals can pick different cards.
- **Limitation**: no shared "what is the other terminal thinking?" channel. Must round-trip through files.
- **MCP servers** available to all terminals: `context7` (library docs, auto-current), `github` (PRs/issues/code search via the official MCP), `playwright` (browser automation), `stitch` (UI design generation), `auggie` (codebase retrieval).

### Layer 3 — Tooling honesty (verification skills)
- `superpowers:verification-before-completion` — forces a "did I actually verify this works end-to-end?" gate before claiming done.
- `superpowers:test-driven-development` — red-green-refactor discipline.
- `superpowers:dispatching-parallel-agents` — codifies fan-out / fan-in for parallel research.
- These exist to prevent the "I think it works because the file was written" failure mode.

### Layer 4 — Hermes itself (the long-running runtime)
This is **the** key insight. Claude Code is the dev environment for *writing* the orchestrator. **Hermes IS the long-running orchestrator.** It already has, on `main` today:
- **Kanban** (`lib/kanban/`) — durable task board, sqlite-backed, Telegram-mirrored.
- **Anchors** (`lib/anchors/` + slash commands `/lock /skip /cancel /confirm /new`) — locked-spec discipline so the agent doesn't drift mid-task.
- **Evaluators** (`lib/evaluators/`) — multi-judge consensus panel; resists single-judge hallucination.
- **Durability** (`lib/durability/checkpoint.py` + `failure_matrix.py` + `trichotomy.py`) — checkpoint-on-tool-call; 33-mode failure trichotomy classifier.
- **Memory** (`lib/memory/`) — REJECTED-inject; /forget; /rejections.
- **Observability** (`lib/observability/`) — OTel + OpenInference attributes streaming to Phoenix.
- **Skill extractor** (`lib/skills/`) — promotes successful patterns to reusable skills.
- **24h escalation watcher** (`autonomous-agent-escalation-watcher-1` sidecar) — F32: if Telegram is silent on a blocked card for 24h, escalates to triage.
- **Daily GCS snapshot** (`config/limits.yaml: snapshots.gcs_snapshot_cron: "0 4 * * *"`) — full state snapshot at 04:00 UTC.

### Layer 5 — External durability (cloud)
- **Vertex AI** (`i-for-ai` project, `global` Opus endpoint) — 24M tokens/min Opus quota, <1% utilization.
- **Phoenix** (local + can ship to hosted) — trace persistence.
- **Honcho** (hosted) — long-term memory across sessions.
- **Chroma Cloud** — vector store, hosted.
- **GCS snapshots** (configured, cron `0 4 * * *`) — disaster-recovery snapshot of `/home/hermes/.hermes/`.
- **SOPS + age** — secrets encryption at rest in-repo.
- **Telegram bot** — out-of-band human channel for Fail-Loud + 24h watchdog.

**Verdict.** The orchestration surface is *complete*. The question is no longer "can we orchestrate?" — it's "are the seams between layers correct?" (see §5).

---

## §5 — Risk register for long-running autonomy

Mapped to the 33-mode failure matrix where applicable. F-codes that already have **handler stubs but no implementation** are called out explicitly (verified via `grep -nE "^def (retry_with_backoff|halt_alert_snapshot|fallback_local_log|disable_chroma_for_session)" lib/` returning **zero matches** — the matrix table lists handler *names* as strings, but the named functions do not exist).

### 5.1 Catastrophic / silent-failure risks (top tier)

| # | Risk | F-code | Today's protection | Gap | Mitigation |
|---|---|---|---|---|---|
| R1 | Context window silently truncates plan mid-execution | — | Claude Code summarizes; Hermes uses Kanban + Anchors to re-orient | Kanban only helps if cards exist; ad-hoc work has no anchor | Always create Kanban card before starting >30-min work |
| R2 | Hermes crashes after partial state mutation (DB half-written) | F28, F29 | Checkpoint hook on every tool call (`lib/durability/checkpoint.py`) | Checkpoint exists but verify it includes Kanban DB, Honcho session, Chroma state | P1 — add a single "snapshot integrity check" CI test |
| R3 | F-code classifier returns F33 (unknown) → fail-loud → 24h human wait | F33 | Trichotomy classifier in `lib/durability/trichotomy.py` | Regex table covers ~25 patterns; novel errors fall through to F33 | P1 — expand regex table from observed Phoenix span errors; add weekly F33-rate metric |
| R4 | F-code handlers are NAMED but not IMPLEMENTED | F1–F33 | 33-row table in `lib/durability/failure_matrix.py` lists handler strings | Functions `retry_with_backoff`, `halt_alert_snapshot`, etc. don't exist in `lib/` | **P0** — implement at minimum: `retry_with_backoff`, `halt_alert_snapshot`, `fallback_local_log`. The rest map to these three plus per-tool variants |
| R5 | Daily budget cap silently exceeded | F21 | `config/limits.yaml: budget.daily_usd_cap: 500, alert_at_pct: 75` | Cap is *configured*; no evidence a metric reads it and fires F21. LiteLLM `/spend` DB now exists post-#71 but isn't wired to a halt | **P1** — wire `/spend/total` poll into the F21 trigger path; add 75% alert |
| R6 | Sub-agent loop runs forever (max_turns_per_task=50 not enforced per call site) | — | `limits.yaml: agent.max_turns_per_task: 50` | Config exists; need to confirm every `Agent` dispatch path actually reads it | **P1** — single source-of-truth read at the dispatch helper |
| R7 | Approval-required tool fires without approval | F30 | Trichotomy entry exists | No central interceptor; relies on each tool author to check | **P1** — middleware in `lib/tools/` that asserts approval based on `limits.yaml: approval.*` |
| R8 | Telegram is down → no human channel → 24h silence triggers F32 | F32 | Escalation watcher sidecar | Watcher itself uses Telegram → circular | **P1** — second channel (email / GitHub issue) as fallback for F32-on-F32 |

### 5.2 Operational drift risks (medium tier)

| # | Risk | Today's protection | Mitigation |
|---|---|---|---|
| R9 | Session reset at 04:00 wipes context mid-task | `cli-config.yaml: session_reset.mode: none` (UNCOMMITTED) | **P0** — ship the override |
| R10 | Kanban heartbeat not refreshed on same-status writes → false F32 | `lib/kanban/telegram_bridge.py` rewrite (UNCOMMITTED) | **P0** — ship the rewrite + tests |
| R11 | Phoenix span backlog OOMs the otel-collector | Collector is unconfigured re: queue size | P1 — set `exporter.otlp.queue_size` + drop policy |
| R12 | Honcho hosted endpoint rate-limits us mid-day | Memory plugin uses Honcho client | P1 — Fail-Soft path (cache + degrade) when 429 from `api.honcho.dev` |
| R13 | LiteLLM proxy crashes → agent has no LLM | Compose `restart: unless-stopped` (verify) | P1 — confirm restart policy; add healthcheck-driven auto-recover |
| R14 | uv.lock drift uncommitted → reproducibility broken | `uv.lock` is untracked-modified right now | P0 — review and commit / revert |
| R15 | Stale local branches (41 unmerged, all squash-shipped) accumulate confusion | None | **P2** — `git branch --merged origin/main` driven cleanup script |
| R16 | Locked `.claude/worktrees/agent-*` dirs leak disk | 3 present, all 0-file-status | **P1** — auto-prune when 0 changes and >7d old |

### 5.3 SDLC / supply-chain risks (per `audit/phase1-to-phase2-readiness-2026-05-19/SYNTHESIS.md`)

These were enumerated in the prior cross-audit and remain unaddressed:

| # | Risk | Standard | Status |
|---|---|---|---|
| R17 | No CodeQL / Semgrep static analysis on PRs | NIST SSDF PW.7 / OWASP ASVS V14 | **P1** — wire `github/codeql-action` |
| R18 | No Trivy / Grype scan of built images | SLSA Build L2 / CIS Docker 4.x | **P1** — add to release workflow |
| R19 | No SBOM emitted | SLSA Provenance / NIST 800-218 PW.4 | **P1** — `anchore/sbom-action` + cosign attest |
| R20 | No image signing (cosign) | SLSA Provenance L3 | **P1** — `sigstore/cosign-installer` + sign-on-tag |
| R21 | No SCA (Snyk / Dependabot enabled but PRs not enforced) | NIST SSDF PW.4 | **P1** — turn Dependabot PRs into blocking checks |
| R22 | GitHub Actions not SHA-pinned (uses tags) | SLSA Source L3 / OWASP CICD-SEC-04 | **P1** — pin all 3rd-party actions to commit SHAs |
| R23 | Branch protection: 11 required checks, but no "Require signed commits" | NIST SSDF PS.2 | **P2** — flip the toggle once contributors are aligned |

---

## §6 — Pending audit findings still unaddressed

Cross-referenced from `audit/phase1-to-phase2-readiness-2026-05-19/SYNTHESIS.md`:

- **17 P1/P2 enterprise SDLC items** (CodeQL, Trivy, SBOM, cosign, SCA, Action SHA pinning, branch-protection toggles, metrics, error tracking, Hermes submodule bump). Most overlap with R17–R23.
- **Failure-matrix handler implementations** (R4 above) — never actioned.
- **Phase 2 spec** — not yet drafted on `main`. `docs/spec-phase1-completion-design` branch exists but is `phase 1 completion`, not Phase 2.
- **README "twelve services" lie** (line 55) — still uncorrected.
- **Handoff §7.3** — still references the simplified-away compose layout.

---

## §7 — References to enrich next (pass-2)

- `~/Professional Profile/Hermes/` (sibling) — for upstream Hermes-Agent state, to confirm submodule-bump candidates and any newly-released plugin contracts.
- `~/Professional Profile/Hermes/docs/` — for canonical hook contract & plugin loader docs (to cross-check we still match upstream).
- Phoenix live trace data at `localhost:6006` — to harvest **real** F33-classified errors and grow the trichotomy regex table from observed reality, not guesswork.
- `gh api /repos/Manzela/AutonomousAgent/dependabot/alerts` — to size R21.
- `gh api /repos/Manzela/AutonomousAgent/code-scanning/alerts` — confirm zero or harvest fix list.
- LiteLLM `/spend/total` endpoint live response — to confirm the DB attach (#71) actually populates and to wire the F21 cap-check (R5).
- `docs/superpowers/HANDOFF-2026-05-19.md` §7.3 — actually-current compose layout for the doc fix.

---

## §8 — Open assumptions (mark before pass-2 confirmation)

1. **Compose stack restarts containers on crash.** Inferred from "healthy ~1h" but `compose.yaml` restart policy not re-read in this pass.
2. **GCS snapshot cron `0 4 * * *` actually fires inside the hermes container.** The config is read; the executor job has not been verified end-to-end this session.
3. **Phoenix span emission is *complete* across all tool calls**, not just the LLM-call wrapper. Spot-check verified `lib/observability/__init__.py` exports both LLM and TOOL spans; full coverage not exhaustively traced.
4. **Honcho hosted endpoint is reachable from inside the hermes container today.** The plugin is configured; a live round-trip has not been observed this session.
5. **`uv.lock` untracked-modification is benign** (e.g. routine dep refresh). Should be diffed before commit.
