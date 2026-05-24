# Findings — Architecture Research Doc vs. AutonomousAgent Repo

**Audit date:** 2026-05-20
**Auditor:** Claude Code (Opus 4.7, /audit skill, Pass 1)
**Target spec:** `/Users/danielmanzela/.gemini/antigravity-ide/brain/c4e71254-9d07-454a-8ef0-52e3ff6703af/autonomous_agent_architecture_research.md` (1028 lines, authored by Antigravity = Claude Opus 4.6 Thinking, dated 2026-05-20)
**Target repo:** `/Users/danielmanzela/RX-Research Project/AutonomousAgent` @ `feat/phase-0a-gcp-migration` (HEAD `dcdc5b4`, base of `main` is `f67b577`)

---

## 0. Critical context (read first)

**The research doc is a "wish-list spec" not an approved roadmap.**

- An **identical copy** of the doc was added to the repo at `docs/architecture/autonomous-agent-architecture-research.md` (untracked per git status, byte-identical 1028 lines to the `~/.gemini` brain copy).
- The doc was authored by a **different AI session** (Antigravity / Opus 4.6 Thinking) on 2026-05-20, not by the project owner.
- It is **NOT reconciled with the official self-RL ADR** at `docs/decisions/0005-self-rl-pipeline-architecture.md`, which scopes:
  - **Soft loop now** (in-context skill creation, memory curation, user modeling — Phase 1, no GPU)
  - **Hard loop later** (Atropos RL training of weights, Phase 4 only, gated by Telegram approval)
- The research doc adds **10 net-new components** (MoE router, Generator Agent, RLFA self-repair, MAPE-K governor, GRPO-trained memory, A2A protocol, Mem0/Letta-style hierarchical memory, RL training stack with DAPO/GRPO, etc.) — most of which are **strictly out of scope** for Phases 1–3 of the current roadmap.

**Implication for the audit:** there are two valid framings.

1. **"Implement the research doc as written"** — produces a 12–18 month roadmap requiring an RL training infrastructure the project doesn't have yet (no GPU runtime, no GRPO trainer, no Atropos pipeline beyond `trajectories/.gitkeep`). This is a different project than what ADR-0005 sanctioned.
2. **"Cherry-pick the gaps the research doc surfaces that DO matter for the current ADR-0005 roadmap"** — produces a much smaller plan: tighten Phase 1/2 observability (GenAI semantic conventions, trajectory shipping), pull P1 memory-split work earlier, add a metacognitive governor as a Phase 2 safety layer.

`audit-plan.md` presents both framings explicitly. The user's pick determines effort: framing #1 ≈ multi-quarter, framing #2 ≈ 4–8 weeks bolted onto existing Phase 1/2 cycles.

---

## 1. Component-by-component current state (with file:line citations)

The research doc enumerates 10 MUST-HAVE components. Below is what actually exists in the repo for each, with primary file paths and observed gaps. **The "exists" column is independent of the research doc's own self-assessment table** — verified by direct file reads.

### Component 9 — Tiered Sandbox Execution

| Aspect | Research doc says | Repo reality |
|---|---|---|
| Tier inventory | 5 tiers: in-process, Docker, gVisor, Firecracker, WASM | **5 tiers**, but different categorization: `in_process`, `shell_sandbox`, `browser_sandbox`, `external_https`, `cloud_sandbox` (`config/toolsets.yaml:6-12`) |
| Egress allowlist | Required (api.anthropic.com, github.com, etc.) | Implemented at compose/firewall level — see Phase 2 ADR; not parameterized as YAML allowlist |
| gVisor / Firecracker / WASM | Spec'd | **Not implemented**. `cloud_sandbox` tier exists in router but is "Phase 2 onward" per yaml comment (`config/toolsets.yaml:11`) — likely backed by Modal/Daytona (microVM-equivalent) per ADR-0003 |
| Resource caps (CPU/mem/disk) | YAML config | Not surfaced in `config/limits.yaml` for sandbox tier; trusted to Docker defaults |

