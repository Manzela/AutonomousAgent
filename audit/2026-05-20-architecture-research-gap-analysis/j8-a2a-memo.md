# J8 — A2A Adoption Spike (decision memo)

**Date:** 2026-05-20
**Author:** Daniel Manzela (+ Claude Opus 4.7)
**Audit item:** J8 in `audit-plan.md` (P2, 2-day timeboxed scoping spike)
**Decision:** **NO — do not wire Google A2A for this single-agent system.** Revisit only if a concrete multi-agent use case lands on the roadmap.
**Status:** Final (decision artifact only; no ADR follows, per audit-plan spec L152: "write — optional, only if outcome is 'yes'")

---

## 1. Critical disambiguation (this is the whole memo)

The research doc's Component 8 lumps two unrelated protocols under one heading. They are not the same thing and one does not satisfy the other:

| Property | **ACP** (Agent Client Protocol) | **A2A** (Agent-to-Agent) |
|---|---|---|
| Origin | Zed Industries (open standard, [zed.dev/blog/agent-client-protocol](https://zed.dev/blog/agent-client-protocol)) | Google ([google.github.io/A2A](https://google.github.io/A2A)) |
| Purpose | IDE / editor ↔ agent integration | Peer agent ↔ peer agent integration |
| Direction | Editor is the client, agent is the server | Both ends are peer agents (either can initiate) |
| Transport | JSON-RPC over stdio (today) | HTTPS + JSON-RPC + agent cards |
| Discovery | None — editor launches agent subprocess | Agent cards published to a registry |
| **Already in Hermes?** | **Yes** — `hermes-agent/acp_adapter/` (pip pkg `agent-client-protocol==0.9.0`, `uv.lock:12`) | **No** — zero references in the repo |

**Audit-plan correction.** The strategic note at `audit-plan.md:263` claims "upstream `hermes-agent/acp_adapter/` is ~70% of A2A capability … Missing piece is just an ACP client to call peer agents." **This is incorrect.** That reading conflates ACP (Zed's IDE protocol, package `agent-client-protocol`) with Google's A2A. Adding an "ACP client" would let Hermes talk to *another editor*, not to another agent. The existing `acp_adapter` contributes **0%** of A2A surface area. This memo supersedes that note.

Evidence (verified 2026-05-20):
- `hermes-agent/uv.lock:12-20` — package `agent-client-protocol==0.9.0` (Zed's pkg)
- `hermes-agent/acp_adapter/server.py:19` — `from acp.schema import …` (Zed schema)
- `hermes-agent/acp_registry/agent.json` — describes "ACP editor integration", links to `hermes-agent.nousresearch.com/docs/user-guide/features/acp`
- Repo-wide grep for `A2A` / `agent2agent` / `agent-to-agent` returns zero hits in any Python file; only stray match is in `feishu.md` referring to Lark bot-to-bot messaging.

---

## 2. Why A2A is not worth wiring (now)

1. **Single-agent system, no peer to talk to.** Hermes today is one agent with a tool belt. A2A is a protocol for *multiple cooperating agents*. There is no second agent on the roadmap (ADR-0005, Phases 0–4), so the protocol has no addressable counterparty.

2. **MCP already covers what the research doc actually means by "agent integration."** Component 8 first half is `MCP_*` toolset registration — that's done (`config/toolsets.yaml`, `lib/toolset_router.py`). The second half (A2A) is a *peer-discovery* layer; we don't need peer discovery for a system with no peers.

3. **No use case on the published roadmap.** ADR-0005 explicitly scopes Phase 4 as RL-training-of-the-single-agent's-weights, not multi-agent coordination. The research doc's MoE/Generator/RLFA components (which would justify A2A) are themselves out of scope per `findings.md` §0.

4. **Adoption cost is non-trivial.** A2A is not a 1-week task: agent cards, peer-discovery registry, RPC envelope, AuthN (mTLS or OAuth), capability declaration, and the receiving-end handler all need to exist. Realistic floor is 1–2 *months* of effort once you include the registry and authn pieces — and that's before the first peer agent is even built. Compare: 2 days to write this memo and rule it out.

5. **Upstream Hermes doesn't speak A2A.** Adding A2A would mean carrying a fork patch that upstream may never accept. ADR-0001 (use upstream Hermes) and our cumulative pattern of contributing back (J13 candidate work, etc.) push toward "stay close to upstream" — A2A would push the other way.

6. **The "agent meets external world" story is already served — by ACP.** When the user wants Hermes embedded in an editor or external orchestrator, the ACP adapter is the integration surface. If a future need arises to coordinate with another *runtime*, the right first move is to extend the existing ACP server to publish capabilities, not to bolt on a second protocol.

---

## 3. Trigger conditions for revisit

Reopen this decision **only if** at least two of the following become true:

- A second agent runtime appears on the roadmap (e.g., a dedicated "research agent" or "coding agent" peer separate from the main Hermes loop).
- A user request requires Hermes to *call* an external agent that exposes A2A (e.g., a Google ADK-built agent or an upstream-shipped A2A server).
- The research doc's MoE Router (Component 1, F2 in audit-plan §3) gets sanctioned — that's the first piece in the doc that would benefit from A2A as the expert-router-to-expert transport.
- The MCP ecosystem stalls on a capability A2A solves cleanly (e.g., long-running back-channel events, peer-initiated handoffs).

None are true on 2026-05-20.

---

## 4. What we keep doing instead

- **ACP adapter stays a first-class integration surface.** Treat it as the supported "external orchestrator → Hermes" entrypoint. Continue tracking upstream ACP schema versions (currently `agent-client-protocol==0.9.0`).
- **MCP is the supported "Hermes → external tool" surface.** Per `config/toolsets.yaml`, default-deny on unknown tiers covers the security boundary.
- **If/when a peer-agent need surfaces**, prefer extending the existing ACP server with capability advertisement before introducing A2A as a second wire protocol — the duplication cost would be high.

---

## 5. Audit-plan downstream updates

- `audit-plan.md:148-153` (J8 spec) — close as "memo written, outcome: no, no ADR follows."
- `audit-plan.md:186` (F7 row, framing-1 table) — flag as **NOT a quick win on top of ACP**; the "alongside existing `hermes-agent/acp_adapter/`" framing implies code reuse that doesn't exist. F7 effort estimate (1–2 months) is reasonable as a *standalone* line item.
- `audit-plan.md:263` (strategic-note table row) — strike the "~70% of A2A capability" claim; it conflates two protocols. The cell should read: "ACP adapter is for IDE integration, not peer agents — does not reduce A2A scope. Memo recommends defer indefinitely."

These edits are bundled with this commit so the audit-plan stays consistent.

---

## 6. References

**This audit:**
- `audit-plan.md` §J8 (L148) — original spec for this spike
- `audit-plan.md` §F7 (L186) and strategic-note table (L263) — items this memo corrects
- `findings.md` §Component 8 (L… ACP/A2A row in the component table) — source of the ambiguous "70%" framing

**Upstream Hermes (verified 2026-05-20):**
- `hermes-agent/acp_adapter/__init__.py` — 1-line docstring (Agent Communication Protocol adapter)
- `hermes-agent/acp_adapter/server.py:1-80` — server impl, imports from `acp.schema`
- `hermes-agent/acp_registry/agent.json` — "ACP editor integration"
- `hermes-agent/uv.lock:12-20` — `agent-client-protocol==0.9.0` from PyPI

**External specs (read 2026-05-20):**
- Agent Client Protocol — https://github.com/zed-industries/agent-client-protocol (Zed Industries)
- Google A2A — https://google.github.io/A2A/ — protocol spec, agent cards, RPC envelope

**Related ADRs:**
- ADR-0001 (use upstream Hermes) — supports "stay close to upstream"
- ADR-0005 (self-RL pipeline) — defines single-agent roadmap; no multi-agent assumption
- ADR-0009 (judge panel as RLAIF) — orthogonal, not affected
