# SP-01 — LangGraph Spine (P0 trunk) — Design Spec

- **Date:** 2026-06-02
- **Status:** DESIGN — pending operator review (brainstorm gate). No implementation until approved.
- **Owner work-item:** OD-1 (production-deliverables-charter), task #11.
- **Grounding:** `audit/2026-06-01-tier1-audit/SP-01-design-grounding.md`, `SP-01-research-synthesis.md`, `production-deliverables-charter.md`; PRD `audit/2026-05-29-prd-gap-autonomous-sdlc/PRD-autonomous-sdlc-agent.md`; LangGraph 1.2.2 interrupt-under-Send spike (this session, empirical).
- **Reviewer model (planned for the build PR):** Claude Opus 4.8 (implementer Sonnet) — different model class per C9.

## 1. Context & goal

The spine is **the control plane**: a LangGraph `StateGraph` that turns an operator goal into a reviewed, shipped change, with human-in-the-loop only at the two high-stakes gates (plan sign-off, ship). It is the P0 trunk — ~11 downstream items (SP-02 decomposition, SP-05 sandbox, SP-06 eval gate, SP-11 fan-out, SP-12 CI/CD, SP-13 Telegram, SP-16 board, SP-27 monitor) inherit its state schema and node contracts.

**Approach:** build a fresh `app/core/graph.py` that **wraps the existing flat `orchestrator.execute()` as a black-box leaf** — it does NOT replace or refactor the orchestrator. Rejected alternatives: refactoring the orchestrator's internals into nodes now (large blast radius); an `AbstractExecutor` seam (YAGNI for one impl).

**Validation:** the tier-1 (Anthropic/OpenAI/Google) + durable-execution (Temporal/DBOS) + Hermes-agent research confirmed this shape is production-correct; the spec's job is to bake in five cheap structural invariants and reject the over-engineering listed in §12.

## 2. Scope

**In (the walking skeleton — what SP-01 builds):**
```
goal_intake → sign_off[interrupt] → seal_spec → execute(wraps orchestrator.execute)
            → ship_gate[interrupt] → ship_effect → END
```
- `InMemorySaver` checkpointer (CI; zero new deps).
- The **full** `SpineState` schema designed complete now (so downstream inherits a stable contract), even though only the skeleton nodes are wired.
- The `AbstractCheckpointer` ABC + the in-memory adapter.
- The exactly-once ledger + approval-receipt invariant.
- The append-only decision-record.

**Out (later layers — designed-for, not built; each with a trigger):**
| Layer | Trigger |
|---|---|
| `clarify ⇄ decompose` (interview→TaskGraph, bounded ≤5 Qs) | post-skeleton; wraps existing `lib/anchors` |
| `fan_out` via `Send` + per-branch worktree/lease | decompose materializes a multi-node TaskGraph |
| `execute ⇄ test ⇄ fix` evaluator-optimizer loop | full topology |
| `eval_gate` (`defer=True` join, non-LLM roots + Gemini leaf) | fan_out lands |
| `gated_action_gate` + untrusted-read tagging | first external-ingestion node |
| SP-27 monitor sidecar | AgentNote-emitting nodes exist |
| REPLAN native time-travel fork | decompose node exists |
| `AsyncPostgresSaver` (prod) | prod-lock; gated behind the §13 spike |
| SP-IR1 `/panic` ≤30s kill-switch | post-skeleton incident-response |
| `lib/durability/checkpoint.py` `post_tool_call` write-hook → read-through shim | post-merge deprecation; DoD-16 single-writable-store (SP-22). The spine already compiles ONE writable checkpointer; the old hook is a Hermes-loop path that does not write spine state (no live split-brain — verified). Tracked: issue #232 |

## 3. Architecture

- **New `app/core/graph.py`** compiles a `StateGraph`. Control flow is **always code-decided** via conditional edges over deterministic signals (`test_exit_code:int`, ledger membership, static §4.1 lookup). **No top-level LLM router/supervisor.**
- **Node-split invariant (load-bearing):** an `interrupt()` node is *pure* (it only pauses); the side-effect lives in a **distinct post-resume node**. `sign_off`(interrupt) → `seal_spec`(sha-pin the TaskSpec, post-resume); `ship_gate`(interrupt) → `ship_effect`(PR/commit/merge, post-resume, ledger-guarded). Never co-locate `interrupt()` and a side-effect in one node body — LangGraph re-runs a node body from the top on resume, so a pre-interrupt side-effect is at-least-once.
- **`execute`** is a call-through leaf that invokes `orchestrator.execute()` (no new class).
- **Checkpointer injected at FastAPI lifespan**, never inside a node. Exactly one writable checkpointer at `compile()`.

## 4. Graph-state schema — `app/core/graph_state.py` (the high-leverage artifact)

Full `SpineState` `TypedDict`, designed complete now. Key fields:

| field | type / reducer | notes |
|---|---|---|
| `thread_id` | `str` | |
| `goal` | `str` | |
| `clarifications` | `Annotated[list, add]` | later layer populates |
| `plan` | `TaskGraph \| None` | full `TaskNode {id, phase, summary, depends_on, acceptance_ref, allowed_paths}` contract complete now |
| `sign_off` / `ship` | `HitlDecision \| None` | `{verb: APPROVE\|REJECT\|REPLAN, actor, reason, interrupt_id, ts}` |
| `tasks` | `Annotated[list, merge_by_task_id]` | **merge-by-task_id reducer**, not `add` — fan-out idempotency |
| `ledger` | `Annotated[set[tuple[str,str,str]], set.union]` | exactly-once witnesses keyed **`(thread_id, task_id, action_kind)`** *(operator-locked)* |
| `decision_record` | `Annotated[list, add]` | append-only JSONL discipline *(operator-locked: plain now, hash-chain deferred)* |
| `replan_parent` / `pre_decompose_checkpoint_id` | `str \| None` | reserved now for REPLAN |
| `workspace_ref` | `WorkspaceRef {kind: branch\|gcs, ref}` | content-addressed digest — **never inline workspace bytes** |
| `cost_accumulator` | `float` | wires to existing `lib/durability/budget_watchdog.py` — no new budget subsystem |
| `fix_attempts` | `int` | cap; escalate-to-human on cap |
| `audit` | `Annotated[list, add]` | |

**Correlation key (spike-confirmed):** the exactly-once key is **`(thread_id, __pregel_task_id, action_kind)`**, reading `__pregel_task_id` from `config["configurable"]`. The naive `(thread_id, node_id, super_step)` **COLLIDES** under `Send` fan-out (only the Send `idx` differs) — it is **deleted** from the schema; `node_id`/`super_step` survive only as human-readable labels. Cross-validated by Temporal/DBOS idempotency doctrine, LangGraph Issue #6626, and Hermes' own positional-index→content-hash fallback.

**Serialization guard:** state carries only serializable/scrubbable types — **no callables** (`agent_id` refs, not `AgentCapability.invoke`). Add a serialize-time type assertion (the serializer routes through `lib/scrubber.py`, SP-R1 scrub-before-persist).

## 5. Durability / checkpointer

- `AbstractCheckpointer` ABC in `app/core/` (the 7th sibling ABC, per the CLAUDE.md adapter rule).
- `app/adapters/inmemory/` wraps LangGraph `InMemorySaver` (CI/skeleton; **zero new deps**).
- `app/adapters/gcp/` wraps `AsyncPostgresSaver` (prod) — **later**; `langgraph-checkpoint-postgres` + `psycopg` are verified absent from `pyproject.toml`, so this path is unbuildable now and gated behind the §13 spike.
- **`DURABILITY_MODE`** is a per-adapter constant passed into `astream` at the runner/lifespan (NOT hardcoded in `graph.py`): `sync` for prod (a checkpoint is persisted *before* the irreversible super-step), `async`/default for CI. The kill-resume exactly-once oracle is only provable under `sync`.
- **Single writable store:** rewire the existing `lib/durability/checkpoint.py` `post_tool_call` write-hook to a **read-through shim**; keep `step-N.json` as a tested read-only fallback for ≥1 release, then delete. No second writable checkpoint store (split-brain hazard).

## 6. HITL interrupt/resume + exactly-once doctrine

- **Verbs:** `APPROVE` / `REJECT` / `REPLAN`, plus an implicit **`TIMEOUT` → safe-default `REJECT`** for irreversible nodes (Hermes `tools/approval.py` pattern).
- **Resume is ALWAYS the id-map** `Command(resume={interrupt_id: decision})` — never the bare scalar (LangGraph 1.2.2 hard-errors on a scalar resume with >1 outstanding interrupt; the id-map is harmless with one).
- **REPLAN = continue-as-new:** fork a new `thread_id` from `pre_decompose_checkpoint_id`, set `replan_parent`; the old thread and its C8-frozen acceptance YAML are left **immutable** (the audit guarantee). No in-place mutation.
- **Doctrine (stated in the module docstrings):** node bodies are **at-least-once**; LangGraph re-runs a node body from the top on resume; the **`(thread_id, task_id, action_kind)` ledger — NOT the checkpointer — is what makes irreversible effects exactly-once.**
- **Approval-receipt invariant:** model the ledger as `UNIQUE(thread_id, task_id, action_kind)`, **write-before-effect** (reserve-key → act → mark-done). On resume, if a receipt exists for the key, **skip the interrupt** — a crash-after-approve never re-prompts. In-memory skeleton = a dict with that key shape.
- Side-effects (`seal_spec`, `ship_effect`) are post-interrupt, ledger-guarded.

## 7. Two-tier trust gate (later layer; library already on main)

`gated_action_gate` (later): **tier-1 HARDLINE** static-FORBIDDEN deny (from `app/core/trust.py` `ActionClass`) **before any consultation**; **tier-2 GATED** → `interrupt()` + human. Monitor/human may only make tier-2 *more* restrictive (Hermes HARDLINE pattern). `action_class` = static PRD-§4.1 lookup, **never** model-computed from untrusted reads. A C4 callgraph assertion enforces that no untrusted-writing node reaches `action_class`.

## 8. Oversight monitor (later layer)

