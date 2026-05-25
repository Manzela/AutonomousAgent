# A2A Day 7 — TaskSpec Bridge (`lib/a2a/task_bridge.py`) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement `lib/a2a/task_bridge.py` — bridge between inbound A2A tasks and the lib.anchors TaskSpec lifecycle, including status mapping and cancel dispatch.

**Architecture:** `bridge_inbound_to_taskspec` constructs a `TaskSpec` (or falls back to a local stub dataclass if `lib.anchors` import fails) from an A2A task dict and an `AgentIdentity`, attaching `acting_for["human_sub"]` as owner and the trace ID in metadata. `bridge_taskspec_status_to_a2a` maps the four `SpecStatus` literals to their A2A `TaskState` strings. Cancel dispatch delegates to the existing `_slash_cancel` path in `lib.anchors`. All logic is pure Python with no network calls — the entire test suite runs with zero GCP dependencies.

**Tech Stack:** `lib.anchors.task_spec.TaskSpec` (pydantic BaseModel), `lib.anchors.task_spec.SpecStatus`, `dataclasses` (local fallback), `typing`, `uuid`. No new pyproject.toml entries needed.

**Worktree:** `feat/a2a-day7-bridge`

**Env setup:** `uv sync --extra a2a --extra dev`

**Test command:**
```bash
uv run pytest lib/a2a/tests/test_task_bridge.py -v
```
Regression:
```bash
uv run pytest lib/a2a/tests/ -v
```

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `lib/a2a/tests/test_task_bridge.py` | **Create** | 5 acceptance tests (TDD first) |
| `lib/a2a/task_bridge.py` | **Implement** | bridge functions + mapping table + cancel dispatch |

---

## Task 1: Test scaffold + stub dataclass

**Files:**
- Create: `lib/a2a/tests/test_task_bridge.py`

- [ ] **Step 1: Create the failing test file**

Create `lib/a2a/tests/test_task_bridge.py` with this exact content:

```python
"""Tests for lib/a2a/task_bridge.py — Day 7 bridge acceptance gate.

Per spike-plan.md §Day 7 acceptance criteria:
  - bridge_inbound_to_taskspec returns a TaskSpec (or local stub) with
    correct owner, metadata, and trace_id
  - All 4 SpecStatus -> TaskState mappings are correct and exhaustive
  - bridge_inbound then bridge_status returns the original A2A state
  - cancel dispatch delegates to /cancel slash command path (stub assert)
  - trace_id appears in TaskSpec metadata under "a2a_trace_id"
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Helpers — minimal AgentIdentity stub (Day 5 not yet merged in this branch)
# ---------------------------------------------------------------------------


@dataclass
class _AgentIdentity:
    """Minimal AgentIdentity stand-in for bridge tests.

    Day 5 auth.py will expose the real AgentIdentity; tests import this local
    stub to avoid a circular dev dependency on the auth branch.
    """

    sub: str
    audience: str
    acting_for: dict[str, str] = field(default_factory=dict)
    expiry: float = 9999999999.0


_IDENTITY = _AgentIdentity(
    sub="agent-a@autonomous-agent-2026.iam.gserviceaccount.com",
    audience="agent-b@autonomous-agent-2026.iam.gserviceaccount.com",
    acting_for={
        "human_sub": "pseudonym:user-001",
        "human_session_id": "sess-abc",
        "consent_scope": "read:trajectories",
    },
)

_A2A_TASK = {
    "id": "task-abc123",
    "status": "SUBMITTED",
    "metadata": {},
}

_TRACE_ID = "4bf92f3577b34da6a3ce929d0e0e4736"


# ---------------------------------------------------------------------------
# Imports under test (must exist before Step 2+ tests can pass)
# ---------------------------------------------------------------------------

from lib.a2a.task_bridge import (  # noqa: E402
    SpecStatus,
    TaskSpec,
    bridge_inbound_to_taskspec,
    bridge_taskspec_status_to_a2a,
    cancel_dispatch,
)


# ---------------------------------------------------------------------------
# Test 1 — bridge_inbound_creates_taskspec
# ---------------------------------------------------------------------------


def test_bridge_inbound_creates_taskspec() -> None:
    """bridge_inbound_to_taskspec returns a TaskSpec-like object with correct
    owner set from acting_for['human_sub'] and a2a_task_id in metadata.
    """
    spec = bridge_inbound_to_taskspec(_A2A_TASK, _IDENTITY)

    # Owner must come from acting_for["human_sub"]
    assert spec.owner == "pseudonym:user-001", (
        f"expected owner 'pseudonym:user-001', got {spec.owner!r}"
    )
    # metadata must contain the originating A2A task id
    assert spec.metadata.get("a2a_task_id") == "task-abc123", (
        f"metadata missing a2a_task_id: {spec.metadata}"
    )
    # spec must have an id (non-empty string or UUID)
    assert spec.id, "TaskSpec.id must be non-empty"


# ---------------------------------------------------------------------------
# Test 2 — mapping table completeness
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "spec_status,expected_a2a_state",
    [
        ("draft", "SUBMITTED"),
        ("draft_locked", "WORKING"),
        ("locked", "WORKING"),
        ("superseded", "CANCELED"),
    ],
)
def test_mapping_table_completeness(spec_status: str, expected_a2a_state: str) -> None:
    """All 4 SpecStatus values map to the correct A2A TaskState string."""
    # Build a minimal TaskSpec-like object with the given status.
    spec = bridge_inbound_to_taskspec(_A2A_TASK, _IDENTITY)
    # Override status for parametrize (works for both stub and real TaskSpec).
    if hasattr(spec, "__dataclass_fields__"):
        import dataclasses
        spec = dataclasses.replace(spec, status=spec_status)
    else:
        # pydantic BaseModel — use model_copy
        spec = spec.model_copy(update={"status": spec_status})

    result = bridge_taskspec_status_to_a2a(spec)
    assert result == expected_a2a_state, (
        f"SpecStatus.{spec_status} -> expected {expected_a2a_state!r}, got {result!r}"
    )


# ---------------------------------------------------------------------------
# Test 3 — round-trip
# ---------------------------------------------------------------------------


def test_bridge_round_trip() -> None:
    """Bridge inbound SUBMITTED task -> TaskSpec.draft -> bridge out -> 'SUBMITTED'."""
    spec = bridge_inbound_to_taskspec(_A2A_TASK, _IDENTITY)
    # A fresh inbound spec should have status='draft'
    assert spec.status == "draft", f"expected initial status 'draft', got {spec.status!r}"
    a2a_state = bridge_taskspec_status_to_a2a(spec)
    assert a2a_state == "SUBMITTED", (
        f"round-trip: draft should map to SUBMITTED, got {a2a_state!r}"
    )


# ---------------------------------------------------------------------------
# Test 4 — cancel dispatch
# ---------------------------------------------------------------------------


def test_cancel_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    """cancel_dispatch calls into the /cancel slash command path.

    The spike-plan §Day 7 says cancel dispatch should delegate to the
    lib.anchors _slash_cancel path. For the spike, we verify that
    cancel_dispatch calls the registered cancel handler (stub assert).
    """
    calls: list[str] = []

    def _fake_cancel(raw_args: str) -> str:
        calls.append(raw_args)
        return "cancelled"

    # Monkeypatch the cancel handler that task_bridge should call
    import lib.a2a.task_bridge as tb
    monkeypatch.setattr(tb, "_anchors_cancel", _fake_cancel)

    result = cancel_dispatch("task-abc123")
    assert calls == ["task-abc123"], (
        f"cancel_dispatch should pass task_id to _anchors_cancel; calls={calls!r}"
    )
    assert result == "cancelled"


# ---------------------------------------------------------------------------
# Test 5 — trace_id in TaskSpec metadata
# ---------------------------------------------------------------------------


def test_trace_id_in_taskspec_metadata() -> None:
    """bridge_inbound_to_taskspec places the trace_id in metadata['a2a_trace_id']."""
    task_with_trace = {
        **_A2A_TASK,
        "metadata": {"traceparent": f"00-{_TRACE_ID}-00f067aa0ba902b7-01"},
    }
    spec = bridge_inbound_to_taskspec(task_with_trace, _IDENTITY, trace_id=_TRACE_ID)
    assert spec.metadata.get("a2a_trace_id") == _TRACE_ID, (
        f"expected trace_id {_TRACE_ID!r} in metadata['a2a_trace_id'], "
        f"got {spec.metadata!r}"
    )
```

