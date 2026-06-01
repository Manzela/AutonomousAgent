"""SP-14: Tests for F34 LoopDetector + F35 StallDetector wiring in post_tool_call,
and TrajectoryShipper.ship_trajectory call from on_session_end.

Coverage matrix (7 assertions + marker round-trip + detector-failure logging = 9 tests):
  1. Looping session trips F34 (positive)
  2. Non-looping session does NOT trip F34 (negative)
  3. Stalled session trips F35 (positive) — via event-driven check() call site
  4. Healthy/progressing session does NOT trip F35 (negative)
  5. Over-budget session vetoed (positive — via existing kanban pre_tool_call hook)
  6. Under-budget session NOT vetoed (negative — existing hook pass-through)
  7. Completed session ships trajectory containing a planted unique marker event
     (marker round-trip — real shipper, in-memory fake sanitize+GCS)
  8. (structural) register adds on_session_end hook
  9. Broken detector: first failure logs WARNING, subsequent failures log DEBUG, hook never raises

Design notes:
- LoopDetector + StallDetector are tested via the WIRED durability hook
  (_sp14_detect_on_tool_call) so this proves the call site exists and dispatches,
  not just that the detector classes work in isolation (those are in
  tests/unit/test_runtime_detectors.py).
- TrajectoryShipper is tested with a fake sanitize client (returns payload verbatim)
  and a fake GCS client (captures uploaded bytes), so the marker round-trip asserts
  on real shipped JSON, not just call counts.
- Budget veto asserts existing behaviour from lib.kanban.__init__._on_pre_tool_call
  which is ALREADY wired and tested — we drive it here from tests to prove the
  contract is in place (PRD says "do not re-implement", just assert the existing path).
- StallDetector periodic watchdog (.check() on a timer) is OUT OF SCOPE for SP-14;
  only the event-driven call at post_tool_call is wired here (SP-27 deferred note).
"""

from __future__ import annotations

import logging
import os
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers: fake GCS and fake sanitize client
# ---------------------------------------------------------------------------


class _FakeSanitizeClient:
    """Sanitize stub: returns the content verbatim (as a bare string).

    The Persistence Trap contract allows bare strings from stubs
    (_extract_sanitized_payload handles str responses — shipper.py:87).
    """

    def sanitize(self, *, template: str, content: str) -> str:  # noqa: ARG002
        return content


class _FakeGCSBlob:
    def __init__(self) -> None:
        self.uploaded: bytes | None = None

    def upload_from_string(self, data: str | bytes, content_type: str = "") -> None:  # noqa: ARG002
        self.uploaded = data if isinstance(data, bytes) else data.encode("utf-8")


class _FakeGCSBucket:
    def __init__(self) -> None:
        self.blobs: dict[str, _FakeGCSBlob] = {}

    def blob(self, name: str) -> _FakeGCSBlob:
        b = _FakeGCSBlob()
        self.blobs[name] = b
        return b


class _FakeGCSClient:
    def __init__(self) -> None:
        self.buckets: dict[str, _FakeGCSBucket] = {}

    def bucket(self, name: str) -> _FakeGCSBucket:
        if name not in self.buckets:
            self.buckets[name] = _FakeGCSBucket()
        return self.buckets[name]


# ---------------------------------------------------------------------------
# Helpers: minimal hook-context stub
# ---------------------------------------------------------------------------


class _Ctx:
    """Minimal plugin registration context (mirrors Hermes' PluginContext)."""

    def __init__(self) -> None:
        self._hooks: dict[str, list] = {}

    def register_hook(self, name: str, fn) -> None:  # noqa: ANN001
        self._hooks.setdefault(name, []).append(fn)

    def invoke(self, name: str, **kwargs: Any) -> list:
        results = []
        for cb in self._hooks.get(name, []):
            results.append(cb(**kwargs))
        return results


# ---------------------------------------------------------------------------
# F34 LoopDetector wiring tests (1 positive + 1 negative)
# ---------------------------------------------------------------------------


