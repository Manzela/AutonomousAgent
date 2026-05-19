"""Unit tests for `_p1_3_checkpoint_on_tool_call` — the hook that wires
`Checkpoint.maybe_write` into the live Hermes `post_tool_call` flow.

PR α-2 (Phase 1.0.1): the Checkpoint class (lib/durability/checkpoint.py) had
ZERO live callers in production code before this PR — well-tested in isolation,
never instantiated by any hook. This module verifies the wiring itself: kwargs
contract, per-session step accounting, fail-open on missing session_id, and
fail-open on Checkpoint.write OSError.

Hermes ``post_tool_call`` kwargs (see ``hermes-agent/model_tools.py`` invoke_hook
site): ``tool_name``, ``args``, ``result``, ``task_id``, ``session_id``,
``tool_call_id``, ``duration_ms``. The hook MUST accept all of these and absorb
unknown future kwargs via ``**_`` — same contract as ``trichotomy.after_tool_call``.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from lib.durability import (
    _p1_3_checkpoint_on_tool_call,
    _recent_tool_history,
    _session_step_counter,
)


@pytest.fixture(autouse=True)
def _reset_module_state():
    """Each test gets a clean per-session counter + history.

    The hook holds module-level state by design (Hermes does not give us
    per-session storage on the hook surface) — so a single test bleeding
    state into the next would cause false positives. Snapshot + restore is
    cheaper than locking the dicts.
    """
    _session_step_counter.clear()
    _recent_tool_history.clear()
    yield
    _session_step_counter.clear()
    _recent_tool_history.clear()


# ---------------------------------------------------------------------------
# Defensive paths
# ---------------------------------------------------------------------------


def test_hook_no_session_id_is_no_op():
    """Missing session_id → no crash, no counter mutation, no checkpoint."""
    out = _p1_3_checkpoint_on_tool_call(tool_name="terminal", result="ok")
    assert out is None
    assert _session_step_counter == {}
    assert _recent_tool_history == {}


def test_hook_empty_string_session_id_is_no_op():
    """Empty string session_id (sometimes synthesized by internal tool paths)
    must be treated as missing — empty string is falsy, so the truthiness
    check at the top of the hook is the right gate."""
    out = _p1_3_checkpoint_on_tool_call(session_id="", tool_name="t", result="ok")
    assert out is None
    assert _session_step_counter == {}


def test_hook_absorbs_unknown_kwargs():
    """Forward-compat: when Hermes adds new kwargs to post_tool_call (e.g.
    sender_id, trace_id, retry_count), the hook must keep returning None
    without raising — same contract that trichotomy.after_tool_call honours
    via ``**_`` (the bug PR #56 fixed)."""
    out = _p1_3_checkpoint_on_tool_call(
        session_id="s",
        tool_name="t",
        future_hermes_kwarg="future_value",
        another_unknown=42,
    )
    assert out is None
    assert _session_step_counter["s"] == 1


# ---------------------------------------------------------------------------
# Per-session step accounting
# ---------------------------------------------------------------------------


def test_hook_increments_session_step_counter():
    """Each call increments the per-session counter independently."""
    _p1_3_checkpoint_on_tool_call(session_id="s1", tool_name="t")
    _p1_3_checkpoint_on_tool_call(session_id="s1", tool_name="t")
    _p1_3_checkpoint_on_tool_call(session_id="s2", tool_name="t")
    assert _session_step_counter["s1"] == 2
    assert _session_step_counter["s2"] == 1


def test_hook_tracks_sessions_independently():
    """Five sessions, three calls each — counters never cross-pollinate."""
    for s in ("a", "b", "c", "d", "e"):
        for _ in range(3):
            _p1_3_checkpoint_on_tool_call(session_id=s, tool_name="t")
    for s in ("a", "b", "c", "d", "e"):
        assert _session_step_counter[s] == 3


def test_hook_caps_recent_tool_history():
    """The rolling history is capped at _RECENT_HISTORY_MAX=20 entries per
    session so a long-running session can't accumulate unbounded memory.
    Capping is in-place so the dict reader sees the bounded version too."""
    from lib.durability import _RECENT_HISTORY_MAX

    for i in range(_RECENT_HISTORY_MAX * 2):
        _p1_3_checkpoint_on_tool_call(
            session_id="long_sess",
            tool_name=f"tool_{i}",
            tool_call_id=f"call_{i}",
        )
    history = _recent_tool_history["long_sess"]
    assert len(history) == _RECENT_HISTORY_MAX
    # Should retain the MOST RECENT _RECENT_HISTORY_MAX entries
    assert history[-1]["tool_name"] == f"tool_{_RECENT_HISTORY_MAX * 2 - 1}"
    assert history[0]["tool_name"] == f"tool_{_RECENT_HISTORY_MAX}"


# ---------------------------------------------------------------------------
# Checkpoint.maybe_write delegation
# ---------------------------------------------------------------------------


@patch("lib.durability.checkpoint.Checkpoint")
def test_hook_calls_maybe_write_with_step_and_state(mock_checkpoint_cls):
    """The hook constructs a Checkpoint and calls .maybe_write(step, state).
    Checkpoint internally enforces the every-N-steps gate — we verify the
    *call* happens with the right step/state, not the gating logic itself
    (that lives in test_checkpoint.py).
    """
    mock_cp = MagicMock()
    mock_checkpoint_cls.return_value = mock_cp

    _p1_3_checkpoint_on_tool_call(
        session_id="sess1",
        tool_name="read_file",
        args={"path": "/etc/hosts"},
        result="ok",
        task_id="task1",
        tool_call_id="tc1",
        duration_ms=10.0,
    )
    mock_cp.maybe_write.assert_called_once()
    call_kwargs = mock_cp.maybe_write.call_args.kwargs
    assert call_kwargs["step"] == 1
    state = call_kwargs["state"]
    assert state["session_id"] == "sess1"
    assert state["task_id"] == "task1"
    assert state["last_tool_name"] == "read_file"
    assert state["last_tool_call_id"] == "tc1"
    assert isinstance(state["recent_tool_history"], list)
    assert state["recent_tool_history"][-1]["tool_name"] == "read_file"


@patch("lib.durability.checkpoint.Checkpoint")
def test_hook_constructs_checkpoint_with_session_root_and_taskspec(mock_checkpoint_cls):
    """The hook MUST pass session_id, taskspec_id, and root_dir=/data/checkpoints
    to ``Checkpoint(...)``. The taskspec_id falls back to a session-derived value
    when task_id is missing (some internal Hermes tool paths don't populate it).
    """
    mock_checkpoint_cls.return_value = MagicMock()

    # task_id supplied → used verbatim as taskspec_id
    _p1_3_checkpoint_on_tool_call(
        session_id="sess-explicit",
        tool_name="t",
        task_id="my-task-7",
    )
    init_kwargs = mock_checkpoint_cls.call_args.kwargs
    assert init_kwargs["session_id"] == "sess-explicit"
    assert init_kwargs["taskspec_id"] == "my-task-7"
    assert init_kwargs["root_dir"] == Path("/data/checkpoints")

    mock_checkpoint_cls.reset_mock()

    # task_id missing → synthesized taskspec_id ("session-<id>")
    _p1_3_checkpoint_on_tool_call(session_id="sess-anon", tool_name="t")
    init_kwargs = mock_checkpoint_cls.call_args.kwargs
    assert init_kwargs["taskspec_id"] == "session-sess-anon"


@patch("lib.durability.checkpoint.Checkpoint")
def test_hook_passes_monotonic_step_to_maybe_write(mock_checkpoint_cls):
    """The hook passes the per-session monotonic step counter so Checkpoint
    can enforce its every-N gating. Three calls → steps 1, 2, 3."""
    mock_cp = MagicMock()
    mock_checkpoint_cls.return_value = mock_cp
    for _ in range(3):
        _p1_3_checkpoint_on_tool_call(session_id="s", tool_name="t")
    steps_passed = [c.kwargs["step"] for c in mock_cp.maybe_write.call_args_list]
    assert steps_passed == [1, 2, 3]


# ---------------------------------------------------------------------------
# Fail-open contract
# ---------------------------------------------------------------------------


def test_hook_no_crash_on_checkpoint_write_failure(monkeypatch):
    """Per the durability contract, the hook MUST fail-open: any exception
    raised by Checkpoint.maybe_write (e.g. ENOSPC, permission denied) is
    swallowed at DEBUG. The counter still increments — we want to know how
    many tool calls happened even when the disk is full, so that when the
    disk recovers, the next checkpoint reflects accurate progress."""
    from lib.durability import checkpoint as cp_mod

    def boom(*a, **kw):
        raise OSError("disk full: no space left on device")

    monkeypatch.setattr(
        cp_mod,
        "Checkpoint",
        lambda **kw: type("X", (), {"maybe_write": boom})(),
    )
    # Must not raise
    out = _p1_3_checkpoint_on_tool_call(session_id="s", tool_name="t")
    assert out is None
    # Counter increments despite the write failure
    assert _session_step_counter["s"] == 1


def test_hook_returns_none_always():
    """Hooks called via Hermes ``invoke_hook`` must return None — the only
    'truthy' return signal Hermes interprets is from ``pre_tool_call``
    (block message). post_tool_call hooks have no return contract."""
    assert _p1_3_checkpoint_on_tool_call(session_id="s", tool_name="t") is None
    assert _p1_3_checkpoint_on_tool_call() is None  # no kwargs
    assert _p1_3_checkpoint_on_tool_call(session_id="s") is None  # only session


# ---------------------------------------------------------------------------
# End-to-end: cadence interaction with the real Checkpoint writer
# ---------------------------------------------------------------------------


def test_hook_writes_file_at_interval_via_real_checkpoint(tmp_path, monkeypatch):
    """Drive the hook with the REAL Checkpoint writer pointed at a tmp dir.
    With interval_steps=5 (matches limits.yaml default), 4 calls produce
    nothing on disk; the 5th call produces step-5.json. This is the unit-
    level analog of the live docker-exec verification.
    """

    # Redirect _CHECKPOINT_ROOT to the tmp dir for this test
    monkeypatch.setattr("lib.durability._CHECKPOINT_ROOT", tmp_path)

    for _ in range(4):
        _p1_3_checkpoint_on_tool_call(session_id="sess-e2e", tool_name="t")
    assert not list((tmp_path / "sess-e2e").glob("step-*.json"))

    _p1_3_checkpoint_on_tool_call(session_id="sess-e2e", tool_name="t")
    files = sorted((tmp_path / "sess-e2e").glob("step-*.json"))
    assert len(files) == 1
    assert files[0].name == "step-5.json"
