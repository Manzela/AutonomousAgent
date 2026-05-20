# Audit Plan — Architecture Research Doc vs. AutonomousAgent Repo

**Audit date:** 2026-05-20
**Auditor:** Claude Code (Opus 4.7, /audit skill, Pass 1)
**Companion doc:** [`findings.md`](./findings.md) (component-by-component current state with `file:line` citations)
**Target spec:** `~/.gemini/antigravity-ide/brain/c4e71254-9d07-454a-8ef0-52e3ff6703af/autonomous_agent_architecture_research.md`
**Status:** Pass 1 draft. Pass 2 enrichment pending. **No work yet — approval gate.**

---

## 0. Read this first — the framing decision

The research doc is a wish-list authored by a *different* AI session (Antigravity / Opus 4.6 Thinking) and dropped into the repo untracked. It is **not reconciled with the official self-RL ADR** (`docs/decisions/0005-self-rl-pipeline-architecture.md`). Before this audit plan can be executed, **the user must pick a framing**:

| | **Framing #1: Implement as-written** | **Framing #2: Cherry-pick for ADR-0005** |
|---|---|---|
| **Scope** | All 10 components in the research doc, including MoE router, Generator Agent, RLFA, GRPO-trained memory | Only the gaps the research doc surfaces that already matter for Phases 1–3 |
| **Effort** | Multi-quarter (≈ 12–18 months calendar; 3-5 FTE-equivalent) | 4–8 weeks bolted onto current Phase 1/2 cycles |
| **Roadmap impact** | Supersedes ADR-0005, requires a new ADR enumerating Phase 4+ workstreams (Atropos, GPU runtime, MoE training) | Preserves ADR-0005 scope; minor patches to Phase 2 spec |
| **Pre-reqs** | GPU runtime (likely Modal/Daytona), Atropos environment, RL training data pipeline, ML-ops headcount | None; the work fits the existing Mac → GCP-VM trajectory |
| **Risk** | Many components (Components 1, 2, 5) have no dependency root in the current codebase; building bottom-up is a science project | Concrete deliverables with measurable next-PR scope; each item has a clear test plan |

**My recommendation:** **Framing #2.** Reasons in priority order:

1. ADR-0005 is **explicit** that the hard RL loop is Phase 4 and gated by Telegram approval — the research doc's Components 1/2/4/5/6 all assume that gate is already lifted.
2. Phase 0a is **mid-flight** (`feat/phase-0a-gcp-migration` is the active branch, 25 commits in flight); reorienting onto a multi-quarter MoE roadmap would freeze that work.
3. Framing #2 items are mostly **observability + hygiene + small surface adds** — they make Framing #1 cheaper later without committing now.
4. The single highest-leverage Framing #1 item (a GRPO reward signal from judge panel) is also a Framing #2 item (J1 below) — Framing #2 is the strict superset of the de-risking work.

The rest of this plan is structured: **§1 Hygiene (do regardless)** → **§2 Framing #2 items (recommended)** → **§3 Framing #1 items (if user chooses)** → **§4 Sequencing notes**.

---

## 1. Framing-independent hygiene (do regardless of framing)

These are issues the research doc *surfaced indirectly* — they exist whether the user picks Framing #1, Framing #2, or neither.

### H1 — Decide the fate of the untracked research doc

- **What:** Pick one of: (a) commit `docs/architecture/autonomous-agent-architecture-research.md` with an explicit "aspirational, not approved" header, (b) move it to `audit/2026-05-20-architecture-research-gap-analysis/source.md`, or (c) delete it from the working tree and rely on the `~/.gemini/brain` copy.
- **Why:** Untracked architecture docs in `docs/` get cargo-culted by future readers as authoritative. The doc is 1028 lines of plausible-sounding spec — it will absolutely be cited as canon by a future session unless gated.
- **Where:** `docs/architecture/autonomous-agent-architecture-research.md` (untracked per `git status`); compare to authoritative `docs/decisions/0005-self-rl-pipeline-architecture.md`.
- **Effort:** **15 minutes.** One commit either way.
- **Recommendation:** option (b) — move under `audit/` so future readers find it next to the gap analysis.

