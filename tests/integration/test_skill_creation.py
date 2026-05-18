"""Verify the skill-extractor nudge fires after a complex synthetic session."""

from __future__ import annotations

import time

import httpx
import pytest

pytestmark = pytest.mark.skip(
    reason="P2 — requires /v1/nudges/skill_extractor/run endpoint; manual skill creation still exercised by acceptance step 2; see docs/superpowers/specs/2026-05-18-phase1-completion-coordination-design.md §6.1 triage matrix"
)


@pytest.mark.slow
def test_complex_session_creates_skill(hermes_url, wait_for_stack, tmp_path):
    """Run 12 synthetic turns using ≥3 distinct tools; assert a skill file is written."""
    session_id = "test-skill-creation-001"
    tools_used = ["ls", "shell", "read_file"]
    for i, tool in enumerate(tools_used * 4):
        httpx.post(
            f"{hermes_url}/v1/turn",
            json={
                "session_id": session_id,
                "message": f"please use {tool} for step {i}",
                "force_tool": tool,
            },
            timeout=15,
        )
    # Trigger the skill extractor
    r = httpx.post(f"{hermes_url}/v1/nudges/skill_extractor/run", timeout=60)
    assert r.status_code == 200
    # Wait for skill file to materialize in mounted skills dir
    deadline = time.time() + 30
    while time.time() < deadline:
        r = httpx.get(f"{hermes_url}/v1/skills", timeout=5)
        if any(s["session_origin"] == session_id for s in r.json().get("skills", [])):
            return
        time.sleep(1)
    pytest.fail("Skill extractor did not produce a skill within 30s")
