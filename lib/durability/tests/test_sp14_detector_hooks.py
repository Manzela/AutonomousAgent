"""SP-14 unit tests: LoopDetector and StallDetector are invoked by the durability
plugin's post_tool_call and on_session_end hooks.

Red-green proof:
- BEFORE this wiring existed, ``_sp14_detect_on_tool_call`` and
  ``_sp14_ship_on_session_end`` did not exist, so these tests would
  error with AttributeError on import.
- AFTER the wiring, the tests pass and demonstrate:
  (a) repeated identical tool calls reaching the threshold dispatch F34 via
      ``lib.durability.handlers.dispatch``;
  (b) on_session_end resets per-session state so memory doesn't leak across
      sessions.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from lib.durability import (
    _sp14_detect_on_tool_call,
    _sp14_ship_on_session_end,
)
from lib.durability.runtime_detectors import LoopDetector, StallDetector


# ---------------------------------------------------------------------------
# Fixture: isolated detector singletons so tests don't share state
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolated_detectors(monkeypatch):
    """Replace the module-level singletons with fresh instances for each test.

    We swap them out with fresh low-threshold instances so tests don't
    need to share global state.
    """
    import lib.durability as _dur_mod

    fresh_loop = LoopDetector(threshold=3)
    fresh_stall = StallDetector(idle_timeout_s=300)
    monkeypatch.setattr(_dur_mod, "_SP14_LOOP_DETECTOR", fresh_loop)
    monkeypatch.setattr(_dur_mod, "_SP14_STALL_DETECTOR", fresh_stall)
    yield fresh_loop, fresh_stall


# ---------------------------------------------------------------------------
# Tests: post_tool_call hook invokes LoopDetector
# ---------------------------------------------------------------------------


class TestLoopDetectHook:
    """_sp14_detect_on_tool_call correctly passes args to the LoopDetector."""

    def test_no_dispatch_below_threshold(self, _isolated_detectors):
        """Calling with unique args each time never fires F34."""
        with patch("lib.durability.handlers.dispatch") as mock_dispatch:
            for i in range(5):
                _sp14_detect_on_tool_call(
                    tool_name="bash",
                    args={"cmd": f"echo {i}"},
                    session_id="sess-A",
                )
            mock_dispatch.assert_not_called()

    def test_dispatch_f34_on_threshold(self, _isolated_detectors):
        """Identical tool/args calls equal to threshold dispatch F34 exactly once."""
        with patch("lib.durability.handlers.dispatch") as mock_dispatch:
            threshold = 3  # matches the isolated fixture
            for _ in range(threshold):
                _sp14_detect_on_tool_call(
                    tool_name="bash",
                    args={"cmd": "ls"},
                    session_id="sess-B",
                )
            mock_dispatch.assert_called_once()
            f_code_dispatched = mock_dispatch.call_args[0][0]
            assert f_code_dispatched == "F34"

    def test_dispatch_includes_session_and_tool(self, _isolated_detectors):
        """F34 dispatch kwargs contain session_id and tool_name."""
        with patch("lib.durability.handlers.dispatch") as mock_dispatch:
            for _ in range(3):
                _sp14_detect_on_tool_call(
                    tool_name="read_file",
                    args={"path": "/etc/hosts"},
                    session_id="sess-C",
                )
            _kwargs = mock_dispatch.call_args[1]
            assert _kwargs.get("session_id") == "sess-C"
            assert _kwargs.get("tool_name") == "read_file"

    def test_no_dispatch_when_session_id_missing(self, _isolated_detectors):
        """Hook is a no-op when session_id is absent (defensive path)."""
        with patch("lib.durability.handlers.dispatch") as mock_dispatch:
            for _ in range(10):
                _sp14_detect_on_tool_call(tool_name="bash", args={})
            mock_dispatch.assert_not_called()

    def test_no_dispatch_when_tool_name_missing(self, _isolated_detectors):
        """Hook does not fire loop detection when tool_name is absent (defensive path)."""
        with patch("lib.durability.handlers.dispatch") as mock_dispatch:
            for _ in range(10):
                _sp14_detect_on_tool_call(session_id="sess-D", args={})
            # Stall detector will run and record activity, but loop detector won't fire F34.
            mock_dispatch.assert_not_called()

    def test_stall_detector_heartbeat_on_normal_call(self, _isolated_detectors):
        """Each tool call updates the stall detector's activity timestamp."""
        fresh_loop, fresh_stall = _isolated_detectors
        with patch("lib.durability.handlers.dispatch"):
            _sp14_detect_on_tool_call(
                tool_name="bash",
                args={"cmd": "pwd"},
                session_id="sess-E",
            )
        # StallDetector should now have an entry for this session.
        # check() with a fresh session and no elapsed time must not fire.
        result = fresh_stall.check(session_id="sess-E")
        assert result is None

    def test_dispatch_does_not_raise_on_handler_error(self, _isolated_detectors):
        """Hook is fail-open: a crashing dispatch must not propagate."""
        with patch("lib.durability.handlers.dispatch", side_effect=RuntimeError("boom")):
            # Should not raise even though dispatch blows up.
            for _ in range(3):
                _sp14_detect_on_tool_call(
                    tool_name="bash",
                    args={"cmd": "ls"},
                    session_id="sess-F",
                )


