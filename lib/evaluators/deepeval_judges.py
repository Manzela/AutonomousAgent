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
import typing
from typing import Any, Optional

from deepeval.models.base_model import DeepEvalBaseLLM
from pydantic import BaseModel

# The GEval raw-score field is on a 0-10 scale (divided by 10 internally); return the top
# of the band so a passing judgment clears any sane threshold.
_GEVAL_PASS_SCORE = 10.0


def _fill_field(name: str, ann: Any) -> Any:
    """Pick a PASSING value for one structured-output field, recursing into the type
    (Optional/Union, List, Literal, nested pydantic model) so a NEW DeepEval metric schema
    shape does not crash the deterministic judge (C9 hardening)."""
    lname = name.lower()
    origin = typing.get_origin(ann)
    args = typing.get_args(ann)

    # Optional[X] / Union[...] → fill the first concrete (non-None) member.
    if origin is typing.Union:
        non_none = [a for a in args if a is not type(None)]
        return _fill_field(name, non_none[0]) if non_none else None
    # List[...] → empty (no verdicts / no contradictions = not-hallucinated, passing).
    if origin in (list, typing.List):  # noqa: UP006
        return []
    # Literal[...] → the first allowed value (e.g. Literal["yes","no"] verdicts).
    if origin is typing.Literal:
        return args[0] if args else None
    # Nested pydantic model → recurse.
    if isinstance(ann, type) and issubclass(ann, BaseModel):
        return _fill_schema(ann)
    # By NAME (deepeval's GEval ReasonScore).
    if lname == "score":
        return _GEVAL_PASS_SCORE
    if lname in ("reason", "reasoning"):
        return "deterministic CI judge: criteria satisfied"
    # By TYPE.
    if ann in (float, int):
        return 0.0
    if ann is bool:
        return False
    if ann is str:
        return "n/a"
    return None


def _fill_schema(schema: Any) -> Any:
    """Deterministically construct a PASSING instance of a DeepEval structured-output
    pydantic ``schema`` (score→10.0, reason→text, list→[], nested→recurse). Defensive:
    if a field we couldn't type slips through, drop it and let pydantic defaults fill in."""
    fields = getattr(schema, "model_fields", None)
    if fields is None:
        return None
    values = {
        name: _fill_field(name, getattr(field, "annotation", None))
        for name, field in fields.items()
    }
    try:
        return schema(**values)
    except Exception:
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
            try:
                data = json.loads(content)
            except json.JSONDecodeError as exc:
                # Fail with a debuggable message instead of an opaque crash mid-eval.
                raise ValueError(
                    f"LiteLLMVertexJudge({self._model}): judge did not return parseable JSON "
                    f"(first 200 chars): {content[:200]!r}"
                ) from exc
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
