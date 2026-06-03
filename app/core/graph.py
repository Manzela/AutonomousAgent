"""SP-01 LangGraph spine (walking skeleton).

Topology (interrupt-split — never co-locate interrupt() with an EXTERNAL side-effect):

    goal_intake -> clarify ⇄[interrupt, ≤5 Qs/round] -> sign_off[interrupt] -> seal_spec
                -> decompose -> execute -> eval_gate --[pass]--> ship_gate[interrupt]
                -> ship_effect -> END
                            --[out-of-scope]--> __halt__

SP-03 clarify ⇄ Q-gen (PRD §3 journey / §6 SP-03): the clarify node drafts the PRD
via the injected spec-drafter and loops (ask_next) until the draft locks, then sign_off
gates on the DRAFTED PRD. PRD reconciliation: §5's diagram sketches decompose before
sign-off, but SP-04 ("interrupt gate AFTER clarify; nothing builds before resume") + the
§3 primary journey put approve before decompose — so sign_off stays before seal_spec.

Control flow is ALWAYS code-decided via conditional edges over the deterministic
HITL verb (and, at eval_gate, the deterministic SP-06 scope verdict). No top-level
LLM router/supervisor. See graph_state.py for the
exactly-once doctrine. `execute` wraps app.core.orchestrator.execute as a black-box
call-through leaf (no new orchestrator class). Irreversible EXTERNAL effects
(SpecStore.save in seal_spec, the durable ship record in ship_effect) live in
distinct post-resume nodes and are ledger-guarded via apply_once().
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import sys
import tempfile
import time
import uuid
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Mapping, Optional

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from app.adapters.inmemory.decompose import InMemoryDecomposer
from app.adapters.inmemory.spec_drafter import InMemorySpecDrafter
from app.core import graph_state as gs
from app.core.decision_record import append_decision
from app.core.decompose import ready_nodes, task_graph_to_requests
from app.core.eval_gate import scope_root_verdict
from app.core.graph_state import SpineState, TaskGraph
from lib.durability.branch_lease import BranchLease, GlobalThreadCap
from app.core.workspace import WorkspaceSession
from app.core.orchestrator import execute as orchestrate
from app.core.sandbox import AbstractSandbox
from app.core.schemas import AgentCapability, AgentID, ExecutionResult, TaskRequest, TaskStatus
from app.core.spec_drafter import AbstractSpecDrafter
from lib.anchors.clarification_driver import run_clarification_round
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


# SP-05: the no-plan / no-sandbox fallback probe (different PID, scrubbed env, no egress) —
# the honest minimal "work" when there is no plan to derive a per-node command from.
_SKELETON_SANDBOX_CMD = [sys.executable, "-c", "import os; print(os.getpid())"]

# SP-05 (F-1): the per-node applier — a deterministic, stdlib-only, trifecta-free stand-in
# for the external Hermes drive. Run by ABSOLUTE PATH (NOT `python -m ...`): the worktree is
# checked out at base_ref and its CWD does not contain this module, so `-m` would not resolve
# it; the applier needs no package import (stdlib only) and writes relative to its CWD.
_APPLIER = str(Path(__file__).resolve().parent / "_workspace_apply.py")

# Catch-all globs are banned upstream by SP-02 (is_catch_all_glob), but guard defensively.
_CATCH_ALL_GLOBS = frozenset({"**", "*", ".", "/", ""})


def _node_output_relpath(node: Mapping[str, Any]) -> str:
    """Derive a repo-relative path the per-node applier writes, IN SCOPE of the node's first
    allowed glob per eval_gate's matcher (exact / fnmatch / dir-prefix). A concrete file glob
    (has an extension, no wildcard) is written directly (exact match); a directory prefix gets
    a deterministic file under it (dir-prefix match). This is the deterministic 'agent work'
    stand-in; the real Hermes-authored diff is the deferred SP-05 drive."""
    globs = [g for g in (node.get("allowed_paths") or []) if g and g not in _CATCH_ALL_GLOBS]
    if not globs:
        return "app/.sp05_node_output.txt"
    g = globs[0].rstrip("/")
    base = g.rsplit("/", 1)[-1]
    if "*" not in g and "?" not in g and "." in base:
        return g  # concrete file → exact-match in scope
    prefix = g.split("*")[0].split("?")[0].rstrip("/")  # static dir prefix of a glob
    return f"{prefix}/sp05_node_output.txt" if prefix else "app/sp05_node_output.txt"


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


# ── nodes (closure over the injected capability + sandbox + drafter — no callables in state) ──
@asynccontextmanager
async def _async_cm(sync_cm) -> AsyncIterator[None]:
    """Bridge a SYNC context manager (SP-R6 ``GlobalThreadCap.slot`` / ``BranchLease.hold`` —
    both wrap a BLOCKING ``BoundedSemaphore.acquire`` / ``fcntl.flock``) into an async one:
    enter/exit OFF the event loop via a worker thread, so a contended acquire never FREEZES the
    loop (the other fan-out leaves + any heartbeat keep running)."""
    await asyncio.to_thread(sync_cm.__enter__)
    exc_info: tuple = (None, None, None)
    try:
        yield
    except BaseException:
        # Forward the REAL exc_info to __exit__ (protocol-correct) so a CM that inspects it for
        # rollback sees the failure; for the SP-R6 lock/semaphore CMs __exit__ ignores args and
        # always releases, so this never leaks — but the bridge stays correct for any CM.
        exc_info = sys.exc_info()
        raise
    finally:
        await asyncio.to_thread(sync_cm.__exit__, *exc_info)


async def _run_node(
    req: TaskRequest,
    *,
    sandbox: Optional[AbstractSandbox],
    capability: AgentCapability,
    cap: GlobalThreadCap,
    lease: BranchLease,
    repo_dir: Path,
    base_ref: str,
    thread_id: str,
) -> dict:
    """Run ONE ready node concurrently. Acquire the SP-R6 global-cap slot FIRST (admission —
    BEFORE creating a worktree, so disk is bounded by the cap), then (only when a real worktree
    will exist) the per-branch lease (serialize same-branch writers — no lost-update). Do the
    node's sandboxed work in its OWN git worktree, snapshot to its OWN node-keyed ref BEFORE
    close(). Returns {node_id, result, changed_paths, symlink_paths, workspace_ref}."""
    node_id = req.task_id
    allowed = req.constraints.get("allowed_paths") if isinstance(req.constraints, dict) else None
    use_ws = sandbox is not None and bool(allowed)

    async with AsyncExitStack() as stack:
        await stack.enter_async_context(_async_cm(cap.slot()))  # admission cap (acquire→create)
        if use_ws:
            await stack.enter_async_context(_async_cm(lease.hold(f"agent/{thread_id}/{node_id}")))

        ws: Optional[WorkspaceSession] = None
        symlink_paths: list[str] = []
        workspace_ref: Optional[dict] = None
        try:
            run_req = req
            if use_ws:
                ws = WorkspaceSession.create(
                    repo_dir=repo_dir, base_ref=base_ref, thread_id=thread_id, node_id=node_id
                )
                if ws.ok:
                    rel = _node_output_relpath({"allowed_paths": allowed})
                    manifest = [{"path": rel, "content": "# autonomousagent sp05 node output\n"}]
                    run_req = req.model_copy(
                        update={
                            "constraints": {
                                **req.constraints,
                                "sandbox_cmd": [sys.executable, _APPLIER, json.dumps(manifest)],
                                "workdir": str(ws.ws_dir),
                            }
                        }
                    )
                else:  # degraded worktree → the probe, empty diff
                    run_req = req.model_copy(
                        update={
                            "constraints": {
                                **(req.constraints or {}),
                                "sandbox_cmd": _SKELETON_SANDBOX_CMD,
                            }
                        }
                    )
            elif sandbox is not None:
                run_req = req.model_copy(
                    update={
                        "constraints": {
                            **(req.constraints or {}),
                            "sandbox_cmd": _SKELETON_SANDBOX_CMD,
                        }
                    }
                )

            result: ExecutionResult = await orchestrate(run_req, capability, sandbox=sandbox)
            if ws is not None and ws.ok and result.status == TaskStatus.COMPLETED:
                diff = ws.diff()
                result = result.model_copy(update={"artifacts": diff})
                symlink_paths = ws.symlinks([(d["status"], d["path"]) for d in diff])
                workspace_ref = ws.snapshot(thread_id=thread_id, node_id=node_id)  # BEFORE close()
            changed_paths = [
                [a.get("status", "M"), a["path"]] for a in (result.artifacts or ()) if a.get("path")
            ]
            return {
                "node_id": node_id,
                "result": result,
                "changed_paths": changed_paths,
                "symlink_paths": symlink_paths,
                "workspace_ref": workspace_ref,
            }
        finally:
            if ws is not None:
                ws.close()  # snapshot already taken; close while the lease is still held


def _build_nodes(
    capability: AgentCapability,
    sandbox: Optional[AbstractSandbox] = None,
    drafter: Optional[AbstractSpecDrafter] = None,
    cap: Optional[GlobalThreadCap] = None,
    lease: Optional[BranchLease] = None,
):
    # SP-03: the clarification spec-drafter is injected (InMemory deterministic in CI;
    # the DEFERRED VertexSpecDrafter in prod). Default to the hermetic in-memory drafter
    # so the spine stays runnable without live Vertex (CLAUDE.md hybrid-adapter rule).
    drafter = drafter or InMemorySpecDrafter()
    # SP-11/SP-R6: the fan-out concurrency cap + per-branch lease. Defaults keep every existing
    # caller working — cap=1 (serial, no behaviour change for single-node plans) + an ephemeral
    # lease dir. SpineRunner injects the real cap (SPINE_MAX_ACTIVE) + a per-runner lease dir.
    cap = cap or GlobalThreadCap(1)
    lease = lease or BranchLease(tempfile.mkdtemp(prefix="aa-lease-"))

    async def goal_intake(state: SpineState, config) -> dict:
        tid = config["configurable"]["thread_id"]
        return {
            "thread_id": tid,
            "fix_attempts": 0,
            "scrubbed": True,  # the runner scrubbed the goal before invoke (SP-R1)
            "audit": [f"goal_intake thread={tid}"],
        }

    async def clarify(state: SpineState, config) -> dict:
        """SP-03: the clarify ⇄ Q-gen loop (PRD §3 primary journey / §5 / SP-03).

        Runs the injected spec-drafter through ``decide_next_action`` each round:
          - action == ask_next (open ambiguity, confidence below the lock threshold) →
            ``interrupt()`` surfaces the typed clarifying questions (≤5/round, each
            referencing its planted token) + the ambiguity report (C18 challenges) +
            the applied_standards (R5 overridable defaults) + auto-resolved assumptions;
            the operator's answers (keyed by question token) accumulate in
            ``clarifications`` and the conditional edge loops back for another round.
          - action == lock/draft_lock/noop/escalate (or NO questions) → the round
            FINALISES the draft into ``spec_draft`` and the edge proceeds to sign_off.

        Termination is guaranteed: the drafter's confidence rises only as tracked
        ambiguities are resolved, and the hybrid circuit-breaker draft_locks once the
        question budget (MAX_CLARIFICATION_QUESTIONS) is exhausted — so an operator who
        never resolves still converges to draft_lock rather than looping forever.

        Topology note (PRD reconciliation): §5's diagram sketches decompose BEFORE
        sign-off, but SP-04 is explicit — "an interrupt() gate AFTER clarify; nothing
        builds before resume" — and the §3 primary journey is goal→clarify→draft→
        approve→decompose. So clarify sits between goal_intake and sign_off, and
        sign_off gates on the DRAFTED PRD (not the raw goal); nothing is sealed/built
        before the operator approves.
        """
        goal = state.get("goal", "")
        prior = state.get("clarifications") or []
        # Accumulate answers + the cumulative question budget across prior rounds.
        answers: dict[str, str] = {}
        questions_asked = 0
        for entry in prior:
            answers.update(entry.get("answers") or {})
            questions_asked += len(entry.get("questions") or [])
        round_index = len(prior)

        outcome = run_clarification_round(
            goal,
            drafter=drafter,
            questions_asked=questions_asked,
            answers=answers,
            round_index=round_index,
        )

        if outcome.action.kind == "ask_next" and outcome.questions:
            resume = interrupt(
                {
                    "gate": "clarify",
                    "round": round_index,
                    "questions": [q.model_dump() for q in outcome.questions],
                    "ambiguities": [a.model_dump() for a in outcome.ambiguities],
                    "applied_standards": [s.model_dump() for s in outcome.applied_standards],
                    "assumptions": [a.model_dump() for a in outcome.assumptions],
                }
            )
            # The operator resumes with answers keyed by question token. Drop the
            # interrupt_id the runner stamps onto dict resumes (it matches no token).
            new_answers = (
                {k: v for k, v in (resume or {}).items() if k != "interrupt_id"}
                if isinstance(resume, dict)
                else {}
            )
            return _scrub_persisted(
                {
                    "clarifications": [
                        {
                            "round": round_index,
                            "questions": [q.model_dump() for q in outcome.questions],
                            "answers": new_answers,
                        }
                    ],
                    "audit": [
                        f"clarify round={round_index} asked={len(outcome.questions)} "
                        f"action=ask_next conf={outcome.confidence:.2f}"
                    ],
                }
            )

        # Locked / draft_locked / noop / escalate (or ask_next with no questions):
        # finalise the drafted PRD. spec_draft carries the TaskSpec fields seal_spec
        # locks PLUS the surfaced report (ambiguities/applied_standards/assumptions)
        # sign_off shows the operator. (Persisting applied_standards/assumptions INTO
        # the locked TaskSpec — PRD §6 SP-03 (1.1) override-status — needs a TaskSpec
        # schema field + a C8 re-pin and is DEFERRED to its own slice.)
        d = outcome.draft
        spec_draft = {
            "title": d.title,
            "intent": d.intent,
            "acceptance_criteria": list(d.acceptance_criteria),
            "in_scope": list(d.in_scope),
            "out_of_scope": list(d.out_of_scope),
            "success_metrics": list(d.success_metrics),
            "constraints": list(d.constraints),
            "ambiguities": [a.model_dump() for a in outcome.ambiguities],
            "applied_standards": [s.model_dump() for s in outcome.applied_standards],
            "assumptions": [a.model_dump() for a in outcome.assumptions],
            "confidence": outcome.confidence,
            "lock_action": outcome.action.kind,
            "lock_reason": outcome.action.reason,
        }
        return _scrub_persisted(
            {
                "spec_draft": spec_draft,
                "audit": [
                    f"clarify locked round={round_index} action={outcome.action.kind} "
                    f"conf={outcome.confidence:.2f} questions=0"
                ],
            }
        )

    async def sign_off(state: SpineState, config) -> dict:
        """PURE interrupt node — pauses only; the decision record append runs once
        on the completing resume pass (after interrupt()).

        SP-03/SP-04: the operator approves the DRAFTED PRD (spec_draft) the clarify
        loop produced — the ambiguity report + applied_standards (overridable
        defaults, R5/C18) are surfaced here — NOT the raw goal."""
        decision = interrupt(
            {
                "gate": "sign_off",
                "question": "Approve the drafted PRD?",
                "spec_draft": state.get("spec_draft") or {},
                "goal": state.get("goal", ""),
            }
        )
        return _record_decision("sign_off", decision)

    async def seal_spec(state: SpineState, config) -> dict:
        """POST-resume effect: sha-pin the TaskSpec (ledger-guarded, exactly-once)."""
        tid = state["thread_id"]
        ptid = config["configurable"]["__pregel_task_id"]
        out: dict = {}

        def _effect() -> None:
            # SP-03: seal the CLARIFIED draft the clarify loop produced (spec_draft),
            # not the raw goal — each field falls back to the skeleton default when the
            # drafter left it empty (e.g. the InMemory drafter only fills the TaskSpec
            # fields for a fully-token-free goal, so a clarified goal uses the defaults).
            draft = state.get("spec_draft") or {}
            goal = state.get("goal", "") or "goal"
            intent = draft.get("intent") or goal
            spec = TaskSpec(
                title=(draft.get("title") or intent or "goal")[:120],
                intent=intent,
                acceptance_criteria=draft.get("acceptance_criteria") or ["operator goal satisfied"],
                scope=Scope(
                    in_scope=draft.get("in_scope") or ["the requested change"],
                    out_of_scope=draft.get("out_of_scope") or ["unrelated work"],
                ),
                success_metrics=draft.get("success_metrics")
                or ["acceptance green on the merged commit"],
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

    async def fan_out(state: SpineState, config) -> dict:
        """SP-11: dispatch the READY DAG frontier CONCURRENTLY (asyncio.gather), each ready
        node in its OWN git worktree+branch (SP-05b) under the SP-R6 global-cap + per-branch
        lease, joining at this single super-step. Fan-out is DAG-topology-driven — a pure
        dependency CHAIN yields ONE ready node per wave (no spurious parallelism). The
        N=1 / no-plan / sandbox=None paths reduce to the F-1/F-2 single-leaf behaviour.

        NO `interrupt` call here (interrupt-split doctrine — a gather'd leaf cannot cleanly suspend);
        irreversible effects stay in ship_effect. DEFERRED: per-leaf Pregel checkpointing
        (gather hides leaves behind ONE __pregel_task_id, so a mid-fan-out crash re-runs all
        leaves idempotently into FRESH worktrees, _merge_by_task_id dedups by task_id); kernel
        FS-confinement (EROFS/gVisor is SP-05c — a git worktree is path-isolation, not a kernel
        boundary); a cross-process cap (the cap is process-local); multi-WAVE re-fan-out (this
        slice dispatches the single ready frontier once)."""
        tid = state["thread_id"]
        plan = state.get("plan")
        repo_dir = Path(__file__).resolve().parents[2]
        base_ref = state.get("base_ref") or "HEAD"

        # isinstance narrows Optional[TaskGraph] -> TaskGraph; the .get("nodes") guard keeps the
        # empty-plan case on the skeleton path (an empty DAG has no ready frontier to dispatch).
        if isinstance(plan, dict) and plan.get("nodes"):
            # The ready frontier is computed against the ALREADY-COMPLETED nodes (NOT a hardcoded
            # empty set): on the first/only entry tasks is empty so this is exactly the roots, but
            # the computation stays correct if a future eval_gate->fan_out loop re-enters for the
            # next wave (a done node is never re-dispatched). C9 (gemini-3.1-pro) finding 1.
            done_ids = {
                t["task_id"]
                for t in (state.get("tasks") or [])
                if t.get("status") == TaskStatus.COMPLETED.value
            }
            ready_ids = {n["id"] for n in ready_nodes(plan, done_ids)}
            reqs = [r for r in task_graph_to_requests(plan) if r.task_id in ready_ids]
        else:
            # no plan: a single skeleton leaf — back-compat with F-1's no-plan execute path
            reqs = [
                TaskRequest(
                    task_id=tid,
                    phase="draft",
                    summary=state.get("goal", ""),
                    constraints={},
                    deadline_s=60.0,
                )
            ]

        async def _one(req: TaskRequest) -> dict:
            # A leaf failure must NOT crash the super-step — surface it as a scored FAILED result.
            try:
                return await _run_node(
                    req,
                    sandbox=sandbox,
                    capability=capability,
                    cap=cap,
                    lease=lease,
                    repo_dir=repo_dir,
                    base_ref=base_ref,
                    thread_id=tid,
                )
            except Exception as exc:  # noqa: BLE001 — degrade a broken leaf, never abort the join
                return {
                    "node_id": req.task_id,
                    "result": ExecutionResult(
                        task_id=req.task_id,
                        status=TaskStatus.FAILED,
                        error=f"fan_out_leaf_error: {exc!r}",
                    ),
                    "changed_paths": [],
                    "symlink_paths": [],
                    "workspace_ref": None,
                }

        outcomes = await asyncio.gather(*[_one(r) for r in reqs])  # gather IS the super-step join

        # Merge: per-task entries (the tasks reducer dedups by task_id across kill+resume),
        # PER-TASK cost keys, and the UNION of every leaf's changed_paths/symlinks (eval_gate
        # scope-scores the whole batch against the union of the plan's allowed globs).
        delta = {
            "tasks": [o["result"].model_dump(mode="json") for o in outcomes],
            "cost_accumulator": {f"{tid}|{o['node_id']}": o["result"].cost_usd for o in outcomes},
            "changed_paths": [cp for o in outcomes for cp in o["changed_paths"]],
            "symlink_paths": [sp for o in outcomes for sp in o["symlink_paths"]],
            "audit": [
                f"fan_out nodes={len(reqs)} sandboxed={sandbox is not None} "
                f"changed={sum(len(o['changed_paths']) for o in outcomes)}"
            ],
        }
        # SP-R1: scrub the whole delta (untrusted leaf stdout + audit) via the same lib.scrubber.
        scrubbed = _scrub_persisted(delta)
        # SP-R7: stamp REAL snapshot refs AFTER scrubbing (the digest is a content hash, not a
        # secret SP-R1's entropy filter must redact). Each leaf's ref is durable under
        # refs/aa-snapshots/<tid>/<node_id> regardless. Record EVERY leaf's ref in workspace_refs
        # (nothing silently dropped — C9 finding 2) and point the single workspace_ref ship_effect
        # consumes at the FIRST real one. Absent (degraded/sandbox=None) → ship_effect falls back.
        all_refs = [o["workspace_ref"] for o in outcomes if o["workspace_ref"]]
        if all_refs:
            scrubbed["workspace_refs"] = all_refs
            scrubbed["workspace_ref"] = all_refs[0]
        return scrubbed

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
        # F-1: any changed path that is a symlink in the worktree ALWAYS violates (a symlink
        # inside an allowed dir can point out-of-scope) — surface them to the verdict.
        verdict = scope_root_verdict(
            changed,
            allowed,
            base=state.get("base_ref") or "main",
            head=_digest(state),
            spec_sha=state.get("spec_sha") or "",
            symlink_paths=tuple(state.get("symlink_paths") or ()),
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
            # SP-R7: prefer the REAL content-addressed ref the execute node snapshotted; the
            # synthetic sha256(goal+spec_sha) below is now a DEGRADED-ONLY fallback (no
            # sandbox / snapshot failed) — kept so the sandbox=None skeleton path still ships.
            delta["workspace_ref"] = state.get("workspace_ref") or {
                "kind": "branch",
                "ref": f"agent/{tid}",
                "digest": _digest(state),
            }
        return delta

    return {
        "goal_intake": goal_intake,
        "clarify": clarify,
        "sign_off": sign_off,
        "seal_spec": seal_spec,
        "decompose": decompose,
        "fan_out": fan_out,
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
def _route_after_clarify(state: SpineState) -> str:
    """SP-03 clarify ⇄ loop: keep asking until a round FINALISES the draft (writes
    ``spec_draft``), then proceed to sign-off ON the drafted PRD. Deterministic on
    state presence — never an LLM decision."""
    return "sign_off" if state.get("spec_draft") else "clarify"


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
    drafter: Optional[AbstractSpecDrafter] = None,
    cap: Optional[GlobalThreadCap] = None,
    lease: Optional[BranchLease] = None,
):
    """Compile the spine StateGraph with the single writable checkpointer.

    `capability`, `sandbox`, and `drafter` are injected here (NOT in state — no
    callables in state); `capability` defaults to a local stub for the skeleton. When
    `sandbox` is provided (SP-05), the `execute` node runs its work in that sandbox
    instead of in-process. `drafter` (SP-03) defaults to the hermetic InMemory
    spec-drafter; prod injects the DEFERRED Vertex concretion. The clarify ⇄ Q-gen
    loop runs goal_intake → clarify (≤5 Qs/round, loops on ask_next) → sign_off ON the
    drafted PRD → seal_spec (SP-04: nothing builds before resume)."""
    capability = capability or _default_capability()
    nodes = _build_nodes(capability, sandbox, drafter, cap, lease)
    g = StateGraph(SpineState)
    for name, fn in nodes.items():
        g.add_node(name, fn)
    g.add_node("__halt__", halt)
    g.add_node("__replan__", replan)

    g.add_edge(START, "goal_intake")
    g.add_edge("goal_intake", "clarify")
    g.add_conditional_edges(
        "clarify",
        _route_after_clarify,
        {"clarify": "clarify", "sign_off": "sign_off"},
    )
    g.add_conditional_edges(
        "sign_off",
        _route_after_sign_off,
        {"seal_spec": "seal_spec", "__halt__": "__halt__", "__replan__": "__replan__"},
    )
    g.add_edge("seal_spec", "decompose")
    g.add_edge("decompose", "fan_out")
    g.add_edge("fan_out", "eval_gate")
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
