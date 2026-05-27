"""Verify the skill-extractor nudge fires after a complex synthetic session.

T-2 fix: converted from unconditional pytest.mark.skip to a conditional skip
guarded by the INTEGRATION_LIVE_STACK environment variable.  The test is skipped
by default in unit-test CI; it runs in dedicated integration CI when the full
docker-compose stack is up.

Set INTEGRATION_LIVE_STACK=1 to enable.
"""

from __future__ import annotations

import os
import time

import httpx
import pytest

_LIVE_STACK = bool(os.environ.get("INTEGRATION_LIVE_STACK"))

pytestmark = [
    pytest.mark.integration,
    pytest.mark.docker,
    pytest.mark.skipif(
        not _LIVE_STACK,
        reason=(
            "Requires INTEGRATION_LIVE_STACK=1 and a running docker-compose stack "
            "with /v1/nudges/skill_extractor/run endpoint. "
            "Previously unconditionally skipped (T-2 fix; see audit/2026-05-27-ground-truth/findings.md P1.F T-2)."
        ),
    ),
]


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