- [ ] **Step 2: Run tests — expect ImportError (task_bridge is empty stub)**

```bash
uv run pytest lib/a2a/tests/test_task_bridge.py -v 2>&1 | head -40
```

Expected output (test collection fails, not a test failure — that's correct TDD red):
```
E   ImportError: cannot import name 'bridge_inbound_to_taskspec' from 'lib.a2a.task_bridge'
```

- [ ] **Step 3: Commit the test scaffold**

```bash
git add lib/a2a/tests/test_task_bridge.py
git commit -m "test(a2a): add Day 7 task_bridge acceptance tests (red)"
```

---

## Task 2: Implement `bridge_inbound_to_taskspec`

**Files:**
- Modify: `lib/a2a/task_bridge.py`

- [ ] **Step 1: Write the full implementation of `lib/a2a/task_bridge.py`**

Replace the contents of `lib/a2a/task_bridge.py` with:

```python
"""A2A <-> Hermes TaskSpec bridge (Day 7).

Public API
----------
bridge_inbound_to_taskspec(a2a_task, agent_identity, *, trace_id=None) -> TaskSpec
    Creates a TaskSpec when an A2A message/send arrives.
    Owner is set from agent_identity.acting_for["human_sub"].
    trace_id (if provided) is stored in metadata["a2a_trace_id"].
    a2a_task["id"] is stored in metadata["a2a_task_id"].

bridge_taskspec_status_to_a2a(spec) -> str
    Maps SpecStatus -> A2A TaskState string.
    Mapping table (spike-plan.md §Day 7):
        draft         -> "SUBMITTED"
        draft_locked  -> "WORKING"
        locked        -> "WORKING"
        superseded    -> "CANCELED"
    Completion/failure semantics are TBD pending evaluator integration.

cancel_dispatch(task_id) -> str
    Dispatches to the /cancel slash command path in lib.anchors.
    Calls _anchors_cancel(task_id) — monkeypatchable for tests.

Spec reference: docs/specification.md §7.6 (task lifecycle), §9 (task states).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

# ---------------------------------------------------------------------------
# lib.anchors import — guarded so the spike works without the full stack
# ---------------------------------------------------------------------------
#
# lib.anchors.task_spec.TaskSpec is a pydantic BaseModel. If it is importable
# (normal dev env with `uv sync --extra a2a --extra dev` resolved), use it
# directly. If not (e.g., a bare Python env without pydantic), fall back to
# a local @dataclass stub with the same field names. Both expose:
#   .id: str   .owner: str   .status: SpecStatus   .metadata: dict

try:
    from lib.anchors.task_spec import SpecStatus, TaskSpec as _LibTaskSpec  # type: ignore[import]

    _USE_LIB_TASKSPEC = True
except Exception:  # ImportError, ValidationError at module level, etc.
    _USE_LIB_TASKSPEC = False
    _LibTaskSpec = None  # type: ignore[assignment]
    SpecStatus = str  # type: ignore[assignment,misc]  # treat as plain string in fallback


# Re-export the types so tests can import them from this module regardless of
# which path was taken above.
if not _USE_LIB_TASKSPEC:
    @dataclass
    class TaskSpec:  # type: ignore[no-redef]
        """Local fallback TaskSpec for environments without pydantic/lib.anchors."""

        id: str
        owner: str
        metadata: dict = field(default_factory=dict)
        status: str = "draft"
else:
    TaskSpec = _LibTaskSpec  # type: ignore[assignment,misc]


# ---------------------------------------------------------------------------
# Mapping table
# ---------------------------------------------------------------------------

_SPEC_STATUS_TO_A2A: dict[str, str] = {
    "draft": "SUBMITTED",
    "draft_locked": "WORKING",
    "locked": "WORKING",
    "superseded": "CANCELED",
}


# ---------------------------------------------------------------------------
# Cancel path — monkeypatchable for tests
# ---------------------------------------------------------------------------


def _anchors_cancel(raw_args: str) -> str:
    """Call lib.anchors._slash_cancel. Importable at call time (not module init)."""
    try:
        from lib.anchors import _slash_cancel  # type: ignore[import]
        return _slash_cancel(raw_args)
    except Exception as exc:
        # If anchors is unavailable (spike env), return a structured stub response.
        return f"cancel stub: {raw_args} ({exc})"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def bridge_inbound_to_taskspec(
    a2a_task: dict[str, Any],
    agent_identity: Any,
    *,
    trace_id: str | None = None,
) -> Any:
    """Convert an inbound A2A task dict into a TaskSpec.

    Args:
        a2a_task: A2A Task object dict with at least an ``"id"`` key.
        agent_identity: An AgentIdentity (real or stub) with an
            ``acting_for`` dict containing ``"human_sub"``.
        trace_id: Optional W3C trace ID (32 hex chars). Stored in
            metadata under ``"a2a_trace_id"`` when provided.

    Returns:
        A TaskSpec (or local stub) with:
          - owner = acting_for["human_sub"]
          - metadata["a2a_task_id"] = a2a_task["id"]
          - metadata["a2a_trace_id"] = trace_id (if provided)
          - status = "draft"
    """
    owner: str = (agent_identity.acting_for or {}).get("human_sub", "unknown")
    a2a_task_id: str = a2a_task.get("id", "")

    metadata: dict[str, Any] = {"a2a_task_id": a2a_task_id}
    if trace_id is not None:
        metadata["a2a_trace_id"] = trace_id

    spec_id = str(uuid.uuid4())

    if _USE_LIB_TASKSPEC:
        # Build the real pydantic TaskSpec via _draft_from_intent pattern.
        # lib.anchors.TaskSpec has mandatory fields (title, intent, …) that
        # the spike fills with placeholders — the real clarification loop will
        # overwrite them.
        from datetime import datetime, timezone
        from uuid import UUID

        from lib.anchors.task_spec import Scope  # type: ignore[import]

        return _LibTaskSpec(
            title=f"A2A inbound task {a2a_task_id[:8]}",
            intent=f"Inbound A2A task from {owner}",
            acceptance_criteria=["TBD - populated via A2A task params"],
            scope=Scope(
                in_scope=["TBD - populated via A2A task params"],
                out_of_scope=["TBD - populated via A2A task params"],
            ),
            success_metrics=["TBD - populated via A2A task params"],
            constraints=[],
            spec_id=UUID(spec_id),
            spec_sha="placeholder",
            created_at=datetime.now(timezone.utc),
            created_by=0,  # A2A inbound — no Telegram user id
            status="draft",
            # Store A2A bridge metadata via the owner field; the real metadata
            # store is a pydantic model — extra fields are forbidden. We encode
            # bridge metadata in a JSON blob convention or use a wrapper layer.
            # For the spike, store metadata in a module-level dict keyed by spec_id.
        )
    else:
        # Fallback stub path
        return TaskSpec(
            id=spec_id,
            owner=owner,
            metadata=metadata,
            status="draft",
        )


# Module-level metadata sidecar for pydantic path (spike workaround for extra="forbid").
# Maps str(spec_id) -> metadata dict. The real implementation will use a proper
# sidecar table or extend TaskSpec schema.
_spec_metadata_sidecar: dict[str, dict[str, Any]] = {}


def _get_spec_metadata(spec: Any) -> dict[str, Any]:
    """Retrieve bridge metadata for a TaskSpec from the sidecar dict."""
    key = str(getattr(spec, "spec_id", None) or getattr(spec, "id", None) or "")
    return _spec_metadata_sidecar.get(key, {})


def _set_spec_metadata(spec: Any, metadata: dict[str, Any]) -> None:
    """Store bridge metadata for a TaskSpec in the sidecar dict."""
    key = str(getattr(spec, "spec_id", None) or getattr(spec, "id", None) or "")
    _spec_metadata_sidecar[key] = metadata


def bridge_taskspec_status_to_a2a(spec: Any) -> str:
    """Map a TaskSpec's SpecStatus to an A2A TaskState string.

    Args:
        spec: A TaskSpec (pydantic model or local stub dataclass).

    Returns:
        One of: "SUBMITTED", "WORKING", "CANCELED".

    Raises:
        KeyError: if spec.status is not in the mapping table. This is
            intentional — unknown statuses must not silently produce
            an incorrect A2A state.
    """
    status: str = getattr(spec, "status", "draft")
    try:
        return _SPEC_STATUS_TO_A2A[status]
    except KeyError:
        raise KeyError(
            f"Unknown SpecStatus {status!r}. Expected one of: "
            f"{list(_SPEC_STATUS_TO_A2A)}"
        ) from None


def cancel_dispatch(task_id: str) -> str:
    """Dispatch a cancel request to the /cancel slash command path.

    Args:
        task_id: The A2A task ID to cancel (passed verbatim to _anchors_cancel).

    Returns:
        The string result from _anchors_cancel (human-readable status).
    """
    return _anchors_cancel(task_id)
```

- [ ] **Step 2: Run tests — Tests 1, 3, 5 should pass; Test 4 needs sidecar wiring; Test 2 parametrize needs dataclass path**

```bash
uv run pytest lib/a2a/tests/test_task_bridge.py -v 2>&1
```

Expected output (first run — may see 3–4 passing, 1 failing on the pydantic path's metadata):
```
PASSED lib/a2a/tests/test_task_bridge.py::test_bridge_inbound_creates_taskspec
PASSED lib/a2a/tests/test_task_bridge.py::test_mapping_table_completeness[draft-SUBMITTED]
PASSED lib/a2a/tests/test_task_bridge.py::test_mapping_table_completeness[draft_locked-WORKING]
PASSED lib/a2a/tests/test_task_bridge.py::test_mapping_table_completeness[locked-WORKING]
PASSED lib/a2a/tests/test_task_bridge.py::test_mapping_table_completeness[superseded-CANCELED]
PASSED lib/a2a/tests/test_task_bridge.py::test_bridge_round_trip
```

**If `test_bridge_inbound_creates_taskspec` fails because pydantic `TaskSpec` raises `ValidationError` (extra fields forbidden), the stub path is not being taken.** Verify with:

```bash
python -c "from lib.anchors.task_spec import TaskSpec; print('pydantic available')"
```

If pydantic is available, the pydantic branch runs. The `metadata` field is not on `TaskSpec`. Fix: route metadata through the sidecar. Update `bridge_inbound_to_taskspec` to call `_set_spec_metadata` after construction and update tests to use a `metadata` property shim:

Add to `lib/a2a/task_bridge.py` after the `bridge_inbound_to_taskspec` function, inside the `_USE_LIB_TASKSPEC` branch, right before `return`:

```python
        # Store metadata in sidecar (pydantic model forbids extra fields)
        _set_spec_metadata_by_id(spec_id, metadata)
        return _LibTaskSpec(...)  # same as before
```

And expose a helper for tests:

```python
def get_spec_metadata_for_test(spec: Any) -> dict[str, Any]:
    """Test helper — retrieve bridge metadata for a spec."""
    return _get_spec_metadata(spec)
```

Then update the two metadata-checking tests in `test_task_bridge.py` to also try `get_spec_metadata_for_test`:

```python
# In test_bridge_inbound_creates_taskspec, replace the metadata assert block:
raw_meta = spec.metadata if hasattr(spec, "metadata") and isinstance(spec.metadata, dict) else {}
if not raw_meta:
    from lib.a2a.task_bridge import get_spec_metadata_for_test
    raw_meta = get_spec_metadata_for_test(spec)
assert raw_meta.get("a2a_task_id") == "task-abc123", f"metadata missing a2a_task_id: {raw_meta}"
```

Apply the same pattern to `test_trace_id_in_taskspec_metadata`.

- [ ] **Step 3: Commit the working implementation**

```bash
git add lib/a2a/task_bridge.py
git commit -m "feat(a2a): implement task_bridge — bridge_inbound, status mapping, cancel dispatch"
```

---

## Task 3: Test 4 (cancel dispatch) + full regression + PR

**Files:**
- Modify: `lib/a2a/tests/test_task_bridge.py` (verify cancel test wiring)
- Run regression

- [ ] **Step 1: Verify all 5 bridge tests pass**

```bash
uv run pytest lib/a2a/tests/test_task_bridge.py -v
```

Expected output:
```
PASSED lib/a2a/tests/test_task_bridge.py::test_bridge_inbound_creates_taskspec
PASSED lib/a2a/tests/test_task_bridge.py::test_mapping_table_completeness[draft-SUBMITTED]
PASSED lib/a2a/tests/test_task_bridge.py::test_mapping_table_completeness[draft_locked-WORKING]
PASSED lib/a2a/tests/test_task_bridge.py::test_mapping_table_completeness[locked-WORKING]
PASSED lib/a2a/tests/test_task_bridge.py::test_mapping_table_completeness[superseded-CANCELED]
PASSED lib/a2a/tests/test_task_bridge.py::test_bridge_round_trip
PASSED lib/a2a/tests/test_task_bridge.py::test_cancel_dispatch
PASSED lib/a2a/tests/test_task_bridge.py::test_trace_id_in_taskspec_metadata

8 passed in X.XXs
```

- [ ] **Step 2: Full A2A regression**

```bash
uv run pytest lib/a2a/tests/ -v
```

Expected: all previously passing tests still pass (test_server_dispatch, test_client, test_plugin_loads) plus the 8 new bridge tests.

- [ ] **Step 3: Commit test fixes if any were needed during Step 1**

```bash
git add lib/a2a/tests/test_task_bridge.py lib/a2a/task_bridge.py
git commit -m "fix(a2a): wire sidecar metadata path for pydantic TaskSpec in task_bridge"
```

(Skip this commit if no fixes were needed.)

- [ ] **Step 4: Push and open PR**

```bash
git push -u origin feat/a2a-day7-bridge
gh pr create \
  --title "feat(a2a): day 7 task bridge — inbound->TaskSpec, status mapping, cancel dispatch" \
  --body "$(cat <<'EOF'
## Summary

- Implements `lib/a2a/task_bridge.py` (was an empty stub since Day 1)
- `bridge_inbound_to_taskspec`: maps A2A task dict + AgentIdentity → TaskSpec (pydantic real path or local stub fallback)
- `bridge_taskspec_status_to_a2a`: 4-entry mapping table (draft→SUBMITTED, draft_locked→WORKING, locked→WORKING, superseded→CANCELED)
- `cancel_dispatch`: routes to `lib.anchors._slash_cancel` (monkeypatchable)
- Adds 8 acceptance tests covering all spike-plan.md §Day 7 acceptance criteria
- No new pyproject.toml deps — uses existing `lib.anchors` and stdlib only

## Test evidence

```
uv run pytest lib/a2a/tests/test_task_bridge.py -v
8 passed in X.XXs

uv run pytest lib/a2a/tests/ -v
XX passed in X.XXs
```

## Notes

- pydantic `TaskSpec` has `extra="forbid"` — bridge metadata (a2a_task_id, a2a_trace_id) stored in a module-level sidecar dict for the spike; real impl will extend the schema or use a proper sidecar table
- completion/failure SpecStatus → A2A TaskState deferred (spike-plan §Day 7 "TBD pending evaluator integration")
EOF
)"
```

- [ ] **Step 5: Watch CI**

```bash
gh pr checks --watch
```

All checks must be green before marking this task done.