### H2 — Reconcile the research doc with ADR-0005

- **What:** Either (a) write an ADR (`docs/decisions/0006-architecture-research-disposition.md`) that explicitly accepts/rejects each of the research doc's 10 components against ADR-0005's Phase 1/2/3/4 scoping, or (b) annotate the research doc itself with an "Approved scope" prefix that down-scopes Components 1, 2, 4, 5, 6, 7 to Phase 4+.
- **Why:** Without a reconciliation document, every future contributor (human or AI) will re-derive the same ambiguity this audit just surfaced. Costs a 30-minute decision to save tens of hours of re-litigation.
- **Where:** New file at `docs/decisions/0006-architecture-research-disposition.md`; references `docs/decisions/0005-self-rl-pipeline-architecture.md` and either the brain copy or H1's relocated copy.
- **Effort:** **2–4 hours** (writing + one review cycle).

### H3 — Update README service inventory if MCPs change

- **What:** If §2 J3 or §3 lands new MCPs, update the README service table in the same PR.
- **Why:** `docs/mcp-inventory.md:75-109` explicitly lists this as a known audit failure mode ("README and inventory updated in the same PR. Stale rows are a known audit failure mode.").
- **Where:** Top-level `README.md` and `docs/mcp-inventory.md`.
- **Effort:** **5 minutes per MCP change.**

---

## 2. Framing #2 — Cherry-picked items aligned to ADR-0005 (RECOMMENDED)

Goal: bank the gaps the research doc usefully surfaced, without committing to the maximalist roadmap. Total estimated effort: **4–8 weeks** of single-engineer cycles (or 2–3 weeks parallelized across the current judge-panel + Phase-0a workstreams).

Items are tagged **P0** (do first) → **P2** (nice-to-have).

### P0 — Foundations Phase 3+ depends on

#### J1 — Wire judge outputs as a structured reward record (RLAIF substrate)

- **What:** Persist each judge-panel run as a structured record (JSONL row) including `task_id`, `tool_call_id`, per-axis scores, consensus verdict, fingerprint, and timestamp. Write to `trajectories/judge-events.jsonl` (gitignored) so Phase 3's trajectory shipper can ingest it.
- **Why:** Today the judge panel (`lib/evaluators/consensus.py`) produces accept/reject + text feedback injected into the next prompt (`lib/evaluators/orchestrator_hook.py:54-66`) — the signal is **thrown away** after one turn. Persisting it gives Phase 4 Atropos a free, high-quality RLAIF reward dataset without committing to GRPO infra now. **This is the single highest-leverage item in the plan.**
- **Where:**
  - `lib/evaluators/orchestrator_hook.py:22-67` — add a post-consensus hook that writes JSONL.
  - `lib/evaluators/consensus.py:55-143` — expose per-axis scores in the consensus output dataclass.
  - `trajectories/` — add `judge-events.jsonl` to gitignore, document schema in `docs/superpowers/specs/2026-05-14-hermes-agent-architecture-design.md`.
- **Effort:** **3–5 days.** Bounded, no new dependencies, schema-evolution-safe.
- **Tests:** Unit test that one judge run writes one well-formed JSONL row; integration test that the file survives a wrapper restart.

#### J2 — Add GenAI semantic-convention attributes to OTel spans

- **What:** Set OTel GenAI conventions on every model-call span: `gen_ai.system`, `gen_ai.request.model`, `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens`, `gen_ai.response.id`. Also add custom `agent.phase` (Phase 1 / 2 / 3 marker) and `agent.task_anchor` (TaskSpec id).
- **Why:** Phoenix and Cloud Trace both consume GenAI semantic conventions for first-class GenAI dashboards. Today `lib/observability/otel_setup.py:34-108` sets only `service.name`. This is the difference between "spans exist" and "spans are useful for debugging a multi-turn agent loop". **Pass 2 must confirm whether upstream Hermes already emits these (see findings.md §3 item 1).** If yes, this item shrinks to "verify and document"; if no, it's wrapper-side instrumentation.
- **Where:**
  - `lib/observability/otel_setup.py:34-108` — extend resource attrs.
  - Whichever module wraps model calls (likely `hermes-agent/agent/litellm_wrapper.py` or similar — pending Pass 2 grep).
  - `deploy/otel/collector.dev.yaml:18-30` — confirm Phoenix exporter doesn't strip GenAI attrs.
