"""Model name → maximum context-length registry.

A static lookup table mapping LiteLLM-style model identifiers
(``vertex_ai/claude-opus-4-7``, ``openai/gpt-4o``, …) to their
published maximum context window in tokens. Consumed by the J9
observability shim (:mod:`lib.observability` ``_post_api_request``)
to compute the ``prompt_tokens / context_length`` ratio that the
:class:`lib.durability.runtime_detectors.ContextUsageDetector`
needs in order to fire ``F36`` (F-CONTEXT) at the warning
threshold.

Why a static table instead of pulling from LiteLLM's ``model_cost``
map?

1. LiteLLM's ``litellm.model_cost`` is a moving target across
   minor versions. Pinning the dependency to a specific patch
   would silently break F36 thresholds whenever we upgrade the
   provider client. A vendored table makes the contract explicit
   and reviewable.
2. We only run a handful of models in production (see
   ``config/limits.yaml`` → ``judge_panel`` and
   ``intent_classifier_model``). A 25-line table is easier to
   audit than an opaque dict import — and a missing-model lookup
   is a *known* state ("return 0 → caller skips the recording")
   rather than an undefined one.

The exact match policy is deliberate: ``claude-3-5-sonnet`` and
``claude-3-5-sonnet-20241022`` may have the same context window
today, but versioned identifiers exist specifically because
providers reserve the right to change behavior on minor revisions.
Prefix/suffix matching would hide that drift; an unknown model
should fail loudly via the F36 silence + the operator log line
in ``_get_context_detector`` rather than be silently mapped to a
sibling.

Last updated 2026-05-21. Sources:

* Anthropic — docs.anthropic.com/en/docs/about-claude/models
* OpenAI    — platform.openai.com/docs/models
* Google    — ai.google.dev/gemini-api/docs/models
"""

from __future__ import annotations

from typing import Optional

# Published maximum context windows. Values represent the combined
# input + output budget where the provider exposes a single number
# (the Anthropic/OpenAI/Vertex pattern). Output-only caps are not
# subtracted because the F-CONTEXT detector is interested in
# *input pressure*, not effective remaining headroom.
_MODEL_CONTEXT_LENGTH: dict[str, int] = {
    # ----- Anthropic Claude 4.x (200K) -----
    "claude-opus-4-7": 200_000,
    "claude-sonnet-4-6": 200_000,
    "claude-haiku-4-5-20251001": 200_000,
    # LiteLLM-prefixed variants — Hermes' run_agent.py emits model
    # strings prefixed with the provider (e.g. ``vertex_ai/`` or
    # ``anthropic/``); the bare identifiers above cover any caller
    # that strips the prefix before invoking the hook.
    "vertex_ai/claude-opus-4-7": 200_000,
    "vertex_ai/claude-sonnet-4-6": 200_000,
    "vertex_ai/claude-haiku-4-5-20251001": 200_000,
    "anthropic/claude-opus-4-7": 200_000,
    "anthropic/claude-sonnet-4-6": 200_000,
    "anthropic/claude-haiku-4-5-20251001": 200_000,
    # ----- OpenAI -----
    "gpt-4o": 128_000,
    "gpt-4o-mini": 128_000,
    "gpt-4-turbo": 128_000,
    "gpt-4": 8_192,
    "gpt-3.5-turbo": 16_385,
    "openai/gpt-4o": 128_000,
    "openai/gpt-4o-mini": 128_000,
    "openai/gpt-4-turbo": 128_000,
    # ----- Google Gemini -----
    "gemini-2.5-pro": 1_048_576,
    "gemini-2.5-flash": 1_048_576,
    "vertex_ai/gemini-2.5-pro": 1_048_576,
    "vertex_ai/gemini-2.5-flash": 1_048_576,
}


def get_model_context_length(model: Optional[str]) -> int:
    """Return the published maximum context length for ``model``, or ``0``.

    A return of ``0`` is the documented "unknown model" signal — the
    caller MUST treat it as "skip F-CONTEXT recording for this call"
    (``ContextUsageDetector.record_usage`` already guards against
    ``context_length <= 0`` by returning ``None``, so callers may also
    pass the result through directly when convenient).

    Lookup is exact-match on the full LiteLLM-style identifier. The
    function does NOT attempt fuzzy/prefix matching by design — see
    the module docstring for the rationale.
    """
    if not model:
        return 0
    return _MODEL_CONTEXT_LENGTH.get(model, 0)
