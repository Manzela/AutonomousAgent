"""P-3 acceptance tests: peer-execution dispatch via A2A boundary.

Per INTEGRATION.md §P-3:
'When peer_endpoint is set, _execute() routes via send_message instead
of in-process.'

Coverage:
  - Peer dispatch calls send_message with correct URL and message shape
  - Local dispatch calls invoke() and does NOT call send_message
  - A2A status mapping (SUBMITTED→INFLIGHT, COMPLETED→COMPLETED, etc.)
  - Timeout handling for both peer and local paths
  - Error handling (A2A errors, invoke exceptions)
  - AgentCapability peer_endpoint URL validation
  - Backward compatibility: capability without peer_endpoint still works
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from app.core.orchestrator import _map_a2a_status, execute
from app.core.schemas import (
    AgentCapability,
    AgentID,
    ExecutionResult,
    TaskRequest,
    TaskStatus,
)


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────


def _make_capability(
    *,
    peer_endpoint: str | None = None,
    invoke: AsyncMock | None = None,
    agent_id: str = "agent-test-01",
) -> AgentCapability:
    """Build a minimal AgentCapability for testing."""
    return AgentCapability(
        agent_id=AgentID(agent_id),
        version="1.0.0",
        phase="draft",
        description="test capability for P-3 dispatch tests",
        peer_endpoint=peer_endpoint,
        invoke=invoke,
    )


def _make_request(
    task_id: str = "task-001",
    summary: str = "compute delta-v for lunar transfer",
) -> TaskRequest:
    """Build a minimal TaskRequest for testing."""
    return TaskRequest(task_id=task_id, summary=summary)


# ─────────────────────────────────────────────────────────────────────
# Peer dispatch tests
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_peer_dispatch_calls_send_message():
    """A capability with peer_endpoint routes via send_message."""
    peer_url = "http://agent-canary:9001/"
    fake_task = {"id": "peer-task-001", "status": "SUBMITTED"}

    with patch("lib.a2a.client.send_message", new=AsyncMock(return_value=fake_task)):
        cap = _make_capability(peer_endpoint=peer_url)
        req = _make_request()
        result = await execute(req, cap)

    assert result.status == TaskStatus.INFLIGHT  # SUBMITTED → INFLIGHT
    assert result.agent_id == AgentID("agent-test-01")
    assert result.output is not None
    assert result.output["id"] == "peer-task-001"
    assert result.task_id == req.task_id  # correlation key must be preserved


@pytest.mark.asyncio
async def test_peer_dispatch_sends_correct_message_shape():
    """The A2A message sent to the peer contains required fields."""
    captured_messages: list[dict] = []
    peer_url = "http://agent-canary:9001/"

    async def _capture(url, message, **kw):
        captured_messages.append({"url": url, "message": message, "kwargs": kw})
        return {"id": "t-001", "status": "SUBMITTED"}

    with patch("lib.a2a.client.send_message", side_effect=_capture):
        cap = _make_capability(peer_endpoint=peer_url)
        req = _make_request(summary="find optimal policy")
        await execute(req, cap)

    assert len(captured_messages) == 1
    call = captured_messages[0]

    # URL matches the peer_endpoint
    assert call["url"] == peer_url

    # Message has required A2A structure
    msg = call["message"]
    assert msg["role"] == "USER"
    assert len(msg["parts"]) == 1
    assert "find optimal policy" in msg["parts"][0]["text"]

    # Metadata carries orchestrator context
    assert msg["metadata"]["orchestrator_task_id"] == "task-001"
    assert msg["metadata"]["phase"] == "draft"


@pytest.mark.asyncio
async def test_peer_dispatch_passes_agent_identity():
    """agent_identity is forwarded to send_message for JWT minting."""
    captured_kwargs: list[dict] = []

    async def _capture(url, message, **kw):
        captured_kwargs.append(kw)
        return {"id": "t-001", "status": "SUBMITTED"}

    with patch("lib.a2a.client.send_message", side_effect=_capture):
        cap = _make_capability(peer_endpoint="http://peer:9001/")
        req = _make_request()
        await execute(req, cap, agent_identity="sa@project.iam.gserviceaccount.com")

    assert captured_kwargs[0]["agent_identity"] == "sa@project.iam.gserviceaccount.com"


@pytest.mark.asyncio
async def test_peer_dispatch_timeout():
    """Peer timeout produces FAILED result, not an exception."""

    async def _timeout(*args, **kwargs):
        raise asyncio.TimeoutError()

    with patch("lib.a2a.client.send_message", side_effect=_timeout):
        cap = _make_capability(peer_endpoint="http://peer:9001/")
        req = _make_request()
        result = await execute(req, cap, peer_timeout_s=0.1)

    assert result.status == TaskStatus.FAILED
    assert "timeout" in (result.error or "").lower()


@pytest.mark.asyncio
async def test_peer_dispatch_error():
    """A2A client error produces FAILED result, not an exception."""

    async def _error(*args, **kwargs):
        raise ConnectionError("peer unreachable")

    with patch("lib.a2a.client.send_message", side_effect=_error):
        cap = _make_capability(peer_endpoint="http://peer:9001/")
        req = _make_request()
        result = await execute(req, cap)

    assert result.status == TaskStatus.FAILED
    assert "peer_error" in (result.error or "")
    assert result.duration_s >= 0.0


@pytest.mark.asyncio
async def test_peer_dispatch_a2a_error() -> None:
    """A2A protocol errors (not network errors) are surfaced in result.error."""
    from lib.a2a.client import A2AUnsupportedOperation

    async def _raise_a2a(*args, **kwargs):
        raise A2AUnsupportedOperation(-32004, "message/send not implemented", None)

    with patch("lib.a2a.client.send_message", side_effect=_raise_a2a):
        cap = _make_capability(peer_endpoint="http://peer:9001/")
        req = _make_request()
        result = await execute(req, cap)

    assert result.status == TaskStatus.FAILED
    assert result.task_id == req.task_id
    assert "a2a_peer_error" in (result.error or "")
    assert "-32004" in (result.error or "")


@pytest.mark.asyncio
async def test_peer_dispatch_a2a_task_not_found() -> None:
    """A2ATaskNotFound is surfaced as FAILED with a2a_peer_error prefix."""
    from lib.a2a.client import A2ATaskNotFound

    async def _raise_not_found(*args, **kwargs):
        raise A2ATaskNotFound(-32001, "task not found", None)

    with patch("lib.a2a.client.send_message", side_effect=_raise_not_found):
        cap = _make_capability(peer_endpoint="http://peer:9001/")
        req = _make_request()
        result = await execute(req, cap)

    assert result.status == TaskStatus.FAILED
    assert result.task_id == req.task_id
    assert "a2a_peer_error" in (result.error or "")
    assert "-32001" in (result.error or "")


# ─────────────────────────────────────────────────────────────────────
# Local dispatch tests
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_local_dispatch_calls_invoke():
    """A capability without peer_endpoint routes via invoke()."""
    mock_invoke = AsyncMock(
        return_value=ExecutionResult(
            task_id="task-001",
            status=TaskStatus.COMPLETED,
            agent_id=AgentID("agent-test-01"),
            output="done",
        )
    )

    cap = _make_capability(invoke=mock_invoke)
    req = _make_request()
    result = await execute(req, cap)

    assert result.status == TaskStatus.COMPLETED
    assert result.output == "done"
    mock_invoke.assert_called_once()


@pytest.mark.asyncio
async def test_local_dispatch_does_not_call_send_message():
    """Local dispatch must NOT call send_message."""
    mock_invoke = AsyncMock(
        return_value=ExecutionResult(
            task_id="task-001",
            status=TaskStatus.COMPLETED,
        )
    )

    with patch("lib.a2a.client.send_message", new=AsyncMock()) as mock_send:
        cap = _make_capability(invoke=mock_invoke)
        req = _make_request()
        await execute(req, cap)

    mock_send.assert_not_called()


@pytest.mark.asyncio
async def test_local_dispatch_no_invoke_fails():
    """A capability with no invoke and no peer_endpoint fails gracefully."""
    cap = _make_capability()  # No invoke, no peer_endpoint
    req = _make_request()
    result = await execute(req, cap)

    assert result.status == TaskStatus.FAILED
    assert "no_invoke_and_no_peer" in (result.error or "")


@pytest.mark.asyncio
async def test_local_dispatch_timeout():
    """Local invoke timeout produces FAILED result."""

    async def _slow(request):
        await asyncio.sleep(10)
        return ExecutionResult(task_id="task-001", status=TaskStatus.COMPLETED)

    cap = _make_capability(invoke=_slow)
    req = _make_request()
    result = await execute(req, cap, local_timeout_s=0.1)

    assert result.status == TaskStatus.FAILED
    assert "timeout" in (result.error or "").lower()


@pytest.mark.asyncio
async def test_local_dispatch_invoke_exception():
    """Exception in invoke() produces FAILED result, not a propagated exception."""

    async def _crash(request):
        raise RuntimeError("agent crashed")

    cap = _make_capability(invoke=_crash)
    req = _make_request()
    result = await execute(req, cap)

    assert result.status == TaskStatus.FAILED
    assert "invoke_exception" in (result.error or "")
    assert "agent crashed" in (result.error or "")


@pytest.mark.asyncio
async def test_local_dispatch_bad_return_type():
    """invoke() returning wrong type produces FAILED result."""
    mock_invoke = AsyncMock(return_value="not an ExecutionResult")

    cap = _make_capability(invoke=mock_invoke)
    req = _make_request()
    result = await execute(req, cap)

    assert result.status == TaskStatus.FAILED
    assert "non_execution_result" in (result.error or "")


# ─────────────────────────────────────────────────────────────────────
# A2A status mapping
# ─────────────────────────────────────────────────────────────────────


def test_a2a_status_mapping():
    """Verify all A2A statuses map to the correct orchestrator TaskStatus."""
    assert _map_a2a_status("SUBMITTED") == TaskStatus.INFLIGHT
    assert _map_a2a_status("WORKING") == TaskStatus.INFLIGHT
    assert _map_a2a_status("INPUT_REQUIRED") == TaskStatus.FAILED
    assert _map_a2a_status("COMPLETED") == TaskStatus.COMPLETED
    assert _map_a2a_status("CANCELED") == TaskStatus.FAILED
    assert _map_a2a_status("FAILED") == TaskStatus.FAILED
    assert _map_a2a_status("UNKNOWN") == TaskStatus.FAILED  # Unknown → FAILED


# ─────────────────────────────────────────────────────────────────────
# AgentCapability peer_endpoint validation
# ─────────────────────────────────────────────────────────────────────


def test_peer_endpoint_valid_http():
    """Valid http:// peer_endpoint is accepted."""
    cap = _make_capability(peer_endpoint="http://agent-canary:9001/")
    assert cap.peer_endpoint == "http://agent-canary:9001/"


