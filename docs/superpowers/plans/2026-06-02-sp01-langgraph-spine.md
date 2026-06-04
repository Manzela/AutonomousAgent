# SP-01 — LangGraph Spine (walking skeleton) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the P0 control-plane trunk — a LangGraph `StateGraph` that turns an operator goal into a reviewed, shipped change with human-in-the-loop at exactly two durable gates (plan sign-off, ship), wrapping the existing `orchestrator.execute()` as a black-box leaf, with an exactly-once ledger that survives process death.

**Architecture:** A fresh `app/core/graph.py` compiles a 6-node interrupt-split skeleton (`goal_intake → sign_off[interrupt] → seal_spec → execute → ship_gate[interrupt] → ship_effect → END`). Control flow is always code-decided via conditional edges over deterministic signals — no LLM router. The **full** `SpineState` schema is designed complete now (≈11 downstream items inherit it) even though only skeleton nodes are wired. A new `AbstractCheckpointer` **provider/factory** ABC (the 7th sibling ABC) injects a LangGraph `BaseCheckpointSaver` at a runner seam; CI uses `InMemorySaver` (zero new deps). Irreversible effects are made exactly-once by a `(thread_id, __pregel_task_id, action_kind)` ledger (write-before-effect), **not** by the checkpointer.

**Tech Stack:** Python 3.11, langgraph 1.2.2 + langgraph-checkpoint 4.1.1 (verified installed; `langgraph-checkpoint-postgres`/`psycopg` verified ABSENT — prod path deferred), pydantic, pytest (`asyncio_mode="auto"`), existing `lib/scrubber.py`, `lib/anchors`, `lib/evaluators/judge_events.py`, `app/core/orchestrator.py`, `app/core/trust.py`.

---

## Grounding (empirically verified this session — do not re-litigate)

All API facts below were confirmed by running `./.venv/bin/python` against the installed langgraph 1.2.2; the plan's code depends on them:

| Fact | Verified value |
|---|---|
| Imports | `from langgraph.graph import StateGraph, START, END`; `from langgraph.types import interrupt, Command, Send, Interrupt`; `from langgraph.config import get_stream_writer, get_config`; `from langgraph.checkpoint.memory import InMemorySaver`; `from langgraph.checkpoint.base import BaseCheckpointSaver` |
| `BaseCheckpointSaver` | Is **itself** a rich ABC (`get_tuple/put/put_writes/list` + async variants, 15+ methods). `InMemorySaver` subclasses it. ⇒ `AbstractCheckpointer` must be a thin **provider** (`build_saver() -> BaseCheckpointSaver`), **not** a re-declaration of that protocol (re-declaring it is the over-engineering §12 forbids). |
| `durability` | A per-call param on `invoke/ainvoke/stream/astream` typed `Literal['sync','async','exit']`; **not** a `compile()` arg. `compile(checkpointer=...)` takes the single writable saver. |
| `__pregel_task_id` | Readable inside a node via `config["configurable"]["__pregel_task_id"]` (node signature `async def node(state, config)`); **stable across crash+resume** (the ledger-guard de-dup produced `count==1`). |
| interrupt/resume | `interrupt(value)` pauses; result carries `__interrupt__: [Interrupt(value=..., id='<hex>')]` (`.id`/`.value` attrs). `get_state(cfg).next == ('<node>',)` confirms pause. Resume via id-map `Command(resume={interrupt_id: decision})`; **only the interrupted node re-runs from the top** (already-completed nodes do not). Bare scalar resume is harmless with one outstanding interrupt but we mandate the id-map for fan-out robustness. |
| crash simulation | A **fresh** `compile()` sharing the **same** saver instance resumes the prior checkpoint — the faithful in-process stand-in for process death with a persistent saver. |
| `orchestrator.execute` | `async def execute(request: TaskRequest, capability: AgentCapability, *, agent_identity=None, peer_timeout_s=30.0, local_timeout_s=60.0) -> ExecutionResult` (app/core/orchestrator.py:89). Always returns `ExecutionResult` (never raises except re-raises `CancelledError`). Frozen leaf — do **not** refactor. |
| schemas (frozen pydantic) | `TaskRequest(task_id, phase="draft", summary="", budget=BudgetVector(), deadline_s=60.0, ...)`; `AgentCapability(agent_id, version, phase, description, invoke=Callable|None, ...)`; `ExecutionResult(task_id, status: TaskStatus, cost_usd=0.0, ...)`; `TaskStatus ∈ {PENDING,INFLIGHT,COMPLETED,FAILED,REFUSED,CANCELED}` (app/core/schemas.py:59-178). |
| ABC pattern | core ABC in `app/core/<name>.py` (`abc.ABC` + `@abstractmethod` raising `NotImplementedError`); impls in `app/adapters/{inmemory,gcp}/<name>.py`; async methods; GCP guards deps with `_HAS_*`/`ImportError`. |
| scrubber | `def scrub_string(text: str, *, source: str = 'unknown') -> str` (lib/scrubber.py:167) — fail-open, returns input unchanged if not `str`. |
| anchors seal | `from lib.anchors.spec_store import SpecStore, compute_spec_sha`; `from lib.anchors.task_spec import TaskSpec, Scope`. Seal = `store.save(spec.model_copy(update={'status':'locked'}))` (NO `frozen=True`). |
| decision-record | Extend `lib/evaluators/judge_events.py:_append_line` discipline (`os.open(O_APPEND|O_CREAT)` + `fcntl.flock`), new path + schema, keyed by `interrupt_id`. Plain JSONL now; hash-chain deferred. |
| acceptance | `audit/acceptance/<id>.yaml` schema = `task,title,driver,phase,run_from,assertions:[{id,description,cmd,expect_exit}],proof`; `cmd` runs `bash -c` at repo root. Pin via `scripts/ci/acceptance_frozen.py --write` (regenerates `SHA256SUMS`), then `chmod 444`. CODEOWNERS-guarded. |
| tests | `tests/unit/`; `pyproject.toml` `asyncio_mode="auto"`, `--strict-markers`; root `conftest.py` blocks non-loopback sockets (autouse); no-skip gate (`scripts/ci/assert_no_skips.py`) — no `skip`/`xfail` without a `SKIPS.yaml` entry. |

### Scope reconciliation (locked by the spec; one explicit minimisation)

- **In (this PR):** the 6-node skeleton, the **full** `SpineState` schema + reducers, `AbstractCheckpointer` ABC + inmemory adapter, the exactly-once ledger + approval-receipt invariant, the append-only decision-record, and the sha-pinned `audit/acceptance/SP-01.yaml`.
- **DoD-4 (channel arbitration) and DoD-17 (workspace rehydrate) are satisfied MINIMALLY at the schema+reducer level** — the `merge_steering` dedup reducer + `arbitrate()` (REJECT-beats-APPROVE) helper, and `workspace_ref` byte-equal survival across kill+resume. The full SP-17 channel bus (real Telegram/board normalisation) and full SP-R7 GCS snapshotter stay as their own downstream items. This is consistent with "full schema now, skeleton nodes only."
- **Out (designed-for, not built):** `clarify⇄decompose`, `fan_out`(Send), `execute⇄test⇄fix`, `eval_gate`, `gated_action_gate`, SP-27 monitor sidecar, `AsyncPostgresSaver` (deps absent), `/panic`. Each has a trigger in spec §2. The `lib/durability/checkpoint.py` write-hook → read-through shim rewire is a **post-merge deprecation** (see "Later", not a build task here).

---

## File structure

**New implementation files**

| File | Responsibility |
|---|---|
| `app/core/graph_state.py` | `SpineState` TypedDict (full schema), `HitlDecision`/`TaskNode`/`TaskGraph`/`WorkspaceRef`/`LedgerReceipt`/`SteeringEvent` types, the reducers (`_merge_by_task_id`, `_ledger_union`, `_merge_counts`, `_merge_cost`, `_merge_steering`), `arbitrate()`, `ledger_key()`/`_ledger_has()`, `assert_serializable_state()` (no-callable + scrub guard). Doctrine docstring. |
| `app/core/checkpointer.py` | `AbstractCheckpointer` ABC — provider/factory seam: `build_saver() -> BaseCheckpointSaver`, `durability_mode: Literal['sync','async','exit']` property, `setup()`/`aclose()` lifecycle. The 7th sibling ABC. |
| `app/adapters/inmemory/checkpointer.py` | `InMemoryCheckpointer(AbstractCheckpointer)` wrapping `InMemorySaver`; `durability_mode='async'` (CI default). Zero new deps. |
| `app/core/decision_record.py` | `append_decision(decision, *, path=None, enabled=None) -> Optional[Path]` — append-only JSONL keyed by `interrupt_id`, reusing the `judge_events` flock discipline; fail-open. |
| `app/core/graph.py` | `build_spine(saver, *, capability=None)` compiles the `StateGraph`; the 6 nodes (interrupt-split), conditional edges (APPROVE/REJECT/REPLAN), ledger-guarded effects. Doctrine docstring. |
| `app/core/spine_runner.py` | `SpineRunner(checkpointer: AbstractCheckpointer, *, capability=None)` — the injection seam a future FastAPI lifespan constructs. `start()/resume()/get_state()`, passing `durability=provider.durability_mode`. Keeps `graph.py` env-agnostic. |

**New test / acceptance files**

| File | Responsibility |
|---|---|
| `tests/unit/test_graph_state.py` | schema + reducer red-greens, incl. the naive-vs-content **key-choice** collision oracle and the no-callable serialize guard. |
| `tests/unit/test_checkpointer.py` | ABC cannot instantiate; `InMemoryCheckpointer.build_saver()` returns a `BaseCheckpointSaver`; `durability_mode=='async'`. |
| `tests/unit/test_decision_record.py` | append-only JSONL keyed by `interrupt_id`; fail-open; concurrent-safe. |
| `tests/unit/test_graph_spine.py` | the graph oracles: skeleton happy-path, REJECT, REPLAN, exactly-once (DoD-1), side-effect-before-interrupt regression, approval-receipt, DoD-4 survival/arbitration, DoD-17 workspace rehydrate. |
| `audit/acceptance/SP-01.yaml` + `audit/acceptance/SHA256SUMS` | sha-pinned oracle registering the DoD assertions; manifest regenerated + `chmod 444`. |

