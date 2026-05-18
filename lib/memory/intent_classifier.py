"""P1-4 intent classifier — TaskSpec-shaped wrapper around the anchors classifier.

The single source of truth for the prompt + category list lives in
``lib.anchors.intent_classifier`` (P1-1). This module adds:

1. A TaskSpec-style API: ``classify(taskspec_id, intent, llm=...)`` instead
   of ``classify_intent(intent, llm=...)``.
2. A process-local cache keyed by ``taskspec_id`` so the resume hook +
   inject hook on the same session don't both pay the Sonnet round-trip.
3. The model id resolution from ``config/limits.yaml memory.intent_classifier_model``
   (falls back to the anchors default ``vertex_ai/claude-sonnet-4-6``).

Reads through ``LiteLLM`` proxy at ``http://localhost:4000`` (kept inside
the ``llm`` adapter abstraction — production wires the real adapter, tests
pass a ``MagicMock`` with ``.complete(prompt, model=...)``).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from lib.anchors.intent_classifier import (
    DEFAULT_MODEL as _ANCHORS_DEFAULT_MODEL,
    INTENT_CATEGORIES,
    classify_intent as _classify_intent,
)

logger = logging.getLogger(__name__)

# Process-local cache: {taskspec_id: category}
_CACHE: dict[str, str] = {}


def _model_from_config() -> str:
    """Resolve ``memory.intent_classifier_model`` from config/limits.yaml.

    Falls back to the anchors default on any read/parse failure — keeps
    classification working even if the config block is missing in dev.
    """
    try:
        import yaml  # local import keeps unit-test cost down
    except ImportError:
        return _ANCHORS_DEFAULT_MODEL
    cfg_path = Path(__file__).resolve().parents[2] / "config" / "limits.yaml"
    if not cfg_path.exists():
        return _ANCHORS_DEFAULT_MODEL
    try:
        cfg = yaml.safe_load(cfg_path.read_text()) or {}
    except Exception:  # noqa: BLE001 — never let config-read break classification
        return _ANCHORS_DEFAULT_MODEL
    return str((cfg.get("memory") or {}).get("intent_classifier_model", _ANCHORS_DEFAULT_MODEL))


def classify(taskspec_id: str, intent: str, *, llm: Any, model: str | None = None) -> str:
    """Classify a TaskSpec intent into one of ``INTENT_CATEGORIES``.

    Args:
        taskspec_id: stable id used as the cache key. Two callers with the
            same id share the result.
        intent: free-form text describing the user's goal.
        llm: object with a ``.complete(prompt, model=...) -> str`` method.
        model: override the model; defaults to the value in
            ``config/limits.yaml memory.intent_classifier_model``.

    Returns:
        A string in ``INTENT_CATEGORIES``. Falls back to ``"unknown"`` on
        any failure (mirrors anchors classifier behaviour).
    """
    if taskspec_id in _CACHE:
        return _CACHE[taskspec_id]
    chosen_model = model or _model_from_config()
    category = _classify_intent(intent, llm=llm, model=chosen_model)
    if category not in INTENT_CATEGORIES:
        category = "unknown"
    _CACHE[taskspec_id] = category
    return category


def clear_cache() -> None:
    """Drop the process-local cache. Used by tests; rarely useful in prod."""
    _CACHE.clear()


__all__ = ["INTENT_CATEGORIES", "classify", "clear_cache"]