**Status:** ✅ **conceptually present, tier-naming mismatch**. The research doc's tier list is technology-centric (gVisor/Firecracker/WASM); the repo's is use-case-centric. Both are valid taxonomies — the gap is documentary, not architectural, UNLESS arbitrary-code-execution from untrusted prompts becomes a real attack surface (then gVisor/Firecracker matters).

**Files of interest:** `config/toolsets.yaml`, `lib/toolset_router.py`, `deploy/sandboxes/`, `deploy/docker-compose.yml` (shell-sandbox service).

---

### Component 10 — Observability & Trajectory Pipeline

| Aspect | Research doc says | Repo reality |
|---|---|---|
| OTel SDK wired | "Every turn, tool call, model call observable" | TracerProvider + BatchSpanProcessor + OTLP HTTP exporter in `lib/observability/otel_setup.py:34-108`. Whether spans cover every turn/tool/model call depends on **upstream Hermes-agent** instrumentation, not on this wrapper. **Unverified.** |
| **GenAI semantic conventions** (`gen_ai.system`, `gen_ai.request.model`, `gen_ai.usage.*`) | Required | **Not present in wrapper code**. Wrapper sets only `service.name`. Upstream Hermes may or may not emit GenAI attrs; needs grep across `hermes-agent/agent/*.py` |
| Agent-custom attrs (`agent.expert_id`, `agent.phase`, `agent.memory.context_usage_pct`, etc.) | Required | **Not present**. Wrapper has no concept of `expert_id` or `phase` because there's no MoE router |
| Trajectory store | "Trajectory shipping → GCS → Atropos JSONL" | **Placeholder only** — `trajectories/.gitkeep` exists. No `trajectory-shipper` service, no JSONL writer, no GCS upload path. Architecture spec (`docs/superpowers/specs/2026-05-14-hermes-agent-architecture-design.md:185-199`) describes the Phase 3 design but **none of it is implemented** |
| GRPO/DAPO trainer | Required | **Not present**. No `lib/training/`, no GPU runner. Phase 4 per ADR-0005 |
| Collector pipeline | OTLP → Phoenix (dev) / Cloud Trace (prod) | Exporters: `otlphttp/phoenix` + `debug` (`deploy/otel/collector.dev.yaml:18-30`). Metrics and logs go to `debug` only. Prod config exists separately (`collector.prod.yaml`, not read). |

**Status:** 🟡 **scaffolding present, semantic depth missing**. Spans flow end-to-end, but they're operational telemetry without GenAI-semantic richness. Trajectory pipeline is 0% implemented.

**Files of interest:** `lib/observability/otel_setup.py`, `deploy/otel/collector.{dev,prod}.yaml`, `trajectories/.gitkeep`.

---

### Component 8 — Communication Protocol Stack (MCP + A2A)

| Aspect | Research doc says | Repo reality |
|---|---|---|
| MCP servers wired | "Implemented" | **2 active** (`github` via `ghcr.io/github/github-mcp-server`, `context7` via `https://mcp.context7.com/sse`), **1 deferred** (`playwright`). See `docs/mcp-inventory.md:32-43` |
| A2A protocol | "Not yet" | **Not present anywhere**. Zero matches for "A2A", "Agent2Agent", "agent card" in `lib/`, `deploy/`, `config/`. No `acp_adapter` integration in wrapper despite upstream Hermes shipping `acp_adapter/` |

**Status:** 🟡 **MCP minimal, A2A absent**. The research doc's claim that A2A is "the agent coordination standard backed by 150+ organizations" is forward-looking — building A2A requires deciding whether this agent should DELEGATE to peer agents at all, which contradicts the single-agent Hermes architecture.

**Files of interest:** `docs/mcp-inventory.md`, `config/hermes/cli-config.yaml` (mcp_servers block), `hermes-agent/acp_adapter/`.

---

### Component 6 — Consensus vs. Episodic Memory Split