class TestLoopDetectorWiring:
    """F34 LoopDetector is called from the post_tool_call hook."""

    def _make_wired_durability(self):
        """Register the durability plugin and return (ctx, loop_detector, stall_detector)."""
        # We import lazily inside the method so monkeypatching in each test
        # doesn't bleed between test classes.
        import lib.durability as dur
        from lib.durability.runtime_detectors import LoopDetector, StallDetector

        loop_det = LoopDetector(threshold=3)
        stall_det = StallDetector(idle_timeout_s=300)

        ctx = _Ctx()
        # Patch the module-level singletons that register() will wire
        with (
            patch.object(dur, "_SP14_LOOP_DETECTOR", loop_det, create=True),
            patch.object(dur, "_SP14_STALL_DETECTOR", stall_det, create=True),
        ):
            dur.register(ctx)

        return ctx, loop_det, stall_det

    def test_looping_session_trips_f34(self, tmp_path, monkeypatch):
        """A session that repeats the same tool call N times trips F34."""
        import lib.durability as dur
        from lib.durability.runtime_detectors import LoopDetector, StallDetector

        fired_codes: list[str] = []

        loop_det = LoopDetector(threshold=3)
        stall_det = StallDetector(idle_timeout_s=300)
        ctx = _Ctx()

        monkeypatch.setattr(dur, "_SP14_LOOP_DETECTOR", loop_det, raising=False)
        monkeypatch.setattr(dur, "_SP14_STALL_DETECTOR", stall_det, raising=False)

        # Patch dispatch so we don't need the full handler stack
        with patch("lib.durability._sp14_dispatch_f_code") as mock_dispatch:
            mock_dispatch.side_effect = lambda code, **kw: fired_codes.append(code)
            dur.register(ctx)

            for i in range(3):
                ctx.invoke(
                    "post_tool_call",
                    tool_name="Bash",
                    args={"command": "ls"},
                    result="ok",
                    session_id="loop-sess",
                    tool_call_id=f"call-{i}",
                    task_id="t1",
                    duration_ms=10.0,
                )

        assert "F34" in fired_codes, f"Expected F34 in fired codes, got {fired_codes}"

    def test_non_looping_session_does_not_trip_f34(self, monkeypatch):
        """A session that alternates tool calls never trips F34."""
        import lib.durability as dur
        from lib.durability.runtime_detectors import LoopDetector, StallDetector

        fired_codes: list[str] = []

        loop_det = LoopDetector(threshold=3)
        stall_det = StallDetector(idle_timeout_s=300)
        ctx = _Ctx()

        monkeypatch.setattr(dur, "_SP14_LOOP_DETECTOR", loop_det, raising=False)
        monkeypatch.setattr(dur, "_SP14_STALL_DETECTOR", stall_det, raising=False)

        with patch("lib.durability._sp14_dispatch_f_code") as mock_dispatch:
            mock_dispatch.side_effect = lambda code, **kw: fired_codes.append(code)
            dur.register(ctx)

            tool_calls = [
                ("Bash", {"command": "ls"}),
                ("Read", {"file_path": "/tmp/foo"}),
                ("Bash", {"command": "pwd"}),
                ("Edit", {"file_path": "/tmp/bar", "old_string": "x", "new_string": "y"}),
            ]
            for i, (tool_name, args) in enumerate(tool_calls):
                ctx.invoke(
                    "post_tool_call",
                    tool_name=tool_name,
                    args=args,
                    result="ok",
                    session_id="noloop-sess",
                    tool_call_id=f"call-{i}",
                    task_id="t1",
                    duration_ms=10.0,
                )

        assert "F34" not in fired_codes, f"Unexpected F34 in {fired_codes}"


# ---------------------------------------------------------------------------
# F35 StallDetector wiring tests (1 positive + 1 negative)
# ---------------------------------------------------------------------------


class _FakeClock:
    def __init__(self, t: float = 1000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, delta: float) -> None:
        self.t += delta