# ---------------------------------------------------------------------------
# Tests: on_session_end hook resets detector state
# ---------------------------------------------------------------------------


class TestSessionEndHook:
    """_sp14_ship_on_session_end resets both detectors for the session."""

    def test_loop_state_cleared_on_session_end(self, _isolated_detectors):
        """After session end the loop detector has no state for the session."""
        fresh_loop, fresh_stall = _isolated_detectors
        # Warm up state.
        fresh_loop.record_tool_call(session_id="sess-G", tool_name="bash", args={"cmd": "ls"})
        fresh_loop.record_tool_call(session_id="sess-G", tool_name="bash", args={"cmd": "ls"})
        # Verify state exists pre-end.
        _, consecutive = fresh_loop.snapshot("sess-G")
        assert consecutive == 2

        _sp14_ship_on_session_end(session_id="sess-G")

        _, consecutive_after = fresh_loop.snapshot("sess-G")
        assert consecutive_after == 0, "LoopDetector state must be cleared on session end"

    def test_stall_state_cleared_on_session_end(self, _isolated_detectors):
        """After session end the stall detector has no state for the session."""
        fresh_loop, fresh_stall = _isolated_detectors
        fresh_stall.record_activity(session_id="sess-H")
        # State should exist.
        assert fresh_stall._sessions.get("sess-H") is not None

        _sp14_ship_on_session_end(session_id="sess-H")

        assert fresh_stall._sessions.get("sess-H") is None

    def test_noop_when_session_id_missing(self, _isolated_detectors):
        """Hook does not crash when session_id is not passed."""
        # Should silently no-op.
        _sp14_ship_on_session_end()

    def test_second_session_unaffected_by_first_end(self, _isolated_detectors):
        """Ending session X does not clear state for session Y."""
        fresh_loop, fresh_stall = _isolated_detectors
        fresh_stall.record_activity(session_id="sess-I")
        fresh_stall.record_activity(session_id="sess-J")

        _sp14_ship_on_session_end(session_id="sess-I")

        assert (
            fresh_stall._sessions.get("sess-J") is not None
        ), "Ending sess-I must not clear sess-J state"


# ---------------------------------------------------------------------------
# Integration smoke: register() wires both hooks via ctx
# ---------------------------------------------------------------------------


class TestRegisterWiresDetectorHooks:
    """register() calls ctx.register_hook for the SP-14 hooks."""

    def test_register_wires_loop_and_session_end_hooks(self):
        """register() registers _sp14_detect_on_tool_call and
        _sp14_ship_on_session_end on the provided ctx object."""
        import lib.durability as _dur_mod

        ctx = MagicMock()
        _dur_mod.register(ctx)

        registered = {
            hook_name: cb for hook_name, cb in (c.args for c in ctx.register_hook.call_args_list)
        }

        assert "post_tool_call" in registered, "post_tool_call must be registered"
        assert "on_session_end" in registered, "on_session_end must be registered"

        # Verify the SP-14 callbacks are actually registered (not just any cb).
        post_tool_call_cbs = [
            cb
            for hook_name, cb in (c.args for c in ctx.register_hook.call_args_list)
            if hook_name == "post_tool_call"
        ]
        assert _dur_mod._sp14_detect_on_tool_call in post_tool_call_cbs

        session_end_cbs = [
            cb
            for hook_name, cb in (c.args for c in ctx.register_hook.call_args_list)
            if hook_name == "on_session_end"
        ]
        assert _dur_mod._sp14_ship_on_session_end in session_end_cbs
