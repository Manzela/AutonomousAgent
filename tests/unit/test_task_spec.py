"""Unit tests for TaskSpec Pydantic model."""

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

from lib.anchors.task_spec import Scope, TaskSpec


def _minimal_kwargs() -> dict:
    return {
        "title": "Audit my repo",
        "intent": "Find security issues in the AutonomousAgent codebase before P2 cloud migration.",
        "acceptance_criteria": ["Security audit doc committed", "Zero P0 issues open"],
        "scope": Scope(in_scope=["lib/", "deploy/"], out_of_scope=["hermes-agent/"]),
        "success_metrics": ["P0 issues resolved within 24h"],
        "constraints": ["Do not modify hermes-agent/ submodule"],
        "spec_id": uuid4(),
        "spec_sha": "a" * 64,  # placeholder; real sha computed by spec_store
        "created_at": datetime.now(timezone.utc),
        "created_by": 7217166969,
    }


def test_minimal_taskspec_validates():
    spec = TaskSpec(**_minimal_kwargs())
    assert spec.title == "Audit my repo"
    assert spec.escalation_h == 24  # default
    assert spec.status == "draft"  # default
    assert spec.intent_category == "unknown"  # default
    assert spec.schema_version == "1"


def test_missing_title_rejected():
    kwargs = _minimal_kwargs()
    del kwargs["title"]
    with pytest.raises(ValidationError):
        TaskSpec(**kwargs)


def test_invalid_intent_category_rejected():
    kwargs = _minimal_kwargs()
    kwargs["intent_category"] = "marketing"  # not in literal set
    with pytest.raises(ValidationError):
        TaskSpec(**kwargs)


def test_status_transition_to_locked():
    spec = TaskSpec(**_minimal_kwargs())
    locked = spec.model_copy(update={"status": "locked"})
    assert locked.status == "locked"


def test_serialization_roundtrip():
    spec = TaskSpec(**_minimal_kwargs())
    json_str = spec.model_dump_json()
    parsed = TaskSpec.model_validate_json(json_str)
    assert parsed.title == spec.title
    assert parsed.spec_id == spec.spec_id


def test_extra_field_forbidden():
    """Typos in field names should raise ValidationError, not be silently dropped."""
    kwargs = _minimal_kwargs()
    kwargs["escalate_h"] = 24  # typo for "escalation_h"
    with pytest.raises(ValidationError):
        TaskSpec(**kwargs)


def test_status_transition_via_model_copy_works():
    """model_copy(update={...}) MUST work for status transitions; this would
    break if anyone adds frozen=True to TaskSpec."""
    spec = TaskSpec(**_minimal_kwargs())
    locked = spec.model_copy(update={"status": "locked"})
    superseded = locked.model_copy(update={"status": "superseded", "superseded_by": "x" * 64})
    assert locked.status == "locked"
    assert superseded.status == "superseded"
    assert superseded.superseded_by == "x" * 64
