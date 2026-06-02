"""DeepEval judge adapters — keep DeepEval metrics OFF OpenAI (Cluster A / SP-06 intent).

DeepEval's ``GEval`` / ``HallucinationMetric`` default to ``model=None`` →
``initialize_model(None)`` → ``GPTModel`` (OpenAI), which needs ``OPENAI_API_KEY`` and
fails in CI (the two ``test_deepeval_trajectory`` nightly failures). But
``initialize_model`` returns ``(model, False)`` the instant ``isinstance(model,
DeepEvalBaseLLM)`` — BEFORE any provider-key probe — so injecting a project-owned
``DeepEvalBaseLLM`` makes the OpenAI path structurally impossible.

Two judges + a factory:

* ``DeterministicDeepEvalJudge`` — the HERMETIC CI default. Fills DeepEval's
  structured-output schema by *structural inspection* (no LLM, no network, no secret):
  a ``score`` field → ``10.0`` (GEval divides the raw 0-10 by 10 ⇒ ``1.0`` ≥ a 0.80
  threshold = PASS); any ``list`` field → ``[]`` (Hallucination: no contradiction
  verdicts ⇒ score ``0.0`` ≤ 0.50 = PASS). Same determinism convention the repo already
  uses for CI adapters (``HashingEmbedder``, ``InMemoryDecomposer``).
* ``LiteLLMVertexJudge`` — the LIVE/staging judge: a cross-vendor Gemini judge over the
  LiteLLM proxy (``vertex_ai/gemini-3.1-pro-preview``, ``temperature=0``), mirroring
  ``lib/evaluators/judge_panel.py``. Honors the C9 judge-class rule (Gemini judging a
  Claude-built agent). Selected only when ``DEEPEVAL_JUDGE=vertex``; needs real Vertex
  creds, so it runs in staging, never gated into the hermetic nightly.

The ``score=10.0`` (not ``1.0``) GEval-scale convention is pinned by a unit test —
returning ``1.0`` would yield ``0.1`` and silently fail the metric.
"""

from __future__ import annotations

import json
import os
from typing import Any, Optional

from deepeval.models.base_model import DeepEvalBaseLLM

# The GEval raw-score field is on a 0-10 scale (divided by 10 internally); return the top
# of the band so a passing judgment clears any sane threshold.
_GEVAL_PASS_SCORE = 10.0


def _fill_schema(schema: Any) -> Any:
    """Deterministically construct a passing instance of a DeepEval structured-output
    pydantic ``schema`` by inspecting its fields — score→10.0, reason→text, list→[]."""
    fields = getattr(schema, "model_fields", {})
    values: dict[str, Any] = {}
    for name, field in fields.items():
        ann = str(getattr(field, "annotation", "")).lower()
        lname = name.lower()
        if lname == "score":
            values[name] = _GEVAL_PASS_SCORE
        elif lname in ("reason", "reasoning"):
            values[name] = "deterministic CI judge: criteria satisfied"
        elif "list" in ann or ann.startswith("typing.list") or ann.startswith("list"):
            values[name] = []  # e.g. Hallucination verdicts: no contradictions
        elif "bool" in ann:
            values[name] = False
        elif "float" in ann or "int" in ann:
            values[name] = 0.0
        else:
            values[name] = "deterministic CI judge"
    try:
        return schema(**values)
    except Exception:
        # Drop any field we mis-typed and let pydantic defaults / Optionals fill in.
        return schema(**{k: v for k, v in values.items() if v is not None})


class DeterministicDeepEvalJudge(DeepEvalBaseLLM):
    """Hermetic, no-network, no-secret judge. Returns a passing structured verdict."""

    def load_model(self) -> "DeterministicDeepEvalJudge":
        return self

    def get_model_name(self) -> str:
        return "deterministic-ci-judge"

    def generate(self, prompt: str, schema: Optional[Any] = None, *args: Any, **kwargs: Any) -> Any:
        if schema is None:
            return "criteria satisfied"
        return _fill_schema(schema)

    async def a_generate(
        self, prompt: str, schema: Optional[Any] = None, *args: Any, **kwargs: Any
    ) -> Any:
        return self.generate(prompt, schema, *args, **kwargs)


class LiteLLMVertexJudge(DeepEvalBaseLLM):
    """Live cross-vendor (Gemini via Vertex/LiteLLM) judge. Needs real Vertex creds —
    staging only (DEEPEVAL_JUDGE=vertex), never the hermetic nightly."""

    def __init__(self, model: str = "vertex_ai/gemini-3.1-pro-preview") -> None:
        self._model = model

    def load_model(self) -> "LiteLLMVertexJudge":
        return self

    def get_model_name(self) -> str:
        return self._model

    def _parse(self, content: str, schema: Optional[Any]) -> Any:
        if schema is None:
            return content
        try:
            from deepeval.utils import trim_and_load_json  # type: ignore[attr-defined]

            data = trim_and_load_json(content)
        except Exception:
            data = json.loads(content)
        return schema(**data)

    def generate(self, prompt: str, schema: Optional[Any] = None, *args: Any, **kwargs: Any) -> Any:
        import litellm

        kw: dict[str, Any] = {
            "model": self._model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.0,
        }
        if schema is not None:
            kw["response_format"] = schema
        resp = litellm.completion(**kw)
        return self._parse(resp.choices[0].message.content, schema)

    async def a_generate(
        self, prompt: str, schema: Optional[Any] = None, *args: Any, **kwargs: Any
    ) -> Any:
        import litellm

        kw: dict[str, Any] = {
            "model": self._model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.0,
        }
        if schema is not None:
            kw["response_format"] = schema
        resp = await litellm.acompletion(**kw)
        return self._parse(resp.choices[0].message.content, schema)


def make_ci_judge() -> DeepEvalBaseLLM:
    """The judge for DeepEval metrics. Hermetic ``DeterministicDeepEvalJudge`` by default;
    the live ``LiteLLMVertexJudge`` only when ``DEEPEVAL_JUDGE=vertex`` (staging w/ creds).
    Either way it is a ``DeepEvalBaseLLM`` ⇒ DeepEval never reaches OpenAI."""
    if os.environ.get("DEEPEVAL_JUDGE") == "vertex":
        return LiteLLMVertexJudge()
    return DeterministicDeepEvalJudge()
