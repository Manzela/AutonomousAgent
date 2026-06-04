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

from unittest.mock import patch

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
def test_hook_does_not_construct_checkpoint_or_write(mock_checkpoint_cls):
    """The hook no longer constructs Checkpoint or calls maybe_write (Issue #232)."""
    _p1_3_checkpoint_on_tool_call(
        session_id="sess1",
        tool_name="read_file",
        args={"path": "/etc/hosts"},
        result="ok",
        task_id="task1",
        tool_call_id="tc1",
        duration_ms=10.0,
    )
    mock_checkpoint_cls.assert_not_called()


def test_hook_returns_none_always():
    """Hooks called via Hermes ``invoke_hook`` must return None — the only
    'truthy' return signal Hermes interprets is from ``pre_tool_call``
    (block message). post_tool_call hooks have no return contract."""
    assert _p1_3_checkpoint_on_tool_call(session_id="s", tool_name="t") is None
    assert _p1_3_checkpoint_on_tool_call() is None  # no kwargs
    assert _p1_3_checkpoint_on_tool_call(session_id="s") is None  # only session


def test_hook_does_not_write_file_at_interval_via_real_checkpoint(tmp_path, monkeypatch):
    """Drive the hook with the real path configuration.
    With writing disabled, even 5 calls produce nothing on disk (Issue #232).
    """
    # Redirect _CHECKPOINT_ROOT to the tmp dir for this test
    monkeypatch.setattr("lib.durability._CHECKPOINT_ROOT", tmp_path)

    for _ in range(10):
        _p1_3_checkpoint_on_tool_call(session_id="sess-e2e", tool_name="t")

    assert not (tmp_path / "sess-e2e").exists() or not list(
        (tmp_path / "sess-e2e").glob("step-*.json")
    )
