# Research-Doc Validation — `autonomous_agent_architecture_research.md`

**Validated:** 2026-05-20
**Source doc:** `/Users/danielmanzela/.gemini/antigravity-ide/brain/c4e71254-9d07-454a-8ef0-52e3ff6703af/autonomous_agent_architecture_research.md`
**Source doc identity:** 1028 lines, 45,299 bytes, authored 2026-05-20 17:58 by "Antigravity / Claude Opus 4.6 Thinking" (per metadata.json)
**Repo HEAD at validation time:** `6bb5c25` (`feat(terraform): Cloud Router + Cloud NAT for VM outbound internet access`)
**Validator session:** This Claude Code session (separate from the parallel session executing Phase 0a Tasks 16–38)

---

## 1. What this memo is

The research doc is an **aspirational reference architecture** describing 10 "MUST-HAVE" components for a 100%-autonomous agent system (MoE + RL + Dynamic Memory with multi-project isolation). It cross-references the existing hermes-agent architecture spec (`docs/superpowers/specs/2026-05-14-hermes-agent-architecture-design.md`) and adds 4 "glue" subsystems beyond the user's original 6-component framework.

It is **NOT a backlog**, **NOT a plan**, and **NOT a current-state snapshot** — the Status column in the Implementation Priority Matrix (research doc lines 904–915) mixes verified facts, partial truths, and overclaims. This memo separates them.

**Methodology:** for every "Your Status" claim, ran `rg`/`grep`/`find`/`ls` against the live tree, then captured the evidence as `file:line` citations.

---

## 2. Verification table