def test_peer_endpoint_valid_https():
    """Valid https:// peer_endpoint is accepted."""
    cap = _make_capability(peer_endpoint="https://agent.example.com/")
    assert cap.peer_endpoint == "https://agent.example.com/"


def test_peer_endpoint_none_is_default():
    """Default peer_endpoint is None (local dispatch)."""
    cap = _make_capability()
    assert cap.peer_endpoint is None


def test_peer_endpoint_invalid_url_rejected():
    """Non-HTTP URLs are rejected at construction time."""
    with pytest.raises(ValueError, match="http://"):
        _make_capability(peer_endpoint="ftp://bad-peer:9001/")


def test_peer_endpoint_bare_hostname_rejected():
    """Bare hostnames without scheme are rejected."""
    with pytest.raises(ValueError, match="http://"):
        _make_capability(peer_endpoint="agent-canary:9001")


# ─────────────────────────────────────────────────────────────────────
# Backward compatibility
# ─────────────────────────────────────────────────────────────────────


def test_existing_capability_unchanged():
    """Capabilities without peer_endpoint behave identically to pre-P3."""
    cap = AgentCapability(
        agent_id=AgentID("legacy-agent"),
        version="0.1.0",
        phase="research",
        description="legacy agent without peer support",
    )
    assert cap.peer_endpoint is None
    assert cap.invoke is None
