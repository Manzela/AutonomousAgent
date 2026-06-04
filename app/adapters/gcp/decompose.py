"""GCP/Vertex Decomposer adapter — DEFERRED stub (SP-02).

The Vertex/LLM concretion of `AbstractDecomposer`: it will drive a Vertex Claude/Gemini
model with a decomposer prompt VENDORED from the `hermes-agent` submodule (PRD §6 SP-02
— "vendored, not imported across boundary") to produce a candidate TaskGraph, then run
it through `assert_decomposition_fidelity` (inherited from `AbstractDecomposer.decompose`)
so an LLM-authored graph can never escape the SP-02 invariants.

NOT YET IMPLEMENTED — kept per the CLAUDE.md builder-agent rule (keep the GCP subclass as
a sibling under app/adapters/gcp/; do NOT delete, do NOT collapse the ABC). CI runs against
`app/adapters/inmemory/decompose.py`; staging+prod run against this once the Vertex client
+ vendored prompt land. Both __init__ and _decompose raise NotImplementedError until then.
"""

from __future__ import annotations

from app.core.decompose import AbstractDecomposer
from app.core.graph_state import TaskGraph
from lib.anchors.task_spec import TaskSpec


class VertexDecomposer(AbstractDecomposer):
    """Vertex-backed LLM decomposer (deferred SP-02 concretion)."""

    def __init__(self) -> None:
        raise NotImplementedError(
            "SP-02 Vertex decomposer not yet wired — vendor the hermes-agent decomposer "
            "prompt and build the Vertex client first (CI uses InMemoryDecomposer)."
        )

    def _decompose(self, spec: TaskSpec) -> TaskGraph:
        raise NotImplementedError(
            "SP-02 Vertex decomposer not yet wired — use InMemoryDecomposer for CI."
        )