| Aspect | Research doc says | Repo reality |
|---|---|---|
| Read-only core vs R/W periphery | Required | **Not modeled formally**. Hermes upstream has `MEMORY.md` / `USER.md` / `SOUL.md` files (config/hermes/) that serve different roles, but there's no enforcement of "read-only" on any layer at the storage layer — they're just markdown files the agent rewrites |
| Per-project namespace partitioning | Required | **Not implemented**. The wrapper is a single-project agent. No multi-project isolation primitives |
| Promotion protocol (periphery → core) with quorum | Required | **Not implemented** |
| Anti-pollution: namespace ACLs, contradiction checker, write-ahead log | Required | Wrapper has `lib/durability/checkpoint.py` (resume snapshots) but no contradiction checker, no namespace ACL |

**Status:** ❌ **conceptually missing**. The wrapper agent is single-project. Multi-project memory isolation is out of scope for the current ADR/roadmap.

**Files of interest:** `config/hermes/MEMORY.md`, `config/hermes/USER.md`, `config/hermes/SOUL.md` (need verification — not read in Pass 1).

---

### Component 3 — Hierarchical Memory Manager

| Aspect | Research doc says | Repo reality |
|---|---|---|
| Working / Episodic / Semantic / Procedural layers | Required | **Partially via upstream Hermes**: SQLite sessions = episodic, Chroma vectors = semantic, skill library = procedural, context window = working. No formal layer boundary code in the wrapper |
| Learned `STORE/RETRIEVE/UPDATE/SUMMARIZE/DISCARD` via GRPO (Yu 2026) | Required | **Not implemented**. Memory is curated via the Hermes-upstream "curator" nudge (every 6h, see architecture spec §3.2 L177-184), which is **prompt-based**, not GRPO-policy-based |
| Mem0/Letta-style virtual context management | Required | **Letta-equivalent not implemented**. Hermes has its own `context_compressor.py` + `conversation_compression.py` (upstream `hermes-agent/agent/`) — different mechanism from MemGPT virtual context |
| Wrapper-side memory subsystem | (any) | `lib/memory/` is **only** the `REJECTED.md` slash-command plugin (`/forget`, `/rejections`) — NOT a hierarchical memory manager (`lib/memory/__init__.py:1-12`, `lib/memory/rejected.py`). |

**Status:** 🟡 **upstream provides 4-layer-like storage, no GRPO-trained policy, no formal layer abstraction in wrapper**. Closing the gap to the research doc's spec ≈ rewriting Hermes' memory subsystem.

**Files of interest:** `lib/memory/`, `hermes-agent/agent/curator.py`, `hermes-agent/agent/context_compressor.py`, `hermes-agent/agent/context_engine.py`, `config/hermes/MEMORY.md`.

---

### Component 4 — Intrinsic Reward Engine

| Aspect | Research doc says | Repo reality |
|---|---|---|
| Reward computation from sandbox verification (RLVR/RLAIF) | Required | **Closest analog: 4-judge consensus panel** in `lib/evaluators/{judge,consensus,orchestrator_hook}.py`. Axes: code-correctness, safety, scope-fit, completeness. Uses RLAIF-style LLM-as-Judge (Sonnet 4.6 / Opus 4.7 / Gemini 3.1 Pro per axis, `lib/evaluators/orchestrator_hook.py:22-27`). **But:** verdicts are accept/reject/unsure; no scalar reward, no policy update, no GRPO trainer downstream — feedback is injected as text into the next prompt (`orchestrator_hook.py:54-66`) |
| Multi-signal composition (correctness, efficiency, style, safety) with weights | Required | Partially: 4 axes are weighted via consensus rule (75% accept/reject threshold + 5th-judge tiebreak, `consensus.py:55-143`). No efficiency or style sub-signals. |
| Step-wise dense rewards | Required | **Not implemented**. Judge runs **post-tool-call**, observational — not per-token, not per-trajectory-step |
| GRPO-ready reward signal | Required | **Not implemented**. Judges produce accept/reject for prompt-injection, not (s,a,r) tuples |

**Status:** 🟡 **LLM-as-Judge feedback loop is solid wrapper-side; no RL-trainable reward signal**. The research doc envisions this as the input to a GRPO/DPO trainer. Today, judge outputs only modify the next prompt — not the model.

**Files of interest:** `lib/evaluators/judge.py`, `lib/evaluators/consensus.py`, `lib/evaluators/orchestrator_hook.py`, `lib/anchors/task_spec.py`.

