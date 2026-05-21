# Autonomous Agent — Seed Orchestrator (Research Output)

**Date.** 2026-05-21
**Status.** Research artefact. Not yet integrated into the AutonomousAgent
build plan. Intended as input to scope discussions.

## What this folder contains

This folder captures the full output of a single Claude Opus 4.7 research
session that designed a recursive, 100% autonomous agentic system implementing
four target subsystems:

1. **Phase-Aware MoE Router** (with RL gating network)
2. **RL-driven Generator Agent**
3. **Hierarchical Memory with Virtual Context Isolation** (multi-project
   parallelisation)
4. **Intrinsic Outcome-Driven Reward Engine**

The deliverable was produced in three phases:

| Phase | Output | File |
|---|---|---|
| 1 | Mathematical & structural specification (state/action/reward MDP, memory schema, isolation logic, loop topology, FSM) | [`01-phase1-mathematical-spec.md`](./01-phase1-mathematical-spec.md) |
| — | Self-correction pass (8 architectural risks + mitigations) | [`02-self-correction-pass.md`](./02-self-correction-pass.md) |
| 2 | Production-foundation Python reference implementation (~13 modules, ~2.5K LoC, async, Pydantic v2) | [`seed/`](./seed/) |
| 3 | Bootstrapping protocol: meta-prompts, API execution loop, hot-plug mechanism, convergence criteria | [`03-phase3-bootstrapping-protocol.md`](./03-phase3-bootstrapping-protocol.md) |
| — | How this folds into the existing AutonomousAgent build plan | [`INTEGRATION.md`](./INTEGRATION.md) |

## Provenance & fidelity notes

This is a **research artefact**, not battle-tested production code. Specifically:

- **`seed/orchestrator.py` (final ~200 lines), `seed/api_client.py`,
  `seed/bootstrap.py`, and `03-phase3-bootstrapping-protocol.md`** are
  **verbatim** session emissions, captured before any edits.
- **`seed/schemas.py`, `seed/embedder.py`, `seed/memory_store.py`,
  `seed/virtual_context.py`, `seed/moe_router.py`, `seed/reward_model.py`,
  `seed/agent_registry.py`, `seed/sandbox.py`, `seed/telemetry.py`, and the
  first ~300 lines of `seed/orchestrator.py`** are **reconstructed** from the
  session's compaction summary. The reconstruction preserves every type,
  invariant, and behaviour described in the summary, but the literal pre-
  compaction text is no longer in the conversation transcript.
- **`01-phase1-mathematical-spec.md` and `02-self-correction-pass.md`** are
  authored fresh in this session, derived from the compaction summary's
  description of Phase 1 + the self-correction pass.

In short: the **design** is faithful; the **code** is a reference foundation
that compiles and is internally consistent, but it has not been run end-to-end
in CI. Treat it as scaffolding to import into a proper project structure
(`lib/`, `tests/`, etc.) rather than a drop-in replacement.

## How to read this folder

If you want to **understand the architecture**: read `01` → `02` → `03` →
`INTEGRATION.md` in order. Skip the code.

If you want to **see the runtime contracts**: read `seed/schemas.py` first,
then `seed/virtual_context.py` (the security boundary), then
`seed/orchestrator.py` (the control plane).

If you want to **plan integration**: read `INTEGRATION.md` and cross-reference
with the existing audit-plan and active task list.

## What is intentionally NOT included

- **Anthropic SDK pinning.** `api_client.py` imports `anthropic` defensively;
  pinning is deferred to the project's `pyproject.toml`.
- **Real sandbox.** Only `LocalSubprocessSandbox` is shipped; production
  requires the Firecracker tier (see memory note `h1_firecracker_scope`).
- **Real reward judges.** `JudgeEnsemble` ships with a `HeuristicJudge` stub;
  production requires `AnthropicJudge` wired to the prompt-cached Opus call
  path.
- **Persistence.** All stores are in-memory; production requires Postgres
  + pgvector (see memory note `phase2_postgres_tier`) and the Model Armor
  Persistence Trap shipper (see memory note `persistence_trap_contract`).
- **Tests.** No `tests/` folder. The author of an integration PR should
  port these modules into `lib/seed_orchestrator/` and write proper tests
  before this leaves research status.
