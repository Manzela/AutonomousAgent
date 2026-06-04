"""SP-IR1 — KillSwitch acceptance tests (hermetic, no Docker/LLM/network).

Oracle map:
  ①  is_active() returns False before trigger.
  ②  trigger() writes the HALT sentinel → is_active() returns True.
  ③  trigger() is idempotent: second call returns 0.0 (no-op).
  ④  clear() removes the sentinel → is_active() returns False again.
  ⑤  RecordingTokenRevoker records exactly one revoke() call after trigger.
  ⑥  trigger() calls reaper.sweep_all() (via a mock reaper).
  ⑦  SLO: trigger() completes in ≤30 s on a real path (measured).
  ⑧  make_recording() factory returns a KillSwitch with a RecordingTokenRevoker.
  ⑨  require_token_revoker() returns the injected revoker; raises when None.
  ⑩  SpineRunner.require_kill_switch() raises when kill_switch not configured.
  ⑪  SpineRunner.require_kill_switch() returns the injected KillSwitch.
  ⑫  trigger() writes a reason string into the sentinel file.
  ⑬  trigger() from two threads: exactly one owns the trigger; both see is_active()==True.
  ⑭  clear() on an already-clear sentinel is a no-op (does not raise).
  ⑮  GithubAppTokenRevoker is a subclass of AbstractTokenRevoker (no network call in test).
"""

from __future__ import annotations

import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.core.kill_switch import (
    AbstractTokenRevoker,
    GithubAppTokenRevoker,
    KillSwitch,
    RecordingTokenRevoker,
)


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture()
def sentinel(tmp_path: Path) -> Path:
    return tmp_path / "aa-kill-switch"


@pytest.fixture()
def ks(sentinel: Path) -> KillSwitch:
    return KillSwitch.make_recording(sentinel_path=sentinel)


# ── Core sentinel behaviour ───────────────────────────────────────────────────


class TestSentinel:
    def test_not_active_before_trigger(self, ks: KillSwitch) -> None:  # ①
        assert not ks.is_active()

    def test_trigger_activates_sentinel(self, ks: KillSwitch) -> None:  # ②
        ks.trigger("test")
        assert ks.is_active()

    def test_trigger_is_idempotent(self, ks: KillSwitch) -> None:  # ③
        ks.trigger("first")
        elapsed = ks.trigger("second")
        assert elapsed == 0.0
        # Still active
        assert ks.is_active()

    def test_clear_removes_sentinel(self, ks: KillSwitch) -> None:  # ④
        ks.trigger("test")
        assert ks.is_active()
        ks.clear()
        assert not ks.is_active()

    def test_trigger_writes_reason_to_sentinel(self, ks: KillSwitch, sentinel: Path) -> None:  # ⑫
        ks.trigger("runaway-loop detected")
        assert "runaway-loop detected" in sentinel.read_text()

    def test_clear_on_clear_sentinel_is_noop(self, ks: KillSwitch) -> None:  # ⑭
        assert not ks.is_active()
        ks.clear()  # must not raise
        assert not ks.is_active()


# ── Token revoker ─────────────────────────────────────────────────────────────