---

### Component 7 — Metacognitive Governor (MAPE-K Loop)

| Aspect | Research doc says | Repo reality |
|---|---|---|
| Monitor (loop / stall / confidence-drop detection) | Required | **Not implemented as a governor**. Closest analogs: `lib/durability/escalation.py` (24h-silence escalation, F32), `lib/durability/budget_watchdog.py` (daily budget cap, F21), `lib/durability/handlers.py` (failure-matrix dispatch). These are **fault handlers**, not metacognitive analyzers — they respond to specific F-codes, not behavioral anomalies |
| Loop detection (3+ identical tool calls) | Required | **Not implemented** |
| Confidence floor / progress stall detection | Required | **Not implemented** |
| Context exhaustion (>0.9 usage) handler | Required | Upstream Hermes has `context_compressor.py` — triggers compaction, not "escalate". No explicit floor |
| Corrective action loop (retry, escalate, swap expert, prune context) | Required | Partial: failure-matrix has dispatch rules per F-code (`lib/durability/failure_matrix.py`); no behavioral-anomaly → corrective-action mapping |

**Status:** ❌ **not implemented as designed**. The repo has failure-matrix-driven fault tolerance for known failure modes (F-codes), but no general-purpose anomaly detection or "is the agent stuck?" monitor.

**Files of interest:** `lib/durability/escalation.py`, `lib/durability/failure_matrix.py`, `lib/durability/handlers.py`, `lib/durability/trichotomy.py`, `docs/architecture/failure-matrix.md`.

---

### Component 1 — Phase-Aware MoE Router

| Aspect | Research doc says | Repo reality |
|---|---|---|
| Phase-level routing, LoRA experts, DeepSeek-V3 dynamic bias load balancing | Required | **Zero matches** for "MoE", "Phase-Aware Router", "router" (in MoE sense), "LoRA", "expert" across `lib/`, `deploy/`, `config/`. Repo does not train models at all (Vertex AI hosted Claude 4.7 only) |
| Trained MoE | Required | Out of scope — depends on Phase 4 RL training landing |

**Status:** ❌ **architecturally out of scope** for current ADR-0005 roadmap.

---

### Component 5 — Free Agent Mechanism (RLFA)

| Aspect | Research doc says | Repo reality |
|---|---|---|
| Expert lifecycle (Active → Warning → Benched → Probation → Replaced/Reinstated) | Required | **Zero matches** for "RLFA", "free agent", "probationary", "batting average" |
| Performance tracker per expert | Required | Not implemented (no experts to track) |

**Status:** ❌ **architecturally out of scope** until Components 1 and 2 exist.

---

### Component 2 — RL-Driven Generator Agent (Agent² framework)

| Aspect | Research doc says | Repo reality |
|---|---|---|
| MDP modeling + algorithmic optimization pipeline that spawns new experts | Required | **Zero matches** for "Generator Agent", "Agent2", "AGA", "spawn expert" |

**Status:** ❌ **architecturally out of scope**. Requires all other components first per the research doc's dependency graph.

---

## 2. Cross-cutting observations

1. **Phase 0a is sucking all the oxygen.** Last 25 commits (`dcdc5b4`…`99aa1bd`) are 100% GCP migration work (Terraform, vm-bootstrap, secrets, override compose). No autonomy-component work in flight. Open PRs: **0**. The current active branch `feat/phase-0a-gcp-migration` is the dominant workstream.

2. **The research doc duplicates inside the repo.** `docs/architecture/autonomous-agent-architecture-research.md` is byte-identical to the brain copy but **untracked** (not in any commit). It's a half-imported aspirational artifact — needs a decision: commit, delete, or pin to an ADR.

3. **There is no Atropos integration anywhere yet.** Despite ADR-0005 calling Atropos the Phase 4 trainer, the only mention is the README link to `github.com/NousResearch/atropos`. `trajectories/` has `.gitkeep` only.

