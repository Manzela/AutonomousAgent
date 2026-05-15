"""Classify TaskSpec intent into one of 7 categories via Sonnet 4.6.

Used by P1-4's REJECTED.md scoping to filter rejection entries to those
relevant to the current task category, avoiding cross-domain noise.
"""

from __future__ import annotations

import logging
from typing import Protocol

logger = logging.getLogger(__name__)

INTENT_CATEGORIES = ("coding", "audit", "research", "writing", "ops", "data", "unknown")
DEFAULT_MODEL = "vertex_ai/claude-sonnet-4-6"


class LlmComplete(Protocol):
    def complete(self, prompt: str, model: str = ...) -> str: ...


_FEW_SHOT_EXAMPLES = """\
Examples:
- "Refactor the JSON parser to use libcst" → coding
- "Find security issues in the auth module" → audit
- "Compare 5 vector DBs for our use case" → research
- "Write the runbook for cloud failover" → writing
- "Set up nightly GCS snapshots" → ops
- "ETL the user analytics into BigQuery" → data
"""


def build_classification_prompt(intent: str) -> str:
    return (
        f"Classify the following task intent into EXACTLY ONE of these categories: "
        f"{', '.join(INTENT_CATEGORIES)}.\n\n"
        f"{_FEW_SHOT_EXAMPLES}\n"
        f"Intent: {intent}\n"
        f"\n"
        f"Respond with the category name only — no explanation, no punctuation."
    )


def classify_intent(intent: str, *, llm: LlmComplete, model: str = DEFAULT_MODEL) -> str:
    """Call Sonnet 4.6 to classify the intent. Returns category string.

    Falls back to 'unknown' on:
    - LLM returning a category not in INTENT_CATEGORIES
    - Empty response
    - Any exception from llm.complete (network, auth, timeout, etc.)
    """
    prompt = build_classification_prompt(intent)
    try:
        raw = llm.complete(prompt, model=model)
    except Exception as exc:
        logger.warning("intent_classifier llm.complete failed; falling back to 'unknown': %s", exc)
        return "unknown"
    cleaned = raw.strip().lower()
    if cleaned in INTENT_CATEGORIES:
        return cleaned
    return "unknown"
