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
    DEFAULT_CONTEXT_WARN_THRESHOLD,
    DEFAULT_LOOP_THRESHOLD,
    DEFAULT_STALL_IDLE_TIMEOUT_S,
    ContextUsageDetector,
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


# ---------- ContextUsageDetector (F36 / J9) -------------------------------


def test_context_detector_default_threshold():
    assert ContextUsageDetector().warn_threshold == DEFAULT_CONTEXT_WARN_THRESHOLD


def test_context_detector_rejects_threshold_out_of_range():
    with pytest.raises(ValueError):
        ContextUsageDetector(warn_threshold=0.0)
    with pytest.raises(ValueError):
        ContextUsageDetector(warn_threshold=1.5)
    with pytest.raises(ValueError):
        ContextUsageDetector(warn_threshold=-0.1)


def test_context_detector_does_not_fire_below_threshold():
    det = ContextUsageDetector(warn_threshold=0.9)
    assert det.record_usage(session_id="s", prompt_tokens=100, context_length=200) is None
    assert det.record_usage(session_id="s", prompt_tokens=890, context_length=1000) is None


def test_context_detector_fires_on_crossing():
    det = ContextUsageDetector(warn_threshold=0.9)
    # 0.95 ratio -> over threshold
    assert det.record_usage(session_id="s", prompt_tokens=950, context_length=1000) == "F36"


def test_context_detector_fires_at_exact_threshold():
    """ratio == warn_threshold should count as crossing (>= not >)."""
    det = ContextUsageDetector(warn_threshold=0.9)
    assert det.record_usage(session_id="s", prompt_tokens=900, context_length=1000) == "F36"


def test_context_detector_does_not_re_fire_within_episode():
    """Once F36 fires for a session, subsequent over-threshold readings stay silent."""
    det = ContextUsageDetector(warn_threshold=0.9)
    assert det.record_usage(session_id="s", prompt_tokens=920, context_length=1000) == "F36"
    assert det.record_usage(session_id="s", prompt_tokens=950, context_length=1000) is None
    assert det.record_usage(session_id="s", prompt_tokens=980, context_length=1000) is None


def test_context_detector_rearms_when_ratio_drops():
    """A drop below threshold re-arms; the next crossing fires again."""
    det = ContextUsageDetector(warn_threshold=0.9)
    assert det.record_usage(session_id="s", prompt_tokens=950, context_length=1000) == "F36"
    # Compaction frees space -> ratio drops below threshold
    assert det.record_usage(session_id="s", prompt_tokens=400, context_length=1000) is None
    # Climbs back -> fires again
    assert det.record_usage(session_id="s", prompt_tokens=950, context_length=1000) == "F36"


def test_context_detector_per_session_isolation():
    det = ContextUsageDetector(warn_threshold=0.9)
    assert det.record_usage(session_id="A", prompt_tokens=950, context_length=1000) == "F36"
    # Session B is independent — first crossing also fires
    assert det.record_usage(session_id="B", prompt_tokens=950, context_length=1000) == "F36"
    # Each session is one-fire-per-episode in isolation
    assert det.record_usage(session_id="A", prompt_tokens=970, context_length=1000) is None
    assert det.record_usage(session_id="B", prompt_tokens=970, context_length=1000) is None


def test_context_detector_handles_zero_context_length():
    """Defensive: div-by-zero must not propagate; treat as no-data, no-fire."""
    det = ContextUsageDetector(warn_threshold=0.9)
    assert det.record_usage(session_id="s", prompt_tokens=950, context_length=0) is None
    assert det.record_usage(session_id="s", prompt_tokens=950, context_length=-100) is None


def test_context_detector_snapshot_reports_state():
    det = ContextUsageDetector(warn_threshold=0.9)
    assert det.snapshot("unknown") == (0.0, False)
    det.record_usage(session_id="s", prompt_tokens=950, context_length=1000)
    ratio, fired = det.snapshot("s")
    assert ratio == pytest.approx(0.95)
    assert fired is True


def test_context_detector_reset_clears_state():
    det = ContextUsageDetector(warn_threshold=0.9)
    det.record_usage(session_id="s", prompt_tokens=950, context_length=1000)
    det.reset(session_id="s")
    assert det.snapshot("s") == (0.0, False)
    # After reset, first crossing fires fresh
    assert det.record_usage(session_id="s", prompt_tokens=950, context_length=1000) == "F36"


# ---------- gauge emission (J9 follow-up) ---------------------------------
#
# The gauge instrument is created lazily on first ``record_usage`` call.
# Tests patch ``_get_context_usage_gauge`` directly with a MagicMock so we
# can assert the contract end-to-end without requiring the OTel metrics
# SDK to be installed in the unit-test venv. The production wiring
# (lib/observability/otel_setup.setup_metrics) is exercised by the
# integration suite — see tests/integration/test_otel_metrics_setup.py.