4. **MCP coverage is minimal vs. potential.** Only `github` + `context7` active. The research doc treats MCP as "P0 done" but the runtime tool surface is narrow. Adding Anthropic's recently-shipped MCPs (filesystem, time, fetch) is a 1-line `cli-config.yaml` change.

5. **Two memory-management ecosystems coexist without integration.** Wrapper has `lib/memory/rejected.py` (REJECTED.md), `lib/anchors/spec_store.py` (TaskSpec store), `lib/evaluators/consensus.py` (3-strike fingerprint tracker). Upstream Hermes has `curator.py`, `context_compressor.py`, `context_engine.py`. **No unified memory facade.** This is OK for now (different concerns), but if Component 3 ever lands, these would need to consolidate.

6. **The judge panel is the closest the project has to RL machinery.** It's a real evaluator-in-the-loop (`lib/evaluators/`), but it acts on text prompts, not gradients. Repurposing the judge outputs as a GRPO reward signal would be the single highest-leverage RL bridge.

---

## 3. To enrich in pass 2

Pass-1 confidence is **medium**. Items I want a parallel Explore agent to confirm or refute:

1. **Does upstream Hermes-agent emit OTel GenAI semantic conventions?** Grep `hermes-agent/agent/*.py` for `gen_ai.system` / `gen_ai.usage` / `gen_ai.request.model`. If yes → Component 10's semantic gap is smaller. If no → the gap is real and needs wrapper-side instrumentation.

2. **Does upstream Hermes-agent have a metacognitive loop?** Check `hermes-agent/agent/curator.py`, `agent_init.py`, `error_classifier.py`, `background_review.py` for loop-detection / confidence-tracking / stall-detection logic. Could materially shrink Component 7 gap.

3. **What's in `hermes-agent/acp_adapter/`?** ACP (Agent Communication Protocol) is the predecessor / cousin to A2A. If upstream ships a working ACP gateway, the A2A gap shrinks to "swap protocols" not "build from scratch".

4. **What's in `config/hermes/MEMORY.md` / `USER.md` / `SOUL.md`?** Pass 1 didn't open these. They drive the upstream memory layer; their structure tells us how close (or far) we are from a formal "consensus core" abstraction.

5. **Failure matrix coverage.** Read `docs/architecture/failure-matrix.md`. If it already enumerates the failure modes Component 7 (MAPE-K) is supposed to detect (loops, stalls, confidence-collapse), then "extending the failure matrix" is the right delivery vehicle — not "build a new MAPE-K module".

6. **Current Phase 0a plan status.** Read `docs/superpowers/plans/2026-05-20-phase-0a-gcp-migration.md` to confirm what's done vs queued, so the audit-plan can sequence behind it without conflicts.

7. **Trajectory-shipper design.** The architecture spec mentions it (`docs/superpowers/specs/2026-05-14-hermes-agent-architecture-design.md:185-199`) but the impl is empty. Confirm there's no half-written shipper hiding in a worktree branch.

8. **Phase 2 spec (`docs/spec/phase2.md`).** Authoritative codified Phase 2 spec. Audit plan should align to it.

Pass 2 will dispatch parallel Explore subagents on items 1-3 and 5-8 (item 4 is a quick local read).

---

## 4. Pass 2 enrichment (2026-05-20)

Three parallel `Explore` subagents + a local read filled in the items above. Material findings:

### 4.1 GenAI semantic conventions — REFRAMED, not absent

- **Upstream Hermes emits zero spans of its own.** No `gen_ai.*` attributes, no `set_attribute`, no `start_span`, no LiteLLM wrapping (`hermes-agent/`: 0 hits across all eight grep patterns).
- **BUT** the hook infrastructure exists upstream: `hermes-agent/hermes_cli/plugins.py:701,1296` defines `register_hook()` / `invoke_hook()` for `pre_llm_call`, `post_llm_call`, `post_api_request` — these are **never invoked anywhere in upstream**.
- **And** the wrapper *already* wraps Hermes hooks with **OpenInference** spans: `lib/observability/__init__.py:189,336` emits `openinference.span.kind=LLM` and attributes like `llm.model_name`, `llm.input_messages.{N}.message.role`, `llm.token_count.*`.