- **Effort:** **2–4 days** (best case: 4h if upstream already does this; worst case: 4 days if we need to write a wrapper around litellm).
- **Tests:** Smoke test that a `hermes -z "hi"` invocation produces a span with all required `gen_ai.*` attrs viewable in Phoenix UI.

#### J3 — Trajectory shipper MVP (file-tail → GCS)

- **What:** Ship a minimal `services/trajectory-shipper/` that tails OTel collector trace exports + `trajectories/judge-events.jsonl` (from J1) and writes to a GCS bucket, partitioned by day. **Sketch only — no SDK uploads yet, just `gsutil cp` from a sidecar with a cron-loop.**
- **Why:** ADR-0005 Phase 3 = "Trajectory collection". The architecture spec (`docs/superpowers/specs/2026-05-14-hermes-agent-architecture-design.md:185-199`) describes the design but **none of it is implemented** (only `trajectories/.gitkeep`). Phase 4 (Atropos) is blocked until trajectories exist. The MVP shipper unblocks Phase 4 work even while Phase 4 itself sits behind the Telegram-approval gate.
- **Where:**
  - New `services/trajectory-shipper/{Dockerfile,shipper.sh,shipper.cron}`.
  - `deploy/docker-compose.yml` — add sidecar to internal network; reuse `volume-init` pattern for credential mount.
  - `terraform/phase-0a-gcp/` — extend the existing GCS bucket setup or create a `gs://<project>-trajectories/` bucket; reuse current IAM binding patterns.
- **Effort:** **5–8 days.** Mostly Terraform + compose wiring; the shipper itself is a 30-line shell script in v1.
- **Tests:** Manual: write a fake JSONL, wait one cron tick, confirm it lands in GCS.

#### J4 — Loop and stall detection as failure-matrix extensions

- **What:** Add two new F-codes to the failure matrix: `F-LOOP` (3+ identical tool calls within a 10-turn window) and `F-STALL` (no successful tool call + no new TaskAnchor edit in 20 turns). Implement detectors in `lib/durability/` and wire them into the existing escalation/dispatch pipeline.
- **Why:** Component 7 (Metacognitive Governor) is the most useful piece of the research doc that's also feasible without RL infra. **It belongs as a failure-matrix extension, not as a new module** (per `findings.md` §1 Component 7). Today, `lib/durability/escalation.py` only handles the F32 24h-silence case; adding F-LOOP and F-STALL slots cleanly into the existing failure-matrix dispatch logic.
- **Where:**
  - `lib/durability/failure_matrix.py` — register new codes.
  - `lib/durability/handlers.py` — add handlers (loop → "summarize+prune", stall → "escalate via existing escalation.py path").
  - `lib/durability/trichotomy.py` (if it owns dispatch) — wire detectors.
  - `docs/architecture/failure-matrix.md` — document. **Pass 2 must read this file** to confirm the registration pattern.
- **Effort:** **4–6 days** (most of it test coverage; the detectors themselves are <100 lines).
- **Tests:** Synthetic trajectories with 3 identical tool calls → F-LOOP fires. Synthetic trajectory with 20 no-op turns → F-STALL fires + escalation watcher gets pinged.

### P1 — Concrete wins, low coordination cost

#### J5 — Expand MCP coverage (filesystem, fetch, time) — **CLOSED 2026-05-20 (partial)**

