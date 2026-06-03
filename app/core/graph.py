"""SP-01 LangGraph spine (walking skeleton).

Topology (interrupt-split — never co-locate interrupt() with an EXTERNAL side-effect):

    goal_intake -> sign_off[interrupt] -> seal_spec -> decompose -> execute
                -> eval_gate --[pass]--> ship_gate[interrupt] -> ship_effect -> END
                            --[out-of-scope]--> __halt__

Control flow is ALWAYS code-decided via conditional edges over the deterministic
HITL verb (and, at eval_gate, the deterministic SP-06 scope verdict). No top-level
LLM router/supervisor. See graph_state.py for the
exactly-once doctrine. `execute` wraps app.core.orchestrator.execute as a black-box
call-through leaf (no new orchestrator class). Irreversible EXTERNAL effects
(SpecStore.save in seal_spec, the durable ship record in ship_effect) live in
distinct post-resume nodes and are ledger-guarded via apply_once().
"""

from __future__ import annotations

import hashlib
import sys
import time
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Callable, Optional

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from app.adapters.inmemory.decompose import InMemoryDecomposer
from app.core import graph_state as gs
from app.core.decision_record import append_decision
from app.core.eval_gate import scope_root_verdict
from app.core.graph_state import SpineState, TaskGraph
from app.core.orchestrator import execute as orchestrate
from app.core.sandbox import AbstractSandbox
from app.core.schemas import AgentCapability, AgentID, ExecutionResult, TaskRequest, TaskStatus
from lib.anchors.spec_store import SpecStore
from lib.anchors.task_spec import Scope, TaskSpec
from lib.scrubber import scrub_string


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
    # SP-R1 (F-4): actor + reason are operator FREE TEXT — they can carry an email,
    # phone, API key, or PEM block. Scrub them through the SAME lib.scrubber.scrub_string
    # the rest of the spine uses BEFORE they reach EITHER the durable JSONL (append_decision)
    # OR the checkpointed state delta, so "no PII verbatim in persisted bytes" holds on the
    # non-repudiation trail too. verb is a constrained arbitration enum, interrupt_id a UUID,
    # and ts a timestamp — none is free text, so none is scrubbed (scrubbing verb could
    # corrupt a valid HitlVerb).
    hitl = {
        "verb": decision.get("verb", "APPROVE"),
        "actor": scrub_string(decision.get("actor", "<unknown>"), source="hitl_decision"),
        "reason": scrub_string(decision.get("reason", ""), source="hitl_decision"),
        "interrupt_id": decision.get("interrupt_id", "<unknown>"),
        "ts": _now(),
    }
    append_decision(hitl)  # durable non-repudiation trail (fail-open) — now scrubbed
    return {gate: hitl, "decision_record": [hitl]}


# SP-05: the skeleton's execute node runs a real subprocess probe inside the sandbox
# (different PID, scrubbed env, no egress) — the honest minimal "work" until the Hermes
# drive lands (next SP-05 slice). It emits the child PID so the isolation is observable.
_SKELETON_SANDBOX_CMD = [sys.executable, "-c", "import os; print(os.getpid())"]