**Reframe:** the gap is **OpenInference vs. GenAI semantic-convention dialects**, not "no spans at all". Phoenix consumes OpenInference natively (so Phoenix dashboards work today). The research doc's "GenAI semantic conventions" is the OTel-spec dialect (`gen_ai.*`); the wrapper uses OpenInference's `llm.*`. Both are real. Choosing which to support is a vendor-coupling decision, not an instrumentation gap.

### 4.2 Metacognitive loop — 20% upstream, formal MAPE-K missing

- **`IterationBudget`** at `iteration_budget.py:17-60` — per-agent counter, thread-safe `.consume()/.refund()`, default 90 parent / 50 subagent.
- **Context-window scaling** at `context_compressor.py:814` — summary budget = 5% of model context window. **No threshold-triggered escalation** above a usage floor.
- **Retry-on-transient-error** at `conversation_loop.py:916-2761` — bounded retries with jittered backoff; binary retry/fail, no confidence scoring.
- **Identical-tool-result dedup** at `context_compressor.py:717` — runs **during compression**, post-hoc, not within an in-window detector.
- **Not present:** no loop detection (3+ identical tool calls in window), no confidence floor, no progress-stall detection (no new state change in N iterations), no corrective-action loop (swap-tool / prune-context / escalate).
- **Files matching `*metacog*`, `*governor*`, `*anomaly*`, `*loop_detect*`** — none exist upstream.

**Reframe:** the IterationBudget skeleton is the right place to *attach* a wrapper-side stall/loop detector. The detector itself stays wrapper-side; no upstream fork needed.

### 4.3 ACP adapter — 70% of A2A capability already shipped

Upstream `hermes-agent/acp_adapter/` is **substantial** (not a stub):
- `server.py` (79K) — `HermesACPAgent(acp.Agent)` class at `server.py:445`, async event loop via `asyncio.run(acp.run_agent(...), use_unstable_protocol=True)`
- `tools.py` (55K) — full tool schema construction and execution dispatch
- `session.py` (23K) — session state + MCP server registration (Stdio / SSE / HTTP)
- `events.py`, `permissions.py`, `auth.py`, `entry.py`, `edit_approval.py`

Protocol: **ACP v2 (Agent Client Protocol) — JSON-RPC 2.0 over stdio.** Auth: runtime provider credentials (auto-detected) + terminal setup method.

Wiring: lazy import at `model_tools.py:805` (`acp_adapter.edit_approval.maybe_require_edit_approval()`). The ACP server is a **standalone process**, not an in-process module.

**Missing for A2A:** there's no ACP *client* — Hermes exposes itself via ACP (agent → client) but cannot consume ACP from peer agents (no `acp.run_client(...)`-style call site). To get A2A, the work is "add a client adapter that calls peer agents as tools", not "build the protocol from scratch".

### 4.4 MEMORY / USER / SOUL / AGENTS files — small persona files, no "consensus core"

All four files in `config/hermes/`:
- `MEMORY.md` (9 lines, 256 B) — project-context seed (deployment, LLM, storage)
- `USER.md` (14 lines, 394 B) — Daniel's identity + communication preferences ("Concise > verbose"; "Don't over-celebrate")
- `SOUL.md` (8 lines, 266 B) — persona defaults ("Verify before claiming"; "Prefer small reversible changes")
- `AGENTS.md` (49 lines, 3.5 K) — **the actual working file**: tools available, new-repo-template pointer, conventional commits, sops discipline

**None of these are a "consensus core"** in the research doc's multi-agent sense. They're agent-persona + working-context bootstrap. The closest thing to "read-only core memory" is `AGENTS.md` (because it defines conventions every session inherits), but it's just a markdown file the agent CAN rewrite — there's no enforcement.

### 4.5 Failure matrix — 33 F-codes already registered, two pattern-fits

All 33 F-codes are live (F1-F33) at `docs/architecture/failure-matrix.md:16-64`, with handler dispatch in `lib/durability/handlers.py` and parity tests at `tests/unit/test_handlers.py::test_all_33_codes_dispatch_to_callable` + `test_failure_matrix.py::test_all_33_codes_present`.

