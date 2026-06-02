"""SP-02 decompose is now WIRED into the live spine flow (seal_spec → decompose →
execute). The `decompose` node loads the sha-pinned LOCKED TaskSpec and produces a
TaskGraph DAG into `state['plan']`; the parallel fan-out OVER the plan (ready-node
dispatch) is the deferred SP-11 layer — `execute` still runs the single skeleton leaf.

NON-VACUOUS: on origin/main the spine flow is seal_spec → execute (no decompose node);
`InMemoryDecomposer` is never called from the graph. After wiring, the node produces a
valid plan and the merged happy-path spine flow still completes (the flow now traverses
decompose).
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from app.adapters.inmemory.checkpointer import InMemoryCheckpointer
from app.core.graph import _build_nodes, _default_capability
from app.core.spine_runner import SpineRunner
from lib.anchors.spec_store import SpecStore
from lib.anchors.task_spec import Scope, TaskSpec


def _locked_spec(store: SpecStore) -> TaskSpec:
    spec = TaskSpec(
        title="widget",
        intent="add a widget to app/core",
        acceptance_criteria=[
            "implement app/core/widget.py with the widget class",
            "verify tests/unit/test_widget.py covers it",
        ],
        scope=Scope(in_scope=["app/core"], out_of_scope=["docs"]),
        success_metrics=["widget tests green"],
        spec_id=uuid4(),
        spec_sha="0" * 64,
        created_at=datetime.now(timezone.utc),
        created_by=0,
    )
    return store.save(spec.model_copy(update={"status": "locked"}))


async def test_decompose_node_produces_plan_from_locked_spec(tmp_path, monkeypatch):
    monkeypatch.setenv("SPINE_SPEC_STORE", str(tmp_path))
    sealed = _locked_spec(SpecStore(tmp_path))
    nodes = _build_nodes(_default_capability())
    out = await nodes["decompose"](
        {"thread_id": "t", "spec_id": str(sealed.spec_id)}, {"configurable": {"thread_id": "t"}}
    )
    plan = out["plan"]
    assert len(plan["nodes"]) == 2, "one TaskNode per acceptance criterion (SP-02 bijection)"
    assert {n["acceptance_ref"] for n in plan["nodes"]} == {"0", "1"}
    assert all(n["allowed_paths"] for n in plan["nodes"]), "every node has non-empty allowed_paths"
    # the audit line reflects the produced plan
    assert "2 node(s)" in out["audit"][0]


async def test_decompose_skips_cleanly_without_spec_id():
    nodes = _build_nodes(_default_capability())
    out = await nodes["decompose"]({"thread_id": "t"}, {"configurable": {"thread_id": "t"}})
    assert "plan" not in out, "no spec_id -> no plan (graceful skip, not a crash)"
    assert "skipped" in out["audit"][0]


async def test_decompose_raises_when_sealed_spec_missing(tmp_path, monkeypatch):
    # C9: spec_id set but spec not found = critical inconsistency → fail LOUD, not skip.
    monkeypatch.setenv("SPINE_SPEC_STORE", str(tmp_path))  # empty store
    nodes = _build_nodes(_default_capability())
    with pytest.raises(RuntimeError, match="not found"):
        await nodes["decompose"](
            {"thread_id": "t", "spec_id": str(uuid4())}, {"configurable": {"thread_id": "t"}}
        )


async def test_plan_summary_is_scrubbed_before_persist(tmp_path, monkeypatch):
    # C9 / SP-R1: a secret in an acceptance criterion (operator text) must NOT land verbatim
    # in the persisted plan.
    monkeypatch.setenv("SPINE_SPEC_STORE", str(tmp_path))
    secret = "sk-ABCDEF0123456789abcdef0123456789abcd"  # pragma: allowlist secret
    spec = TaskSpec(
        title="s",
        intent="s",
        acceptance_criteria=[f"implement app/core/x.py using key {secret}"],
        scope=Scope(in_scope=["app/core"], out_of_scope=["docs"]),
        success_metrics=["green"],
        spec_id=uuid4(),
        spec_sha="0" * 64,
        created_at=datetime.now(timezone.utc),
        created_by=0,
    )
    sealed = SpecStore(tmp_path).save(spec.model_copy(update={"status": "locked"}))
    nodes = _build_nodes(_default_capability())
    import json

    out = await nodes["decompose"](
        {"thread_id": "t", "spec_id": str(sealed.spec_id)}, {"configurable": {"thread_id": "t"}}
    )
    assert secret not in json.dumps(
        out
    ), "a secret in the acceptance text leaked into the persisted plan"


async def test_decompose_actually_runs_in_the_compiled_spine_flow():
    # C9: prove the EDGES (seal_spec → decompose → execute) — drive the full spine and assert
    # decompose executed in the live flow (plan populated + audit entry), not just callable.
    runner = SpineRunner(InMemoryCheckpointer(), capability=_default_capability())
    tid = "goal-decompose-flow"
    r1 = await runner.start(
        thread_id=tid, goal="implement app/core/widget.py and verify tests/unit/test_widget.py"
    )
    signoff = r1["__interrupt__"][0]
    await runner.resume(
        thread_id=tid,
        interrupt_id=signoff.id,
        decision={"verb": "APPROVE", "actor": "op", "reason": "go"},
    )
    state = runner.get_state(tid).values
    assert state.get("plan") is not None, "decompose did not run between seal_spec and execute"
    assert state["plan"]["nodes"], "the compiled-flow plan has no nodes"
    assert any(
        "decompose:" in a for a in state.get("audit", [])
    ), "no decompose audit entry in the flow"


def test_decompose_is_registered_in_the_node_set():
    nodes = _build_nodes(_default_capability())
    assert "decompose" in nodes
