"""Unit tests for VertexDecomposer concretion (SP-02)."""

from __future__ import annotations

import json
from unittest import mock
from uuid import uuid4
from datetime import datetime, timezone

from app.adapters.gcp.decompose import VertexDecomposer, TaskGraphModel
from lib.anchors.task_spec import TaskSpec, Scope


def _spec() -> TaskSpec:
    return TaskSpec(
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


def test_vertex_decomposer_init():
    decomposer = VertexDecomposer(model="gemini-3.1-pro-preview")
    assert decomposer.model == "vertex_ai/gemini-3.1-pro-preview"


def test_vertex_decomposer_decompose_success():
    decomposer = VertexDecomposer()
    spec = _spec()

    mock_choice = mock.MagicMock()
    mock_choice.message.content = json.dumps(
        {
            "nodes": [
                {
                    "id": "n0",
                    "phase": "draft",
                    "summary": "implement widget",
                    "depends_on": [],
                    "acceptance_ref": "0",
                    "allowed_paths": ["app/core/widget.py"],
                },
                {
                    "id": "n1",
                    "phase": "verify",
                    "summary": "verify widget tests",
                    "depends_on": ["n0"],
                    "acceptance_ref": "1",
                    "allowed_paths": ["tests/unit/test_widget.py"],
                },
            ],
            "edges": [],  # will be computed by adapter
        }
    )

    mock_resp = mock.MagicMock()
    mock_resp.choices = [mock_choice]

    with mock.patch("litellm.completion", return_value=mock_resp) as mock_comp:
        graph = decomposer.decompose(spec)

        assert "nodes" in graph
        assert len(graph["nodes"]) == 2
        assert graph["nodes"][1]["id"] == "n1"

        # Verify edge computation matches depends_on
        assert graph["edges"] == [("n0", "n1")]

        mock_comp.assert_called_once()
        args, kwargs = mock_comp.call_args
        assert kwargs["model"] == decomposer.model
        assert kwargs["response_format"] == TaskGraphModel
        assert kwargs["temperature"] == 0.0
