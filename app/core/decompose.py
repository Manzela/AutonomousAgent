"""AbstractDecomposer + TaskGraph validation + TaskSpec→TaskRequest phase bridge (SP-02).

This is the goal→DAG decomposition seam (PRD §6 EPIC-1 SP-02, P-2). It follows the
HYBRID ADAPTER PATTERN (CLAUDE.md):

  - the ABSTRACT decomposer (`AbstractDecomposer`) lives HERE in app/core/,
  - a DETERMINISTIC, hermetic, rule-based in-memory decomposer lives in
    `app/adapters/inmemory/decompose.py` (CI runs against it — NO LLM, NO Vertex),
  - the Vertex/LLM concretion is a DEFERRED stub (`app/adapters/gcp/decompose.py`):
    a thin shell that raises NotImplementedError until SP-02's Vertex wiring lands.

The decomposer turns a LOCKED `TaskSpec` (lib/anchors/task_spec.py — which has NO
`phase` field) into a validated `TaskGraph` of `TaskNode`s (app/core/graph_state.py),
ASSIGNING an SDLC phase per node. The pure validators here are the SP-02 acceptance
oracles and are import-only — they perform NO IO and call NO model.

INVARIANTS this module enforces (the SP-02 property tests):

  - VALIDATION: the graph is acyclic, every `depends_on` resolves to a node id, and
    every node's `phase` is a valid SDLC phase.
  - FIDELITY: the union of node `acceptance_ref` indices == the set of
    `TaskSpec.acceptance_criteria` indices (no orphan criterion, no invented node).
  - NON-CATCH-ALL `allowed_paths`: every node has a non-empty, acceptance-derived
    `allowed_paths` that is NOT a catch-all wildcard (`**` / `*` / `.` / `/`). A
    catch-all FAILS fidelity — else SP-06's out-of-scope fail-branch is unreachable.

The phase bridge (`task_graph_to_requests`) translates each `TaskNode` into a
`TaskRequest` (app/core/schemas.py) carrying the per-node phase — the missing piece
the router/fan-out (SP-11) consumes.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import get_args, get_type_hints

from app.core.graph_state import TaskGraph, TaskNode
from app.core.schemas import TaskRequest
from lib.anchors.task_spec import TaskSpec

# THE five SDLC phases — derived from the TaskNode.phase Literal so the two never drift.
# graph_state uses `from __future__ import annotations`, so the raw __annotations__ are
# strings; get_type_hints() resolves the Literal back to a real type to read get_args.
SDLC_PHASES: tuple[str, ...] = get_args(get_type_hints(TaskNode)["phase"])

# Catch-all globs that make an `allowed_paths` entry meaningless for scope-gating.
# `**` matches everything; `*` matches everything at one level; `.`/`/` denote the
# whole tree / repo root. An EMPTY / whitespace-only entry is also treated as catch-all.
_CATCH_ALL_GLOBS: frozenset[str] = frozenset({"**", "*", ".", "/", "**/*", "./**", "*/**", "./*"})


class DecompositionError(ValueError):
    """Raised when a TaskGraph fails validation or decomposition fidelity."""


# ── Pure validators (import-only; the SP-02 acceptance oracles) ─────────────
def is_catch_all_glob(glob: str) -> bool:
    """True iff `glob` is a catch-all that cannot constrain scope.

    A catch-all is empty/whitespace-only, OR a bare wildcard (`**`, `*`, `.`, `/`,
    `**/*`, `./**`, …), OR a normalized form thereof. A path-prefixed wildcard like
    `app/core/**` is NOT catch-all (it constrains to a real subtree).
    """
    if glob is None:
        return True
    stripped = glob.strip()
    if not stripped:
        return True
    # Normalize a leading "./" so "./**" == "**".
    normalized = stripped
    while normalized.startswith("./"):
        normalized = normalized[2:]
    normalized = normalized.strip("/") or "/"
    return stripped in _CATCH_ALL_GLOBS or normalized in _CATCH_ALL_GLOBS


def acceptance_indices(graph: TaskGraph) -> set[int]:
    """The set of acceptance-criterion indices the graph's nodes reference.

    Each node's `acceptance_ref` is a comma-separated list of 0-based criterion
    indices (a single node MAY cover >1 criterion; v1 in-memory emits exactly one).
    Raises DecompositionError if any ref is non-integer.
    """
    out: set[int] = set()
    for node in graph["nodes"]:
        for token in _split_refs(node["acceptance_ref"]):
            try:
                out.add(int(token))
            except ValueError as exc:  # noqa: PERF203 — explicit per-token diagnostic
                raise DecompositionError(
                    f"node {node['id']!r} has non-integer acceptance_ref token {token!r}"
                ) from exc
    return out


def _split_refs(acceptance_ref: str) -> list[str]:
    return [t.strip() for t in str(acceptance_ref).split(",") if t.strip()]


def validate_taskgraph(graph: TaskGraph) -> None:
    """Structural DAG validation. Raises DecompositionError on the first violation.

    Checks (SP-02 (2)): unique ids; every `phase` is a valid SDLC phase; every
    `depends_on` resolves to a known node id; the dependency graph is acyclic; the
    `edges` list mirrors `depends_on` exactly.
    """
    nodes = graph["nodes"]
    if not nodes:
        raise DecompositionError("TaskGraph has no nodes")

    ids = [n["id"] for n in nodes]
    if len(ids) != len(set(ids)):
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        raise DecompositionError(f"duplicate node ids: {dupes}")
    id_set = set(ids)

    for n in nodes:
        if n["phase"] not in SDLC_PHASES:
            raise DecompositionError(
                f"node {n['id']!r} has invalid phase {n['phase']!r} (expected one of {SDLC_PHASES})"
            )
        for dep in n["depends_on"]:
            if dep not in id_set:
                raise DecompositionError(
                    f"node {n['id']!r} has unresolved depends_on {dep!r} (dangling edge)"
                )
            if dep == n["id"]:
                raise DecompositionError(f"node {n['id']!r} depends_on itself (self-loop)")

    # Edge/depends_on mirror check.
    declared_edges = {tuple(e) for e in graph.get("edges", [])}
    expected_edges = {(dep, n["id"]) for n in nodes for dep in n["depends_on"]}
    if declared_edges != expected_edges:
        raise DecompositionError(
            f"edges do not mirror depends_on: declared={sorted(declared_edges)} "
            f"expected={sorted(expected_edges)}"
        )

    _assert_acyclic(nodes)


def _assert_acyclic(nodes: list[TaskNode]) -> None:
    """Kahn topological sort; DecompositionError if a cycle remains."""
    deps = {n["id"]: set(n["depends_on"]) for n in nodes}
    indegree = {nid: len(d) for nid, d in deps.items()}
    # dependents[x] = nodes that depend on x
    dependents: dict[str, list[str]] = {nid: [] for nid in deps}
    for nid, d in deps.items():
        for dep in d:
            dependents[dep].append(nid)

    ready = [nid for nid, deg in indegree.items() if deg == 0]
    visited = 0
    while ready:
        cur = ready.pop()
        visited += 1
        for child in dependents[cur]:
            indegree[child] -= 1
            if indegree[child] == 0:
                ready.append(child)

    if visited != len(nodes):
        remaining = sorted(nid for nid, deg in indegree.items() if deg > 0)
        raise DecompositionError(f"TaskGraph has a cycle (unresolvable nodes: {remaining})")


def assert_decomposition_fidelity(graph: TaskGraph, spec: TaskSpec) -> None:
    """The SP-02 fidelity oracle (SP-02 (3)+(4)).

    1. Runs structural validation first.
    2. The union of node `acceptance_ref` indices == EXACTLY the set of
       `TaskSpec.acceptance_criteria` indices — no orphan criterion (a criterion no
       node covers) and no invented node (a ref outside the criterion range).
    3. Every node has a NON-EMPTY `allowed_paths`, none of which is a catch-all
       wildcard.
    """
    validate_taskgraph(graph)

    n_criteria = len(spec.acceptance_criteria)
    expected = set(range(n_criteria))
    covered = acceptance_indices(graph)

    invented = covered - expected
    if invented:
        raise DecompositionError(
            f"invented node(s): acceptance_ref indices {sorted(invented)} are outside the "
            f"criterion range 0..{n_criteria - 1} (no invented criterion allowed)"
        )
    orphans = expected - covered
    if orphans:
        raise DecompositionError(
            f"orphan criteri(a): indices {sorted(orphans)} are covered by no node "
            f"(every acceptance criterion must map to a node)"
        )

    for node in graph["nodes"]:
        paths = node["allowed_paths"]
        if not paths:
            raise DecompositionError(
                f"node {node['id']!r} has empty allowed_paths (must be non-empty)"
            )
        for p in paths:
            if is_catch_all_glob(p):
                raise DecompositionError(
                    f"node {node['id']!r} has catch-all allowed_paths entry {p!r} "
                    f"(a wildcard fails fidelity — SP-06's out-of-scope branch needs a "
                    f"real, acceptance-derived path)"
                )


# ── TaskSpec→TaskRequest phase bridge (SP-02 (5)) ───────────────────────────
def task_graph_to_requests(graph: TaskGraph) -> list[TaskRequest]:
    """Bridge each validated TaskNode → a TaskRequest carrying its per-node phase.

    `TaskSpec` has no `phase`; the decomposer assigned one per node, and this bridge
    surfaces it on the routable `TaskRequest` (the missing piece SP-11 fan-out + the
    router consume). The node id becomes the request task_id, the per-node summary +
    phase carry over, and the node's `allowed_paths` ride along in `constraints`.
    """
    requests: list[TaskRequest] = []
    for node in graph["nodes"]:
        requests.append(
            TaskRequest(
                task_id=node["id"],
                phase=node["phase"],
                summary=node["summary"],
                constraints={
                    "acceptance_ref": node["acceptance_ref"],
                    "allowed_paths": list(node["allowed_paths"]),
                    "depends_on": list(node["depends_on"]),
                },
            )
        )
    return requests


# ── The abstract decomposer seam ────────────────────────────────────────────
class AbstractDecomposer(ABC):
    """Locked TaskSpec -> validated TaskGraph.

    Concretions (in-memory rule-based for CI; Vertex/LLM for staging+prod) MUST return
    a graph that passes BOTH `validate_taskgraph` and `assert_decomposition_fidelity`.
    `decompose()` is the public entrypoint; it calls the subclass `_decompose()` hook
    and then enforces the SP-02 invariants so a broken decomposition can never escape.
    """

    def decompose(self, spec: TaskSpec) -> TaskGraph:
        """Decompose `spec` into a TaskGraph, enforcing SP-02 invariants before return."""
        graph = self._decompose(spec)
        assert_decomposition_fidelity(graph, spec)
        return graph

    @abstractmethod
    def _decompose(self, spec: TaskSpec) -> TaskGraph:
        """Subclass hook: build the raw TaskGraph (validated by `decompose`)."""
        raise NotImplementedError(f"{self.__class__.__name__}._decompose() must be implemented")
