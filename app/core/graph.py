"""SP-01 LangGraph spine (walking skeleton).

Topology (interrupt-split — never co-locate interrupt() with an EXTERNAL side-effect):

    goal_intake -> sign_off[interrupt] -> seal_spec -> execute
                -> ship_gate[interrupt] -> ship_effect -> END

Control flow is ALWAYS code-decided via conditional edges over the deterministic
HITL verb. No top-level LLM router/supervisor. See graph_state.py for the
exactly-once doctrine. `execute` wraps app.core.orchestrator.execute as a black-box
call-through leaf (no new orchestrator class). Irreversible EXTERNAL effects
(SpecStore.save in seal_spec, the durable ship record in ship_effect) live in
distinct post-resume nodes and are ledger-guarded via apply_once().
"""

from __future__ import annotations

import hashlib
import time
import uuid
from datetime import datetime, timezone
from typing import Callable, Optional

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from app.core import graph_state as gs
from app.core.decision_record import append_decision
from app.core.graph_state import SpineState
from app.core.orchestrator import execute as orchestrate
from app.core.schemas import AgentCapability, ExecutionResult, TaskRequest, TaskStatus
from lib.anchors.spec_store import SpecStore
from lib.anchors.task_spec import Scope, TaskSpec


# ── small utilities ────────────────────────────────────────────────────────
def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _receipt(tid: str, ptid: str, kind: str, node_label: str) -> dict:
    return {
        "thread_id": tid,
        "pregel_task_id": ptid,
        "action_kind": kind,
        "node_label": node_label,
        "super_step_label": 0,
        "ts": _now(),
    }


def _ledger_has(state: SpineState, key: tuple) -> bool:
    return any(gs.ledger_key(r) == key for r in state.get("ledger", []))


def _digest(state: SpineState) -> str:
    payload = (state.get("goal") or "") + (state.get("spec_sha") or "")
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# Stable namespace for deriving a DETERMINISTIC spec_id from (thread_id, __pregel_task_id),
# so a seal_spec re-run (same ptid, verified stable across resume) overwrites the SAME
# SpecStore file rather than minting a duplicate spec on crash-retry (C9 finding 3).
_SPEC_NS = uuid.uuid5(uuid.NAMESPACE_URL, "autonomousagent.spine.spec")


def _spec_id_for(tid: str, ptid: str) -> uuid.UUID:
    return uuid.uuid5(_SPEC_NS, f"{tid}:{ptid}")


def apply_once(
    state: SpineState,
    *,
    tid: str,
    ptid: str,
    kind: str,
    node_label: str,
    effect: Callable[[], None],
) -> dict:
    """Ledger guard for an irreversible EXTERNAL effect.

    GUARANTEE (precise): exactly-once across a node RE-ENTRY where the receipt is
    already durable in state — loops, fan-out re-dispatch, and a re-driven resume
    (proven load-bearing by test_apply_once_guard_is_load_bearing_on_reentry). The
    receipt + counter are state-channel writes (exactly-once via langgraph).

    LIMIT (honest): this is check-then-act WITHIN one node, so a crash STRICTLY between
    effect() and the node's checkpoint commit leaves no durable receipt and the effect
    re-runs on resume — AT-LEAST-ONCE. True end-to-end exactly-once for that window comes
    from the external op being idempotent (spec §12: git content-addressing + check-then-act;
    here seal_spec uses a deterministic spec_id so the save is idempotent). The ledger is the
    fast-path dedup, not a substitute for an idempotent effect."""
    key = (tid, ptid, kind)
    if _ledger_has(state, key):
        return {"audit": [f"{node_label} SKIP (receipt {gs._key_str(key)} present)"]}
    effect()
    return {
        "ledger": [_receipt(tid, ptid, kind, node_label)],
        "execution_counts": {gs._key_str(key): 1},
        "audit": [f"{node_label} effect applied"],
    }