**Two existing codes worth examining before adding new ones:**
- **F25 — "Clarification loop max"** — likely the closest analog to a generic loop-detector. Needs handler-code read to confirm what it actually detects.
- **F26 — "3-strike rejection"** — fingerprint tracker already in `lib/evaluators/consensus.py`.

**Confirmed gaps:**
- No F-code for `>0.9 context window usage` (only F19 "Token budget" + F20 "Memory inject overflow" are context-adjacent).
- No F-code for "3+ identical tool calls in N-turn window".

**Canonical pattern for new F-code** (4 steps, CI-guarded):
1. Add to `FAILURE_MATRIX` dict in `lib/durability/failure_matrix.py`.
2. Add row to `docs/architecture/failure-matrix.md` (CI row-count guard enforces parity).
3. Add classifier regex in `lib/durability/trichotomy.py::_CLASSIFIERS` OR direct raise site.
4. Add handler in `lib/durability/handlers.py::HANDLER_REGISTRY` OR stub delegation to one of the baselines (`retry_with_backoff` / `fallback_local_log` / `halt_alert_snapshot`).

Effort for adding F-LOOP + F-STALL drops from "build something" to "follow the established pattern" — **3 days** total including tests, not 4-6.

### 4.6 Phase 0a plan — checkboxes are out of date

`docs/superpowers/plans/2026-05-20-phase-0a-gcp-migration.md` reports **0 of 134 tasks checked**. **But** git log on the active branch (`feat/phase-0a-gcp-migration` @ `dcdc5b4`) shows Task AC-7 has shipped (`feat(ci): phase-0a-deploy.yml — build+push to AR + IAP deploy + smoke check (AC-7)`), and memory says "parallel session deep in Phase E/G at HEAD 4b38232". **The plan checkboxes are not being maintained as the source of truth — git log + open PRs are.**

This means:
- Sequencing decisions should look at git log + PR status, not the plan file.
- The plan **does** touch `deploy/docker-compose.yml` (Phase F Task 26 — `gcplogs` log driver, new `deploy/docker-compose.gcp.override.yml`) → J3/J5 collision risk confirmed.
- The plan **does not** touch `lib/observability/` → J2 collision risk is **lower** than Pass 1 estimated.
- The plan **does** add Terraform Phase C tasks 14-16 (Secret Manager + snapshot scheduling) → J3 Terraform additions for trajectory shipper should coordinate.

### 4.7 Trajectory shipper — confirmed absent, no `services/` dir exists

- No `services/` directory exists at all in the repo root.
- Glob `*trajectory*`, `*shipper*`, `*exporter*` hits **only**: `hermes-agent/trajectory_compressor.py` (utility), `hermes-agent/agent/trajectory.py` (data model), `tests/test_trajectory_compressor*.py`. **None of these ship anywhere.**
- `gs://` / `gcs` in `deploy/`: only **comments** in `deploy/Dockerfile.hermes` and `deploy/docker-compose.yml` referencing *future* snapshots.
- `trajectories/.gitkeep` is the sole marker.

**J3 (trajectory shipper MVP) cannot be reduced** — there's no half-built shipper hiding anywhere.

### 4.8 Phase 2 spec — system-of-record only, NOT a forward roadmap

`docs/spec/phase2.md` is **explicit**: "What this spec is not: It is not a roadmap. It is not a list of things we wish were true." (lines 1-22, 492-510)

Out-of-scope (Appendix B): trajectory shipper, expanded MCPs, GenAI OTel attrs, failure-matrix extensions, metacognitive governor — i.e., **every Framing #2 J-item is "Phase 3 territory"** per the current spec.

**BUT:** ADR-4 (lines 390-416) feature-flags new Layer-5 integrations (GCS snapshot executor is the precedent). So Phase 2 does not *forbid* feature-flagged additions; it just declines to sanction them in scope.

**Implication for H2:** the reconciliation ADR (`docs/decisions/0006-architecture-research-disposition.md`) is **load-bearing** — it's the document that converts Framing #2 J-items from "out of Phase 2 scope" to "approved Phase 3 increments behind feature flags".