- **Status:** Shipped 2 of 3 in commit `a667ad6` (`fetch`, `time` as stdio subprocesses via `uvx`). `filesystem` + `git` deferred with documented blockers (see "Deferred" table in `docs/mcp-inventory.md`).
- **Actual scope vs plan:**
  - Plan assumed **3 HTTP sidecars in `docker-compose.yml`**. Reality: official Anthropic MCPs are stdio-only Python servers — no HTTP wrapper exists upstream. Chose stdio subprocess pattern (matches upstream design, no compose changes needed). New "stdio vs HTTP MCPs" section in inventory documents the divergence.
  - `filesystem` deferred: npm-only — adding nodejs to the `python:3.11-slim` base would expand supply-chain ~150MB for marginal gain.
  - `git` deferred: needs a workspace mount the `read_only: true` Hermes container lacks; wiring requires an ADR on self-modifying source tree vs sandboxed workspace clone.
- **What shipped:**
  - `config/hermes/cli-config.yaml` — added `fetch` + `time` entries with rationale comment.
  - `docs/mcp-inventory.md` — 2 new active rows + new "stdio vs HTTP MCPs" section + 2 new "Deferred" rows. Count now 4 active / 3 deferred (was 2 active / 1 deferred).
- **Effort actual:** ~2 hours (less than the planned 2 days because docker-compose sidecars were skipped — net scope reduced).
- **Tests:** stdio MCPs are exercised by Hermes startup; no `curl` healthcheck applies. The official Anthropic MCPs have their own test coverage upstream. **Known gap:** `tests/integration/test_evaluators_smoke.py` not extended (checklist item 5). Tracked as a follow-up.

#### J6 — Sandbox tier-naming reconciliation note

- **What:** Write a short doc (`docs/architecture/sandbox-tiers.md`, ~50 lines) explaining the difference between the research doc's tech-centric tiers (gVisor/Firecracker/WASM) and the repo's use-case-centric tiers (in_process/shell_sandbox/browser_sandbox/external_https/cloud_sandbox). State which threat model each defends against.
- **Why:** Prevents a future session from misreading the research doc as "we need to swap to gVisor". The two taxonomies are both valid; the gap is documentary, not architectural (per `findings.md` §1 Component 9). A 50-line doc is cheaper than refactoring the toolset_router.
- **Where:**
  - New `docs/architecture/sandbox-tiers.md`.
  - References `config/toolsets.yaml:6-12` and `lib/toolset_router.py`.
- **Effort:** **2–3 hours.**

#### J7 — MEMORY/USER/SOUL/REJECTED audit pass

- **What:** Read all of `config/hermes/{MEMORY,USER,SOUL}.md` and `lib/memory/rejected.py`. Document in `docs/architecture/memory-layers.md` (~80 lines) what each file is for, who writes to it, when it's read, and which (if any) is closest to the research doc's "consensus core" concept.
- **Why:** **Findings §3 item 4** flagged that Pass 1 didn't open these files. Until they're documented, the wrapper's memory subsystem looks more random than it is. This is also the cheapest possible step toward Component 3 (Hierarchical Memory Manager) — establishes the layer baseline.
- **Where:**
  - Read: `config/hermes/MEMORY.md`, `config/hermes/USER.md`, `config/hermes/SOUL.md`, `lib/memory/rejected.py`.
  - Write: new `docs/architecture/memory-layers.md`.
- **Effort:** **4–6 hours.**

### P2 — Useful, but defer until P0/P1 land

#### J8 — A2A research spike (scoping only, no impl) — **CLOSED 2026-05-20**

- **Status:** Memo delivered at `j8-a2a-memo.md`. **Outcome: NO**, do not wire Google A2A. Critical finding: the upstream `hermes-agent/acp_adapter/` is Zed's Agent Client Protocol (IDE integration), **not** Google's Agent-to-Agent peer protocol — the audit's earlier "70% capability" claim is retracted. No ADR-0007 written (per spec: ADR optional, outcome was "no").
- **What:** 2-day timeboxed read of `hermes-agent/acp_adapter/` upstream code + the public A2A spec. Output: a 1-page memo answering "Is A2A worth wiring at all for a single-agent system?" — **decision artifact, no code.**
- **Why:** Component 8 says A2A is "not yet" — but the research doc never asks whether a *single-agent* wrapper should adopt a peer-to-peer protocol at all. The memo settles the question before any implementation effort.
- **Where:** `hermes-agent/acp_adapter/` (read), new `docs/decisions/0007-a2a-adoption.md` (write — optional, only if outcome is "yes").
- **Effort:** **2 days max** (hard timebox). **Actual:** ~3 hours including disambiguation evidence-gathering.