class TestStallDetectorWiring:
    """F35 StallDetector event-driven check() is wired into post_tool_call."""

    def test_stalled_session_trips_f35(self, monkeypatch):
        """Session with no activity for > timeout fires F35 on next post_tool_call."""
        import lib.durability as dur
        from lib.durability.runtime_detectors import LoopDetector, StallDetector

        fired_codes: list[str] = []
        clk = _FakeClock()

        loop_det = LoopDetector(threshold=5)
        stall_det = StallDetector(idle_timeout_s=30, clock=clk)
        ctx = _Ctx()

        monkeypatch.setattr(dur, "_SP14_LOOP_DETECTOR", loop_det, raising=False)
        monkeypatch.setattr(dur, "_SP14_STALL_DETECTOR", stall_det, raising=False)

        with patch("lib.durability._sp14_dispatch_f_code") as mock_dispatch:
            mock_dispatch.side_effect = lambda code, **kw: fired_codes.append(code)
            dur.register(ctx)

            # First tool call: marks session active
            ctx.invoke(
                "post_tool_call",
                tool_name="Bash",
                args={"command": "ls"},
                result="ok",
                session_id="stall-sess",
                tool_call_id="call-0",
                task_id="t1",
                duration_ms=10.0,
            )

            # Advance clock past idle timeout
            clk.advance(31)

            # Second tool call: stall_det.check() should return F35
            ctx.invoke(
                "post_tool_call",
                tool_name="Bash",
                args={"command": "pwd"},
                result="ok",
                session_id="stall-sess",
                tool_call_id="call-1",
                task_id="t1",
                duration_ms=5.0,
            )

        assert "F35" in fired_codes, f"Expected F35 in fired codes, got {fired_codes}"

    def test_healthy_progressing_session_does_not_trip_f35(self, monkeypatch):
        """A session with regular activity never trips F35."""
        import lib.durability as dur
        from lib.durability.runtime_detectors import LoopDetector, StallDetector

        fired_codes: list[str] = []
        clk = _FakeClock()

        loop_det = LoopDetector(threshold=10)
        stall_det = StallDetector(idle_timeout_s=30, clock=clk)
        ctx = _Ctx()

        monkeypatch.setattr(dur, "_SP14_LOOP_DETECTOR", loop_det, raising=False)
        monkeypatch.setattr(dur, "_SP14_STALL_DETECTOR", stall_det, raising=False)

        with patch("lib.durability._sp14_dispatch_f_code") as mock_dispatch:
            mock_dispatch.side_effect = lambda code, **kw: fired_codes.append(code)
            dur.register(ctx)

            # Simulate 5 tool calls each arriving within 10s of the last
            for i in range(5):
                clk.advance(5)  # 5s between calls — well under 30s timeout
                ctx.invoke(
                    "post_tool_call",
                    tool_name="Read",
                    args={"file_path": f"/tmp/file{i}"},
                    result="content",
                    session_id="healthy-sess",
                    tool_call_id=f"call-{i}",
                    task_id="t1",
                    duration_ms=3.0,
                )

        assert "F35" not in fired_codes, f"Unexpected F35 in {fired_codes}"


# ---------------------------------------------------------------------------
# Budget veto tests (1 positive + 1 negative)
# These assert the EXISTING pre_tool_call veto path — do NOT re-implement it.
# Two call sites: lib.kanban raises BudgetExhaustedError; lib.anchors returns a block dict.
# ---------------------------------------------------------------------------


class TestBudgetVeto:
    def test_over_budget_raises_budget_exhausted_error(self, tmp_path):
        """Over-budget session: kanban's pre_tool_call raises BudgetExhaustedError when
        HALT_F21 sentinel file is present."""
        from lib.kanban import BudgetExhaustedError, _on_pre_tool_call

        sentinel = tmp_path / "HALT_F21"
        sentinel.write_text("halted")

        with patch.dict(os.environ, {"HALT_SENTINEL_PATH": str(sentinel)}):
            # kanban checks /data/HALT_F21 by default; it uses os.path.exists
            # We mock the existence check using monkeypatch of the path the kanban
            # hook reads (it hardcodes os.path.exists("/data/HALT_F21"))
            with patch("os.path.exists", side_effect=lambda p: p == "/data/HALT_F21"):
                with pytest.raises(BudgetExhaustedError):
                    _on_pre_tool_call(
                        tool_name="Bash",
                        args={"command": "ls"},
                        session_id="over-budget-sess",
                        tool_call_id="call-0",
                        task_id="t1",
                    )

    def test_under_budget_not_vetoed(self, tmp_path):
        """Under-budget session (no HALT sentinel): kanban's pre_tool_call returns None."""
        from lib.kanban import _on_pre_tool_call

        # Ensure no sentinel exists
        with patch("os.path.exists", return_value=False):
            # patch telegram_bridge so no real Telegram call fires
            with patch("lib.kanban.telegram_bridge.telegram_msg_to_card", return_value="card-1"):
                result = _on_pre_tool_call(
                    tool_name="Bash",
                    args={"command": "ls"},
                    session_id="under-budget-sess-unique-abc123",
                    tool_call_id="call-0",
                    task_id="t1",
                )
        assert result is None, f"Expected None from under-budget hook, got {result!r}"


