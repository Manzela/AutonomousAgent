# Reconciliation — Orchestration Spec vs. Research Doc

**Written:** 2026-05-20
**Sources reconciled:**
- A — `docs/superpowers/specs/2026-05-20-claude-gemini-gcp-orchestration-design.md` (416 lines, committed at `2b99596` by the parallel session)
- B — `~/.gemini/antigravity-ide/brain/c4e71254-9d07-454a-8ef0-52e3ff6703af/autonomous_agent_architecture_research.md` (1028 lines, authored by Antigravity 2026-05-20 17:58)
**Companion memo:** `research-doc-validation.md` (in this dir)

---

## 1. Why this memo exists

A and B were written **on the same day, by independent sessions, with no cross-references between them.** A is operational (how to run Phase 0a using Claude + Gemini against GCP). B is aspirational (what a 100%-autonomous agent system looks like end-to-end).

They are not in conflict. They are **non-overlapping**: A covers Component #8 (Protocol Stack) and Component #10 (Observability), partially touches Component #9 (Sandbox) and Component #3 (Memory infrastructure-as-code), and is **silent on the other six.**

This memo maps the 10 research-doc components onto A's actionable surface, flags integration points that need defining, and produces a single forward-looking backlog.

---

## 2. Component-by-component mapping

| # | Research-doc component | Covered in A (orchestration spec)? | Covered in current Phase 0a plan? | Gap / next step |
|---|---|---|---|---|
| 1 | **Phase-Aware MoE Router** | ❌ Silent | ❌ Not in Phase 0a | Out of scope until Phase 3+. Needs design doc that defines what a "phase" is in this system (research doc treats it as a black box). |
| 2 | **RL Generator Agent (Agent²)** | ❌ Silent | ❌ Not in Phase 0a | Out of scope until Phase 4. Pre-requisites: trajectory pipeline (Component #10) + reward signal (Component #4) must exist first. |
| 3 | **Hierarchical Memory** | ⚠️ Indirect — A migrates `chroma-cloud.env` + `honcho.env` to Secret Manager (Tasks 24-25), but only as credential plumbing | ✅ Plumbing only | **Semantic routing is the gap.** Plugin sockets exist at `hermes-agent/plugins/memory/{9 backends}` (see validation memo §3.1). What's missing is a policy layer that maps memory writes to the right backend by intent (facts → consensus, episodes → reflective, etc.). Could be designed independently of GCP work. |
| 4 | **Intrinsic Reward (CUDA Agent / RLVR / GRPO)** | ❌ Silent | ❌ Not in Phase 0a | Out of scope until Phase 3+. Blocked on Component #10 trajectory pipeline producing usable training signal. |
| 5 | **Free Agent (RLFA)** | ❌ Silent | ❌ Not in Phase 0a | Out of scope until Phase 3+. |
| 6 | **Consensus / Episodic Split** | ❌ Silent | ❌ Not in Phase 0a | Same gap as #3 — both resolve via the memory-routing policy layer. |
| 7 | **Metacognitive Governor (MAPE-K)** | ⚠️ A's 4-tier escalation ladder (L0 Retry → L1 Reflexion → L2 Fallback → L3 Human) at A:276-303 is a **proto-Governor** — it makes meta decisions about retry/escalation. But it lives in Claude's orchestration logic, not as a separate component. | ⚠️ Partially (the escalation ladder is in A) | The escalation ladder is the closest thing in the repo to MAPE-K. Future formalization would lift it out of the orchestrator and give it persistent state (a "governance memory" — which also hits Component #3 / #6). |
| 8 | **Protocol Stack (MCP + A2A)** | ✅ MCP fully covered (cloudrun + bigquery MCPs at A:1-69; Gemini MCP via subprocess invocation) | ✅ Already deployed (per validation memo §2 row 4) | **A2A is the gap.** A's "Claude orchestrator ↔ Gemini executor" loop **is** a two-agent system, but it uses ad-hoc subprocess+JSON envelopes (A:136-155), not the A2A protocol. If A2A standardization matters, this is where to insert it — the envelope at A:136-155 is the natural seam. |
| 9 | **Tiered Sandbox** | ⚠️ A mentions `shell-sandbox` is one of the 10 containers that must be running (per AC-2), but does not extend the sandbox tiers | ✅ Single tier present (`deploy/docker-compose.yml:205-209`) | Multi-tier sandboxing (gVisor / Firecracker / network-isolated) is not in Phase 0a. Single-tier is sufficient for current workload. |
| 10 | **Observability / Trajectory Pipeline** | ✅ Cloud Logging sink + alert policies (A Task 27 at A:262); ✅ Phoenix continues running on VM (per validation memo §2 row 3); ❌ trajectory pipeline itself silent | ✅ Logging + monitoring; ❌ trajectories | **Trajectory pipeline is the gap.** `trajectories/` is empty placeholder. Once #4 / #5 are designed, this needs to capture step-level decisions + rewards in a format consumable by GRPO. **Cloud Trace question is also open** — A does not actually wire it (see validation memo §3.2). |

---

## 3. Disagreements and silences

### 3.1 Where A and B agree
- Both treat **MCP** as the integration backbone.
- Both treat **shell-sandbox** as a current capability (✅).
- Both implicitly treat **Phoenix** as the dev-trace destination.

### 3.2 Where A is silent on something B claims
- B implies "Cloud Trace for prod" — A does not wire it (only `gcplogs` log driver; see validation memo §3.2). **Add to backlog as net-new task, not status flip.**
- B claims SQLite + Chroma deployed — A's Phase 0a explicitly migrates credentials for **Chroma Cloud**, confirming the architectural choice (validation memo §3.3). The research doc framing is the inverse of the actual decision.

### 3.3 Where B is silent on something A introduces
- A's **plan-then-apply gate** (A:87-134) is a discipline that B does not articulate. This pattern (Terraform `plan -out`, `show -json`, review `resource_changes` for `delete`/`replace`, then bound `apply tfplan`) is one of the most important safety mechanisms in the repo. Worth explicitly elevating to a "core orchestration invariant" if/when the research doc is revised.
- A's **4-tier escalation ladder** (A:276-303) is a proto-MAPE-K (see §2 row 7). B treats MAPE-K as ❌-not-implemented, but a building block exists in A.
- A's **fan-out cap of 5 + OAuth probe gate** (A:156-178) is a concurrency-safety mechanism B never discusses.
- A's **structured envelope** (A:136-155) is the seam where A2A would slot in. B doesn't see this.

### 3.4 Where both are silent
- **Multi-project isolation** (a research doc title-page claim) is not implemented anywhere. No project-router, no per-project memory scoping, no per-project sandbox. This is a design gap that neither doc engages with seriously.

---

## 4. Recommended single backlog

Merging the two docs' actionables into one prioritized list. **P0** = blocks Phase 0a completion. **P1** = next phase (post-cutover). **P2/P3** = research doc Phase 3+ aspirations.

### P0 — gates Phase 0a cutover
- ✅ Already in flight by parallel session: **WIF `attribute_condition` patch** (A:391) + **`hermes-provider` Secret Manager registration** (A:392). Per the most recent commits, these are being executed *now*. The corresponding items in this audit's `audit-plan.md` (P0-C, P0-D) are tracking the same work.
- ⚠️ **Audit P0-A pre-flight blocker** (hermes 24h survival RCA, AC-1) is still in `audit/2026-05-20-state-of-the-repo-v2/audit-plan.md` and is not visible in A or B. **This is the most likely path to acceptance failure.** Recommended action: capture the existing 24h ledger evidence and either close AC-1 with proof or open it as the highest-priority remaining P0.

### P1 — post-cutover (next 30 days)
- **Cloud Trace exporter for prod** (validation memo §3.2). Net-new task; add OTel `googlecloud` exporter to a `otel/collector.prod.yaml` and reference from `gcp.override.yml`. Not destructive.
- **Memory-routing policy layer** (research doc Component #3 + #6, see §2 above). Design-then-implement. Plugin sockets exist; the missing piece is a policy in `lib/memory/intent_classifier.py` that maps intent → backend by semantic class (consensus / episodic / scratch). Independent of GCP work; can run in parallel with Phase 0a stabilization.
- **MEMORY.md anti-self-reinforcement mitigation** (validation memo §4). Lightweight: add a provenance tag to every memory entry + a weekly contradiction sweep script.

### P2 — Phase 3 research
- Trajectory pipeline (Component #10, the deferred half) — populates `trajectories/` with step-level decision logs in a format consumable by future training.
- Formalize the 4-tier escalation ladder (A:276-303) as a standalone Metacognitive Governor module with persistent state. This makes Component #7 a real component instead of an emergent property of the orchestrator.

### P3 — Phase 4+ aspirations
- Phase-Aware MoE Router (Component #1) — needs a "phase" abstraction defined first.
- RL Generator Agent / Agent² (Component #2), Free Agent / RLFA (Component #5), Intrinsic Reward / CUDA Agent / RLVR / GRPO (Component #4) — all blocked on the trajectory pipeline producing reward signal.
- A2A protocol replacement of ad-hoc JSON envelope (Component #8). Optional if MCP + envelope remains sufficient.

---

## 5. Open recommendation to the user

When the parallel session finishes Phase 0a (Tasks 16–38 complete + AC-1..AC-10 verified), the natural next move is **not** to start on research-doc Phase 3 components. The natural next move is:

1. **Close AC-1** (hermes 24h survival). This is the only Phase 0a AC that is not currently being driven by the parallel session and that this audit's `audit-plan.md` P0-A item flags as unresolved.
2. **Decide on memory-routing policy** (P1 item). This is the highest-leverage single addition: it unlocks Component #3 *and* #6 *and* the anti-self-reinforcement work, all with no infrastructure dependency.
3. **Add Cloud Trace exporter** (P1 item). Closes the validation memo's §3.2 overclaim and gives prod the trace fidelity B implies it already has.

These three items are mostly independent and could be parallelized (one human + Claude per stream).

---

## 6. References

- A — `docs/superpowers/specs/2026-05-20-claude-gemini-gcp-orchestration-design.md`
- B — `~/.gemini/antigravity-ide/brain/c4e71254-9d07-454a-8ef0-52e3ff6703af/autonomous_agent_architecture_research.md`
- Validation memo — `audit/2026-05-20-state-of-the-repo-v2/research-doc-validation.md`
- Pass-1 findings — `audit/2026-05-20-state-of-the-repo-v2/findings.md`
- Pass-1 fix plan — `audit/2026-05-20-state-of-the-repo-v2/audit-plan.md`
- Hermes architecture spec — `docs/superpowers/specs/2026-05-14-hermes-agent-architecture-design.md`
- Phase 0a spec — `docs/superpowers/specs/2026-05-20-phase-0a-gcp-always-online-design.md`
- Phase 0a plan — `docs/superpowers/plans/2026-05-20-phase-0a-gcp-always-online-implementation-plan.md`
- Memory backends — `hermes-agent/plugins/memory/{byterover,hindsight,holographic,honcho,mem0,openviking,retaindb,supermemory}`
- Memory selector — `lib/memory/intent_classifier.py`
- Compose dev — `deploy/docker-compose.yml` (lines 168, 191, 205-209 referenced above)
- Compose GCP override — `deploy/docker-compose.gcp.override.yml` (lines 8-9, 39-44)
