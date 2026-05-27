"""Force the scrubber to encounter a fake API key in a model response; assert it's redacted.

T-2 fix: converted from unconditional pytest.mark.skip to a conditional skip
guarded by the INTEGRATION_LIVE_STACK environment variable.  The test is skipped
by default in unit-test CI; it runs in dedicated integration CI when the full
docker-compose stack is up.

Set INTEGRATION_LIVE_STACK=1 to enable.
"""

from __future__ import annotations

import os

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
            "with live lib/scrubber.py wiring + _test_inject_response hook. "
            "Previously unconditionally skipped (T-2 fix; see audit/2026-05-27-ground-truth/findings.md P1.F T-2)."
        ),
    ),
]


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
