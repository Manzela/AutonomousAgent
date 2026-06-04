"""Standalone LLM cost computation (extracted from lib/observability).

P0-2 (Go-Live audit): this module provides the cost-per-request calculation
used by BOTH the observability OTel histogram AND the per-graph budget
gate (``lib/durability/graph_budget.py``).  Extracting it breaks the
``app/core → lib/observability`` dependency that would otherwise create
a circular import path.

The pricing source is LiteLLM's ``model_prices_and_context_window`` data
(maintained by the LiteLLM team; already a required dependency via the
judge panel).  ``llm_request_cost_usd`` returns ``None`` when LiteLLM
cannot price the model — callers record NOTHING rather than fabricating
a cost (SP-O1: cost claims must be SOURCED, never invented).
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)


def llm_request_cost_usd(model: str, prompt_tokens: int, completion_tokens: int) -> Optional[float]:
    """Per-request cost in USD from the LiteLLM pricing map.

    Returns ``None`` — and the caller records NOTHING — when LiteLLM
    cannot price ``model`` (unknown model, missing dep, or bad token counts),
    so an unpriced model yields an ABSENT datapoint rather than a fabricated
    cost.

    Thread-safe: ``litellm.cost_per_token`` reads a module-level dict
    (no global mutable state beyond the pricing data loaded at import).
    """
    try:
        import litellm  # lazy import: kept off the module-load path

        prompt_cost, completion_cost = litellm.cost_per_token(
            model=model,
            prompt_tokens=max(0, int(prompt_tokens)),
            completion_tokens=max(0, int(completion_tokens)),
        )
        total = float(prompt_cost) + float(completion_cost)
        return total if total >= 0.0 else None
    except Exception as exc:  # noqa: BLE001  unknown model / litellm absent / bad data
        logger.debug("llm_request_cost_usd: LiteLLM could not price %r: %s", model, exc)
        return None