class TestTokenRevoker:
    def test_recording_revoker_records_call(self, ks: KillSwitch) -> None:  # ⑤
        ks.trigger("test")
        assert ks.recording_revoker.revoked
        assert ks.recording_revoker.revoke_count == 1

    def test_make_recording_returns_kill_switch_with_recording_revoker(
        self, sentinel: Path
    ) -> None:  # ⑧
        inst = KillSwitch.make_recording(sentinel_path=sentinel)
        assert isinstance(inst.recording_revoker, RecordingTokenRevoker)

    def test_require_token_revoker_returns_revoker(self, ks: KillSwitch) -> None:  # ⑨
        revoker = ks.require_token_revoker()
        assert isinstance(revoker, AbstractTokenRevoker)

    def test_require_token_revoker_raises_when_none(self, sentinel: Path) -> None:  # ⑨
        ks_no_revoker = KillSwitch(sentinel_path=sentinel)
        with pytest.raises(RuntimeError, match="Token revoker not configured"):
            ks_no_revoker.require_token_revoker()

    def test_github_app_revoker_is_abstract_transport_subclass(self) -> None:  # ⑮
        assert issubclass(GithubAppTokenRevoker, AbstractTokenRevoker)

    def test_github_app_revoker_uses_correct_endpoint(self) -> None:
        """C9-H1: revoke endpoint must be DELETE /installation/token, not the create path."""
        import urllib.request

        captured_requests: list[urllib.request.Request] = []

        def mock_urlopen(req, timeout=None):
            captured_requests.append(req)
            # Simulate 204 No Content response

            class _FakeResp:
                status = 204

                def __enter__(self):
                    return self

                def __exit__(self, *_):
                    pass

            return _FakeResp()

        revoker = GithubAppTokenRevoker(token="tok-abc", installation_id=42)
        with patch("urllib.request.urlopen", mock_urlopen):
            revoker.revoke()

        assert len(captured_requests) == 1
        req = captured_requests[0]
        assert req.full_url == "https://api.github.com/installation/token"
        assert req.method == "DELETE"

    def test_github_app_revoker_token_revoked_before_reaper_sweep(self, sentinel: Path) -> None:
        """C9-H2: token revocation must complete before the unbounded reaper sweep."""
        order: list[str] = []

        class OrderingRevoker(AbstractTokenRevoker):
            def revoke(self) -> None:
                order.append("revoke")

        mock_reaper = MagicMock()
        mock_reaper.sweep_all.side_effect = lambda: order.append("sweep") or 0

        ks = KillSwitch(
            reaper=mock_reaper,
            token_revoker=OrderingRevoker(),
            sentinel_path=sentinel,
        )
        ks.trigger("order-test")
        assert order == ["revoke", "sweep"], f"Expected revoke before sweep, got: {order}"


# ── Reaper integration ────────────────────────────────────────────────────────


class TestReaperIntegration:
    def test_trigger_calls_reaper_sweep_all(self, sentinel: Path) -> None:  # ⑥
        mock_reaper = MagicMock()
        mock_reaper.sweep_all.return_value = 3
        ks = KillSwitch(
            reaper=mock_reaper,
            token_revoker=RecordingTokenRevoker(),
            sentinel_path=sentinel,
        )
        ks.trigger("test")
        mock_reaper.sweep_all.assert_called_once()


# ── SLO ───────────────────────────────────────────────────────────────────────


class TestSLO:
    def test_trigger_completes_within_30_seconds(self, ks: KillSwitch) -> None:  # ⑦
        elapsed = ks.trigger("slo-test")
        assert elapsed < 30.0, f"KillSwitch.trigger() took {elapsed:.3f} s (SLO=30 s)"


# ── Concurrency ───────────────────────────────────────────────────────────────


class TestConcurrency:
    def test_concurrent_trigger_exactly_one_owner(self, sentinel: Path) -> None:  # ⑬
        # Two threads race to trigger; exactly one should get elapsed > 0, the other 0.0
        revoker = RecordingTokenRevoker()
        ks = KillSwitch(token_revoker=revoker, sentinel_path=sentinel)
        results: list[float] = []
        barrier = threading.Barrier(2)

        def _trigger() -> None:
            barrier.wait()
            results.append(ks.trigger("concurrent"))

        t1 = threading.Thread(target=_trigger)
        t2 = threading.Thread(target=_trigger)
        t1.start()
        t2.start()
        t1.join(timeout=5)
        t2.join(timeout=5)

        assert ks.is_active()
        # One thread owned the trigger (elapsed > 0), one was idempotent (elapsed == 0.0)
        assert len(results) == 2
        owners = [r for r in results if r > 0.0]
        skipped = [r for r in results if r == 0.0]
        assert len(owners) == 1
        assert len(skipped) == 1
        # Token revoked exactly once
        assert revoker.revoke_count == 1


# ── SpineRunner integration ───────────────────────────────────────────────────


class TestSpineRunnerIntegration:
    def test_require_kill_switch_raises_when_not_configured(self) -> None:  # ⑩
        from app.adapters.inmemory.checkpointer import InMemoryCheckpointer
        from app.core.spine_runner import SpineRunner

        runner = SpineRunner(InMemoryCheckpointer())
        with pytest.raises(RuntimeError, match="KillSwitch not configured"):
            runner.require_kill_switch()

    def test_require_kill_switch_returns_injected_instance(self, sentinel: Path) -> None:  # ⑪
        from app.adapters.inmemory.checkpointer import InMemoryCheckpointer
        from app.core.spine_runner import SpineRunner

        ks = KillSwitch.make_recording(sentinel_path=sentinel)
        runner = SpineRunner(InMemoryCheckpointer(), kill_switch=ks)
        assert runner.require_kill_switch() is ks