**Conventions:** branch `feat/sp01-langgraph-spine` from `main` (confirm branch-name regex with `git config` / the repo's pr-meta gate before pushing); conventional commit titles `feat(spine): …` / `test(spine): …`, lowercase subject; signed commits (gitsign/GPG); never `--no-verify`; never push unless asked.

---

## Task 0: Branch + async-node interrupt smoke (de-risk the harness)

**Files:**
- Test: `tests/unit/test_graph_spine.py` (create, smoke only)

- [ ] **Step 1: Create the branch from main**

```bash
git -C "$(git rev-parse --show-toplevel)" fetch origin
git switch -c feat/sp01-langgraph-spine origin/main
```

- [ ] **Step 2: Write a smoke test proving async-node interrupt + id-map resume + `__pregel_task_id`**

Create `tests/unit/test_graph_spine.py`:

```python
"""SP-01 spine oracles. Skeleton: goal_intake -> sign_off[interrupt] -> seal_spec
-> execute -> ship_gate[interrupt] -> ship_effect -> END."""
from __future__ import annotations

import operator
from typing import Annotated

from typing_extensions import TypedDict

from langgraph.graph import StateGraph, START, END
from langgraph.types import interrupt, Command
from langgraph.checkpoint.memory import InMemorySaver


async def test_async_node_interrupt_idmap_resume_and_pregel_task_id():
    """Harness smoke: async node, interrupt, id-map resume, __pregel_task_id read."""

    class S(TypedDict):
        log: Annotated[list, operator.add]
        seen_ptid: str
        decision: str

    async def gate(state, config):
        d = interrupt({"q": "ok?"})
        return {"decision": d, "log": ["gate resumed"]}

    async def effect(state, config):
        ptid = config["configurable"]["__pregel_task_id"]
        return {"seen_ptid": ptid, "log": [f"effect ptid={bool(ptid)}"]}

    g = StateGraph(S)
    g.add_node("gate", gate)
    g.add_node("effect", effect)
    g.add_edge(START, "gate")
    g.add_edge("gate", "effect")
    g.add_edge("effect", END)
    app = g.compile(checkpointer=InMemorySaver())
    cfg = {"configurable": {"thread_id": "smoke"}}

    r1 = await app.ainvoke({"log": [], "seen_ptid": "", "decision": ""}, cfg, durability="sync")
    assert "__interrupt__" in r1
    intr = r1["__interrupt__"][0]
    assert intr.id and intr.value == {"q": "ok?"}
    assert app.get_state(cfg).next == ("gate",)

    r2 = await app.ainvoke(Command(resume={intr.id: "APPROVE"}), cfg, durability="sync")
    assert r2["decision"] == "APPROVE"
    assert r2["seen_ptid"]  # __pregel_task_id was non-empty inside the node
```

- [ ] **Step 3: Run it — expect PASS (harness/env sanity, no SP-01 code yet)**

Run: `uv run python -m pytest tests/unit/test_graph_spine.py::test_async_node_interrupt_idmap_resume_and_pregel_task_id -q`
Expected: PASS (1 passed).

- [ ] **Step 4: Commit**

```bash
git add tests/unit/test_graph_spine.py
git commit -m "test(spine): pin async-node interrupt + id-map resume + __pregel_task_id harness"
```

---

## Task 1: `graph_state.py` — full `SpineState` schema + reducers

**Files:**
- Create: `app/core/graph_state.py`
- Test: `tests/unit/test_graph_state.py`

- [ ] **Step 1: Write failing reducer + key-choice tests**

Create `tests/unit/test_graph_state.py`:

```python
"""SP-01 graph_state schema + reducer oracles, incl. the ledger key-choice red-green."""
from __future__ import annotations

import pytest

from app.core import graph_state as gs


def test_merge_by_task_id_is_idempotent_lww():
    a = [{"task_id": "t1", "status": "inflight"}]
    b = [{"task_id": "t1", "status": "completed"}, {"task_id": "t2", "status": "completed"}]
    out = gs._merge_by_task_id(a, b)
    by_id = {t["task_id"]: t for t in out}
    assert by_id["t1"]["status"] == "completed"   # last-write-wins per task_id
    assert set(by_id) == {"t1", "t2"}             # no duplicate t1


def test_ledger_union_dedups_by_three_tuple_key():
    r1 = {"thread_id": "T", "pregel_task_id": "p1", "action_kind": "ship",
          "node_label": "ship_effect", "super_step_label": 5, "ts": "z"}
    r1b = dict(r1, ts="later")                    # same key, different payload
    out = gs._ledger_union([r1], [r1b])
    assert len(out) == 1                           # one receipt per (thread,ptid,kind)
    assert out[0]["ts"] == "z"                     # first write wins (write-before-effect)


def test_naive_key_collides_content_key_distinct():
    """DoD-1 red-green ON THE KEY CHOICE: two parallel Send branches share
    (node_id, super_step) but differ in __pregel_task_id. The naive key collapses
    them (FAIL — one entry for two tasks); the content key keeps them distinct."""
    branch_a = {"thread_id": "T", "pregel_task_id": "pA", "action_kind": "ship",
                "node_id": "execute", "super_step": 4}
    branch_b = {"thread_id": "T", "pregel_task_id": "pB", "action_kind": "ship",
                "node_id": "execute", "super_step": 4}

    naive = {gs._naive_key(branch_a), gs._naive_key(branch_b)}
    content = {gs.ledger_key(branch_a), gs.ledger_key(branch_b)}

    assert len(naive) == 1     # COLLISION: (T, execute, 4) is identical -> exactly-once oracle false-positives
    assert len(content) == 2   # DISTINCT: __pregel_task_id separates the two branches


def test_merge_counts_and_cost_are_additive():
    assert gs._merge_counts({"k": 1}, {"k": 1}) == {"k": 2}
    assert gs._merge_cost({"a": 0.5}, {"a": 0.25, "b": 1.0}) == {"a": 0.75, "b": 1.0}


def test_merge_steering_dedups_on_channel_origin_id():
    e1 = {"channel": "telegram", "origin_id": "m1", "verb": "APPROVE", "interrupt_id": "i", "ts": "1"}
    dup = dict(e1, ts="2")
    e2 = {"channel": "board", "origin_id": "c9", "verb": "REJECT", "interrupt_id": "i", "ts": "3"}
    out = gs._merge_steering([e1], [dup, e2])
    keys = {(e["channel"], e["origin_id"]) for e in out}
    assert keys == {("telegram", "m1"), ("board", "c9")}   # at-most-once per (channel,origin_id)
    assert sum(1 for e in out if (e["channel"], e["origin_id"]) == ("telegram", "m1")) == 1


def test_arbitrate_reject_beats_approve_for_one_interrupt():
    events = [
        {"channel": "telegram", "origin_id": "m1", "verb": "APPROVE", "interrupt_id": "i7", "ts": "1"},
        {"channel": "board", "origin_id": "c9", "verb": "REJECT", "interrupt_id": "i7", "ts": "2"},
    ]
    assert gs.arbitrate(events, "i7") == "REJECT"          # reject beats approve (deterministic C15)
    only_approve = [events[0]]
    assert gs.arbitrate(only_approve, "i7") == "APPROVE"


def test_assert_serializable_rejects_callables_and_scrubs_secrets():
    with pytest.raises(TypeError):
        gs.assert_serializable_state({"goal": (lambda: 1)})   # no callables in state
    cleaned = gs.scrub_state({"goal": "token sk-ABCDEF0123456789abcdef0123456789abcd"})  <!-- pragma: allowlist secret -->
    assert "sk-ABCDEF0123456789abcdef0123456789abcd" not in cleaned["goal"]  <!-- pragma: allowlist secret -->
```

- [ ] **Step 2: Run — expect import/AttributeError fail**

Run: `uv run python -m pytest tests/unit/test_graph_state.py -q`
Expected: FAIL (`ModuleNotFoundError: app.core.graph_state` / `AttributeError`).

- [ ] **Step 3: Implement `app/core/graph_state.py`**

```python
"""SpineState — the LangGraph control-plane state contract (SP-01).

DOCTRINE (load-bearing — read before editing a node):
- Node bodies are AT-LEAST-ONCE. langgraph 1.2.2 re-runs an *interrupted* node's
  body from the top on resume (verified empirically). A side-effect placed BEFORE
  interrupt() in the same node body is therefore at-least-once.
- The (thread_id, __pregel_task_id, action_kind) LEDGER — NOT the checkpointer —
  is what makes irreversible effects EXACTLY-ONCE. reserve-key -> act -> mark-done,
  write-before-effect; on re-entry a present receipt SKIPS the effect.
- State carries ONLY serializable/scrubbable types. NO callables (agent_id refs,
  never AgentCapability.invoke). The serializer routes through lib/scrubber.py
  (SP-R1) and asserts no-callable at serialize time.
- node_id / super_step are HUMAN-READABLE LABELS ONLY. They are NEVER part of the
  exactly-once key — that key is (thread_id, __pregel_task_id, action_kind). The
  naive (thread_id, node_id, super_step) key COLLIDES under Send fan-out.
"""
from __future__ import annotations

from typing import Annotated, Any, Literal, Optional

from typing_extensions import TypedDict

from lib.scrubber import scrub_string

# ── Verbs / kinds ──────────────────────────────────────────────────────────
HitlVerb = Literal["APPROVE", "REJECT", "REPLAN", "TIMEOUT"]
ActionKind = Literal["seal_spec", "ship"]   # the skeleton's two irreversible effect kinds

# Deterministic arbitration precedence (C15): higher number wins. REJECT/REPLAN
# beat APPROVE; TIMEOUT maps to the safe default (treated as REJECT-strength).
_VERB_PRECEDENCE: dict[str, int] = {"APPROVE": 0, "TIMEOUT": 2, "REPLAN": 3, "REJECT": 4}


class HitlDecision(TypedDict):
    verb: HitlVerb
    actor: str
    reason: str
    interrupt_id: str
    ts: str


class WorkspaceRef(TypedDict):
    kind: Literal["branch", "gcs"]
    ref: str        # branch name or GCS object path — NEVER inline workspace bytes
    digest: str     # sha256 of workspace content; the DoD-17 byte-equal oracle


class TaskNode(TypedDict):
    id: str
    phase: Literal["research", "draft", "refine", "verify", "ship"]
    summary: str
    depends_on: list[str]
    acceptance_ref: str
    allowed_paths: list[str]


class TaskGraph(TypedDict):
    nodes: list[TaskNode]
    edges: list[tuple[str, str]]   # (from_id, to_id) depends_on edges


class LedgerReceipt(TypedDict):
    thread_id: str
    pregel_task_id: str      # config["configurable"]["__pregel_task_id"]
    action_kind: ActionKind
    node_label: str          # human-readable only — NOT part of the key
    super_step_label: int    # human-readable only — NOT part of the key
    ts: str


class SteeringEvent(TypedDict):
    channel: str             # "telegram" | "board" | ...
    origin_id: str           # provider message id — the (channel,origin_id) idempotency key
    verb: HitlVerb
    interrupt_id: Optional[str]
    ts: str


# ── Reducers ───────────────────────────────────────────────────────────────
def _merge_by_task_id(existing: Optional[list], incoming: Optional[list]) -> list:
    """tasks reducer: last-write-wins per task_id (fan-out idempotency, not `add`)."""
    by_id: dict[str, Any] = {t["task_id"]: t for t in (existing or [])}
    for t in incoming or []:
        by_id[t["task_id"]] = t
    return list(by_id.values())


def ledger_key(receipt: dict) -> tuple[str, str, str]:
    """THE exactly-once key: (thread_id, __pregel_task_id, action_kind)."""
    return (receipt["thread_id"], receipt["pregel_task_id"], receipt["action_kind"])


def _naive_key(receipt: dict) -> tuple[str, str, int]:
    """The REJECTED key — present only so the collision is a runnable red-green."""
    return (receipt["thread_id"], receipt["node_id"], receipt["super_step"])


def _ledger_union(existing: Optional[list], incoming: Optional[list]) -> list:
    """Dedup receipts by ledger_key; first write wins (write-before-effect)."""
    seen: dict[tuple, Any] = {}
    for r in (existing or []) + (incoming or []):
        seen.setdefault(ledger_key(r), r)
    return list(seen.values())


def _merge_counts(existing: Optional[dict], incoming: Optional[dict]) -> dict:
    """Additive int merge (the DoD-1 proof counter)."""
    out = dict(existing or {})
    for k, v in (incoming or {}).items():
        out[k] = out.get(k, 0) + v
    return out


def _merge_cost(existing: Optional[dict], incoming: Optional[dict]) -> dict:
    """Additive float merge (cost_accumulator; wires to budget_watchdog later)."""
    out = dict(existing or {})
    for k, v in (incoming or {}).items():
        out[k] = out.get(k, 0.0) + v
    return out


def _merge_steering(existing: Optional[list], incoming: Optional[list]) -> list:
    """DoD-4 part 1: at-most-once survival, deduped on (channel, origin_id)."""
    by_key: dict[tuple, Any] = {}
    for e in (existing or []) + (incoming or []):
        by_key.setdefault((e["channel"], e["origin_id"]), e)
    return list(by_key.values())


def arbitrate(events: list, interrupt_id: str) -> Optional[str]:
    """DoD-4 part 2: deterministic C15 winner for one interrupt_id.
    REJECT > REPLAN > TIMEOUT > APPROVE. Returns None if no event matches."""
    candidates = [e for e in events if e.get("interrupt_id") == interrupt_id]
    if not candidates:
        return None
    return max(candidates, key=lambda e: _VERB_PRECEDENCE[e["verb"]])["verb"]


# ── Serialize-time guard (SP-R1) ──────────────────────────────────────────
def assert_serializable_state(state: dict) -> None:
    """Raise TypeError if any value (recursively) is a callable. Enforces the
    'no AgentCapability.invoke in state — only agent_id refs' invariant."""
    def _check(v: Any) -> None:
        if callable(v) and not isinstance(v, type):
            raise TypeError(f"non-serializable callable in state: {v!r}")
        if isinstance(v, dict):
            for x in v.values():
                _check(x)
        elif isinstance(v, (list, tuple)):
            for x in v:
                _check(x)
    for value in state.values():
        _check(value)


def scrub_state(state: dict) -> dict:
    """Scrub every string value through lib/scrubber.py before persistence (SP-R1).
    Returns a shallow-cleaned copy; callers must assert_serializable_state() first."""
    assert_serializable_state(state)

    def _scrub(v: Any) -> Any:
        if isinstance(v, str):
            return scrub_string(v, source="spine_state")
        if isinstance(v, dict):
            return {k: _scrub(x) for k, x in v.items()}
        if isinstance(v, list):
            return [_scrub(x) for x in v]
        return v
    return {k: _scrub(v) for k, v in state.items()}


# ── The full state contract (designed complete now) ────────────────────────
class SpineState(TypedDict, total=False):
    # correlation / identity
    thread_id: str
    goal: str
    # spec contracts
    clarifications: Annotated[list, lambda a, b: (a or []) + (b or [])]
    plan: Optional[TaskGraph]
    spec_sha: Optional[str]
    spec_id: Optional[str]
    # HITL decisions
    sign_off: Optional[HitlDecision]
    ship: Optional[HitlDecision]
    # work
    tasks: Annotated[list, _merge_by_task_id]
    # exactly-once ledger + proof counter
    ledger: Annotated[list, _ledger_union]
    execution_counts: Annotated[dict, _merge_counts]
    # non-repudiation / audit
    decision_record: Annotated[list, lambda a, b: (a or []) + (b or [])]
    audit: Annotated[list, lambda a, b: (a or []) + (b or [])]
    # steering (DoD-4) — full bus deferred; reducer+arbitration live now
    steering_events: Annotated[list, _merge_steering]
    # replan (continue-as-new) — reserved now, wired in Task 7
    replan_parent: Optional[str]
    pre_decompose_checkpoint_id: Optional[str]
    # workspace (DoD-17) — content-addressed digest, never inline bytes
    workspace_ref: Optional[WorkspaceRef]
    # cost / caps
    cost_accumulator: Annotated[dict, _merge_cost]
    fix_attempts: int
    # hygiene
    scrubbed: bool
```

- [ ] **Step 4: Run — expect PASS**

Run: `uv run python -m pytest tests/unit/test_graph_state.py -q`
Expected: PASS (7 passed).

- [ ] **Step 5: Commit**

```bash
git add app/core/graph_state.py tests/unit/test_graph_state.py
git commit -m "feat(spine): full SpineState schema + reducers; ledger key-choice red-green"
```

---

## Task 2: `AbstractCheckpointer` ABC + `InMemoryCheckpointer` adapter

**Files:**
- Create: `app/core/checkpointer.py`, `app/adapters/inmemory/checkpointer.py`
- Test: `tests/unit/test_checkpointer.py`

- [ ] **Step 1: Write failing test**

Create `tests/unit/test_checkpointer.py`:

```python
"""SP-01 checkpointer provider seam (the 7th sibling ABC)."""
from __future__ import annotations

import pytest

from langgraph.checkpoint.base import BaseCheckpointSaver

from app.core.checkpointer import AbstractCheckpointer
from app.adapters.inmemory.checkpointer import InMemoryCheckpointer


def test_abstract_checkpointer_cannot_instantiate():
    with pytest.raises(TypeError):
        AbstractCheckpointer()   # ABC with abstract build_saver


async def test_inmemory_build_saver_returns_basecheckpointsaver():
    cp = InMemoryCheckpointer()
    saver = cp.build_saver()
    assert isinstance(saver, BaseCheckpointSaver)


def test_inmemory_durability_mode_is_async_for_ci():
    assert InMemoryCheckpointer().durability_mode == "async"


async def test_setup_and_aclose_are_noops():
    cp = InMemoryCheckpointer()
    await cp.setup()
    await cp.aclose()   # must not raise
```

- [ ] **Step 2: Run — expect import fail**

Run: `uv run python -m pytest tests/unit/test_checkpointer.py -q`
Expected: FAIL (`ModuleNotFoundError: app.core.checkpointer`).

- [ ] **Step 3: Implement the ABC**

Create `app/core/checkpointer.py`:

```python
"""AbstractCheckpointer — the durability injection seam (SP-01, 7th sibling ABC).

This is a PROVIDER/FACTORY, NOT a re-declaration of LangGraph's checkpointer
protocol. langgraph's BaseCheckpointSaver is already a rich ABC (get_tuple/put/
put_writes/list + async variants); re-implementing that surface would be the
over-engineering SP-22/§12 forbids. The provider yields exactly ONE writable
BaseCheckpointSaver to graph.compile(checkpointer=...) and carries the per-adapter
DURABILITY_MODE that the runner passes into astream/ainvoke (never hardcoded in
graph.py). Injected at the runner/FastAPI lifespan, never inside a node.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Literal

from langgraph.checkpoint.base import BaseCheckpointSaver

DurabilityMode = Literal["sync", "async", "exit"]


class AbstractCheckpointer(ABC):
    @property
    @abstractmethod
    def durability_mode(self) -> DurabilityMode:
        """'sync' for prod (checkpoint persisted before the irreversible super-step;
        the kill-resume exactly-once oracle is only provable here), 'async'/default
        for CI."""
        raise NotImplementedError(
            f"{self.__class__.__name__}.durability_mode must be implemented"
        )

    @abstractmethod
    def build_saver(self) -> BaseCheckpointSaver:
        """Return the single writable LangGraph checkpointer passed to compile()."""
        raise NotImplementedError(
            f"{self.__class__.__name__}.build_saver() must be implemented"
        )

    async def setup(self) -> None:
        """One-time backend setup (e.g. Postgres .setup() migration). Default no-op."""
        return None

    async def aclose(self) -> None:
        """Release backend resources (e.g. close a connection pool). Default no-op."""
        return None
```

- [ ] **Step 4: Implement the inmemory adapter**

Create `app/adapters/inmemory/checkpointer.py`:

```python
"""InMemoryCheckpointer — CI/skeleton durability provider. Zero new deps.

Wraps langgraph's InMemorySaver. One saver instance per provider (the shared
instance is what lets a fresh compile() resume a prior checkpoint — the
in-process stand-in for process death)."""
from __future__ import annotations

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver

from app.core.checkpointer import AbstractCheckpointer, DurabilityMode


class InMemoryCheckpointer(AbstractCheckpointer):
    def __init__(self) -> None:
        self._saver = InMemorySaver()

    @property
    def durability_mode(self) -> DurabilityMode:
        return "async"

    def build_saver(self) -> BaseCheckpointSaver:
        return self._saver
```

- [ ] **Step 5: Run — expect PASS**

Run: `uv run python -m pytest tests/unit/test_checkpointer.py -q`
Expected: PASS (4 passed).

- [ ] **Step 6: Commit**

```bash
git add app/core/checkpointer.py app/adapters/inmemory/checkpointer.py tests/unit/test_checkpointer.py
git commit -m "feat(spine): AbstractCheckpointer provider ABC + InMemory adapter (7th sibling ABC)"
```

---

## Task 3: `decision_record.py` — append-only non-repudiation JSONL

**Files:**
- Create: `app/core/decision_record.py`
- Test: `tests/unit/test_decision_record.py`

- [ ] **Step 1: Write failing test**

Create `tests/unit/test_decision_record.py`:

```python
"""SP-01 decision-record: plain append-only JSONL keyed by interrupt_id (§9)."""
from __future__ import annotations

import json

from app.core.decision_record import append_decision


def _decision(verb, iid):
    return {"verb": verb, "actor": "operator", "reason": "ok", "interrupt_id": iid, "ts": "z"}


def test_append_writes_one_jsonl_line_keyed_by_interrupt_id(tmp_path):
    p = tmp_path / "decision-record.jsonl"
    append_decision(_decision("APPROVE", "i1"), path=p)
    append_decision(_decision("REJECT", "i2"), path=p)
    lines = p.read_text().splitlines()
    assert len(lines) == 2
    rec0 = json.loads(lines[0])
    assert rec0["interrupt_id"] == "i1"
    assert rec0["verb"] == "APPROVE"
    assert rec0["schema_version"] == 1


def test_append_is_append_only_not_truncating(tmp_path):
    p = tmp_path / "dr.jsonl"
    for i in range(5):
        append_decision(_decision("APPROVE", f"i{i}"), path=p)
    assert len(p.read_text().splitlines()) == 5


def test_append_is_fail_open_on_bad_path():
    # a path under a non-existent, uncreatable parent must not raise (fail-open)
    assert append_decision(_decision("APPROVE", "i"), path="/proc/nonexistent/dr.jsonl") is None
```

- [ ] **Step 2: Run — expect import fail**

Run: `uv run python -m pytest tests/unit/test_decision_record.py -q`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement**

Create `app/core/decision_record.py`:

```python
"""Append-only HITL decision-record (SP-01 §9 non-repudiation).

Extends the judge_events.py append-only discipline (os.O_APPEND + fcntl.flock),
keyed by interrupt_id, fail-open. PLAIN JSONL now; the tamper-evident hash-chain
is deferred to the SP-27 monitor layer / the #192 TamperEvidentLedger follow-up.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Optional

try:
    import fcntl  # POSIX
except ImportError:  # pragma: no cover - Windows fallback
    fcntl = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
_REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PATH = _REPO_ROOT / "trajectories" / "decision-record.jsonl"


def _append_line(path: Path, line: str) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        if fcntl is not None:
            fcntl.flock(fd, fcntl.LOCK_EX)
        os.write(fd, (line + "\n").encode("utf-8"))
    finally:
        if fcntl is not None:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                pass
        os.close(fd)


def append_decision(
    decision: dict[str, Any],
    *,
    path: Optional[Path | str] = None,
    enabled: Optional[bool] = None,
) -> Optional[Path]:
    """Append one HITL decision as a JSONL record keyed by interrupt_id.
    Fail-open: returns the written path, or None on any failure / disabled."""
    if enabled is False:
        return None
    target = Path(path) if path is not None else DEFAULT_PATH
    try:
        record = {
            "schema_version": SCHEMA_VERSION,
            "interrupt_id": decision["interrupt_id"],
            "verb": decision["verb"],
            "actor": decision.get("actor", "<unknown>"),
            "reason": decision.get("reason", ""),
            "ts": decision.get("ts", ""),
        }
        target.parent.mkdir(parents=True, exist_ok=True)
        _append_line(target, json.dumps(record, ensure_ascii=False, separators=(",", ":")))
        return target
    except Exception as exc:  # noqa: BLE001 - fail-open audit append
        logger.warning("decision_record append failed (fail-open): %s", exc)
        return None
```

- [ ] **Step 4: Run — expect PASS**

Run: `uv run python -m pytest tests/unit/test_decision_record.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add app/core/decision_record.py tests/unit/test_decision_record.py
git commit -m "feat(spine): append-only decision-record JSONL keyed by interrupt_id"
```

---

## Task 4: `graph.py` skeleton happy-path + `spine_runner.py`

**Files:**
- Create: `app/core/graph.py`, `app/core/spine_runner.py`
- Test: `tests/unit/test_graph_spine.py` (append)

- [ ] **Step 1: Write the failing skeleton acceptance test**

Append to `tests/unit/test_graph_spine.py`:

```python
import pytest

from app.core.schemas import AgentCapability, ExecutionResult, TaskStatus
from app.core.spine_runner import SpineRunner
from app.adapters.inmemory.checkpointer import InMemoryCheckpointer
from app.core import graph_state as gs


def _stub_capability(status=TaskStatus.COMPLETED):
    async def _invoke(request, **_):
        return ExecutionResult(task_id=request.task_id, status=status, cost_usd=0.01)
    return AgentCapability(
        agent_id="stub-agent", version="1", phase="draft",
        description="skeleton stub capability", invoke=_invoke,
    )


async def test_skeleton_happy_path_two_interrupts_seal_execute_ship():
    runner = SpineRunner(InMemoryCheckpointer(), capability=_stub_capability())
    tid = "goal-happy"

    r1 = await runner.start(thread_id=tid, goal="ship a hello-world endpoint")
    assert "__interrupt__" in r1
    signoff = r1["__interrupt__"][0]
    assert signoff.id and "sign_off" in str(signoff.value).lower() or signoff.value
    assert runner.get_state(tid).next == ("sign_off",)   # nothing built before approval

    r2 = await runner.resume(thread_id=tid, interrupt_id=signoff.id,
                             decision={"verb": "APPROVE", "actor": "op", "reason": "lgtm"})
    assert "__interrupt__" in r2                          # paused again at ship_gate
    ship = r2["__interrupt__"][0]
    assert ship.id != signoff.id
    assert runner.get_state(tid).next == ("ship_gate",)
    assert r2.get("spec_sha")                             # seal_spec ran (sha-pinned)
    assert any(t["status"] == "completed" for t in r2.get("tasks", []))  # execute ran

    r3 = await runner.resume(thread_id=tid, interrupt_id=ship.id,
                             decision={"verb": "APPROVE", "actor": "op", "reason": "ship it"})
    assert "__interrupt__" not in r3                      # reached END
    assert runner.get_state(tid).next == ()
    # exactly-once: the ship effect is witnessed once in the ledger
    ship_receipts = [r for r in r3["ledger"] if r["action_kind"] == "ship"]
    assert len(ship_receipts) == 1
    assert r3["execution_counts"][gs._key_str(gs.ledger_key(ship_receipts[0]))] == 1
    # two HITL decisions recorded (sign_off + ship)
    verbs = [d["verb"] for d in r3["decision_record"]]
    assert verbs.count("APPROVE") == 2
```

> Note: this test references `gs._key_str` — a small helper that stringifies the ledger tuple for the `execution_counts` dict key. Add it in Step 3.

- [ ] **Step 2: Run — expect import fail**

Run: `uv run python -m pytest "tests/unit/test_graph_spine.py::test_skeleton_happy_path_two_interrupts_seal_execute_ship" -q`
Expected: FAIL (`ModuleNotFoundError: app.core.spine_runner`).

- [ ] **Step 3: Add the `_key_str` helper to `graph_state.py`**

Add to `app/core/graph_state.py` (next to `ledger_key`):

```python
def _key_str(key: tuple) -> str:
    """Stringify a ledger key for use as an execution_counts dict key."""
    return "|".join(str(p) for p in key)
```

- [ ] **Step 4: Implement `app/core/graph.py`**

Create `app/core/graph.py`:

```python
"""SP-01 LangGraph spine (walking skeleton).

Topology (interrupt-split — never co-locate interrupt() with a side-effect):
    goal_intake -> sign_off[interrupt] -> seal_spec -> execute
                -> ship_gate[interrupt] -> ship_effect -> END
Control flow is ALWAYS code-decided via conditional edges over deterministic
signals (the HITL verb). No top-level LLM router/supervisor. See graph_state.py
for the exactly-once doctrine. execute wraps app.core.orchestrator.execute as a
black-box call-through leaf (no new orchestrator class)."""
from __future__ import annotations

import time
import uuid
from typing import Optional

from langgraph.graph import StateGraph, START, END
from langgraph.types import interrupt, Command  # noqa: F401  (Command re-exported for runner)

from app.core import graph_state as gs
from app.core.graph_state import SpineState
from app.core.decision_record import append_decision
from app.core.orchestrator import execute as orchestrate
from app.core.schemas import AgentCapability, ExecutionResult, TaskRequest, TaskStatus
from lib.anchors.spec_store import SpecStore, compute_spec_sha
from lib.anchors.task_spec import Scope, TaskSpec

# ── node helpers ───────────────────────────────────────────────────────────
def _ledger_has(state: SpineState, key: tuple) -> bool:
    return any(gs.ledger_key(r) == key for r in state.get("ledger", []))


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _record_decision(state: SpineState, gate: str, decision: dict, interrupt_id: str) -> dict:
    """Build a HitlDecision, append it to the durable JSONL, return the state delta."""
    hitl = {
        "verb": decision.get("verb", "APPROVE"),
        "actor": decision.get("actor", "<unknown>"),
        "reason": decision.get("reason", ""),
        "interrupt_id": interrupt_id,
        "ts": _now(),
    }
    append_decision(hitl)  # durable non-repudiation trail (fail-open)
    return {gate: hitl, "decision_record": [hitl]}


# ── nodes ──────────────────────────────────────────────────────────────────
def _build_nodes(capability: AgentCapability):

    async def goal_intake(state: SpineState, config) -> dict:
        tid = config["configurable"]["thread_id"]
        return {
            "thread_id": tid,
            "audit": [f"goal_intake thread={tid}"],
            "fix_attempts": 0,
            "scrubbed": False,
        }

    async def sign_off(state: SpineState, config) -> dict:
        """PURE interrupt node — pauses only. No side-effect here."""
        decision = interrupt({"gate": "sign_off", "question": "Approve PRD?",
                              "goal": state["goal"]})
        intr_id = _outstanding_interrupt_id(config)
        return _record_decision(state, "sign_off", decision, intr_id)

    async def seal_spec(state: SpineState, config) -> dict:
        """POST-resume effect: sha-pin the TaskSpec (ledger-guarded, exactly-once)."""
        tid = state["thread_id"]
        ptid = config["configurable"]["__pregel_task_id"]
        key = (tid, ptid, "seal_spec")
        if _ledger_has(state, key):
            return {"audit": ["seal_spec SKIP (receipt present)"]}
        spec = TaskSpec(
            title=state["goal"][:120] or "goal",
            intent=state["goal"],
            acceptance_criteria=["operator goal satisfied"],
            scope=Scope(in_scope=["the requested change"], out_of_scope=["unrelated work"]),
            success_metrics=["acceptance green on the merged commit"],
            created_by=0,
        )
        store = SpecStore(SpecStore_root())
        sealed = store.save(spec.model_copy(update={"status": "locked"}))
        receipt = _receipt(tid, ptid, "seal_spec", "seal_spec")
        return {
            "spec_sha": sealed.spec_sha,
            "spec_id": str(sealed.spec_id),
            "ledger": [receipt],
            "execution_counts": {gs._key_str(key): 1},
            "audit": [f"seal_spec sha={sealed.spec_sha[:12]}"],
        }

    async def execute(state: SpineState, config) -> dict:
        """Black-box call-through leaf to orchestrator.execute (no new class)."""
        req = TaskRequest(task_id=state["thread_id"], phase="draft",
                          summary=state["goal"], deadline_s=60.0)
        result: ExecutionResult = await orchestrate(req, capability)
        return {
            "tasks": [result.model_dump(mode="json")],
            "cost_accumulator": {f"{state['thread_id']}|execute": result.cost_usd},
            "audit": [f"execute status={result.status.value}"],
        }

    async def ship_gate(state: SpineState, config) -> dict:
        """PURE interrupt node — prod-approval. No side-effect here."""
        decision = interrupt({"gate": "ship", "question": "Ship to prod?",
                              "spec_sha": state.get("spec_sha")})
        intr_id = _outstanding_interrupt_id(config)
        return _record_decision(state, "ship", decision, intr_id)

    async def ship_effect(state: SpineState, config) -> dict:
        """POST-resume effect: the irreversible ship (ledger-guarded, exactly-once).
        Skeleton effect = mark shipped; real PR/commit/merge lands in a later layer."""
        tid = state["thread_id"]
        ptid = config["configurable"]["__pregel_task_id"]
        key = (tid, ptid, "ship")
        if _ledger_has(state, key):
            return {"audit": ["ship_effect SKIP (receipt present)"]}
        receipt = _receipt(tid, ptid, "ship", "ship_effect")
        return {
            "ledger": [receipt],
            "execution_counts": {gs._key_str(key): 1},
            "workspace_ref": state.get("workspace_ref")
            or {"kind": "branch", "ref": f"agent/{tid}", "digest": _digest(state)},
            "audit": ["ship_effect shipped"],
        }

    return {
        "goal_intake": goal_intake, "sign_off": sign_off, "seal_spec": seal_spec,
        "execute": execute, "ship_gate": ship_gate, "ship_effect": ship_effect,
    }


# ── conditional routing (code-decided, deterministic on the HITL verb) ──────
def _route_after_sign_off(state: SpineState) -> str:
    verb = (state.get("sign_off") or {}).get("verb", "APPROVE")
    return {"APPROVE": "seal_spec", "REJECT": "__halt__", "REPLAN": "__replan__"}.get(verb, "__halt__")


def _route_after_ship_gate(state: SpineState) -> str:
    verb = (state.get("ship") or {}).get("verb", "APPROVE")
    return {"APPROVE": "ship_effect", "REJECT": "__halt__", "REPLAN": "__replan__"}.get(verb, "__halt__")


async def halt(state: SpineState, config) -> dict:
    return {"audit": ["HALTED by operator"]}


async def replan(state: SpineState, config) -> dict:
    """REPLAN = continue-as-new. Mark the fork; the runner spawns a new thread_id.
    The old thread + its sealed spec are left IMMUTABLE (the audit guarantee)."""
    return {
        "replan_parent": state["thread_id"],
        "pre_decompose_checkpoint_id": config["configurable"].get("checkpoint_id"),
        "audit": ["REPLAN requested (continue-as-new)"],
    }


# ── small utilities ────────────────────────────────────────────────────────
def _receipt(tid: str, ptid: str, kind: str, node_label: str) -> dict:
    return {"thread_id": tid, "pregel_task_id": ptid, "action_kind": kind,
            "node_label": node_label, "super_step_label": 0, "ts": _now()}


def _digest(state: SpineState) -> str:
    import hashlib
    return hashlib.sha256((state.get("goal", "") + state.get("spec_sha", "")).encode()).hexdigest()


def _outstanding_interrupt_id(config) -> str:
    """Best-effort interrupt-id for the decision record. The id-map resume binds
    the value; this label correlates the durable decision to that interrupt."""
    return config["configurable"].get("__pregel_task_id", "<unknown>")


def SpecStore_root():
    import os
    from pathlib import Path
    root = os.environ.get("SPINE_SPEC_STORE", "")
    return Path(root) if root else (gs.__spec_store_default())  # see graph_state helper below


# ── graph assembly ─────────────────────────────────────────────────────────
def build_spine(saver, *, capability: Optional[AgentCapability] = None):
    """Compile the spine StateGraph with the single writable checkpointer.
    `capability` is injected here (NOT in state — no callables in state); defaults
    to a local stub for the skeleton."""
    capability = capability or _default_capability()
    nodes = _build_nodes(capability)
    g = StateGraph(SpineState)
    for name, fn in nodes.items():
        g.add_node(name, fn)
    g.add_node("__halt__", halt)
    g.add_node("__replan__", replan)

    g.add_edge(START, "goal_intake")
    g.add_edge("goal_intake", "sign_off")
    g.add_conditional_edges("sign_off", _route_after_sign_off,
                            {"seal_spec": "seal_spec", "__halt__": "__halt__", "__replan__": "__replan__"})
    g.add_edge("seal_spec", "execute")
    g.add_edge("execute", "ship_gate")
    g.add_conditional_edges("ship_gate", _route_after_ship_gate,
                            {"ship_effect": "ship_effect", "__halt__": "__halt__", "__replan__": "__replan__"})
    g.add_edge("ship_effect", END)
    g.add_edge("__halt__", END)
    g.add_edge("__replan__", END)
    return g.compile(checkpointer=saver)


def _default_capability() -> AgentCapability:
    async def _invoke(request: TaskRequest, **_) -> ExecutionResult:
        return ExecutionResult(task_id=request.task_id, status=TaskStatus.COMPLETED)
    return AgentCapability(agent_id="spine-local", version="1", phase="draft",
                           description="skeleton local stub", invoke=_invoke)
```

> The `SpecStore_root()` indirection above keeps the spec-store path overridable in tests via `SPINE_SPEC_STORE`. Add the tiny default helper in the next step.

- [ ] **Step 5: Add the spec-store default helper to `graph_state.py`**

Add to `app/core/graph_state.py`:

```python
def __spec_store_default():
    """Default SpecStore root (under the repo's data dir; override with SPINE_SPEC_STORE)."""
    import os
    from pathlib import Path
    base = os.environ.get("SPINE_DATA_DIR", "/tmp/spine-specs")
    p = Path(base)
    p.mkdir(parents=True, exist_ok=True)
    return p
```

- [ ] **Step 6: Implement `app/core/spine_runner.py`**

Create `app/core/spine_runner.py`:

```python
"""SpineRunner — the checkpointer/durability injection seam.

A future FastAPI lifespan constructs `SpineRunner(InMemoryCheckpointer())` (or the
prod provider) ONCE and shares it. graph.py stays env-agnostic: durability comes
from the provider, never hardcoded. Mirrors OrchestratorConfig-style injection."""
from __future__ import annotations

from typing import Any, Optional

from langgraph.types import Command

from app.core.checkpointer import AbstractCheckpointer, DurabilityMode
from app.core.graph import build_spine
from app.core.schemas import AgentCapability


def _initial_state(thread_id: str, goal: str) -> dict:
    return {"thread_id": thread_id, "goal": goal, "clarifications": [], "tasks": [],
            "ledger": [], "execution_counts": {}, "decision_record": [], "audit": [],
            "steering_events": [], "cost_accumulator": {}, "fix_attempts": 0,
            "scrubbed": False}


class SpineRunner:
    def __init__(self, checkpointer: AbstractCheckpointer, *,
                 capability: Optional[AgentCapability] = None) -> None:
        self._provider = checkpointer
        self._saver = checkpointer.build_saver()
        self._capability = capability
        self._app = build_spine(self._saver, capability=capability)

    @property
    def durability(self) -> DurabilityMode:
        return self._provider.durability_mode

    def _cfg(self, thread_id: str) -> dict:
        return {"configurable": {"thread_id": thread_id}}

    async def start(self, *, thread_id: str, goal: str,
                    durability: Optional[DurabilityMode] = None) -> dict:
        return await self._app.ainvoke(
            _initial_state(thread_id, goal), self._cfg(thread_id),
            durability=durability or self.durability)

    async def resume(self, *, thread_id: str, interrupt_id: str, decision: Any,
                     durability: Optional[DurabilityMode] = None) -> dict:
        return await self._app.ainvoke(
            Command(resume={interrupt_id: decision}), self._cfg(thread_id),
            durability=durability or self.durability)

    def get_state(self, thread_id: str):
        return self._app.get_state(self._cfg(thread_id))
```

- [ ] **Step 7: Run the skeleton acceptance test — iterate to green**

Run: `uv run python -m pytest "tests/unit/test_graph_spine.py::test_skeleton_happy_path_two_interrupts_seal_execute_ship" -q`
Expected: PASS. (If the interrupt `value` assertion is brittle, assert on `signoff.value["gate"] == "sign_off"` instead.)

- [ ] **Step 8: Run the full unit suite for regressions**

Run: `uv run python -m pytest tests/unit/test_graph_spine.py tests/unit/test_graph_state.py tests/unit/test_checkpointer.py tests/unit/test_decision_record.py -q`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add app/core/graph.py app/core/spine_runner.py app/core/graph_state.py tests/unit/test_graph_spine.py
git commit -m "feat(spine): 6-node interrupt-split skeleton + SpineRunner injection seam"
```

---

## Task 5: Exactly-once oracle (DoD-1) — kill+resume + side-effect-before-interrupt regression

**Files:**
- Test: `tests/unit/test_graph_spine.py` (append)

- [ ] **Step 1: Write the exactly-once + regression tests**

Append to `tests/unit/test_graph_spine.py`:

```python
async def test_ship_effect_exactly_once_under_crash_resume():
    """DoD-1: drive to ship_gate, simulate process death (fresh compile, SAME saver),
    resume APPROVE; the ledger-guarded ship effect executes exactly once (count==1)."""
    cp = InMemoryCheckpointer()
    tid = "goal-eo"
    runner = SpineRunner(cp, capability=_stub_capability())
    r1 = await runner.start(thread_id=tid, goal="ship X", durability="sync")
    signoff = r1["__interrupt__"][0]
    r2 = await runner.resume(thread_id=tid, interrupt_id=signoff.id,
                             decision={"verb": "APPROVE", "actor": "op", "reason": "y"},
                             durability="sync")
    ship = r2["__interrupt__"][0]

    # simulate crash: a brand-new runner over the SAME checkpointer provider
    runner2 = SpineRunner(cp, capability=_stub_capability())
    r3 = await runner2.resume(thread_id=tid, interrupt_id=ship.id,
                              decision={"verb": "APPROVE", "actor": "op", "reason": "y"},
                              durability="sync")
    ship_receipts = [r for r in r3["ledger"] if r["action_kind"] == "ship"]
    assert len(ship_receipts) == 1
    counts = list(r3["execution_counts"].values())
    assert counts and all(c == 1 for c in counts)   # every effect witnessed exactly once


async def test_side_effect_before_interrupt_double_acts_RED():
    """Non-vacuous proof of the failure mode the node-split prevents: an effect placed
    BEFORE interrupt() in the SAME node body re-runs on resume -> count==2."""
    import operator
    from typing import Annotated
    from typing_extensions import TypedDict
    from langgraph.graph import StateGraph, START, END
    from langgraph.types import interrupt, Command
    from langgraph.checkpoint.memory import InMemorySaver

    class S(TypedDict):
        counts: Annotated[dict, gs._merge_counts]
        decision: str

    async def bad_gate(state, config):
        # WRONG: side-effect before interrupt() -> at-least-once on resume
        out = {"counts": {"ship": 1}}
        d = interrupt({"q": "ship?"})
        out["decision"] = d
        return out

    g = StateGraph(S)
    g.add_node("bad_gate", bad_gate)
    g.add_edge(START, "bad_gate")
    g.add_edge("bad_gate", END)
    saver = InMemorySaver()
    app = g.compile(checkpointer=saver)
    cfg = {"configurable": {"thread_id": "red"}}
    r1 = await app.ainvoke({"counts": {}, "decision": ""}, cfg, durability="sync")
    intr = r1["__interrupt__"][0]
    r2 = await app.ainvoke(Command(resume={intr.id: "APPROVE"}), cfg, durability="sync")
    assert r2["counts"]["ship"] == 2   # RED: the effect double-acted (this is the anti-pattern)


async def test_post_resume_node_split_is_exactly_once_GREEN():
    """GREEN counterpart: the same effect in a DISTINCT post-resume node runs once."""
    cp = InMemoryCheckpointer()
    runner = SpineRunner(cp, capability=_stub_capability())
    tid = "green"
    r1 = await runner.start(thread_id=tid, goal="g", durability="sync")
    so = r1["__interrupt__"][0]
    r2 = await runner.resume(thread_id=tid, interrupt_id=so.id,
                             decision={"verb": "APPROVE", "actor": "op", "reason": "y"},
                             durability="sync")
    sh = r2["__interrupt__"][0]
    r3 = await runner.resume(thread_id=tid, interrupt_id=sh.id,
                             decision={"verb": "APPROVE", "actor": "op", "reason": "y"},
                             durability="sync")
    assert all(c == 1 for c in r3["execution_counts"].values())
```

- [ ] **Step 2: Run — expect PASS (the implementation from Task 4 already ledger-guards effects)**

Run: `uv run python -m pytest tests/unit/test_graph_spine.py -q -k "exactly_once or double_acts or node_split"`
Expected: PASS (3 passed). The RED test asserts the *failure mode exists* (count==2 for the deliberately-wrong node); the GREEN tests prove the real spine is count==1.

- [ ] **Step 3: Commit**

```bash
git add tests/unit/test_graph_spine.py
git commit -m "test(spine): DoD-1 exactly-once oracle + side-effect-before-interrupt red-green"
```

---

## Task 6: REJECT + REPLAN flows

**Files:**
- Test: `tests/unit/test_graph_spine.py` (append)
- Possibly modify: `app/core/spine_runner.py` (add `replan()` helper for the new thread_id)

- [ ] **Step 1: Write REJECT + REPLAN tests**

Append to `tests/unit/test_graph_spine.py`:

```python
async def test_reject_at_sign_off_halts_nothing_built():
    runner = SpineRunner(InMemoryCheckpointer(), capability=_stub_capability())
    tid = "goal-reject"
    r1 = await runner.start(thread_id=tid, goal="do X")
    so = r1["__interrupt__"][0]
    r2 = await runner.resume(thread_id=tid, interrupt_id=so.id,
                             decision={"verb": "REJECT", "actor": "op", "reason": "no"})
    assert "__interrupt__" not in r2                      # no ship gate
    assert runner.get_state(tid).next == ()              # halted at END
    assert not r2.get("spec_sha")                         # seal_spec never ran
    assert not [r for r in r2.get("ledger", []) if r["action_kind"] == "ship"]
    assert any("HALTED" in a for a in r2["audit"])
    assert r2["decision_record"][-1]["verb"] == "REJECT"  # audited


async def test_replan_at_sign_off_forks_new_thread_old_immutable():
    cp = InMemoryCheckpointer()
    runner = SpineRunner(cp, capability=_stub_capability())
    old = "goal-replan"
    r1 = await runner.start(thread_id=old, goal="do X")
    so = r1["__interrupt__"][0]
    r2 = await runner.resume(thread_id=old, interrupt_id=so.id,
                             decision={"verb": "REPLAN", "actor": "op", "reason": "redo"})
    assert r2["replan_parent"] == old
    assert runner.get_state(old).next == ()              # old thread terminal
    # old thread's sealed spec is untouched (never sealed on REPLAN)
    assert not r2.get("spec_sha")
    # a fresh thread_id can start independently (continue-as-new) on the same saver
    new = "goal-replan-2"
    r3 = await runner.start(thread_id=new, goal="do X (replanned)")
    assert "__interrupt__" in r3
    assert runner.get_state(old).values["thread_id"] == old   # old state preserved/immutable
```

- [ ] **Step 2: Run — expect PASS (routing from Task 4 already handles REJECT/REPLAN)**

Run: `uv run python -m pytest tests/unit/test_graph_spine.py -q -k "reject or replan"`
Expected: PASS (2 passed).

- [ ] **Step 3: Commit**

```bash
git add tests/unit/test_graph_spine.py
git commit -m "test(spine): REJECT halts (nothing built); REPLAN forks new thread, old immutable"
```

---

## Task 7: Approval-receipt invariant + TIMEOUT→REJECT

**Files:**
- Modify: `app/core/graph.py` (sign_off/ship_gate skip-interrupt-if-receipt; TIMEOUT mapping)
- Test: `tests/unit/test_graph_spine.py` (append)

- [ ] **Step 1: Write the approval-receipt + TIMEOUT tests**

Append to `tests/unit/test_graph_spine.py`:

```python
async def test_crash_after_approve_does_not_reprompt_sign_off():
    """Approval-receipt invariant: once sign_off is APPROVE'd and recorded, a resume
    after a crash must NOT surface the sign_off interrupt again."""
    cp = InMemoryCheckpointer()
    runner = SpineRunner(cp, capability=_stub_capability())
    tid = "goal-receipt"
    r1 = await runner.start(thread_id=tid, goal="g", durability="sync")
    so = r1["__interrupt__"][0]
    r2 = await runner.resume(thread_id=tid, interrupt_id=so.id,
                             decision={"verb": "APPROVE", "actor": "op", "reason": "y"},
                             durability="sync")
    # after approve we are at ship_gate, NOT back at sign_off
    assert runner.get_state(tid).next == ("ship_gate",)
    # sign_off decision is durably present exactly once
    assert sum(1 for d in r2["decision_record"] if d["interrupt_id"] == r2["sign_off"]["interrupt_id"]) == 1


async def test_timeout_maps_to_safe_default_reject():
    runner = SpineRunner(InMemoryCheckpointer(), capability=_stub_capability())
    tid = "goal-timeout"
    r1 = await runner.start(thread_id=tid, goal="g")
    so = r1["__interrupt__"][0]
    r2 = await runner.resume(thread_id=tid, interrupt_id=so.id,
                             decision={"verb": "TIMEOUT", "actor": "system", "reason": "no response"})
    assert runner.get_state(tid).next == ()              # halted (TIMEOUT -> safe-default reject)
    assert not r2.get("spec_sha")
```

- [ ] **Step 2: Run — expect TIMEOUT test fail (router doesn't map TIMEOUT yet)**

Run: `uv run python -m pytest tests/unit/test_graph_spine.py -q -k "timeout or reprompt"`
Expected: `test_timeout_maps_to_safe_default_reject` FAILS (TIMEOUT falls through to `__halt__` only if mapped). The reprompt test should PASS already (post-resume node-split means sign_off is not re-entered).

- [ ] **Step 3: Map TIMEOUT→REJECT in the routers**

Edit `app/core/graph.py` `_route_after_sign_off` and `_route_after_ship_gate` — change the mapping default so `TIMEOUT` routes to `__halt__`:

```python
def _route_after_sign_off(state: SpineState) -> str:
    verb = (state.get("sign_off") or {}).get("verb", "APPROVE")
    return {"APPROVE": "seal_spec", "REJECT": "__halt__",
            "REPLAN": "__replan__", "TIMEOUT": "__halt__"}.get(verb, "__halt__")


def _route_after_ship_gate(state: SpineState) -> str:
    verb = (state.get("ship") or {}).get("verb", "APPROVE")
    return {"APPROVE": "ship_effect", "REJECT": "__halt__",
            "REPLAN": "__replan__", "TIMEOUT": "__halt__"}.get(verb, "__halt__")
```

- [ ] **Step 4: Run — expect PASS**

Run: `uv run python -m pytest tests/unit/test_graph_spine.py -q -k "timeout or reprompt"`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add app/core/graph.py tests/unit/test_graph_spine.py
git commit -m "feat(spine): approval-receipt invariant (no re-prompt) + TIMEOUT->safe-default REJECT"
```

---

## Task 8: DoD-4 (channel arbitration survives resume) + DoD-17 (workspace rehydrate)

**Files:**
- Test: `tests/unit/test_graph_spine.py` (append)

- [ ] **Step 1: Write DoD-4 + DoD-17 tests**

Append to `tests/unit/test_graph_spine.py`:

```python
async def test_dod4_steering_dedup_survives_resume_and_reject_wins():
    """DoD-4: inbound steering normalizes to SteeringEvents deduped on (channel,
    origin_id), survives kill+resume at-most-once, and REJECT beats APPROVE."""
    cp = InMemoryCheckpointer()
    runner = SpineRunner(cp, capability=_stub_capability())
    tid = "goal-steer"
    r1 = await runner.start(thread_id=tid, goal="g", durability="sync")
    so = r1["__interrupt__"][0]
    iid = so.id
    # Inject two conflicting steering events (and a duplicate) via the state channel.
    # In the skeleton, the runner threads steering_events through the resume Command update.
    events = [
        {"channel": "telegram", "origin_id": "m1", "verb": "APPROVE", "interrupt_id": iid, "ts": "1"},
        {"channel": "telegram", "origin_id": "m1", "verb": "APPROVE", "interrupt_id": iid, "ts": "1"},  # dup
        {"channel": "board", "origin_id": "c9", "verb": "REJECT", "interrupt_id": iid, "ts": "2"},
    ]
    from langgraph.types import Command
    r2 = await runner._app.ainvoke(
        Command(resume={iid: {"verb": gs.arbitrate(events, iid), "actor": "bus", "reason": "arb"}},
                update={"steering_events": events}),
        runner._cfg(tid), durability="sync")
    # dedup: telegram/m1 survives once, board/c9 once
    keys = {(e["channel"], e["origin_id"]) for e in r2["steering_events"]}
    assert keys == {("telegram", "m1"), ("board", "c9")}
    # arbitration: reject beat approve -> the graph halted (no ship gate, no seal)
    assert gs.arbitrate(r2["steering_events"], iid) == "REJECT"
    assert runner.get_state(tid).next == ()
    assert not r2.get("spec_sha")


async def test_dod17_workspace_ref_byte_equal_across_crash_resume():
    """DoD-17: the workspace_ref digest (the FS resume piece) is byte-equal after a
    crash+resume — the second, independent resume-state proof distinct from DoD-1."""
    cp = InMemoryCheckpointer()
    runner = SpineRunner(cp, capability=_stub_capability())
    tid = "goal-ws"
    r1 = await runner.start(thread_id=tid, goal="g", durability="sync")
    so = r1["__interrupt__"][0]
    # seed a workspace_ref before the ship via the resume update (skeleton stand-in for SP-R7)
    from langgraph.types import Command
    digest = "deadbeef" * 8
    r2 = await runner._app.ainvoke(
        Command(resume={so.id: {"verb": "APPROVE", "actor": "op", "reason": "y"}},
                update={"workspace_ref": {"kind": "branch", "ref": f"agent/{tid}", "digest": digest}}),
        runner._cfg(tid), durability="sync")
    pre = runner.get_state(tid).values["workspace_ref"]["digest"]
    sh = r2["__interrupt__"][0]
    runner2 = SpineRunner(cp, capability=_stub_capability())   # crash + restart
    r3 = await runner2.resume(thread_id=tid, interrupt_id=sh.id,
                              decision={"verb": "APPROVE", "actor": "op", "reason": "y"},
                              durability="sync")
    post = r3["workspace_ref"]["digest"]
    assert pre == digest and post == digest   # byte-equal rehydrate across the crash
```

- [ ] **Step 2: Run — expect PASS (reducers from Task 1 + the graph carry both fields)**

Run: `uv run python -m pytest tests/unit/test_graph_spine.py -q -k "dod4 or dod17"`
Expected: PASS (2 passed). If `ship_effect` overwrites a seeded `workspace_ref`, confirm its `state.get("workspace_ref") or {...}` fallback preserves the seeded digest (it does — see Task 4 `ship_effect`).

- [ ] **Step 3: Commit**

```bash
git add tests/unit/test_graph_spine.py
git commit -m "test(spine): DoD-4 steering dedup+arbitration survives resume; DoD-17 workspace byte-equal rehydrate"
```

---

## Task 9: Acceptance oracle `audit/acceptance/SP-01.yaml` + SHA256SUMS

**Files:**
- Create: `audit/acceptance/SP-01.yaml`
- Modify: `audit/acceptance/SHA256SUMS` (regenerated)

- [ ] **Step 1: Confirm the full unit suite is green (the assertions wrap it)**

Run: `uv run python -m pytest tests/unit/test_graph_spine.py tests/unit/test_graph_state.py tests/unit/test_checkpointer.py tests/unit/test_decision_record.py -q`
Expected: PASS (all).

- [ ] **Step 2: Write `audit/acceptance/SP-01.yaml`**

Create `audit/acceptance/SP-01.yaml`:

```yaml
task: SP-01
title: "LangGraph spine walking-skeleton — durable interrupt/resume + exactly-once ledger"
driver: P
phase: gate-1
run_from: repo_root
assertions:
  - id: sp01-schema-and-key-choice
    description: >
      Full SpineState schema + reducers load; the naive (thread_id,node_id,super_step)
      ledger key COLLIDES under simulated Send fan-out while the content key
      (thread_id,__pregel_task_id,action_kind) stays distinct (red-green on the key).
    cmd: |
      uv run python -m pytest tests/unit/test_graph_state.py -q
    expect_exit: 0
  - id: sp01-checkpointer-provider
    description: >
      AbstractCheckpointer ABC cannot instantiate; InMemoryCheckpointer.build_saver()
      yields a langgraph BaseCheckpointSaver; durability_mode == async (CI).
    cmd: |
      uv run python -m pytest tests/unit/test_checkpointer.py -q
    expect_exit: 0
  - id: sp01-decision-record-append-only
    description: append-only decision-record JSONL keyed by interrupt_id, fail-open.
    cmd: |
      uv run python -m pytest tests/unit/test_decision_record.py -q
    expect_exit: 0
  - id: sp01-skeleton-two-interrupts
    description: >
      goal_intake -> sign_off[interrupt] -> seal_spec -> execute -> ship_gate[interrupt]
      -> ship_effect -> END; two interrupts surface WITH ids; checkpoint persists+restores;
      nothing builds before sign-off APPROVE.
    cmd: |
      uv run python -m pytest "tests/unit/test_graph_spine.py::test_skeleton_happy_path_two_interrupts_seal_execute_ship" -q
    expect_exit: 0
  - id: sp01-dod1-exactly-once
    description: >
      DoD-1: kill mid-graph (fresh compile, same saver), resume; the ledger-guarded
      ship effect executes exactly once (count==1) under sync durability.
    cmd: |
      uv run python -m pytest "tests/unit/test_graph_spine.py::test_ship_effect_exactly_once_under_crash_resume" "tests/unit/test_graph_spine.py::test_post_resume_node_split_is_exactly_once_GREEN" -q
    expect_exit: 0
  - id: sp01-side-effect-before-interrupt-red
    description: >
      Non-vacuous: an effect placed BEFORE interrupt() in the same node body
      double-acts on resume (count==2) — the anti-pattern the node-split prevents.
    cmd: |
      uv run python -m pytest "tests/unit/test_graph_spine.py::test_side_effect_before_interrupt_double_acts_RED" -q
    expect_exit: 0
  - id: sp01-reject-and-replan
    description: >
      REJECT halts with nothing built (no seal, no ship receipt), audited; REPLAN
      forks a new thread_id with replan_parent set and leaves the old thread immutable.
    cmd: |
      uv run python -m pytest tests/unit/test_graph_spine.py -q -k "reject or replan"
    expect_exit: 0
  - id: sp01-approval-receipt-and-timeout
    description: >
      Crash-after-approve does not re-prompt sign_off (approval-receipt invariant);
      TIMEOUT maps to safe-default REJECT for the irreversible gates.
    cmd: |
      uv run python -m pytest tests/unit/test_graph_spine.py -q -k "reprompt or timeout"
    expect_exit: 0
  - id: sp01-dod4-dod17
    description: >
      DoD-4: steering events dedup on (channel,origin_id), survive resume, REJECT beats
      APPROVE. DoD-17: workspace_ref digest is byte-equal across a crash+resume.
    cmd: |
      uv run python -m pytest tests/unit/test_graph_spine.py -q -k "dod4 or dod17"
    expect_exit: 0
proof:
  ci_workflow: .github/workflows/ci.yml
  artifact: >
    SP-01 walking-skeleton red-greens: full SpineState schema + ledger key-choice
    collision, checkpointer provider ABC, decision-record JSONL, 2-interrupt skeleton,
    DoD-1 exactly-once (kill+resume), side-effect-before-interrupt regression,
    REJECT/REPLAN, approval-receipt + TIMEOUT, DoD-4 arbitration, DoD-17 rehydrate.
```

- [ ] **Step 3: Verify the YAML's own assertions run green via the repo's runner**

Run: `uv run python scripts/ci/run_acceptance.py audit/acceptance/SP-01.yaml`
Expected: every assertion `[PASS]`, exit 0.

- [ ] **Step 4: Regenerate the sha-pinned manifest**

Run: `uv run python scripts/ci/acceptance_frozen.py --write`
Then verify integrity: `uv run python scripts/ci/acceptance_frozen.py`
Expected: exit 0 (manifest now includes the `SP-01.yaml` line).

- [ ] **Step 5: Set immutability (chmod 444) per the C8 discipline**

```bash
chmod 444 audit/acceptance/SP-01.yaml audit/acceptance/SHA256SUMS
```

- [ ] **Step 6: Commit**

```bash
git add audit/acceptance/SP-01.yaml audit/acceptance/SHA256SUMS
git commit -m "feat(spine): sha-pinned audit/acceptance/SP-01.yaml registering the skeleton oracles"
```

---

## Task 10: Full-suite regression + branch finalization

**Files:** none (verification + handoff)

- [ ] **Step 1: Run the whole unit + CI-gate suite (no skips)**

Run: `uv run python -m pytest tests/unit tests/ci -q -p no:randomly`
Expected: PASS, zero skips introduced by SP-01.

- [ ] **Step 2: Confirm the no-skip + import-hygiene gates are satisfied**

Run:
```bash
uv run python -m pytest tests/unit/test_graph_spine.py --junitxml=/tmp/sp01.xml -q
uv run python scripts/ci/assert_no_skips.py /tmp/sp01.xml
```
Expected: `assert_no_skips.py` exits 0 (skipped==0).

- [ ] **Step 3: Confirm `import langgraph` + the new modules import cleanly (C5 import-hygiene)**

Run: `uv run python -c "import app.core.graph, app.core.graph_state, app.core.checkpointer, app.core.spine_runner, app.core.decision_record, app.adapters.inmemory.checkpointer; print('imports OK')"`
Expected: `imports OK`.

- [ ] **Step 4: Use the finishing-a-development-branch skill to open the PR**

REQUIRED SUB-SKILL: `superpowers:finishing-a-development-branch`. The PR body MUST include `Reviewer model:` (a different model class than the implementer per C9) and the `## Evidence` / `## Test Truth` blocks the pr-meta gate expects. Do NOT push until the operator asks.

---

## Later (designed-for; NOT in this PR — each has a spec §2 trigger)

- **Rewire `lib/durability/checkpoint.py` `post_tool_call` → read-through shim** (single writable store, SP-22/§5). The skeleton already passes exactly one writable checkpointer to `compile()`; the old `step-N.json` writer is orthogonal (old orchestrator path). The shim + one-release read-only fallback + deletion is a post-merge deprecation, not a skeleton build step.
- **`app/adapters/gcp/checkpointer.py` (`AsyncPostgresSaver`)** — gated behind the §13 spike; add `langgraph-checkpoint-postgres` + `psycopg[binary]` (verified absent) and confirm `__pregel_task_id` stability + commit ordering on the async saver first.
- **FastAPI app + lifespan** constructing `SpineRunner(provider)` once — lands with the server/SP-13 layer.
- **`clarify⇄decompose`, `fan_out`(Send) + per-branch worktree/lease, `execute⇄test⇄fix`, `eval_gate`(defer=True), `gated_action_gate`(two-tier, trust.py), SP-27 monitor sidecar, REPLAN native time-travel fork, full SP-17 bus, full SP-R7 GCS snapshot, SP-IR1 /panic** — each triggered as in spec §2.

---

## Self-review

**1. Spec coverage** — every spec §11 oracle maps to a task: DoD-1 (Task 5 + key-choice in Task 1), DoD-4 (Task 8), DoD-17 (Task 8), skeleton acceptance (Task 4), REJECT/REPLAN (Task 6), exactly-once regression (Task 5), `SP-01.yaml`+SHA256SUMS (Task 9). §3 node-split (Task 4 interrupt-split + Task 5 RED proof). §4 full schema + ledger re-key (Task 1). §5 checkpointer provider + DURABILITY_MODE (Task 2, runner Task 4). §6 id-map resume + approval-receipt + TIMEOUT (Tasks 4,7). §9 decision-record (Task 3). §12 anti-over-engineering honored (provider ABC not protocol re-decl; no second writable store; no saga; no frozen dataclass — `model_copy` seal). §7/§8 (gated_action_gate, monitor) correctly deferred.

**2. Placeholder scan** — no "TBD"/"add error handling"/"similar to Task N". Every code step shows complete code; every run step shows the exact command + expected result. The two prose `> Note:` callouts point at helpers that are themselves defined in an explicit step.

**3. Type consistency** — `ledger_key`/`_naive_key`/`_key_str`/`_ledger_union`/`_merge_counts`/`_merge_steering`/`arbitrate`/`assert_serializable_state`/`scrub_state` are defined once in `graph_state.py` (Task 1, with `_key_str` added in Task 4 Step 3) and referenced with those exact names in graph.py/tests. `AbstractCheckpointer.build_saver()`/`durability_mode`/`setup`/`aclose` match between ABC (Task 2), adapter (Task 2), and `SpineRunner` (Task 4). `SpineRunner.start/resume/get_state/_cfg/_app` names are consistent across Tasks 4–8. `HitlDecision`/`SteeringEvent`/`WorkspaceRef`/`LedgerReceipt` TypedDict field names match their construction sites.

**Known integration risk to watch during execution (flagged, not a placeholder):** Tasks 8's DoD-4/DoD-17 tests use `runner._app.ainvoke(Command(..., update={...}))` to inject steering/workspace state at the resume boundary (the skeleton stand-in for the deferred SP-17 bus / SP-R7 snapshotter). If LangGraph 1.2.2 rejects combining `resume=` and `update=` in one `Command`, split into two calls: first `ainvoke(Command(update={...}))` to write the channel, then `ainvoke(Command(resume={...}))` to resume — both share the thread_id/saver. Resolve at the test's first red.

---

## Addendum — corrections applied during execution (2026-06-02)

This plan was executed inline (worktree `feat/sp01-langgraph-spine` from `origin/main`).
The original code blocks above were corrected during execution by **two reviews**; the
shipped implementation reflects these, not the verbatim blocks above. Net result:
**13 files, +1540 lines, 33 unit tests, all gates green** (full suite 1125 passed / 0 failed;
C4 dead-code, C5 import-hygiene, C11 no-sentinel, substring-lint, no-skips, coverage-floor
0.4786 line / 0.5031 branch, acceptance SP-01.yaml 12/12, SHA256SUMS integrity).

**Pre-execution adversarial review (5 same-class critics) — 2 blockers fixed:**
1. The `BaseCheckpointSaver`-is-already-an-ABC fact → `AbstractCheckpointer` is a thin
   **provider** (`build_saver`), not a re-declared protocol (caught before writing).
2. `seal_spec`'s `TaskSpec(...)` omitted the 3 required fields (`spec_id`, `spec_sha`,
   `created_at`) → would `ValidationError`. Fixed with explicit construction.
