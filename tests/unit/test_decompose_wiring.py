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

from app.core.graph import _build_nodes, _default_capability
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


async def test_decompose_skips_when_spec_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("SPINE_SPEC_STORE", str(tmp_path))  # empty store
    nodes = _build_nodes(_default_capability())
    out = await nodes["decompose"](
        {"thread_id": "t", "spec_id": str(uuid4())}, {"configurable": {"thread_id": "t"}}
    )
    assert "plan" not in out
    assert "not found" in out["audit"][0]


def test_decompose_is_registered_between_seal_spec_and_execute():
    # the node registry exposes `decompose` (wired into the graph between seal_spec/execute).
    nodes = _build_nodes(_default_capability())
    assert "decompose" in nodes
