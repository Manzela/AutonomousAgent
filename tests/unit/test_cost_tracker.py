"""Unit tests for lib/cost.py and lib/cost_tracker.py (P0-2)."""

from __future__ import annotations

import threading
from unittest.mock import patch

import pytest


# ── lib/cost.py tests ────────────────────────────────────────────────────────


class TestLlmRequestCostUsd:
    def test_returns_float_for_known_model(self):
        from lib.cost import llm_request_cost_usd

        with patch("litellm.cost_per_token", return_value=(0.001, 0.002)):
            cost = llm_request_cost_usd("gpt-4", 100, 50)
            assert cost == 0.003
            assert isinstance(cost, float)

    def test_returns_none_for_unknown_model(self):
        from lib.cost import llm_request_cost_usd

        with patch("litellm.cost_per_token", side_effect=Exception("unknown model")):
            cost = llm_request_cost_usd("nonexistent-model", 100, 50)
            assert cost is None

    def test_clamps_negative_tokens(self):
        from lib.cost import llm_request_cost_usd

        with patch("litellm.cost_per_token", return_value=(0.0, 0.0)) as mock:
            llm_request_cost_usd("test-model", -5, -10)
            mock.assert_called_once_with(
                model="test-model",
                prompt_tokens=0,
                completion_tokens=0,
            )

    def test_returns_none_for_negative_cost(self):
        from lib.cost import llm_request_cost_usd

        with patch("litellm.cost_per_token", return_value=(-1.0, -1.0)):
            cost = llm_request_cost_usd("test-model", 100, 50)
            assert cost is None


# ── lib/cost_tracker.py tests ────────────────────────────────────────────────


class TestCostTracker:
    def test_initial_state(self):
        from lib.cost_tracker import CostTracker

        tracker = CostTracker()
        assert tracker.total_usd == 0.0
        assert tracker.call_count == 0
        assert tracker.priced_count == 0

    def test_accumulation(self):
        from lib.cost_tracker import CostTracker

        tracker = CostTracker()
        # Patch at the location where the tracker imports it
        with patch("lib.cost_tracker.llm_request_cost_usd", return_value=0.005):
            tracker.record("gpt-4", 100, 50)
            tracker.record("gpt-4", 200, 100)
        assert tracker.total_usd == pytest.approx(0.010)
        assert tracker.call_count == 2
        assert tracker.priced_count == 2

    def test_unpriced_model_does_not_accumulate(self):
        from lib.cost_tracker import CostTracker

        tracker = CostTracker()
        with patch("lib.cost_tracker.llm_request_cost_usd", return_value=None):
            result = tracker.record("unknown-model", 100, 50)
        assert result is None
        assert tracker.total_usd == 0.0
        assert tracker.call_count == 1
        assert tracker.priced_count == 0

    def test_mixed_priced_and_unpriced(self):
        from lib.cost_tracker import CostTracker

        tracker = CostTracker()
        with patch("lib.cost_tracker.llm_request_cost_usd", side_effect=[0.003, None, 0.007]):
            tracker.record("model-a", 100, 50)
            tracker.record("model-b", 100, 50)
            tracker.record("model-a", 200, 100)
        assert tracker.total_usd == pytest.approx(0.010)
        assert tracker.call_count == 3
        assert tracker.priced_count == 2

    def test_reset(self):
        from lib.cost_tracker import CostTracker

        tracker = CostTracker()
        with patch("lib.cost_tracker.llm_request_cost_usd", return_value=0.005):
            tracker.record("gpt-4", 100, 50)
        assert tracker.total_usd > 0
        tracker.reset()
        assert tracker.total_usd == 0.0
        assert tracker.call_count == 0
        assert tracker.priced_count == 0

    def test_thread_safety(self):
        """Multiple threads recording simultaneously should not lose data."""
        from lib.cost_tracker import CostTracker

        tracker = CostTracker()
        n_threads = 10
        calls_per_thread = 100

        with patch("lib.cost_tracker.llm_request_cost_usd", return_value=0.001):

            def _record():
                for _ in range(calls_per_thread):
                    tracker.record("gpt-4", 10, 5)

            threads = [threading.Thread(target=_record) for _ in range(n_threads)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

        assert tracker.call_count == n_threads * calls_per_thread
        assert tracker.priced_count == n_threads * calls_per_thread
        assert tracker.total_usd == pytest.approx(n_threads * calls_per_thread * 0.001)

    def test_repr(self):
        from lib.cost_tracker import CostTracker

        tracker = CostTracker()
        r = repr(tracker)
        assert "CostTracker" in r
        assert "total_usd=" in r
        assert "calls=" in r

    def test_active_tracker_contextvar(self):
        from lib.cost_tracker import CostTracker, active_tracker
        from lib.observability import _post_api_request
        from unittest.mock import MagicMock
        import lib.observability as obs

        tracker = CostTracker()
        token = active_tracker.set(tracker)
        try:
            mock_tracer = MagicMock()
            mock_span = MagicMock()
            with (
                patch("lib.observability._tracer", mock_tracer),
                patch("lib.cost_tracker.llm_request_cost_usd", return_value=0.005),
                patch.dict(obs._LLM_SPANS, {"test-session": (mock_span, None)}),
                patch.dict(obs._LLM_MODEL_BY_SESSION, {"test-session": "test-model"}),
            ):
                _post_api_request(
                    session_id="test-session",
                    usage={"input_tokens": 100, "output_tokens": 50},
                    response_model="test-model",
                )
            assert tracker.total_usd == pytest.approx(0.005)
            assert tracker.call_count == 1
            assert tracker.priced_count == 1
        finally:
            active_tracker.reset(token)
