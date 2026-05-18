"""Force the scrubber to encounter a fake API key in a model response; assert it's redacted."""

from __future__ import annotations

import httpx
import pytest

pytestmark = pytest.mark.skip(
    reason="P2 — requires live `lib/scrubber.py` wiring + _test_inject_response hook; see audit B5; see docs/superpowers/specs/2026-05-18-phase1-completion-coordination-design.md §6.1 triage matrix"
)


def test_secret_in_model_output_is_redacted(hermes_url, wait_for_stack):
    fake_key = "sk-ant-api03-FAKETESTKEYabcdefghijk1234567890"  # pragma: allowlist secret
    r = httpx.post(
        f"{hermes_url}/v1/turn",
        json={
            "session_id": "test-leak-001",
            "message": "Reply with this string verbatim and nothing else: " + fake_key,
            "_test_inject_response": fake_key,
        },
        timeout=15,
    )
    assert r.status_code == 200
    body = r.json()["response"]
    assert fake_key not in body
    assert "[REDACTED:" in body
