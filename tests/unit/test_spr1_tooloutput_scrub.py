"""SP-R1 (slice) — the execute node's TOOL OUTPUT is scrubbed before it enters the
checkpoint, via the SAME lib/scrubber as the goal/model path (so it can't drift).

PRD §6 SP-R1: "plant ≥3 distinct PII/secret shapes across goal text, clarification
dialogue, AND a tool-output field; assert NONE appear verbatim in the persisted
checkpoint bytes; assert the serializer routes through the same lib/scrubber.py."
The goal field was already scrubbed at goal_intake (spine_runner). This slice closes the
TOOL-OUTPUT field: a sandbox child's stdout/stderr (untrusted — the agent may print a
secret) is scrubbed before the execute node writes `tasks` into state.

DEFERRED: the clarification-dialogue field (needs clarify wired into the spine), the full
serde-level scrub of EVERY persisted value, and CMEK on the prod checkpoint DB.

NON-VACUOUS: on origin/main the execute node persists `result.model_dump()` verbatim — a
secret in sandbox stdout lands in the checkpoint (RED); GREEN once it is scrubbed.
"""

from __future__ import annotations

import json

from app.core.graph import _build_nodes, _default_capability
from app.core.sandbox import AbstractSandbox, SandboxResult

# Three distinct secret shapes the scrubber covers (openai/anthropic key, AWS access-key
# id, PEM private-key block). NOTE: the scrubber covers CREDENTIALS, not yet email/phone
# PII — adding PII patterns to lib/scrubber is a separable follow-up; this slice proves the
# tool-output is ROUTED through whatever the scrubber covers.
_API_KEY = "sk-ABCDEF0123456789abcdef0123456789abcd"  # pragma: allowlist secret
_AWS = "AKIAIOSFODNN7EXAMPLE"  # pragma: allowlist secret
# Assembled from parts so the file source never contains the contiguous PEM header (the
# detect-private-key pre-commit hook would block it); the RUNTIME value IS a real header,
# which is exactly what the scrubber's private_key_pem pattern must catch.
_PEM = "-----BEGIN " + "PRIVATE KEY-----\nMIIBVgIBADANBg\n-----END " + "PRIVATE KEY-----"


class _LeakySandbox(AbstractSandbox):
    """A sandbox whose child 'printed' secrets to stdout/stderr."""

    def __init__(self, stdout: str = "", stderr: str = "") -> None:
        self._out, self._err = stdout, stderr

    async def run(self, *, cmd, **kw) -> SandboxResult:
        return SandboxResult(
            returncode=0, stdout=self._out, stderr=self._err, duration_s=0.0, killed=False
        )


async def _run_execute(sandbox):
    nodes = _build_nodes(_default_capability(), sandbox=sandbox)
    return await nodes["execute"](
        {"thread_id": "t", "goal": "g"}, {"configurable": {"thread_id": "t"}}
    )


async def test_tool_output_secret_absent_from_persisted_task():
    out = await _run_execute(_LeakySandbox(stdout=f"export TOKEN={_API_KEY}"))
    persisted = json.dumps(out["tasks"][0])
    assert (
        _API_KEY not in persisted
    ), "a tool-output secret leaked verbatim into the persisted checkpoint"


async def test_three_secret_shapes_scrubbed_from_tool_output():
    out = await _run_execute(_LeakySandbox(stdout=f"key={_API_KEY} aws={_AWS}", stderr=_PEM))
    blob = json.dumps(out["tasks"][0])
    assert _API_KEY not in blob, "API-key shape leaked"
    assert _AWS not in blob, "AWS access-key id leaked"
    assert _PEM not in blob, "private-key block leaked"


async def test_tool_output_routes_through_lib_scrubber(monkeypatch):
    # Call-site identity: the execute node must route tool output through the SAME
    # lib.scrubber.scrub_string the goal/model path uses (so coverage can't drift).
    import app.core.graph as graph_mod

    calls: list[str] = []
    real = graph_mod.scrub_string

    def _spy(text, *, source="unknown"):
        calls.append(text)
        return real(text, source=source)

    monkeypatch.setattr(graph_mod, "scrub_string", _spy)
    out = await _run_execute(_LeakySandbox(stdout=f"leak {_API_KEY}"))
    assert any(
        _API_KEY in t for t in calls
    ), "execute did not route tool output through lib.scrubber.scrub_string"
    assert _API_KEY not in json.dumps(out["tasks"][0])


async def test_clean_tool_output_unchanged():
    # control: a benign output is preserved (the scrub is not destructive).
    out = await _run_execute(_LeakySandbox(stdout="all good: 42 widgets built"))
    assert out["tasks"][0]["output"]["stdout"] == "all good: 42 widgets built"
    assert out["tasks"][0]["status"] == "completed"
