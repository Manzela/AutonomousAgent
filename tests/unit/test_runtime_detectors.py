"""Unit tests for LoopDetector (F34) and StallDetector (F35).

LoopDetector tests cover:
- threshold tripping and reset semantics
- fingerprint distinguishes args (different args = different fingerprint)
- concurrent record_tool_call from multiple threads
- per-session isolation
- reset wipes state

StallDetector tests cover:
- idle-timeout firing
- record_activity re-arms the detector
- set_task_state(in_progress=False) suppresses firing
- fired-once semantics until re-armed
- per-session isolation
"""

from __future__ import annotations

import threading

import pytest

from lib.durability.runtime_detectors import (
    DEFAULT_LOOP_THRESHOLD,
    DEFAULT_STALL_IDLE_TIMEOUT_S,
    LoopDetector,
    StallDetector,
    _fingerprint,
)


# ---------- _fingerprint --------------------------------------------------


def test_fingerprint_stable_across_dict_ordering():
    fp_a = _fingerprint("Bash", {"command": "ls", "timeout": 5000})
    fp_b = _fingerprint("Bash", {"timeout": 5000, "command": "ls"})
    assert fp_a == fp_b


def test_fingerprint_distinguishes_tool_and_args():
    fp_bash = _fingerprint("Bash", {"command": "ls"})
    fp_read = _fingerprint("Read", {"command": "ls"})
    fp_other_args = _fingerprint("Bash", {"command": "pwd"})
    assert fp_bash != fp_read
    assert fp_bash != fp_other_args


def test_fingerprint_handles_none_args():
    fp = _fingerprint("NoArgs", None)
    assert isinstance(fp, str) and len(fp) == 64


# ---------- LoopDetector --------------------------------------------------


def test_loop_detector_default_threshold():
    assert LoopDetector().threshold == DEFAULT_LOOP_THRESHOLD


def test_loop_detector_rejects_threshold_below_two():
    with pytest.raises(ValueError):
        LoopDetector(threshold=1)


def test_loop_detector_fires_on_threshold():
    det = LoopDetector(threshold=3)
    args = {"command": "ls"}
    assert det.record_tool_call(session_id="s", tool_name="Bash", args=args) is None
    assert det.record_tool_call(session_id="s", tool_name="Bash", args=args) is None
    assert det.record_tool_call(session_id="s", tool_name="Bash", args=args) == "F34"


def test_loop_detector_resets_after_firing():
    """After firing F34, the counter should be cleared so it takes another
    full threshold-run to fire again."""
    det = LoopDetector(threshold=2)
    args = {"command": "ls"}
    det.record_tool_call(session_id="s", tool_name="Bash", args=args)
    assert det.record_tool_call(session_id="s", tool_name="Bash", args=args) == "F34"
    # First call after reset: counter back to 1, no fire.
    assert det.record_tool_call(session_id="s", tool_name="Bash", args=args) is None


def test_loop_detector_different_call_resets_streak():
    det = LoopDetector(threshold=3)
    args_a = {"command": "ls"}
    args_b = {"command": "pwd"}
    det.record_tool_call(session_id="s", tool_name="Bash", args=args_a)
    det.record_tool_call(session_id="s", tool_name="Bash", args=args_a)
    # Different args -> streak resets to 1
    assert det.record_tool_call(session_id="s", tool_name="Bash", args=args_b) is None
    # One more of args_b = 2 of 3, still no fire
    assert det.record_tool_call(session_id="s", tool_name="Bash", args=args_b) is None
    # Third of args_b = fire
    assert det.record_tool_call(session_id="s", tool_name="Bash", args=args_b) == "F34"


def test_loop_detector_per_session_isolation():
    det = LoopDetector(threshold=3)
    args = {"command": "ls"}
    # Session A approaches threshold
    det.record_tool_call(session_id="A", tool_name="Bash", args=args)
    det.record_tool_call(session_id="A", tool_name="Bash", args=args)
    # Session B's first call should not be affected
    assert det.record_tool_call(session_id="B", tool_name="Bash", args=args) is None
    # Session A's third call should still fire
    assert det.record_tool_call(session_id="A", tool_name="Bash", args=args) == "F34"


def test_loop_detector_reset_clears_session():
    det = LoopDetector(threshold=3)
    args = {"command": "ls"}
    det.record_tool_call(session_id="s", tool_name="Bash", args=args)
    det.record_tool_call(session_id="s", tool_name="Bash", args=args)
    det.reset(session_id="s")
    # Fresh start: counter is 0 again.
    fp, n = det.snapshot(session_id="s")
    assert (fp, n) == (None, 0)