# ---------------------------------------------------------------------------
# Trajectory marker round-trip (1 test)
# ---------------------------------------------------------------------------


class TestTrajectoryMarkerRoundTrip:
    """A completed session ships a trajectory containing a planted unique marker event."""

    def test_completed_session_ships_marker_event(self, monkeypatch):
        """on_session_end ships a trajectory; the marker event is present in shipped bytes."""
        import lib.durability as dur
        from lib.durability.runtime_detectors import LoopDetector, StallDetector
        from lib.trajectory.shipper import TrajectoryShipper

        MARKER = "SP14-PLANTED-CANARY-TOKEN-2026-sp14"

        fake_sanitize = _FakeSanitizeClient()
        fake_gcs = _FakeGCSClient()

        shipper = TrajectoryShipper(
            bucket="test-traj-bucket",
            template="test-template",
            sanitize_client=fake_sanitize,
            gcs_client=fake_gcs,
        )

        loop_det = LoopDetector(threshold=5)
        stall_det = StallDetector(idle_timeout_s=300)
        ctx = _Ctx()

        monkeypatch.setattr(dur, "_SP14_LOOP_DETECTOR", loop_det, raising=False)
        monkeypatch.setattr(dur, "_SP14_STALL_DETECTOR", stall_det, raising=False)
        monkeypatch.setattr(dur, "_SP14_TRAJECTORY_SHIPPER", shipper, raising=False)

        # Suppress dispatch so F-codes don't try to reach real handlers
        with patch("lib.durability._sp14_dispatch_f_code"):
            dur.register(ctx)

            # Simulate tool calls — one carries the marker
            ctx.invoke(
                "post_tool_call",
                tool_name="Read",
                args={"file_path": "/src/main.py"},
                result="content",
                session_id="marker-sess",
                tool_call_id="call-0",
                task_id="t1",
                duration_ms=5.0,
            )

            ctx.invoke(
                "post_tool_call",
                tool_name="Bash",
                args={"command": f"echo {MARKER}"},
                result=MARKER,
                session_id="marker-sess",
                tool_call_id="call-1",
                task_id="t1",
                duration_ms=8.0,
            )

            # Trigger on_session_end → ship_trajectory
            ctx.invoke(
                "on_session_end",
                session_id="marker-sess",
            )

        # Find the shipped blob for this session
        traj_bucket = fake_gcs.buckets.get("test-traj-bucket")
        assert traj_bucket is not None, "No bucket written — ship_trajectory was not called"

        # The expected GCS key for ship_trajectory
        expected_key = "malt/trajectory/marker-sess.json"
        assert (
            expected_key in traj_bucket.blobs
        ), f"Expected blob key {expected_key!r} not found in {list(traj_bucket.blobs.keys())}"

        blob = traj_bucket.blobs[expected_key]
        assert blob.uploaded is not None, "Blob has no uploaded content"

        shipped_text = blob.uploaded.decode("utf-8")
        assert (
            MARKER in shipped_text
        ), f"Planted marker {MARKER!r} not found in shipped trajectory:\n{shipped_text[:500]}"


# ---------------------------------------------------------------------------
# Register hook-names test (structural)
# ---------------------------------------------------------------------------


class TestSP14RegisterHooks:
    def test_register_adds_on_session_end_hook(self, monkeypatch):
        """SP-14 wiring registers an on_session_end hook for trajectory shipping."""
        import lib.durability as dur
        from lib.durability.runtime_detectors import LoopDetector, StallDetector

        loop_det = LoopDetector(threshold=5)
        stall_det = StallDetector(idle_timeout_s=300)

        monkeypatch.setattr(dur, "_SP14_LOOP_DETECTOR", loop_det, raising=False)
        monkeypatch.setattr(dur, "_SP14_STALL_DETECTOR", stall_det, raising=False)

        ctx_mock = MagicMock()
        dur.register(ctx_mock)

        hook_names = [call.args[0] for call in ctx_mock.register_hook.call_args_list]
        assert (
            "on_session_end" in hook_names
        ), f"Expected on_session_end in registered hooks: {hook_names}"


