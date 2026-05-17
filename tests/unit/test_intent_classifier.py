"""Unit tests for intent_classifier — mocks LLM call."""

from unittest.mock import MagicMock

from lib.anchors.intent_classifier import (
    INTENT_CATEGORIES,
    classify_intent,
    build_classification_prompt,
)


def test_prompt_contains_all_categories():
    prompt = build_classification_prompt("Audit my repo for security issues.")
    for cat in INTENT_CATEGORIES:
        assert cat in prompt


def test_prompt_contains_intent():
    intent = "Refactor the auth module to use JWT."
    prompt = build_classification_prompt(intent)
    assert intent in prompt


def test_classify_intent_returns_valid_category():
    fake_llm = MagicMock()
    fake_llm.complete.return_value = "coding"
    result = classify_intent("Refactor the auth module.", llm=fake_llm)
    assert result == "coding"


def test_classify_intent_falls_back_on_invalid_response():
    fake_llm = MagicMock()
    fake_llm.complete.return_value = "marketing"  # not in INTENT_CATEGORIES
    result = classify_intent("...", llm=fake_llm)
    assert result == "unknown"


def test_classify_intent_strips_response():
    fake_llm = MagicMock()
    fake_llm.complete.return_value = "  audit  \n"
    result = classify_intent("...", llm=fake_llm)
    assert result == "audit"


def test_classify_intent_empty_response_falls_back():
    fake_llm = MagicMock()
    fake_llm.complete.return_value = ""
    result = classify_intent("...", llm=fake_llm)
    assert result == "unknown"


def test_classify_intent_exception_falls_back():
    """Exceptions from llm.complete should fall back to 'unknown', not propagate."""
    fake_llm = MagicMock()
    fake_llm.complete.side_effect = RuntimeError("network down")
    result = classify_intent("...", llm=fake_llm)
    assert result == "unknown"