def test_loop_detector_reset_on_unknown_session_is_noop():
    """reset() must not raise on never-seen session ids."""
    LoopDetector().reset(session_id="never-seen")  # should not raise


def test_loop_detector_concurrent_records_no_race():
    """100 threads each calling 5 times should yield deterministic counts.

    All threads use the same session + same fingerprint so the global
    consecutive count would be 500 — but with the reset-on-fire semantics
    we expect to see exactly floor(500/threshold) fires.
    """
    det = LoopDetector(threshold=5)
    args = {"x": 1}
    fire_count = [0]
    lock = threading.Lock()

    def run() -> None:
        for _ in range(5):
            r = det.record_tool_call(session_id="s", tool_name="T", args=args)
            if r == "F34":
                with lock:
                    fire_count[0] += 1

    threads = [threading.Thread(target=run) for _ in range(100)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # 500 calls / threshold 5 = exactly 100 fires.
    assert fire_count[0] == 100


# ---------- StallDetector -------------------------------------------------


class _FakeClock:
    """Manual clock for deterministic time-based tests."""

    def __init__(self, t0: float = 1000.0):
        self.t = t0

    def __call__(self) -> float:
        return self.t

    def advance(self, delta_s: float) -> None:
        self.t += delta_s


def test_stall_detector_default_timeout():
    assert StallDetector().idle_timeout_s == DEFAULT_STALL_IDLE_TIMEOUT_S


def test_stall_detector_rejects_zero_timeout():
    with pytest.raises(ValueError):
        StallDetector(idle_timeout_s=0)


def test_stall_detector_no_state_returns_none():
    det = StallDetector(idle_timeout_s=10)
    assert det.check(session_id="never-recorded") is None


def test_stall_detector_fresh_activity_does_not_fire():
    clk = _FakeClock()
    det = StallDetector(idle_timeout_s=10, clock=clk)
    det.record_activity(session_id="s")
    clk.advance(5)
    assert det.check(session_id="s") is None


def test_stall_detector_fires_after_timeout():
    clk = _FakeClock()
    det = StallDetector(idle_timeout_s=10, clock=clk)
    det.record_activity(session_id="s")
    clk.advance(11)
    assert det.check(session_id="s") == "F35"


def test_stall_detector_fires_once_until_rearmed():
    clk = _FakeClock()
    det = StallDetector(idle_timeout_s=10, clock=clk)
    det.record_activity(session_id="s")
    clk.advance(11)
    assert det.check(session_id="s") == "F35"
    # Still idle - no second fire
    clk.advance(5)
    assert det.check(session_id="s") is None
    # Re-arm via activity, then idle again - fires again
    det.record_activity(session_id="s")
    clk.advance(11)
    assert det.check(session_id="s") == "F35"


def test_stall_detector_in_progress_false_suppresses():
    clk = _FakeClock()
    det = StallDetector(idle_timeout_s=10, clock=clk)
    det.record_activity(session_id="s")
    det.set_task_state(session_id="s", in_progress=False)
    clk.advance(100)
    assert det.check(session_id="s") is None


def test_stall_detector_set_task_state_re_enables():
    clk = _FakeClock()
    det = StallDetector(idle_timeout_s=10, clock=clk)
    det.record_activity(session_id="s")
    det.set_task_state(session_id="s", in_progress=False)
    clk.advance(100)
    assert det.check(session_id="s") is None
    # Re-enable; with elapsed > timeout, next check fires
    det.set_task_state(session_id="s", in_progress=True)
    assert det.check(session_id="s") == "F35"


def test_stall_detector_per_session_isolation():
    clk = _FakeClock()
    det = StallDetector(idle_timeout_s=10, clock=clk)
    det.record_activity(session_id="A")
    clk.advance(5)
    det.record_activity(session_id="B")
    clk.advance(6)
    # A has been idle 11s total -> F35; B has been idle 6s -> None
    assert det.check(session_id="A") == "F35"
    assert det.check(session_id="B") is None


def test_stall_detector_reset_clears_state():
    clk = _FakeClock()
    det = StallDetector(idle_timeout_s=10, clock=clk)
    det.record_activity(session_id="s")
    clk.advance(11)
    det.reset(session_id="s")
    # After reset, no state -> no fire
    assert det.check(session_id="s") is None