#### J9 — Context-usage gauge → soft escalation — **CLOSED 2026-05-20 (detector shipped; OTel gauge deferred)**

- **What was planned:** Surface upstream Hermes' context-window usage as a `agent.memory.context_usage_pct` OTel gauge + an F-CONTEXT code at 0.9 that triggers an earlier compaction.
- **Reality on inspection of `hermes-agent/agent/context_compressor.py`:**
  - Upstream already compacts at **0.5** (`threshold_percent` default), so a 0.9 detector firing means compaction either failed or was suppressed by the anti-thrashing guard (≥ 2 ineffective compactions in a row). 0.9 is therefore a **"compaction already failed"** warning surface, NOT a second compaction trigger as the original plan implied.
  - The signal source is `last_prompt_tokens / context_length` — both already on the `ContextCompressor` instance.
- **What shipped (this PR):**
  - `lib/durability/failure_matrix.py` — `F36` / **F-CONTEXT** (Fail-Soft, handler `escalate_context_pressure` — stub for now, dispatches to `halt_alert_snapshot` via the matrix's stub mechanism).
  - `lib/durability/runtime_detectors.py` — `ContextUsageDetector` class with `record_usage(session_id, prompt_tokens, context_length)`. Mirrors `StallDetector`'s one-fire-per-episode + re-arm semantics; thread-safe via single mutex.
  - `config/limits.yaml` — `durability.context_detector.warn_threshold: 0.9`.
  - `tests/unit/test_runtime_detectors.py` — 10 new tests for `ContextUsageDetector`.
  - `tests/unit/test_failure_matrix.py` — F36 presence + handler-name check.
  - `docs/architecture/failure-matrix.md` — new "Runtime detectors (F34-F36)" subsection (also backfills F34/F35 which were code-only).
- **What's explicitly DEFERRED:**
  - The OTel `agent.memory.context_usage_pct` **gauge**. Adding it requires wiring a `MeterProvider` + periodic OTLP metrics exporter into `lib/observability/otel_setup.py`, which is currently **trace-only**. The detector exposes the ratio via `snapshot(session_id)` and via warn-level logs, but cannot publish a metric until the SDK is expanded. Tracked as a follow-up.
  - The runtime wiring of `ContextUsageDetector.record_usage(...)` into Hermes' actual post-model-call lifecycle. Hermes upstream does not expose `post_llm_call` hooks (see audit-plan J13). Today the detector is an importable component; full wiring depends on either J13 (upstream PR) or our wrapper-side `_post_llm_call` (see `lib/observability/__init__.py`).
- **Effort actual:** ~3 hours (vs planned 3-5 days — most of the savings came from realizing the OTel gauge needed MeterProvider work that's outside this slice, and from the upstream-hook gap making "full wiring" out-of-scope anyway).

#### J10 — Document the judge panel as RLAIF

- **What:** Update `docs/decisions/0005-self-rl-pipeline-architecture.md` (or write a follow-up ADR) explicitly naming the judge panel as the project's RLAIF substrate, citing the J1 JSONL schema as the trajectory hand-off contract.
- **Why:** Without this, the judge panel keeps getting described as "prompt-injection-for-rejection" — its role as the long-term reward signal stays invisible. **Once J1 lands, this becomes a 1-hour ADR update.**
- **Where:** `docs/decisions/0005-self-rl-pipeline-architecture.md` (extend) or new `docs/decisions/0008-judge-panel-as-rlaif.md`.
- **Effort:** **1–2 hours.**

---

## 3. Framing #1 — Full research-doc implementation (alternative, NOT recommended)

Listed for completeness. Only execute if user explicitly chooses to supersede ADR-0005 with a new MoE/RL roadmap.

| Pri | Item | Where | Rough effort | Pre-req |
|---|---|---|---|---|
| P0 | **F1.** Stand up Atropos training environment (GPU runtime via Modal/Daytona, vLLM-served candidate models, GRPO/DAPO trainer wired) | New `services/atropos-trainer/`, new Terraform module under `terraform/phase-4-rl/` | **3–6 months** (full ML-ops project) | New ADR superseding 0005, dedicated GPU budget |
| P0 | **F2.** Phase-Aware MoE Router (Component 1) with LoRA expert library and DeepSeek-V3 auxiliary-loss-free load balancing | New `lib/moe/`, new `services/expert-vllm/` | **2–4 months** | F1 |
| P0 | **F3.** RL-Driven Generator Agent (Component 2 — Agent² framework, MDP modeling, optimization pipeline) | New `lib/generator/` | **3–6 months** | F1, F2 |
| P1 | **F4.** GRPO-trained Hierarchical Memory Manager (Component 3) — learned STORE/RETRIEVE/UPDATE/SUMMARIZE/DISCARD per Yu 2026 | Rewrite `hermes-agent/agent/curator.py` + new `lib/memory/manager.py` | **2–4 months** | F1 |
| P1 | **F5.** RLFA Free Agent lifecycle (Component 5) — Active/Warning/Benched/Probation/Replaced state machine | New `lib/rlfa/` | **1–2 months** | F2 |
| P1 | **F6.** Multi-project consensus core (Component 6) — read-only core layer, namespace ACLs, write-ahead log, promotion quorum | New `lib/memory/consensus/`, schema migrations on SQLite | **2–3 months** | None (could land independently, but value depends on multi-agent context) |
| P2 | **F7.** A2A protocol stack (Component 8 second half) — agent cards, peer discovery, RPC envelope. **NB:** `hermes-agent/acp_adapter/` is Zed's IDE protocol (`agent-client-protocol` pkg), not Google A2A — does NOT reduce this scope. | New `lib/a2a/` (greenfield); ACP adapter unaffected | **1–2 months** (standalone, no reuse) | J8 memo (2026-05-20) recommends **defer indefinitely**; reopen only on multi-agent roadmap trigger |
| P2 | **F8.** gVisor / Firecracker / WASM tiers in sandbox router (Component 9 second half) | `deploy/sandboxes/`, `lib/toolset_router.py`, new tier configs | **1–2 months per tier** | None |
| P2 | **F9.** Full Metacognitive Governor module (Component 7) as a stateful service, not failure-matrix extension | New `lib/metacognition/` | **2–3 months** | F2 (needs expert-swap action) |

**Framing #1 totals:** ≈ 18–30 person-months. With one engineer, that's 1.5–2.5 calendar years. Even with three engineers in parallel, ~6–10 months — and most of the work blocks on F1 (the Atropos environment).

**Recommended only if** the user's goal has materially shifted from "ship the Hermes wrapper to Phase 2 GCP" to "build a research-grade autonomous-agent platform". Today's main branch and active workstream do not reflect that shift.

---

## 4. Sequencing notes & dependencies

If user picks **Framing #2**, the order I recommend:

```
[H1, H2 — hygiene, parallel, day 1]
       │
       ▼
[J2 — GenAI conventions] ──┐
                            │
[J1 — judge JSONL] ─────────┤── these three can run in parallel
                            │
[J4 — failure-matrix ext] ──┘
                            │
                            ▼
                       [J3 — shipper MVP]   (depends on J1's JSONL schema)
                            │
                            ▼
                       [J10 — RLAIF ADR]    (depends on J1 landing first)

[J5, J6, J7, J8, J9 — slot in opportunistically between any of the above]
```

If user picks **Framing #1**, F1 is the blocking root for almost everything else. Do not start F2/F3/F4/F5/F9 before Atropos is reachable. F6, F7, F8 can run independently of F1 but coordinate with the rest of the team because they touch shared subsystems.

**Conflict with current Phase 0a work:** Framing #2 items J3 (trajectory-shipper Terraform) and J5 (new MCP sidecars in compose) will touch files Phase 0a is actively editing. Recommend **sequencing Framing #2 AFTER `feat/phase-0a-gcp-migration` lands on main** — should be 1–2 weeks based on PR #112 progress per memory.

---

## 5. What Pass 2 will sharpen

Pass 2 dispatches parallel Explore subagents on these items (from `findings.md §3`):

1. **GenAI semantic conventions in upstream Hermes** → could collapse J2 from 4 days to 4 hours.
2. **Upstream metacognitive loop** → could collapse J4 from "new module" to "extension of existing".
3. **`hermes-agent/acp_adapter/` contents** → directly informs J8.
4. **MEMORY/USER/SOUL/REJECTED contents** → directly informs J7 (this one is a local read, no subagent needed).
5. **Failure-matrix coverage** (`docs/architecture/failure-matrix.md`) → directly informs J4.
6. **Phase 0a plan status** (`docs/superpowers/plans/2026-05-20-phase-0a-gcp-migration.md`) → informs §4 sequencing.
7. **Trajectory-shipper hidden impl** in any worktree branch → could collapse J3 to "finish + wire".
8. **Phase 2 spec** (`docs/spec/phase2.md`) → confirms J-items don't conflict with sanctioned Phase 2 scope.

Any of those returning evidence that materially changes a J-item's effort estimate will get reflected in a `## Changes from Pass 1` section appended to this file.

---

## 6. Approval gate

**Do not start implementation.** Wait for the user to:

1. **Pick a framing** (#1, #2, or hybrid).
2. **Pick a subset of items** to execute first (most likely H1, H2, J1, J2, J4 — but user's call).
3. **Approve Pass 2 dispatch** (or skip it if Pass 1 is enough).

Once approved, hand off to `superpowers:executing-plans` for the chosen subset, with `superpowers:using-git-worktrees` for isolated execution.

---

## 7. Changes from Pass 1 (Pass 2 enrichment, 2026-05-20)

Three parallel `Explore` subagents + a local read of `config/hermes/{MEMORY,USER,SOUL,AGENTS}.md`. Material updates below — full evidence in `findings.md §4`.

### Promotions / scope changes

| Item | Pass 1 | Pass 2 | Reason |
|---|---|---|---|
| **H2** (reconcile research doc with ADR-0005) | P0 hygiene | **P0 hygiene — now load-bearing** | `docs/spec/phase2.md:1-22,492-510` explicitly declares Phase 2 is system-of-record and excludes every Framing #2 J-item from scope. H2's reconciliation ADR is the *vehicle* by which Framing #2 items become approvable. Without it, J1-J10 have no sanctioning authority. |
| **J8** (A2A research spike) | P2, 2-day timebox, decision artifact only | **Closed 2026-05-20 — outcome: NO** (see `j8-a2a-memo.md`) | Memo found: ACP (Zed's `agent-client-protocol` pkg, for IDE integration) ≠ Google A2A (peer-agent protocol). The Pass-2 "~70% of A2A capability" claim **conflated two protocols** and is retracted; `acp_adapter/` contributes 0% A2A surface area. F7 (A2A protocol stack) effort estimate stands as standalone, not a reduction. |
| **J4** (loop + stall detection) | 4-6 days | **3 days** | Failure matrix pattern is well-established with CI guards (`tests/unit/test_handlers.py::test_all_33_codes_dispatch_to_callable`). Adding F-LOOP + F-STALL is "follow the 4-step pattern", not "build infra". Caveat: **read F25 ("Clarification loop max") handler before adding F-LOOP** — F25 might already partially cover it. |

### Demotions / scope reductions

| Item | Pass 1 | Pass 2 | Reason |
|---|---|---|---|
| **J2** (GenAI semantic conventions) | 2-4 days | **1-3 days, REFRAMED** | The wrapper *already* emits OpenInference spans (`lib/observability/__init__.py:189,336` — `llm.*` attrs). The gap is **OpenInference vs. GenAI dialect**, not "no instrumentation". Decision point: dual-emit vs. switch dialects vs. status-quo. **Cheapest path:** add a span-processor that maps `llm.*` → `gen_ai.*` on egress. **Right path:** depends on whether Cloud Trace (prod) consumes OpenInference natively. |
| **J7** (MEMORY/USER/SOUL audit) | 4-6 hours, ~80-line doc | **1-2 hours, ~30 lines** | The files are tiny (256-394 bytes each, plus a 3.5K `AGENTS.md`). They're persona + working-context bootstrap, not a consensus core. Documenting them is faster than expected because there's less to document. |
| **J1** (judge JSONL persistence) | 3-5 days | **3-5 days, unchanged — but priority raised** | This is the **single highest-leverage item** in the plan AND it's foundational for J3 (trajectory shipper needs a schema). Move J1 to Day 1 of execution. |

### No-change items

- **H1, H3, J3, J5, J6, J9, J10** — Pass 2 evidence aligns with Pass 1 estimates.
- **J3 (trajectory shipper)** confirmed: no hidden half-built impl anywhere. `services/` directory doesn't even exist in repo root. 5-8 day estimate stands.

### New items surfaced by Pass 2

| New | What | Why | Effort |
|---|---|---|---|
| **J11** | Add a `--dual-emit-gen-ai` flag (or env var) to `lib/observability/__init__.py:189-336` that *additionally* emits `gen_ai.*` attrs alongside `llm.*` — gated off by default to preserve Phoenix compatibility | Resolves J2's OpenInference-vs-GenAI fork without forcing a dialect commitment. Defers the vendor-coupling decision until Cloud Trace prod telemetry is wired. | **1 day** (it's a pure attribute-mapping shim) |
| **J12** | Pre-execution: read `lib/durability/handlers.py::HANDLER_REGISTRY` for F25's handler implementation and decide whether F25 should be extended OR a new F-LOOP added | Prevents duplicate F-codes for loop-detection. F25 = "Clarification loop max" might already cover the case J4 wants — needs a 30-min read before J4 starts. | **30 min** (gating decision for J4) |
| **J13** | If user picks Framing #2: file upstream PR to **Hermes-agent** that wires `invoke_hook("pre_llm_call", ...)` and `invoke_hook("post_llm_call", ...)` into the actual LiteLLM call site | Today the hook registration exists but is never invoked upstream (`hermes-agent/hermes_cli/plugins.py:701,1296` — registration only). If we land this upstream, J2 becomes trivial and persists across submodule bumps. Avoids a permanent monkey-patch in our wrapper. | **2-3 days** if upstream review is fast; **multi-week** if not. Optional. |

### Sequencing — updated

Recommended Framing #2 sequencing, post-Pass 2:

```
Day 1   [H1, H2, J12 — 30 min decision gates]
Day 2-6 [J1 — judge JSONL persistence] ←── Day-1 priority; unblocks J3
Day 2-4 [J2 — GenAI semantic conventions or J11 dual-emit shim]   ┐  parallel
Day 2-5 [J4 — F-LOOP + F-STALL (gated on J12 outcome)]            ┘  with J1
Day 7-14 [J3 — trajectory shipper MVP]   (depends on J1 schema)
Day 8+  [J5, J6, J7 — opportunistic; J8 if Framing #2 promoted A2A spike]
Day 15+ [J10, J13 — documentation + optional upstream PR]
```

### Risk callouts updated

- **Phase 0a plan checkboxes are stale.** `docs/superpowers/plans/2026-05-20-phase-0a-gcp-migration.md` shows 0/134 done but git log + memory show Phase E/G work in flight. **Sequencing decisions should rely on git log + open PRs, not the plan file's checkboxes.**
- **Phase 0a does NOT touch `lib/observability/`.** Collision risk for J2/J11 is **lower** than Pass 1 estimated.
- **Phase 0a DOES touch `deploy/docker-compose.yml`** (Phase F Task 26 + new `deploy/docker-compose.gcp.override.yml`). J3 (shipper sidecar) and J5 (new MCP sidecars) should still sequence **after** Phase 0a lands on main.
- **Phase 2 spec explicitly excludes** all Framing #2 J-items from sanctioned scope. **H2 must land first** to provide approval authority. Without H2, the work is unsanctioned and will be reverted on principle.

### What Pass 2 did NOT change

- Framing recommendation: **still Framing #2**.
- Overall effort estimate: **still 4-8 weeks for Framing #2**; **still multi-quarter for Framing #1**.
- Approval gate still mandatory before any implementation.
