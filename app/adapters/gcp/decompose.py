"""GCP/Vertex Decomposer adapter — concretion (SP-02).

The Vertex/LLM concretion of `AbstractDecomposer`: it drives a Vertex Claude/Gemini
model to produce a candidate TaskGraph.
"""

from __future__ import annotations

import json
import logging

import litellm
from pydantic import BaseModel, Field
from typing import Literal

from app.core.decompose import AbstractDecomposer
from app.core.graph_state import TaskGraph
from lib.anchors.task_spec import TaskSpec

logger = logging.getLogger(__name__)


class TaskNodeModel(BaseModel):
    id: str = Field(description="Unique node ID, e.g., 'n0', 'n1'")
    phase: Literal["research", "draft", "refine", "verify", "ship"]
    summary: str = Field(description="Short summary of work")
    depends_on: list[str] = Field(
        default_factory=list, description="IDs of nodes this node depends on"
    )
    acceptance_ref: str = Field(
        description="Comma-separated 0-based indices of acceptance criteria covered"
    )
    allowed_paths: list[str] = Field(
        description="List of non-catch-all glob paths allowed to be modified"
    )


class TaskGraphModel(BaseModel):
    nodes: list[TaskNodeModel]
    edges: list[tuple[str, str]] = Field(
        default_factory=list, description="Explicit dependency edges matching depends_on"
    )


class VertexDecomposer(AbstractDecomposer):
    """Vertex-backed LLM decomposer."""

    def __init__(self, *, model: str = "vertex_ai/gemini-3-5-flash") -> None:
        if model and not (
            model.startswith("vertex_ai/")
            or model.startswith("openrouter/")
            or model.startswith("hosted_vllm/")
        ):
            self.model = f"vertex_ai/{model}"
        else:
            self.model = model or "vertex_ai/gemini-3-5-flash"

    def _decompose(self, spec: TaskSpec) -> TaskGraph:
        """Decompose spec using Vertex structured output."""
        criteria_list = [f"{i}: {c}" for i, c in enumerate(spec.acceptance_criteria)]
        criteria_str = "\n".join(criteria_list)

        system_prompt = (
            "You are a software delivery planner. Convert the locked TaskSpec into a directed acyclic graph (DAG) of implementation nodes.\n\n"
            "Rules:\n"
            "1. Every node must map to at least one acceptance criterion. Use acceptance_ref (0-indexed comma-separated list) to indicate which ones.\n"
            "2. The union of all nodes' acceptance_ref must cover exactly all criteria. No orphan criteria allowed.\n"
            "3. Nodes depends_on must form an acyclic dependency graph.\n"
            "4. Every node must have non-empty allowed_paths. None of these paths can be a catch-all wildcard (like '*', '**', '.', '/'). They must be concrete paths or directories.\n"
            "5. Ensure edges list matches all depends_on relationships precisely (e.g. (dep, node_id))."
        )

        user_content = (
            f"Title: {spec.title}\n"
            f"Intent: {spec.intent}\n\n"
            f"Acceptance Criteria:\n{criteria_str}\n\n"
            f"In Scope Roots: {json.dumps(spec.scope.in_scope)}\n"
            f"Out of Scope Roots: {json.dumps(spec.scope.out_of_scope)}\n"
        )

        resp = litellm.completion(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            response_format=TaskGraphModel,
            temperature=0.0,
        )

        raw_content = resp.choices[0].message.content
        if not raw_content:
            raise ValueError("VertexDecomposer: model returned empty content")

        data = json.loads(raw_content)

        # Enforce edges match depends_on relations exactly just in case the model mismatches
        nodes_list = data.get("nodes", [])
        computed_edges = []
        for n in nodes_list:
            node_id = n.get("id")
            for dep in n.get("depends_on", []):
                computed_edges.append((dep, node_id))
        data["edges"] = computed_edges

        # Convert elements to tuple for edges list matching the type tuple[str, str]
        data["edges"] = [tuple(e) for e in data["edges"]]

        return data
