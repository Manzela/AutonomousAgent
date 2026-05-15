"""Classify TaskSpec intent into one of 7 categories via Sonnet 4.6.

Used by P1-4's REJECTED.md scoping to filter rejection entries to those
relevant to the current task category, avoiding cross-domain noise.
"""

from __future__ import annotations

from typing import Protocol

INTENT_CATEGORIES = ("coding", "audit", "research", "writing", "ops", "data", "unknown")


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


def classify_intent(
    intent: str, *, llm: LlmComplete, model: str = "vertex_ai/claude-sonnet-4-6"
) -> str:
    """Call Sonnet 4.6 to classify the intent. Returns category string.

    Falls back to 'unknown' on any unexpected response (model returned
    a category not in our enum, empty response, etc.).
    """
    prompt = build_classification_prompt(intent)
    raw = llm.complete(prompt, model=model)
    cleaned = raw.strip().lower()
    if cleaned in INTENT_CATEGORIES:
        return cleaned
    return "unknown"