SP-27 monitor = a **different-model-class sidecar** (per the C9 rule; not a subgraph node — that would compile under the same graph credential and muddy the class boundary). It consumes `astream(stream_mode=['updates','custom'])` `AgentNote`s (`get_stream_writer`) and emits `REJECT`/`REPLAN` via the same id-map resume path. **Lock the `AgentNote` read-surface in the schema now; defer wiring.**

## 9. Non-repudiation decision-record

*(operator-locked)* **Plain append-only JSONL now** — extend the existing `judge_events.py` append-only discipline, keyed by `interrupt_id`. Defer the tamper-evident **hash-chain** to the SP-27 monitor layer / the `#192` `TamperEvidentLedger` follow-up. The audit trail exists from line one; no premature crypto.

## 10. Error handling

- `fix_attempts` cap → **escalate-to-human** (never silently mark done).
- Unsatisfiable spec → `INTERRUPT_FOR_HUMAN` (never auto-REPLAN — anti-drift).
- Fail-open on non-critical (monitor unavailable does not block the graph); fail-closed on safety (`gated_action_gate`).

## 11. Testing / acceptance (TDD; the charter's hard oracles)

- **DoD-1 (exactly-once conversation-state):** a per-node execution counter; kill the graph mid-`execute`, resume, assert **every key → count == 1** under `sync` durability. The build MUST first show the naive `(thread_id,node_id,super_step)` key **FAIL** this (it double-counts), then the `(thread_id, task_id, action_kind)` key **PASS** — red-green on the key choice itself.
- **DoD-4 (channel arbitration):** every inbound steering message normalizes to a `SteeringEvent` deduped on `(channel, origin_id)` and survives resume; REJECT beats APPROVE (deterministic C15 arbitration).
- **DoD-17 (workspace rehydrate):** `workspace_ref` byte-equal rehydrate — the second, independent resume-state piece distinct from DoD-1's conversation counter.
- **Skeleton acceptance test:** `goal_intake → interrupt@sign_off → resume APPROVE(id-map) → seal_spec → execute(orchestrator stubbed via the inmemory adapter) → interrupt@ship → resume APPROVE → ship_effect → END`; assert two interrupts surfaced *with ids*, the checkpoint persisted+restored across each interrupt, and the ledger proves exactly-once on a re-driven resume.
- **REJECT test** (halts, audited, decision recorded). **REPLAN test** (new `thread_id`, `replan_parent` set, old thread + YAML immutable).
- **Exactly-once regression:** a side-effect-before-`interrupt()` node must fail red, then go green by moving the effect to a post-resume node / ledger-guard.
- CI runs `adapters/inmemory`; prod runs `adapters/gcp`. New sha-pinned `audit/acceptance/SP-01.yaml` registers the oracle.

## 12. Anti-over-engineering (explicit rejections — grounded)

- **No saga/compensation engine** — git content-addressing + check-then-act is naturally idempotent; deploy-rollback lives at SP-26/SP-IR1 as discrete operator actions.
- **No second writable checkpoint store** — read-through shim only.
- **No child-thread-per-Send-branch** — native `GraphBubbleUp` gives per-branch pause; child-threads only if the ledger key collided (it doesn't, keyed on `__pregel_task_id`).
- **No bespoke fan-out barrier counter** — `add_node(defer=True)` is the join primitive.
- **No seed MoE/PPO/reward/Free-Agent port** — PRD non-goal #1.
- **No top-level LLM supervisor** — the DAG encodes control.
- **No prompt-injection classifier as primary defense** — the load-bearing control is structural (zero-standing-secrets + default-deny egress, already shipped).
- **No `frozen=True` dataclass** — `spec_sha` + write-once is the immutability oracle (frozen would break draft→locked `model_copy`).
- **No Postgres-now** — deps absent; gated behind the spike.

## 13. Open spikes / operator items

- **Pre-prod-lock spike (gates §5 Postgres):** confirm `config["configurable"]["__pregel_task_id"]` is present and **stable across kill+resume on `AsyncPostgresSaver`** (the session spike covered only `InMemorySaver`/sync). Add `langgraph-checkpoint-postgres` + `psycopg[binary]` (verified absent). Also assert a consistent ledger-row + checkpoint commit order.
- **(Resolved this session:)** sibling-non-cancellation under `Send` + interrupted-node-re-run on 1.2.2 — empirically confirmed; no further spike needed pre-fan_out.

## 14. File plan

- `app/core/graph.py` (new) — the StateGraph + node-split.
- `app/core/graph_state.py` (new) — `SpineState`, `HitlDecision`, `TaskNode`/`TaskGraph`, `WorkspaceRef`, reducers.
- `app/core/checkpointer.py` (new) — `AbstractCheckpointer` ABC.
- `app/adapters/inmemory/checkpointer.py` (new) — wraps `InMemorySaver`.
- `app/adapters/gcp/checkpointer.py` (later) — wraps `AsyncPostgresSaver`.
- `tests/unit/test_graph_spine.py` + the exactly-once oracle; `audit/acceptance/SP-01.yaml` (sha-pinned) + SHA256SUMS.
- (later) rewire `lib/durability/checkpoint.py` write-hook → read-through shim.