| # | Research doc claim (Status col) | Verdict | Evidence (`file:line`) | Notes |
|---|---|---|---|---|
| 1 | P0 **Sandbox** — ✅ "Have shell-sandbox + sandbox-runner" | **VERIFIED** | `deploy/docker-compose.yml:205-209` (`shell-sandbox` service, `image: autonomousagent/shell-sandbox:0.1.0`) | Build context `./sandboxes`, hardened per CIS 5.x baseline (per comment at `:321`) |
| 2 | P0 **Observability** — ✅ "OTel + Phoenix wired (dev)" | **VERIFIED (dev)** | `deploy/docker-compose.yml:168` (`otel-collector`), `:191-199` (`phoenix`), `:112` (`OTEL_EXPORTER_OTLP_ENDPOINT: http://otel-collector:4318`) | Phoenix image pinned to `sha256:732f178b...` at `:193`; OTLP gRPC on `127.0.0.1:4317`, UI on `127.0.0.1:6006` |
| 3 | P0 Observability — "Need: Cloud Trace for prod" (implies prod ≠ dev) | **OVERCLAIM** | `deploy/docker-compose.gcp.override.yml:1-35` — override **only** adds `gcplogs` log driver (`x-gcplogs` anchor at `:8-9`) and bind-mounts `hermes-data` (`:39-44`). No Cloud Trace exporter, no swap of `otel-collector` config, no `googlecloud` OTLP exporter | Phoenix runs in prod too. Cloud Logging captures container stdout/stderr; traces still terminate at Phoenix-on-VM. If "Cloud Trace for prod" is the intent, the override **does not implement it** — this is a gap, not a status. |
| 4 | P0 **MCP integration** — ✅ "MCP servers in place" | **VERIFIED** | `docs/mcp-inventory.md` exists (referenced in research doc line 662); per recent commit `4a2ae23` ("mcp inventory + nightly evaluator smoke + auto-review workflow") | |
| 5 | P0 **A2A protocol** — ❌ "Not implemented" | **VERIFIED** | `rg "A2AProtocol\|AgentToAgent"` returns 0 functional matches; the 8 hits for "A2A" are in lockfiles + unrelated docs (`hermes-agent/website/docs/user-guide/messaging/feishu.md`, package-lock.json files) | True gap. |
| 6 | P1 **Memory** — 🟡 "Have memory files, no formal Consensus/Episodic split. Mem0 mentioned but not deployed" | **INCOMPLETE / SIGNIFICANTLY UNDERSTATED** | `hermes-agent/plugins/memory/` contains **9 pluggable backends**: `byterover/`, `hindsight/`, `holographic/`, `honcho/`, `mem0/`, `openviking/`, `retaindb/`, `supermemory/`. `lib/memory/` is the **intent classifier / plugin selector** (`intent_classifier.py`, `plugin.yaml`, `rejected.py`) — not the backend itself. | Memory is much further along than the research doc implies. Mem0 is one of nine, not the headline. The genuine gap is the **Consensus/Episodic split** (research doc Component #6) — the plugin architecture exists, but no router enforces "facts go here / episodes go there." |
| 7 | P1 Memory — "SQLite + Chroma deployed" | **HALF-RIGHT** | `deploy/docker-compose.yml:22` (`# chroma-data: removed (Phase 1 — using Chroma Cloud instead of self-hosted)`) + `:41-54` (explicit comment block: "Chroma Cloud chosen over self-hosted for snapshots + replication"). `deploy/chroma/auth.json` (94 bytes) holds Chroma Cloud credentials. | Chroma is **cloud-managed, not a local container** — research doc's framing implies a self-hosted deployment that was deliberately rejected in Phase 1. SQLite half is plausible (LiteLLM uses Postgres in compose at `litellm-db`, not SQLite — also worth flagging) but the research doc does not name where SQLite lives. |
| 8 | P2 **Reward Engine / CUDA Agent / RLVR / GRPO** — ⏳ "Phase 3/4 placeholder" | **VERIFIED (placeholder)** | `trajectories/` exists but contains only `.gitkeep` (`ls -la trajectories/` → 0-byte file, last touched 2026-05-14). No GRPO/RLVR/CUDA-Agent modules in code. | Aligns with research doc's own claim. |
| 9 | P2 **Metacognitive Governor (MAPE-K)** — ❌ "Not implemented" | **VERIFIED** | `rg -c "MAPE-K\|MetacognitiveGovernor"` returns 0 across `--type py`. | True gap. |
| 10 | P3 **Phase-Aware MoE Router** — ❌ "Not implemented" | **VERIFIED** | `rg "PhaseAware\|Phase-Aware"` returns 0 in `--type py`. | True gap. |
| 11 | P3 **RLFA (Reinforcement Learning Free Agent)** — ❌ "Not implemented" | **VERIFIED** | `rg "RLFA\|FreeAgent"` returns 0 in `--type py`. | True gap. |
| 12 | P4 **Generator Agent (Agent²)** — ❌ "Not implemented" | **VERIFIED** | `rg "Agent2\|GeneratorAgent"` returns 0 in `--type py`. | True gap. |

---

## 3. Key corrections (what the research doc gets wrong)

### 3.1 Memory architecture is further along than "🟡 Partial"

The doc treats memory as "memory files, no formal split, Mem0 mentioned." Reality:

- **`hermes-agent/plugins/memory/`** is a **9-backend pluggable architecture** (byterover, hindsight, holographic, honcho, mem0, openviking, retaindb, supermemory). Each is a separate plugin directory.
- **`lib/memory/intent_classifier.py`** + **`plugin.yaml`** + **`rejected.py`** form an intent classifier / plugin selector. This is the front-door router.
- What's actually missing for research doc Component #6 ("Consensus & Episodic Split") is the **policy layer** that decides which plugin gets which kind of memory write — facts → consensus, traces → episodic. The plugin sockets exist; the routing intent is undefined.

**Action for backlog:** raise memory from "🟡 Partial" to **"🟢 Plugin layer present, ⚠️ semantic routing missing"** and re-scope the work to "define Consensus vs Episodic routing policy across the 9 backends" — not "deploy Mem0."

### 3.2 "Phoenix (dev) / Cloud Trace (prod)" is not actually wired

The implication is that flipping to prod gets Cloud Trace. The override file (`deploy/docker-compose.gcp.override.yml`) only adds the `gcplogs` log driver — it does not:

- Change `otel-collector`'s config (`./otel/collector.dev.yaml` still mounted in prod)
- Add a `googlecloud` OTLP exporter
- Stop the Phoenix service

So in Phase 0a's GCP deploy, **Phoenix is still the trace sink** and Cloud Logging only captures container stdout. If Cloud Trace is the long-term destination, it is a **net-new task**, not a status to be moved to ✅.

**Action for backlog:** Add explicit task — "Add `googlecloud` trace exporter to `otel/collector.prod.yaml` + reference it from gcp.override.yml," scoped to Phase 0b.

### 3.3 Chroma decision is the inverse of what the doc implies

Research doc: "SQLite + Chroma deployed." Repo reality (per the in-source comment block at `deploy/docker-compose.yml:43-54`): self-hosted Chroma was **deliberately removed** in Phase 1 in favor of Chroma Cloud because of `latest` image churn and built-in snapshots. This is a **considered architectural decision**, not a missing piece.

---

## 4. Open question worth flagging: reflective memory self-reinforcement

Research doc line 986 raises (without answering): **"How to prevent reflective memory from self-reinforcing errors?"**

This applies **directly** to the auto-memory pattern used in this very session (see `~/.claude/CLAUDE.md` auto-memory section + the active memory store at `~/.claude/projects/-Users-danielmanzela-RX-Research-Project-AutonomousAgent/memory/`). The current pattern has no anti-self-reinforcement mitigation:

- A wrong inference written to memory becomes "ground truth" for the next session.
- The current rule "Before recommending from memory, verify" is enforced by prompt, not by structure.
- No decay, no confidence scores, no contradiction-detection sweep.

This is a real risk for the autonomous agent vision. Three plausible mitigations the research doc could have explored:

1. **Provenance tagging:** every memory entry carries its source (file:line / commit SHA / verification command + output). Stale provenance auto-marks the entry as suspect.
2. **Contradiction sweep:** periodic LLM pass that re-reads MEMORY.md, picks pairs of related entries, and flags ones that disagree with current code.
3. **Decay:** memories created from inference (not from explicit user instruction) expire on a TTL unless re-confirmed.

**Action for backlog:** if/when MetacognitiveGovernor (Component #7) is built, anti-self-reinforcement should be a first-class concern of its memory-management loop — not a footnote.

---

## 5. Roadmap reality check

Research doc lines 919–946 contain a 16-week Gantt-style roadmap starting **2026-06**. Comparing against the actual plans directory:

```
docs/superpowers/plans/
  └── (Phase 0a plans exist; nothing matches the research doc's "Foundation Hardening" or "Memory Architecture" week-numbered milestones)
```

**Phase 0a (GCP migration)** — currently being executed by the parallel Claude session via Tasks 16–38 of `docs/superpowers/plans/2026-05-20-phase-0a-gcp-always-online-implementation-plan.md`, latest commit `6bb5c25` (Cloud Router + NAT) — **is invisible in the research doc**. The doc's "Foundation Hardening" track doesn't mention always-online infrastructure, WIF, Secret Manager, or any GCP-specific work.

This is a signal: **the research doc and the active Phase 0a plan were written by separate threads, on the same day, without cross-pollination.** They each describe a partial picture.

---

## 6. What this doc IS and ISN'T useful for

| Use case | Verdict |
|---|---|
| Architecture reference for "what 100% autonomous looks like" | ✅ Useful. The 10-component framework + dependency graph + protocol-stack survey (MCP/A2A) is the most cohesive treatment in the project. |
| Backlog / sprint planning input | ⚠️ Needs translation. The Status column is unreliable (see §2). The Roadmap dates are fictional. |
| Phase-mapping (what fits Phase 0a vs Phase 1 vs Phase 3) | ❌ Doesn't engage with the existing phasing. Treats Phase 0a as if it doesn't exist. |
| Current-state snapshot of the repo | ❌ Do not use. Use `audit/2026-05-20-state-of-the-repo-v2/findings.md` for that. |

---

## 7. Cross-references

- Live audit findings — `audit/2026-05-20-state-of-the-repo-v2/findings.md`
- Live audit fix plan — `audit/2026-05-20-state-of-the-repo-v2/audit-plan.md`
- Parallel session's orchestration spec — `docs/superpowers/specs/2026-05-20-claude-gemini-gcp-orchestration-design.md`
- Existing hermes architecture spec — `docs/superpowers/specs/2026-05-14-hermes-agent-architecture-design.md`
- Phase 0a implementation plan — `docs/superpowers/plans/2026-05-20-phase-0a-gcp-always-online-implementation-plan.md`
- Auto-memory pattern docs — `~/.claude/CLAUDE.md` (auto memory section) + `~/.claude/projects/-Users-danielmanzela-RX-Research-Project-AutonomousAgent/memory/MEMORY.md`
- Memory backends — `hermes-agent/plugins/memory/{byterover,hindsight,holographic,honcho,mem0,openviking,retaindb,supermemory}`
- Memory selector — `lib/memory/{intent_classifier.py,plugin.yaml,rejected.py}`
- Compose dev — `deploy/docker-compose.yml`
- Compose GCP override — `deploy/docker-compose.gcp.override.yml`

---

## 8. Recommended next reads (companion memos in this audit dir)

- `orchestration-spec-vs-research-doc-reconciliation.md` (next) — maps the research doc's 10-component framework onto the parallel session's Claude+Gemini+GCP orchestration design, and identifies which components actually have homes in the active Phase 0a plan.
