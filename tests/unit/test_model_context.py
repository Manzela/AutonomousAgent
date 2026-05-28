"""Unit tests for lib.observability.model_context.

Verifies the static model-context-length registry:
- Known models return correct context lengths
- Unknown models return 0
- Prefixed and bare model identifiers both resolve
- None/empty string returns 0
"""

from __future__ import annotations

import pytest

from lib.observability.model_context import get_model_context_length


class TestGetModelContextLength:
    """Verify the model context length lookup table."""

    @pytest.mark.parametrize(
        "model,expected",
        [
            ("claude-opus-4-7", 200_000),
            ("vertex_ai/claude-opus-4-7", 200_000),
            ("anthropic/claude-sonnet-4-6", 200_000),
            ("gpt-4o", 128_000),
            ("openai/gpt-4o", 128_000),
            ("gpt-4", 8_192),
            ("gpt-3.5-turbo", 16_385),
            ("gemini-2.5-pro", 1_048_576),
            ("vertex_ai/gemini-2.5-flash", 1_048_576),
        ],
    )
    def test_known_models(self, model: str, expected: int) -> None:
        assert get_model_context_length(model) == expected

    def test_unknown_model_returns_zero(self) -> None:
        assert get_model_context_length("nonexistent-model-xyz") == 0

    def test_none_returns_zero(self) -> None:
        assert get_model_context_length(None) == 0

    def test_empty_string_returns_zero(self) -> None:
        assert get_model_context_length("") == 0

    def test_bare_and_prefixed_agree(self) -> None:
        bare = get_model_context_length("claude-opus-4-7")
        prefixed = get_model_context_length("vertex_ai/claude-opus-4-7")
        assert bare == prefixed == 200_000

    def test_all_entries_are_positive(self) -> None:
        from lib.observability.model_context import _MODEL_CONTEXT_LENGTH

        for model, length in _MODEL_CONTEXT_LENGTH.items():
            assert length > 0, f"Model {model} has non-positive context length: {length}"