def _record_decision(gate: str, decision) -> dict:
    """Build a HitlDecision from the resumed value (which the runner stamped with
    the real Interrupt.id), append it to the durable JSONL, return the state delta.

    Runs AFTER interrupt() -> executes once on the completing resume pass. The STATE
    decision_record (the checkpointed copy) is exactly-once. The external JSONL append
    is at-least-once-by-design: an audit trail must never LOSE a decision, so a duplicate
    on a re-driven resume is the safe direction — readers dedup by interrupt_id (§9)."""
    if not isinstance(decision, dict):
        decision = {"verb": str(decision)}
    hitl = {
        "verb": decision.get("verb", "APPROVE"),
        "actor": decision.get("actor", "<unknown>"),
        "reason": decision.get("reason", ""),
        "interrupt_id": decision.get("interrupt_id", "<unknown>"),
        "ts": _now(),
    }
    append_decision(hitl)  # durable non-repudiation trail (fail-open)
    return {gate: hitl, "decision_record": [hitl]}


# ── nodes (closure over the injected capability — no callables in state) ────
def _build_nodes(capability: AgentCapability):
    async def goal_intake(state: SpineState, config) -> dict:
        tid = config["configurable"]["thread_id"]
        return {
            "thread_id": tid,
            "fix_attempts": 0,
            "scrubbed": True,  # the runner scrubbed the goal before invoke (SP-R1)
            "audit": [f"goal_intake thread={tid}"],
        }

    async def sign_off(state: SpineState, config) -> dict:
        """PURE interrupt node — pauses only; the decision record append runs once
        on the completing resume pass (after interrupt())."""
        decision = interrupt(
            {"gate": "sign_off", "question": "Approve PRD?", "goal": state.get("goal", "")}
        )
        return _record_decision("sign_off", decision)

    async def seal_spec(state: SpineState, config) -> dict:
        """POST-resume effect: sha-pin the TaskSpec (ledger-guarded, exactly-once)."""
        tid = state["thread_id"]
        ptid = config["configurable"]["__pregel_task_id"]
        out: dict = {}

        def _effect() -> None:
            spec = TaskSpec(
                title=(state.get("goal", "") or "goal")[:120],
                intent=state.get("goal", "") or "goal",
                acceptance_criteria=["operator goal satisfied"],
                scope=Scope(in_scope=["the requested change"], out_of_scope=["unrelated work"]),
                success_metrics=["acceptance green on the merged commit"],
                created_by=0,
                spec_id=_spec_id_for(
                    tid, ptid
                ),  # deterministic -> idempotent re-save (C9 finding 3)
                spec_sha="0" * 64,  # placeholder; SpecStore.save() overwrites via compute_spec_sha
                created_at=datetime.now(timezone.utc),
            )
            store = SpecStore(gs.default_spec_store_root())
            sealed = store.save(spec.model_copy(update={"status": "locked"}))
            out["spec_sha"] = sealed.spec_sha
            out["spec_id"] = str(sealed.spec_id)

        delta = apply_once(
            state, tid=tid, ptid=ptid, kind="seal_spec", node_label="seal_spec", effect=_effect
        )
        delta.update(out)
        return delta

    async def execute(state: SpineState, config) -> dict:
        """Black-box call-through leaf to orchestrator.execute (no new class)."""
        req = TaskRequest(
            task_id=state["thread_id"],
            phase="draft",
            summary=state.get("goal", ""),
            deadline_s=60.0,
        )
        result: ExecutionResult = await orchestrate(req, capability)
        return {
            "tasks": [result.model_dump(mode="json")],
            "cost_accumulator": {f"{state['thread_id']}|execute": result.cost_usd},
            "audit": [f"execute status={result.status.value}"],
        }

    async def ship_gate(state: SpineState, config) -> dict:
        """PURE interrupt node — prod-approval."""
        decision = interrupt(
            {"gate": "ship", "question": "Ship to prod?", "spec_sha": state.get("spec_sha")}
        )
        return _record_decision("ship", decision)

    async def ship_effect(state: SpineState, config) -> dict:
        """POST-resume effect: the irreversible ship (ledger-guarded, exactly-once).
        Skeleton external effect = append a durable SHIPPED record; the real
        PR/commit/merge lands in a later layer."""
        tid = state["thread_id"]
        ptid = config["configurable"]["__pregel_task_id"]

        def _effect() -> None:
            append_decision(
                {
                    "verb": "SHIPPED",
                    "actor": "spine",
                    "reason": "ship_effect",
                    "interrupt_id": (state.get("ship") or {}).get("interrupt_id", "<unknown>"),
                    "ts": _now(),
                }
            )

        delta = apply_once(
            state, tid=tid, ptid=ptid, kind="ship", node_label="ship_effect", effect=_effect
        )
        if "ledger" in delta:  # effect ran (not skipped) -> stamp the workspace ref
            delta["workspace_ref"] = state.get("workspace_ref") or {
                "kind": "branch",
                "ref": f"agent/{tid}",
                "digest": _digest(state),
            }
        return delta

    return {
        "goal_intake": goal_intake,
        "sign_off": sign_off,
        "seal_spec": seal_spec,
        "execute": execute,
        "ship_gate": ship_gate,
        "ship_effect": ship_effect,
    }