def _scrub_persisted(obj):
    """SP-R1: recursively scrub every string in a value before it enters the checkpoint,
    via the SAME ``lib.scrubber.scrub_string`` as the goal/model path (so PII/secret
    coverage cannot drift between paths). Used on the execute node's tool output (a
    sandbox child's stdout/stderr is untrusted — it may print a secret)."""
    if isinstance(obj, str):
        return scrub_string(obj, source="execute_tool_output")
    if isinstance(obj, dict):
        return {k: _scrub_persisted(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_scrub_persisted(v) for v in obj]
    return obj


# ── nodes (closure over the injected capability + sandbox — no callables in state) ──
def _build_nodes(capability: AgentCapability, sandbox: Optional[AbstractSandbox] = None):
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

    async def decompose(state: SpineState, config) -> dict:
        """SP-02: decompose the LOCKED TaskSpec into a TaskGraph DAG, wiring the
        InMemoryDecomposer into the live spine flow (seal_spec -> decompose -> execute).

        A pure, deterministic read of the sha-pinned locked spec into ``state['plan']`` —
        no LLM, no side effect (so no ledger guard needed; re-running yields the same plan).
        The parallel fan-out OVER this plan (ready-node dispatch) is the deferred SP-11
        layer; today ``execute`` still runs the single skeleton leaf."""
        spec_id = state.get("spec_id")
        if not spec_id:
            return {"audit": ["decompose: no spec_id in state; skipped"]}
        spec = SpecStore(gs.default_spec_store_root()).get_by_id(spec_id)
        if spec is None:
            # C9: a sealed spec MUST be findable — a miss with spec_id set is a critical
            # inconsistency (storage failure / corruption). Fail LOUD; never proceed with
            # an empty plan.
            raise RuntimeError(
                f"decompose: locked spec {spec_id} not found in the store — a sealed spec "
                "must be retrievable; refusing to build an empty plan"
            )
        plan = InMemoryDecomposer().decompose(spec)
        # SP-R1 (C9): scrub before persist — the plan node summaries derive from the
        # operator's acceptance_criteria text, which can carry PII.
        return _scrub_persisted(
            {
                "plan": plan,
                "audit": [
                    f"decompose: {len(plan['nodes'])} node(s), {len(plan['edges'])} edge(s) "
                    f"from {len(spec.acceptance_criteria)} criteria"
                ],
            }
        )

    async def execute(state: SpineState, config) -> dict:
        """Black-box call-through leaf to orchestrator.execute (no new class).

        SP-05: when a ``sandbox`` is injected, the node runs its work in a REAL
        sandbox child (different PID, scrubbed env, no egress) instead of in-process;
        the command is carried on ``TaskRequest.constraints["sandbox_cmd"]``. With no
        sandbox the merged SP-01/SP-04 in-process skeleton path is unchanged.
        """
        constraints = {"sandbox_cmd": _SKELETON_SANDBOX_CMD} if sandbox is not None else {}
        req = TaskRequest(
            task_id=state["thread_id"],
            phase="draft",
            summary=state.get("goal", ""),
            constraints=constraints,
            deadline_s=60.0,
        )
        result: ExecutionResult = await orchestrate(req, capability, sandbox=sandbox)
        # SP-06: execute is the AUTHORITATIVE producer of the agent diff the eval_gate scores.
        # Derive it from the result's artifacts (each {"path","status"} entry) on EVERY run and
        # write it unconditionally, so eval_gate can never read a STALE changed_paths from a
        # prior super-step / re-driven resume (C9 fail-safe). Empty in the pure skeleton — the
        # orchestrator surfaces no diff yet; the real git diff arrives with the SP-05 drive.
        changed_paths = [
            [a.get("status", "M"), a["path"]] for a in (result.artifacts or ()) if a.get("path")
        ]
        delta = {
            "tasks": [result.model_dump(mode="json")],
            "cost_accumulator": {f"{state['thread_id']}|execute": result.cost_usd},
            "changed_paths": changed_paths,
            "audit": [f"execute status={result.status.value} sandboxed={sandbox is not None}"],
        }
        # SP-R1 (C9 hardening): scrub the ENTIRE delta — every new string value entering
        # the checkpoint (untrusted sandbox tool output AND the audit line) routes through
        # the same lib.scrubber as goal_intake, so no path can leak. Ints/floats (cost,
        # returncode) pass through unchanged.
        return _scrub_persisted(delta)

    async def eval_gate(state: SpineState, config) -> dict:
        """SP-06 (slice 1): PRD-conformance SHIP PRECONDITION — score the agent's changed
        paths against the LOCKED plan's allowed scope and BLOCK ship on any out-of-scope
        change. Deterministic, no LLM (the LLM DAGMetric leaf is the deferred SP-06 slice-2).

        In the walking skeleton ``execute`` reports no diff, so ``changed_paths`` is empty and
        the verdict trivially PASSES; when the real Hermes drive (SP-05) overwrites
        ``changed_paths`` with the git diff, this SAME gate blocks a non-conformant ship.
        Pure read (no external effect) → no ledger guard; re-running yields the same verdict."""
        plan = state.get("plan")
        changed = [tuple(c) for c in (state.get("changed_paths") or [])]
        allowed = _allowed_globs_from_plan(plan)
        # base/head are non-load-bearing verdict METADATA (the scope decision is over
        # changed×allowed, not the refs). base defaults to "main" and head to the state
        # content-digest in the skeleton; both become the real workspace refs when the SP-05
        # drive attaches a workspace_ref. base is overridable via state['base_ref'] (C9 minor).
        verdict = scope_root_verdict(
            changed,
            allowed,
            base=state.get("base_ref") or "main",
            head=_digest(state),
            spec_sha=state.get("spec_sha") or "",
        )
        delta = {
            "eval_verdict": asdict(verdict),
            "audit": [
                f"eval_gate passed={verdict.passed} violations={len(verdict.violations)} "
                f"allowed_globs={len(allowed)} changed={len(changed)}"
            ],
        }
        return _scrub_persisted(delta)

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

        ship_decision = state.get("ship")
        ship_iid = ship_decision["interrupt_id"] if ship_decision else "<unknown>"

        def _effect() -> None:
            append_decision(
                {
                    "verb": "SHIPPED",
                    "actor": "spine",
                    "reason": "ship_effect",
                    "interrupt_id": ship_iid,
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
        "decompose": decompose,
        "execute": execute,
        "eval_gate": eval_gate,
        "ship_gate": ship_gate,
        "ship_effect": ship_effect,
    }


def _allowed_globs_from_plan(plan: Optional[TaskGraph]) -> list[str]:
    """Union of every plan node's ``allowed_paths`` — the in-scope glob set the SP-06
    scope gate scores the agent's diff against. Empty when there is no plan."""
    if not plan:
        return []
    globs: list[str] = []
    for node in plan.get("nodes", []):
        globs.extend(node.get("allowed_paths", []))
    return sorted(set(globs))


# ── conditional routing (code-decided, deterministic on the HITL verb) ──────
def _route_after_sign_off(state: SpineState) -> str:
    d = state.get("sign_off")
    verb = d["verb"] if d else "APPROVE"
    return {
        "APPROVE": "seal_spec",
        "REJECT": "__halt__",
        "REPLAN": "__replan__",
        "TIMEOUT": "__halt__",
    }.get(verb, "__halt__")


def _route_after_eval_gate(state: SpineState) -> str:
    """SP-06 SHIP PRECONDITION — FAIL-CLOSED: proceed to ship_gate ONLY on an explicit
    passing verdict; an absent OR malformed verdict BLOCKS ship (→ __halt__), so a gate that
    did not run or returned a bad verdict can never let an unverified change ship (C9)."""
    v = state.get("eval_verdict")
    return "ship_gate" if (v and v.get("passed") is True) else "__halt__"


def _route_after_ship_gate(state: SpineState) -> str:
    d = state.get("ship")
    verb = d["verb"] if d else "APPROVE"
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
        agent_id=AgentID("spine-local"),
        version="1",
        phase="draft",
        description="skeleton local stub",
        invoke=_invoke,
    )


def build_spine(
    saver,
    *,
    capability: Optional[AgentCapability] = None,
    sandbox: Optional[AbstractSandbox] = None,
):
    """Compile the spine StateGraph with the single writable checkpointer.

    `capability` and `sandbox` are injected here (NOT in state — no callables in
    state); `capability` defaults to a local stub for the skeleton. When `sandbox`
    is provided (SP-05), the `execute` node runs its work in that sandbox instead of
    in-process; default `None` preserves the merged in-process skeleton."""
    capability = capability or _default_capability()
    nodes = _build_nodes(capability, sandbox)
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
    g.add_edge("seal_spec", "decompose")
    g.add_edge("decompose", "execute")
    g.add_edge("execute", "eval_gate")
    g.add_conditional_edges(
        "eval_gate",
        _route_after_eval_gate,
        {"ship_gate": "ship_gate", "__halt__": "__halt__"},
    )
    g.add_conditional_edges(
        "ship_gate",
        _route_after_ship_gate,
        {"ship_effect": "ship_effect", "__halt__": "__halt__", "__replan__": "__replan__"},
    )
    g.add_edge("ship_effect", END)
    g.add_edge("__halt__", END)
    g.add_edge("__replan__", END)
    return g.compile(checkpointer=saver)
