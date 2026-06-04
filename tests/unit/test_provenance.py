"""Unit tests for the output provenance metadata generation and schema support."""

from __future__ import annotations

from app.core.schemas import ExecutionResult, TaskStatus
from lib.guardrails.sanitize import generate_provenance_metadata


def test_generate_provenance_metadata():
    output_data = {"result": "success", "data": [1, 2, 3]}
    model = "gemini-2.5-pro"

    metadata = generate_provenance_metadata(output_data, model_version=model)

    assert "timestamp" in metadata
    assert metadata["model_version"] == model
    assert "integrity_hash" in metadata
    assert metadata["origin"] == "autonomous-agent-sandbox"

    # Assert hash is stable and valid
    import hashlib

    expected_hash = hashlib.sha256(str(output_data).encode("utf-8")).hexdigest()
    assert metadata["integrity_hash"] == expected_hash


def test_execution_result_with_provenance():
    prov = {
        "timestamp": "2026-06-04T12:00:00Z",
        "model_version": "gemini-2.5-pro",
        "integrity_hash": "abc123hash",
        "origin": "autonomous-agent-sandbox",
    }

    result = ExecutionResult(
        task_id="task-1",
        status=TaskStatus.COMPLETED,
        output="some output text",
        provenance=prov,
    )

    assert result.provenance == prov
    assert result.provenance["integrity_hash"] == "abc123hash"