3. The RED "side-effect-before-interrupt" test must use an **external** observable —
   langgraph rolls back state-channel writes from an interrupted node (so a reducer write
   is vacuously 1, not 2). Rewritten.
4. The exactly-once GREEN oracle was **vacuous** (the guard never fired in a linear graph).
   Added the load-bearing `apply_once` re-entry test with a negative control.
5. decision-record keyed by the real `Interrupt.id` (runner stamps it into the resume
   payload), not `__pregel_task_id`; scrub-before-persist wired at the runner ingress;
   DoD-4 scoped to the proven primitive (dedup/survival/arbitrate, not graph-coupled halt).

**Post-implementation C9 review (Gemini, different model class) — 3 majors applied:**
- `seal_spec` `spec_id` is now derived deterministically from `(thread_id, __pregel_task_id)`
  (`uuid5`) so a crash-retry overwrites the same spec instead of duplicating it.
- `apply_once` + doctrine docstrings corrected to the **honest** guarantee: exactly-once
  across *durable* re-entry; a crash strictly in the act→commit window is at-least-once and
  relies on the external op's idempotency (now provided by the deterministic `spec_id`).
- Documented that the external decision-record JSONL is at-least-once-by-design (audit never
  loses a decision; dedup by `interrupt_id`), while the state copy is exactly-once.
- Two Gemini findings ("invalid decorators", "durability not @property") were diff-format
  misreads — refuted by ruff + passing tests + the actual `@property` decorators.

The `lib/durability/checkpoint.py` shim rewire, `app/adapters/gcp/checkpointer.py`
(`AsyncPostgresSaver`), and the FastAPI lifespan remain deferred per spec §2 (the spine is
registered in `config/dead_code_entrypoints.txt` as present-not-yet-wired until then).