# ── conditional routing (code-decided, deterministic on the HITL verb) ──────
def _route_after_sign_off(state: SpineState) -> str:
    verb = (state.get("sign_off") or {}).get("verb", "APPROVE")
    return {
        "APPROVE": "seal_spec",
        "REJECT": "__halt__",
        "REPLAN": "__replan__",
        "TIMEOUT": "__halt__",
    }.get(verb, "__halt__")


def _route_after_ship_gate(state: SpineState) -> str:
    verb = (state.get("ship") or {}).get("verb", "APPROVE")
    return {
        "APPROVE": "ship_effect",
        "REJECT": "__halt__",
        "REPLAN": "__replan__",
        "TIMEOUT": "__halt__",
    }.get(verb, "__halt__")


async def halt(state: SpineState, config) -> dict:
    return {"audit": ["HALTED by operator"]}


async def replan(state: SpineState, config) -> dict:
    """REPLAN = continue-as-new. Mark the fork; the actual child-thread spawn is the
    deferred REPLAN native-time-travel layer. The old thread + its sealed spec are
    left IMMUTABLE (the audit guarantee)."""
    return {
        "replan_parent": state["thread_id"],
        "pre_decompose_checkpoint_id": config["configurable"].get("checkpoint_id"),
        "audit": ["REPLAN requested (continue-as-new; child-thread spawn deferred)"],
    }


# ── graph assembly ─────────────────────────────────────────────────────────
def _default_capability() -> AgentCapability:
    async def _invoke(request: TaskRequest) -> ExecutionResult:
        return ExecutionResult(task_id=request.task_id, status=TaskStatus.COMPLETED)

    return AgentCapability(
        agent_id="spine-local",
        version="1",
        phase="draft",
        description="skeleton local stub",
        invoke=_invoke,
    )


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
    g.add_conditional_edges(
        "sign_off",
        _route_after_sign_off,
        {"seal_spec": "seal_spec", "__halt__": "__halt__", "__replan__": "__replan__"},
    )
    g.add_edge("seal_spec", "execute")
    g.add_edge("execute", "ship_gate")
    g.add_conditional_edges(
        "ship_gate",
        _route_after_ship_gate,
        {"ship_effect": "ship_effect", "__halt__": "__halt__", "__replan__": "__replan__"},
    )
    g.add_edge("ship_effect", END)
    g.add_edge("__halt__", END)
    g.add_edge("__replan__", END)
    return g.compile(checkpointer=saver)