def test_context_detector_emits_gauge_on_every_record(monkeypatch):
    """Every record_usage call sets the gauge — including below-threshold
    readings — because dashboards need a continuous view of context pressure."""
    from unittest.mock import MagicMock

    from lib.durability import runtime_detectors as rd

    fake_gauge = MagicMock()
    monkeypatch.setattr(rd, "_get_context_usage_gauge", lambda: fake_gauge)

    det = ContextUsageDetector(warn_threshold=0.9)
    det.record_usage(session_id="s1", prompt_tokens=100, context_length=1000)  # 0.10
    det.record_usage(session_id="s1", prompt_tokens=500, context_length=1000)  # 0.50
    det.record_usage(session_id="s1", prompt_tokens=950, context_length=1000)  # 0.95 (fires F36)

    # All three readings emit the gauge.
    assert fake_gauge.set.call_count == 3
    expected_ratios = [0.10, 0.50, 0.95]
    for call, expected in zip(fake_gauge.set.call_args_list, expected_ratios):
        # Positional arg 0 is the value.
        assert call.args[0] == pytest.approx(expected)
        # Attributes kwargs must carry session.id (lower-case OTel convention).
        assert call.kwargs.get("attributes", {}).get("session.id") == "s1"


def test_context_detector_emits_gauge_even_after_firing(monkeypatch):
    """Once F36 fires the detector stays silent on the dispatch path, but
    the gauge must KEEP recording so operators see whether the pressure
    is climbing further or being relieved."""
    from unittest.mock import MagicMock

    from lib.durability import runtime_detectors as rd

    fake_gauge = MagicMock()
    monkeypatch.setattr(rd, "_get_context_usage_gauge", lambda: fake_gauge)

    det = ContextUsageDetector(warn_threshold=0.9)
    assert det.record_usage(session_id="s", prompt_tokens=950, context_length=1000) == "F36"
    # Subsequent over-threshold readings — F36 stays silent (already fired)
    # but the gauge MUST still record the new ratio.
    assert det.record_usage(session_id="s", prompt_tokens=970, context_length=1000) is None
    assert det.record_usage(session_id="s", prompt_tokens=990, context_length=1000) is None
    assert fake_gauge.set.call_count == 3


def test_context_detector_gauge_emission_swallows_exceptions(monkeypatch, caplog):
    """A broken exporter must NOT propagate up and break the detector
    (the gauge is observability, not control)."""
    from unittest.mock import MagicMock

    from lib.durability import runtime_detectors as rd

    broken_gauge = MagicMock()
    broken_gauge.set.side_effect = RuntimeError("exporter offline")
    monkeypatch.setattr(rd, "_get_context_usage_gauge", lambda: broken_gauge)

    det = ContextUsageDetector(warn_threshold=0.9)
    # Must not raise.
    result = det.record_usage(session_id="s", prompt_tokens=950, context_length=1000)
    # F36 still fires correctly — gauge failure is decoupled.
    assert result == "F36"


def test_context_detector_no_gauge_when_sdk_unavailable(monkeypatch):
    """When _get_context_usage_gauge returns None (SDK missing) the detector
    must continue to work as a pure in-memory state machine."""
    from lib.durability import runtime_detectors as rd

    monkeypatch.setattr(rd, "_get_context_usage_gauge", lambda: None)

    det = ContextUsageDetector(warn_threshold=0.9)
    # Below threshold — no fire
    assert det.record_usage(session_id="s", prompt_tokens=100, context_length=1000) is None
    # Crossing threshold — F36
    assert det.record_usage(session_id="s", prompt_tokens=950, context_length=1000) == "F36"
    ratio, fired = det.snapshot("s")
    assert ratio == pytest.approx(0.95)
    assert fired is True


def test_get_context_usage_gauge_caches_failure(monkeypatch):
    """If the first attempt to create the gauge fails, subsequent calls
    must return None without re-attempting (and re-logging) the import."""
    from lib.durability import runtime_detectors as rd

    # Reset module-level cache so the test starts from a clean slate.
    monkeypatch.setattr(rd, "_context_usage_gauge", None)
    monkeypatch.setattr(rd, "_gauge_init_failed", False)

    call_count = {"n": 0}

    def fake_meter_factory(*_args, **_kwargs):
        call_count["n"] += 1
        raise ImportError("opentelemetry not available in this venv")

    # Patch the module-level import path by injecting a fake metrics module.
    import sys

    fake_metrics_module = type(sys)("opentelemetry_fake")
    fake_metrics_module.get_meter = fake_meter_factory  # type: ignore[attr-defined]
    fake_opentelemetry = type(sys)("opentelemetry_fake_pkg")
    fake_opentelemetry.metrics = fake_metrics_module  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "opentelemetry", fake_opentelemetry)
    monkeypatch.setitem(sys.modules, "opentelemetry.metrics", fake_metrics_module)

    g1 = rd._get_context_usage_gauge()
    g2 = rd._get_context_usage_gauge()
    g3 = rd._get_context_usage_gauge()
    assert g1 is None and g2 is None and g3 is None
    # The factory ran exactly once — subsequent calls short-circuited on the failure cache.
    assert call_count["n"] == 1
