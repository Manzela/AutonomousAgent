import pytest
from lib.router.intent_router import resolve_model


def test_resolve_model_valid_intents():
    spec = resolve_model("orchestrator")
    assert spec.model == "vertex_ai/gemini-3-1-pro-preview"
    assert spec.daily_cost_cap_usd == 200.0
    assert spec.max_tokens == 8192

    spec = resolve_model("architect")
    assert spec.model == "vertex_ai/claude-opus-4-7"
    assert spec.daily_cost_cap_usd == 150.0


def test_resolve_model_missing_intent_fails_closed():
    # Missing intent falls back to orchestrator, NOT opus
    spec = resolve_model("invalid_intent")
    assert spec.model == "vertex_ai/gemini-3-1-pro-preview"
    assert spec.daily_cost_cap_usd == 200.0


def test_resolve_model_stubbed_raises_w1j():
    # 'privacy' tier is still stub-until-w1j (Qwen vLLM not yet provisioned)
    with pytest.raises(NotImplementedError, match="W1.J pending"):
        resolve_model("privacy")


def test_resolve_model_deep_math_falls_to_orchestrator():
    # deep-math tier REMOVED 2026-05-28 per user directive: "We won't use
    # DeepSeek R1 at the moment."  Intent now falls through to orchestrator.
    spec = resolve_model("deep-math")
    assert spec.model == "vertex_ai/gemini-3-1-pro-preview"
    assert spec.daily_cost_cap_usd == 200.0
