"""SP-02 — TaskGraph DAG validation + decomposition fidelity + TaskSpec→TaskRequest phase bridge.

These are the PRD §6 EPIC-1 SP-02 acceptance oracles (red-green property tests):

  (1) AbstractDecomposer ABC cannot instantiate; InMemoryDecomposer is deterministic
      (no LLM) and hermetic — same TaskSpec in => same TaskGraph out.
  (2) TaskGraph VALIDATION: acyclic, every depends_on resolves to a node id, every node
      has a valid SDLC phase.
  (3) DECOMPOSITION FIDELITY: union of node acceptance_ref == TaskSpec.acceptance_criteria
      indices (no orphan criterion, no invented node).
  (4) NON-CATCH-ALL allowed_paths: every node has a non-empty acceptance-derived
      allowed_paths that is NOT a wildcard (**/*/./ FAILS the fidelity test).
  (5) TaskSpec→TaskRequest phase bridge: TaskSpec has no phase; the bridge assigns one
      per node and emits a TaskRequest carrying it.

The Vertex concretion is a deferred stub — these tests touch ONLY the in-memory adapter +
the pure core validators (CI must be hermetic; no live Vertex/LLM calls).
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from typing import Any
import pytest

from app.adapters.inmemory.decompose import InMemoryDecomposer
from app.core.decompose import (
    SDLC_PHASES,
    AbstractDecomposer,
    DecompositionError,
    acceptance_indices,
    assert_decomposition_fidelity,
    is_catch_all_glob,
    task_graph_to_requests,
    validate_taskgraph,
)
from app.core.schemas import TaskRequest
from lib.anchors.task_spec import Scope, TaskSpec


# ── fixtures ────────────────────────────────────────────────────────────────
def _spec(acceptance_criteria: list[str], **over) -> TaskSpec:
    kwargs: dict[str, Any] = {
        "title": "Build the widget pipeline",
        "intent": "Ship a validated widget pipeline before the P2 cutover.",
        "acceptance_criteria": acceptance_criteria,
        "scope": Scope(in_scope=["app/"], out_of_scope=["hermes-agent/"]),
        "success_metrics": ["pipeline green in CI"],
        "constraints": [],
        "spec_id": uuid4(),
        "spec_sha": "a" * 64,
        "created_at": datetime.now(timezone.utc),
        "created_by": 7217166969,
    }
    kwargs.update(over)
    return TaskSpec(**kwargs)


_MULTI = _spec(
    [
        "Research the widget API surface and document the design",
        "Implement app/core/widget.py with the WidgetEngine class",
        "Add tests/unit/test_widget.py covering the engine",
        "Verify the pipeline is green and deploy the service",
    ]
)


# ── (1) ABC + deterministic in-memory adapter ───────────────────────────────
def test_abstract_decomposer_cannot_instantiate():
    with pytest.raises(TypeError):
        AbstractDecomposer()  # type: ignore[abstract]


def test_inmemory_decomposer_is_subclass_of_abc():
    assert issubclass(InMemoryDecomposer, AbstractDecomposer)


def test_inmemory_decomposer_is_deterministic_hermetic():
    """Same TaskSpec in => byte-identical TaskGraph out (no LLM, no nondeterminism)."""
    d = InMemoryDecomposer()
    g1 = d.decompose(_MULTI)
    g2 = d.decompose(_MULTI)
    assert g1 == g2


# ── (2) DAG validation: acyclic, depends_on resolves, valid phase ───────────
def test_valid_graph_passes_validation():
    g = InMemoryDecomposer().decompose(_MULTI)
    validate_taskgraph(g)  # must not raise


def test_cycle_is_rejected():
    g = InMemoryDecomposer().decompose(_MULTI)
    # Introduce a 2-cycle between the first two nodes: n0<->n1 (n0 already a chain root).
    b = g["nodes"][1]["id"]
    g["nodes"][0]["depends_on"] = [b]  # n0 now depends on n1 too
    # rebuild edges to mirror depends_on exactly, so the edge-mirror check passes and the
    # acyclicity check is what trips (non-vacuous cycle oracle).
    g["edges"] = [(dep, n["id"]) for n in g["nodes"] for dep in n["depends_on"]]
    with pytest.raises(DecompositionError, match="(?i)cycle"):
        validate_taskgraph(g)


def test_dangling_depends_on_is_rejected():
    g = InMemoryDecomposer().decompose(_MULTI)
    g["nodes"][1]["depends_on"] = ["does-not-exist"]
    with pytest.raises(DecompositionError, match="(?i)depends_on|unresolved|dangling"):
        validate_taskgraph(g)


def test_invalid_phase_is_rejected():
    g = InMemoryDecomposer().decompose(_MULTI)
    g["nodes"][0]["phase"] = "deploy"  # not an SDLC phase
    with pytest.raises(DecompositionError, match="(?i)phase"):
        validate_taskgraph(g)


def test_every_node_phase_is_a_valid_sdlc_phase():
    g = InMemoryDecomposer().decompose(_MULTI)
    for n in g["nodes"]:
        assert n["phase"] in SDLC_PHASES


def test_edges_mirror_depends_on():
    g = InMemoryDecomposer().decompose(_MULTI)
    edge_set = {tuple(e) for e in g["edges"]}
    expected = {(dep, n["id"]) for n in g["nodes"] for dep in n["depends_on"]}
    assert edge_set == expected


# ── (3) decomposition fidelity: criterion ↔ node bijection on acceptance_ref ─
def test_fidelity_union_equals_criteria_indices():
    spec = _MULTI
    g = InMemoryDecomposer().decompose(spec)
    assert acceptance_indices(g) == set(range(len(spec.acceptance_criteria)))
    assert_decomposition_fidelity(g, spec)  # must not raise


def test_orphan_criterion_fails_fidelity():
    """A criterion with no covering node (drop a node) FAILS fidelity."""
    spec = _MULTI
    g = InMemoryDecomposer().decompose(spec)
    g["nodes"] = g["nodes"][:-1]  # drop last node => its criterion is orphaned
    g["edges"] = [e for e in g["edges"] if e[1] in {n["id"] for n in g["nodes"]}]
    with pytest.raises(DecompositionError, match="(?i)orphan|criteri|fidelity"):
        assert_decomposition_fidelity(g, spec)


def test_invented_node_fails_fidelity():
    """A node referencing a criterion index that does not exist FAILS fidelity."""
    spec = _MULTI
    g = InMemoryDecomposer().decompose(spec)
    invented = dict(g["nodes"][0])
    invented["id"] = "invented"
    invented["acceptance_ref"] = str(len(spec.acceptance_criteria) + 5)
    invented["depends_on"] = []
    g["nodes"].append(invented)
    with pytest.raises(DecompositionError, match="(?i)invent|criteri|fidelity|range"):
        assert_decomposition_fidelity(g, spec)


# ── (4) non-catch-all allowed_paths ─────────────────────────────────────────
@pytest.mark.parametrize("glob", ["**", "*", ".", "/", "**/*", "./**", "", "   "])
def test_catch_all_globs_are_detected(glob):
    assert is_catch_all_glob(glob) is True


@pytest.mark.parametrize("glob", ["app/core/widget.py", "tests/unit/test_widget.py", "app/core/**"])
def test_specific_globs_are_not_catch_all(glob):
    assert is_catch_all_glob(glob) is False


def test_every_node_has_nonempty_noncatchall_allowed_paths():
    g = InMemoryDecomposer().decompose(_MULTI)
    for n in g["nodes"]:
        assert n["allowed_paths"], f"node {n['id']} has empty allowed_paths"
        for p in n["allowed_paths"]:
            assert not is_catch_all_glob(p), f"node {n['id']} has catch-all glob {p!r}"


def test_wildcard_allowed_paths_fails_fidelity():
    """A node whose allowed_paths is a wildcard FAILS the fidelity test (else SP-06's
    out-of-scope fail-branch is unreachable)."""
    spec = _MULTI
    g = InMemoryDecomposer().decompose(spec)
    g["nodes"][0]["allowed_paths"] = ["**"]
    with pytest.raises(DecompositionError, match="(?i)catch-all|wildcard|allowed_paths"):
        assert_decomposition_fidelity(g, spec)


def test_empty_allowed_paths_fails_fidelity():
    spec = _MULTI
    g = InMemoryDecomposer().decompose(spec)
    g["nodes"][0]["allowed_paths"] = []
    with pytest.raises(DecompositionError, match="(?i)allowed_paths|empty|non-empty"):
        assert_decomposition_fidelity(g, spec)


# ── (5) multi-step golden: >1 node spanning >=2 phases ──────────────────────
def test_multistep_goal_yields_more_than_one_node_spanning_two_phases():
    g = InMemoryDecomposer().decompose(_MULTI)
    assert len(g["nodes"]) > 1
    phases = {n["phase"] for n in g["nodes"]}
    assert len(phases) >= 2, f"expected >=2 distinct phases, got {phases}"


def test_single_criterion_yields_single_node():
    spec = _spec(["Implement app/core/widget.py with the WidgetEngine class"])
    g = InMemoryDecomposer().decompose(spec)
    assert len(g["nodes"]) == 1
    validate_taskgraph(g)
    assert_decomposition_fidelity(g, spec)


# ── (6) TaskSpec→TaskRequest phase bridge ───────────────────────────────────
def test_phase_bridge_emits_one_request_per_node_with_phase():
    """TaskSpec has no phase; the bridge assigns one per node and the emitted
    TaskRequest carries it."""
    g = InMemoryDecomposer().decompose(_MULTI)
    reqs = task_graph_to_requests(g)
    assert len(reqs) == len(g["nodes"])
    assert all(isinstance(r, TaskRequest) for r in reqs)
    by_id = {n["id"]: n for n in g["nodes"]}
    for r in reqs:
        assert r.task_id in by_id
        assert r.phase == by_id[r.task_id]["phase"]
        assert r.phase in SDLC_PHASES


def test_taskspec_has_no_phase_field():
    """Guard the bridge's reason-for-existing: TaskSpec genuinely has no phase."""
    assert "phase" not in TaskSpec.model_fields


def test_bridge_preserves_summary_into_request():
    g = InMemoryDecomposer().decompose(_MULTI)
    reqs = task_graph_to_requests(g)
    by_id = {n["id"]: n for n in g["nodes"]}
    for r in reqs:
        assert r.summary == by_id[r.task_id]["summary"]
