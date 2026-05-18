"""Unit tests for the memory-scoped intent classifier wrapper.

The memory wrapper adapts ``lib.anchors.intent_classifier.classify_intent``
to a TaskSpec-style API (``classify(taskspec_id, intent, llm=...)``) and
adds an LRU cache keyed by ``taskspec_id`` so multiple session-start calls
don't re-pay the Sonnet round-trip.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from lib.memory import intent_classifier


@pytest.fixture(autouse=True)
def _reset_cache():
    intent_classifier.clear_cache()
    yield
    intent_classifier.clear_cache()


def test_classifier_returns_known_category():
    fake_llm = MagicMock()
    fake_llm.complete.return_value = "coding"
    out = intent_classifier.classify("spec-1", "Refactor auth", llm=fake_llm)
    assert out == "coding"


def test_classifier_returns_unknown_on_unparseable_response():
    fake_llm = MagicMock()
    fake_llm.complete.return_value = "completely garbled !@#"
    out = intent_classifier.classify("spec-2", "do something", llm=fake_llm)
    assert out == "unknown"


def test_classifier_caches_by_taskspec_id():
    fake_llm = MagicMock()
    fake_llm.complete.return_value = "audit"
    a = intent_classifier.classify("spec-cached", "Find SQLi", llm=fake_llm)
    b = intent_classifier.classify("spec-cached", "Find SQLi", llm=fake_llm)
    assert a == b == "audit"
    assert fake_llm.complete.call_count == 1
