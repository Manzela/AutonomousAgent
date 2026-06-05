"""Unit tests for VertexSpecDrafter concretion (SP-03)."""

from __future__ import annotations

import json
from unittest import mock

from app.adapters.gcp.spec_drafter import VertexSpecDrafter
from app.core.spec_drafter import DraftResult, MAX_QUESTIONS_PER_ROUND


def test_vertex_spec_drafter_init():
    drafter = VertexSpecDrafter(project="test-proj", location="us-west1", model="gemini-1.5-pro")
    assert drafter.project == "test-proj"
    assert drafter.location == "us-west1"
    assert drafter.model == "vertex_ai/gemini-1.5-pro"


def test_vertex_spec_drafter_draft_success():
    drafter = VertexSpecDrafter()

    mock_choice = mock.MagicMock()
    mock_choice.message.content = json.dumps(
        {
            "title": "Test Title",
            "intent": "Test Intent",
            "acceptance_criteria": ["criteria 1"],
            "in_scope": ["in scope"],
            "out_of_scope": ["out of scope"],
            "success_metrics": ["metric"],
            "constraints": ["constraint"],
            "ambiguities": [],
            "questions": [
                {"references_token": "token1", "text": "What is x?", "category": "functional"}
            ],
            "applied_standards": [],
            "assumptions": [],
        }
    )

    mock_resp = mock.MagicMock()
    mock_resp.choices = [mock_choice]

    with mock.patch("litellm.completion", return_value=mock_resp) as mock_comp:
        result = drafter.draft("My intent", answers={"prev_token": "prev_ans"}, round_index=1)

        assert isinstance(result, DraftResult)
        assert result.title == "Test Title"
        assert result.intent == "Test Intent"
        assert result.questions[0].references_token == "token1"

        mock_comp.assert_called_once()
        args, kwargs = mock_comp.call_args
        assert kwargs["model"] == drafter.model
        assert kwargs["response_format"] == DraftResult
        assert kwargs["temperature"] == 0.0


def test_vertex_spec_drafter_question_slicing_safety():
    drafter = VertexSpecDrafter()

    # Generate 10 questions (exceeding max limit of 5)
    questions = [
        {"references_token": f"token{i}", "text": f"Q{i}", "category": "functional"}
        for i in range(10)
    ]

    mock_choice = mock.MagicMock()
    mock_choice.message.content = json.dumps(
        {
            "title": "Title",
            "intent": "Intent",
            "acceptance_criteria": [],
            "in_scope": [],
            "out_of_scope": [],
            "success_metrics": [],
            "constraints": [],
            "ambiguities": [],
            "questions": questions,
            "applied_standards": [],
            "assumptions": [],
        }
    )

    mock_resp = mock.MagicMock()
    mock_resp.choices = [mock_choice]

    with mock.patch("litellm.completion", return_value=mock_resp):
        result = drafter.draft("Intent")
        assert len(result.questions) == MAX_QUESTIONS_PER_ROUND
        assert result.questions[-1].references_token == f"token{MAX_QUESTIONS_PER_ROUND - 1}"