# ---------------------------------------------------------------------------
# Detector failure logging: WARNING once, DEBUG thereafter, fail-open (1 test)
# ---------------------------------------------------------------------------


class TestDetectorFailureLogging:
    """_sp14_detect_on_tool_call: broken detector logs WARNING first, DEBUG thereafter,
    and never raises (fail-open preserved)."""

    def test_broken_loop_detector_logs_warning_then_debug_never_raises(self, monkeypatch, caplog):
        """A LoopDetector that raises on every call:
        - first invocation → WARNING in caplog
        - second invocation → DEBUG in caplog (not a second WARNING)
        - the hook itself never raises (fail-open)
        """
        import lib.durability as dur
        from lib.durability.runtime_detectors import LoopDetector, StallDetector

        SESSION = "broken-loop-sess-unique-xyz"

        # Use a fresh StallDetector so the session has no stale state.
        stall_det = StallDetector(idle_timeout_s=300)

        # Create a LoopDetector whose record_tool_call always raises.
        broken_loop_det = LoopDetector(threshold=3)
        monkeypatch.setattr(
            broken_loop_det,
            "record_tool_call",
            MagicMock(side_effect=RuntimeError("injected boom")),
        )

        monkeypatch.setattr(dur, "_SP14_LOOP_DETECTOR", broken_loop_det, raising=False)
        monkeypatch.setattr(dur, "_SP14_STALL_DETECTOR", stall_det, raising=False)

        # Ensure the dedup set is clean for this session before we start.
        with dur._SP14_SESSION_EVENTS_LOCK:
            dur._SP14_DETECTOR_FAILURE_SEEN.discard((SESSION, "LoopDetector"))

        ctx = _Ctx()
        with patch("lib.durability._sp14_dispatch_f_code"):
            dur.register(ctx)

            # ---- First broken call ----
            with caplog.at_level(logging.DEBUG, logger="lib.durability"):
                caplog.clear()
                # Must not raise (fail-open contract).
                ctx.invoke(
                    "post_tool_call",
                    tool_name="Bash",
                    args={"command": "ls"},
                    result="ok",
                    session_id=SESSION,
                    tool_call_id="call-0",
                    task_id="t1",
                    duration_ms=10.0,
                )

            warning_records = [
                r
                for r in caplog.records
                if r.levelno == logging.WARNING and "LoopDetector" in r.message
            ]
            assert len(warning_records) >= 1, (
                f"Expected at least 1 WARNING for LoopDetector on first failure; "
                f"got records: {[(r.levelname, r.message) for r in caplog.records]}"
            )

            # ---- Second broken call (same session) ----
            with caplog.at_level(logging.DEBUG, logger="lib.durability"):
                caplog.clear()
                ctx.invoke(
                    "post_tool_call",
                    tool_name="Bash",
                    args={"command": "ls"},
                    result="ok",
                    session_id=SESSION,
                    tool_call_id="call-1",
                    task_id="t1",
                    duration_ms=10.0,
                )

            # Second failure must NOT emit a new WARNING for LoopDetector.
            second_warning_records = [
                r
                for r in caplog.records
                if r.levelno == logging.WARNING and "LoopDetector" in r.message
            ]
            assert len(second_warning_records) == 0, (
                f"Expected NO second WARNING for LoopDetector on repeated failure; "
                f"got: {[(r.levelname, r.message) for r in caplog.records]}"
            )

            # Second failure MUST emit a DEBUG record for LoopDetector.
            debug_records = [
                r
                for r in caplog.records
                if r.levelno == logging.DEBUG and "LoopDetector" in r.message
            ]
            assert len(debug_records) >= 1, (
                f"Expected at least 1 DEBUG for LoopDetector on repeated failure; "
                f"got records: {[(r.levelname, r.message) for r in caplog.records]}"
            )

        # Cleanup: remove dedup entries so other tests in the same process are
        # not affected if pytest reuses module-level state.
        with dur._SP14_SESSION_EVENTS_LOCK:
            dur._SP14_DETECTOR_FAILURE_SEEN.discard((SESSION, "LoopDetector"))
