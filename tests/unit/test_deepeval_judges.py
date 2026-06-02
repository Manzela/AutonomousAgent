"""Cluster-A hermetic guard — DeepEval metrics run with a project ``DeepEvalBaseLLM`` so
they NEVER reach OpenAI (asserted with ``OPENAI_API_KEY`` unset), and the GEval 0-10
score scale is honored. Mirrors the SP-06 trap-proof guard in test_sp06_scope_gate.py.

NON-VACUOUS: with ``model=None`` (the pre-fix state) GEval/HallucinationMetric route to
``GPTModel`` and raise "OpenAI API key is not configured"; these pass only because the
deterministic ``DeepEvalBaseLLM`` is injected.
"""

from __future__ import annotations

import pytest
from deepeval.metrics import GEval, HallucinationMetric
from deepeval.models.base_model import DeepEvalBaseLLM
from deepeval.test_case import LLMTestCase, LLMTestCaseParams

from lib.evaluators.deepeval_judges import DeterministicDeepEvalJudge, make_ci_judge


@pytest.fixture(autouse=True)
def _no_openai(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("DEEPEVAL_JUDGE", raising=False)


def test_make_ci_judge_is_base_llm_and_bypasses_providers():
    j = make_ci_judge()
    assert isinstance(j, DeepEvalBaseLLM) and isinstance(j, DeterministicDeepEvalJudge)
    # initialize_model returns (model, False) for a DeepEvalBaseLLM BEFORE any provider
    # probe — so no GPTModel / OpenAI is ever constructed.
    from deepeval.metrics.utils import initialize_model

    model, using_native = initialize_model(j)
    assert model is j and using_native is False
    assert type(model).__module__.startswith("lib.evaluators")


def test_geval_passes_without_openai_key():
    g = GEval(
        name="r",
        evaluation_steps=["1. ok"],
        evaluation_params=[LLMTestCaseParams.INPUT, LLMTestCaseParams.ACTUAL_OUTPUT],
        threshold=0.80,
        model=make_ci_judge(),
        async_mode=False,
    )
    g.measure(LLMTestCase(input="x", actual_output="y"))
    assert g.score >= 0.80, f"GEval should pass hermetically; got {g.score} (10.0-vs-1.0 scale?)"


def test_hallucination_passes_without_openai_key():
    h = HallucinationMetric(threshold=0.5, model=make_ci_judge(), async_mode=False)
    h.measure(LLMTestCase(input="x", actual_output="y", context=["y"]))
    assert h.score <= 0.5


def test_geval_scale_convention_is_ten_not_one():
    # The judge returns score=10.0 (GEval divides by 10 → 1.0). Returning 1.0 would yield
    # 0.1 and silently FAIL the 0.8 threshold — pin the convention so a refactor can't drift.
    from lib.evaluators.deepeval_judges import _GEVAL_PASS_SCORE

    assert _GEVAL_PASS_SCORE == 10.0
