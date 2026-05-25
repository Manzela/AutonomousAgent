"""Day 9 scrubber tests — scrub_inbound_params redacts PHI in A2A message params."""

from __future__ import annotations

from lib.a2a.scrubber import scrub_inbound_params


def test_ssn_redacted() -> None:
    params = {"message": {"parts": [{"text": "Patient SSN is 123-45-6789 please verify"}]}}
    result = scrub_inbound_params(params)
    assert "123-45-6789" not in str(result)
    assert "[REDACTED]" in str(result)


def test_email_redacted() -> None:
    params = {"message": {"parts": [{"text": "Contact patient.name@hospital.org for details"}]}}
    result = scrub_inbound_params(params)
    assert "patient.name@hospital.org" not in str(result)
    assert "[REDACTED]" in str(result)


def test_clean_params_unchanged() -> None:
    params = {
        "message": {"parts": [{"text": "Compute the trajectory for agent alpha"}]},
        "task_id": "task-abc123",
    }
    result = scrub_inbound_params(params)
    assert result["task_id"] == "task-abc123"
    assert result["message"]["parts"][0]["text"] == "Compute the trajectory for agent alpha"


def test_nested_dict_values_are_scrubbed() -> None:
    params = {"context": {"patient": {"contact": "Call 555-123-4567 for John"}}}
    result = scrub_inbound_params(params)
    assert "555-123-4567" not in str(result)
    assert "[REDACTED]" in str(result)


def test_non_string_values_are_not_altered() -> None:
    params = {"count": 42, "flag": True, "nothing": None}
    assert scrub_inbound_params(params) == {"count": 42, "flag": True, "nothing": None}


def test_list_of_strings_scrubbed() -> None:
    params = {"items": ["hello world", "SSN: 987-65-4321", "clean text"]}
    result = scrub_inbound_params(params)
    assert "987-65-4321" not in str(result)
    assert "[REDACTED]" in str(result)
    assert result["items"][0] == "hello world"
    assert result["items"][2] == "clean text"
