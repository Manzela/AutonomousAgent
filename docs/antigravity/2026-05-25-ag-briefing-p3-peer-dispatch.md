# Antigravity Briefing — P-3: A2A Peer-Execution Dispatch
**Date:** 2026-05-25
**Model:** Claude Opus 4.6 Thinking (recommended for orchestrator architecture reasoning)
**Priority:** HIGH — this connects the spike (lib/a2a/) to the orchestrator (app/) and enables real multi-agent routing
**Collision boundary:** You own `app/` exclusively. Do NOT touch `lib/a2a/` (Claude Code's territory).

---

## 1. What You Are Building

The seed orchestrator's `_execute()` method currently dispatches tasks to local sandbox only. **P-3 extends it to route across the A2A boundary** when the chosen expert lives on a peer node rather than locally.

From `docs/research/autonomous-agent-seed-orchestrator/INTEGRATION.md §P-3`:
> Extend `AgentCapability` with a `peer_endpoint: str | None` field. When set, the orchestrator's `_execute()` routes the task across the A2A boundary instead of in-process.

---

## 2. Files to Read FIRST (in order)

1. `docs/research/autonomous-agent-seed-orchestrator/04-gcp-native-adapter-plan.md` — **REQUIRED before any code**. Locks the hybrid pattern and the exact interfaces to preserve.
2. `docs/research/autonomous-agent-seed-orchestrator/INTEGRATION.md` — §P-3 acceptance criteria (two-node test).
3. `docs/research/autonomous-agent-seed-orchestrator/01-phase1-mathematical-spec.md` — §MoE dispatch and the Free Agent FSM that drives `_execute()`.
4. `docs/research/autonomous-agent-seed-orchestrator/seed/orchestrator.py` — reference `_execute()` implementation you'll be extending.
5. `app/core/schemas.py` — current `AgentCapability` dataclass (your primary target).
6. `lib/a2a/client.py` — `send_message(peer_url, message, *, agent_identity, timeout)` API you'll call for peer dispatch. **Read-only** — do not modify.

---

## 3. Guiding Questions to Resolve Before Writing Code

1. **What fields does `AgentCapability` currently have?** Read `app/core/schemas.py` → look for `AgentCapability`. Note the exact field names — your `peer_endpoint` addition must not break existing tests.

2. **How does `_execute()` currently invoke the local sandbox?** Read `seed/orchestrator.py` → find `_execute()`. The pattern is `agent_module.run(request, ctx)` — peer dispatch should branch on `capability.peer_endpoint is not None`.

3. **What `message` shape does A2A expect?** `lib/a2a/client.send_message` expects `{"role": "USER", "parts": [{"text": "..."}]}`. Construct this from the orchestrator's `request` dict.

4. **Where does `agent_identity` come from?** The orchestrator has access to the current session's `AgentIdentity` (from auth). Pass it through to `send_message` so outbound JWTs are minted. If the orchestrator doesn't carry identity yet, use `None` for the spike (fail-open auth).

5. **What validates that a two-node routing works?** Per INTEGRATION.md P-3 acceptance: "Two-node test: project P with 3 agents, one local, two remote. Routing distribution roughly matches the local case after the same number of trajectories." For the spike, the acceptance test can be simpler: given a capability with `peer_endpoint` set, verify `send_message` is called with the correct peer URL (mock the client, no real network).

---

## 4. Implementation Spec

### 4a. `app/core/schemas.py` — Extend AgentCapability

Find the `AgentCapability` dataclass/model. Add:

```python
# A2A peer-execution endpoint (P-3). None = local dispatch (default).
# When set, _execute() routes this capability's tasks via lib/a2a/client.send_message
# to the peer agent at this URL instead of running the agent module locally.
peer_endpoint: str | None = None
```

Constraints:
- Must be backward-compatible: existing capabilities without `peer_endpoint` behave identically (default `None` = local)
- If using Pydantic: add `peer_endpoint: Optional[str] = None`
- If using dataclass: `peer_endpoint: str | None = None`
- Validate that `peer_endpoint`, when set, is a valid URL (starts with `http://` or `https://`)

### 4b. `app/core/orchestrator.py` (or `seed/orchestrator.py` port) — Extend `_execute()`

Find or create the `_execute()` method. Add the A2A routing branch:

```python
async def _execute(
    self,
    capability: "AgentCapability",
    request: dict[str, Any],
    ctx: Any,
) -> dict[str, Any]:
    """Dispatch a task to the capability — local or across the A2A boundary.

    If capability.peer_endpoint is set, routes via A2A send_message.
    Otherwise executes the agent module in-process via sandbox.
    """
    if capability.peer_endpoint:
        return await self._execute_via_a2a(capability, request, ctx)
    # Existing local dispatch (unchanged)
    return await self._execute_local(capability, request, ctx)


async def _execute_via_a2a(
    self,
    capability: "AgentCapability",
    request: dict[str, Any],
    ctx: Any,
) -> dict[str, Any]:
    """Route task to peer agent via A2A JSON-RPC send_message.

    Constructs an A2A Message from the request dict, sends to
    capability.peer_endpoint, and returns the resulting Task.

    The peer's SA email is looked up from config/a2a/peers.yaml by URL;
    auth is fail-open if the peer is not configured (spike posture).
    """
    from lib.a2a.client import send_message

    # Build A2A Message from orchestrator request
    # The spec (§7.6.1) requires at least `parts` with one text entry.
    intent_text = request.get("intent", "") or request.get("text", "") or str(request)
    message = {
        "role": "USER",
        "parts": [{"text": intent_text}],
        "metadata": {
            "orchestrator_request_id": request.get("id", ""),
            "capability_name": capability.name if hasattr(capability, "name") else "",
        },
    }

    # agent_identity from session context if available; None = fail-open
    agent_identity = getattr(ctx, "agent_identity", None)

    task = await send_message(
        capability.peer_endpoint,
        message,
        agent_identity=agent_identity,
        timeout=30.0,
    )
    return {"status": task.get("status", "UNKNOWN"), "task_id": task.get("id"), "peer": True}
```

### 4c. `app/tests/test_peer_dispatch.py` — Acceptance tests

Create `app/tests/test_peer_dispatch.py`:

```python
"""P-3 acceptance test: peer-execution dispatch via A2A boundary.

Per INTEGRATION.md §P-3:
'When peer_endpoint is set, _execute() routes via send_message instead of in-process.'

Spike-level test: mock send_message; verify routing decision and message shape.
Full two-node test is deferred (requires live peer + real network).
"""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock


# --- Helpers -----------------------------------------------------------------

def _make_capability(peer_endpoint=None):
    """Build a minimal AgentCapability for testing."""
    from app.core.schemas import AgentCapability
    return AgentCapability(
        name="test-capability",
        description="test",
        peer_endpoint=peer_endpoint,
        # fill other required fields with defaults
    )


# --- Tests -------------------------------------------------------------------

@pytest.mark.asyncio
async def test_local_capability_does_not_call_send_message():
    """A capability without peer_endpoint takes the local dispatch path."""
    # Verify send_message is NOT called when peer_endpoint is None
    with patch("lib.a2a.client.send_message", new=AsyncMock()) as mock_send:
        cap = _make_capability(peer_endpoint=None)
        # Call _execute — implementation detail of how the orchestrator is instantiated
        # may vary; adjust import to match your orchestrator class
        # ...
        mock_send.assert_not_called()


@pytest.mark.asyncio
async def test_peer_capability_calls_send_message_with_correct_url():
    """A capability with peer_endpoint calls send_message with that URL."""
    peer_url = "http://agent-canary:9001/"
    fake_task = {"id": "task-peer-001", "status": "SUBMITTED"}

    with patch("lib.a2a.client.send_message", new=AsyncMock(return_value=fake_task)) as mock_send:
        cap = _make_capability(peer_endpoint=peer_url)
        # Invoke _execute_via_a2a directly for the spike test
        from app.core.orchestrator import SeedOrchestrator  # adjust import
        orch = SeedOrchestrator(...)
        result = await orch._execute_via_a2a(cap, {"intent": "compute delta-v"}, ctx=MagicMock())

    mock_send.assert_called_once()
    call_args = mock_send.call_args
    assert call_args.args[0] == peer_url or call_args.kwargs.get("peer_url") == peer_url
    assert result["peer"] is True
    assert result["task_id"] == "task-peer-001"


@pytest.mark.asyncio
async def test_a2a_message_contains_intent_text():
    """The A2A message sent to the peer contains the intent from the request."""
    peer_url = "http://agent-canary:9001/"
    fake_task = {"id": "task-peer-002", "status": "SUBMITTED"}
    captured_messages = []

    async def _capture_send(url, message, **kw):
        captured_messages.append(message)
        return fake_task

    with patch("lib.a2a.client.send_message", side_effect=_capture_send):
        cap = _make_capability(peer_endpoint=peer_url)
        from app.core.orchestrator import SeedOrchestrator  # adjust import
        orch = SeedOrchestrator(...)
        await orch._execute_via_a2a(cap, {"intent": "find optimal policy"}, ctx=MagicMock())

    assert len(captured_messages) == 1
    msg = captured_messages[0]
    assert "find optimal policy" in msg["parts"][0]["text"]
    assert msg["role"] == "USER"
```

---

## 5. Key Constraints

| Rule | Detail |
|------|--------|
| Do NOT touch `lib/a2a/` | Claude Code owns that directory. Call `lib.a2a.client.send_message` via import but don't modify client.py |
| Preserve existing ABCs | `AbstractMemoryStore`, `AbstractSandbox`, `AbstractEmbedder`, `AbstractMoERouter` must stay exactly as written in `04-gcp-native-adapter-plan.md` |
| Keep `AgentCapability` backward-compatible | `peer_endpoint` defaults to `None`; no breaking change for local-only setups |
| Fail-open auth | Pass `agent_identity=None` if the orchestrator doesn't carry identity yet; auth will send unauthenticated (sprint posture) |
| No `git add -A` | Stage specific files only |
| Branch naming | `feat/p3-a2a-peer-dispatch` |
| PR title | `feat(app): P-3 a2a peer-execution dispatch — route to peer via send_message` |

---

## 6. Acceptance Criteria

```bash
# All of these must pass:

# 1. AgentCapability has peer_endpoint field
python3 -c "from app.core.schemas import AgentCapability; c = AgentCapability(name='t', description='t', peer_endpoint='http://peer:9001/'); assert c.peer_endpoint == 'http://peer:9001/'; print('OK')"

# 2. Tests pass
uv run pytest app/tests/test_peer_dispatch.py -v

# 3. Existing tests unaffected
uv run pytest app/tests/ -v

# 4. No lib/a2a/ files modified
git diff --name-only main | grep "lib/a2a" && echo "ERROR: should not touch lib/a2a" || echo "CLEAN"

# 5. PR open and CI green
gh pr checks --watch
```
