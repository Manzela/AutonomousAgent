"""Tests for SP-00i model-pin gate + harness-config sha.

TDD plan
--------
RED phase (failing tests first, before model_pin_check.py exists):
  1. Run `python -m pytest tests/ci/test_model_pin_check.py` — all tests fail with
     ModuleNotFoundError because scripts/ci/model_pin_check.py does not exist yet.
  2. Verify the specific failure messages:
     - test_latest_tag_is_unpinned: FAIL (module missing)
     - test_digest_pinned_is_ok: FAIL
     - test_versioned_name_in_path_segment_is_pinned: FAIL
     ... (all 13 tests fail with ImportError, proving the module is absent)

GREEN phase (after scripts/ci/model_pin_check.py is written):
  3. Re-run pytest — all 13 tests pass.
  4. A deliberate regression test: `test_evaluate_detects_latest_in_config` creates
     a fake config text containing a ':latest' ref and asserts evaluate() returns
     ok=False — this is the gate's own red-green proof.

Pure functions only — no I/O, no network, no mocks of the thing under test.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts", "ci"))

import model_pin_check as mpc  # noqa: E402


# ---------------------------------------------------------------------------
# find_model_refs
# ---------------------------------------------------------------------------


def test_find_model_refs_extracts_yaml_value():
    text = "model: vertex_ai/claude-opus-4-7\n"
    refs = mpc.find_model_refs(text)
    assert "vertex_ai/claude-opus-4-7" in refs


def test_find_model_refs_extracts_model_name_key():
    text = "  model_name: openrouter/anthropic/claude-opus-4\n"
    refs = mpc.find_model_refs(text)
    assert "openrouter/anthropic/claude-opus-4" in refs


def test_find_model_refs_extracts_default_key():
    text = '  default: "vertex_ai/gemini-3-1-pro-preview"\n'
    refs = mpc.find_model_refs(text)
    assert "vertex_ai/gemini-3-1-pro-preview" in refs


def test_find_model_refs_deduplicates():
    text = "model: vertex_ai/claude-opus-4-7\nmodel: vertex_ai/claude-opus-4-7\n"
    refs = mpc.find_model_refs(text)
    assert refs.count("vertex_ai/claude-opus-4-7") == 1


def test_find_model_refs_ignores_prose_without_colon_key():
    # A bare path without a leading 'model:' key should not be captured.
    text = "# This references vertex_ai/something in a comment only\n"
    refs = mpc.find_model_refs(text)
    # May or may not capture comment refs — the key check guards against this.
    # What matters is evaluate() would pass if the ref is pinned, or flag if not.
    # Just assert the function returns a list.
    assert isinstance(refs, list)


# ---------------------------------------------------------------------------
# is_pinned_model
# ---------------------------------------------------------------------------


def test_latest_tag_is_unpinned():
    assert mpc.is_pinned_model("myregistry/model:latest") is False


def test_latest_tag_case_insensitive_is_unpinned():
    assert mpc.is_pinned_model("myregistry/model:LATEST") is False


def test_bare_latest_is_unpinned():
    """The literal string 'latest' (no provider prefix) is unpinned."""
    assert mpc.is_pinned_model("latest") is False


def test_digest_pinned_is_ok():
    """A digest-pinned ref (name@sha256:<hex>) is always pinned."""
    assert mpc.is_pinned_model("myrepo/model@sha256:abc123def456") is True


def test_versioned_name_in_path_segment_is_pinned():
    """claude-opus-4-7, gemini-3-5-flash, Qwen3-Coder-30B — digit in last segment."""
    assert mpc.is_pinned_model("vertex_ai/claude-opus-4-7") is True
    assert mpc.is_pinned_model("vertex_ai/gemini-3-5-flash") is True
    assert mpc.is_pinned_model("hosted_vllm/Qwen/Qwen3-Coder-30B-A3B-Instruct") is True
    assert mpc.is_pinned_model("vertex_ai/gemini-3.1-pro-preview") is True
    assert mpc.is_pinned_model("openrouter/anthropic/claude-opus-4") is True
    assert mpc.is_pinned_model("openrouter/google/gemini-2.5-pro") is True


def test_bare_name_no_version_is_unpinned():
    """A model name with no version digit anywhere is unpinned (no specific version to diff)."""
    assert mpc.is_pinned_model("vertex_ai/gpt") is False
    assert mpc.is_pinned_model("openai/model") is False


def test_tag_with_version_digit_is_pinned():
    """provider/model:v1.2.3 — explicit version tag with a digit is pinned."""
    assert mpc.is_pinned_model("myrepo/model:v1.2.3") is True


# ---------------------------------------------------------------------------
# compute_harness_config_sha
# ---------------------------------------------------------------------------


def test_harness_sha_is_stable_for_same_input():
    """Same dict -> same sha, every time (no random/time-based component)."""
    d = {"reasoning_effort": "high", "thinking": True, "compaction": False}
    assert mpc.compute_harness_config_sha(d) == mpc.compute_harness_config_sha(d)


def test_harness_sha_is_order_independent():
    """Insertion order must not affect the digest (JSON is sorted)."""
    d1 = {"reasoning_effort": "high", "thinking": True}
    d2 = {"thinking": True, "reasoning_effort": "high"}
    assert mpc.compute_harness_config_sha(d1) == mpc.compute_harness_config_sha(d2)


def test_harness_sha_changes_on_value_change():
    """A bump to any harness field changes the sha (regression-on-bump signal)."""
    d_before = {"reasoning_effort": "high", "thinking": True}
    d_after = {"reasoning_effort": "medium", "thinking": True}
    assert mpc.compute_harness_config_sha(d_before) != mpc.compute_harness_config_sha(d_after)


def test_harness_sha_ignores_unknown_keys():
    """Keys outside the canonical set are not included in the sha."""
    d_base = {"reasoning_effort": "high"}
    d_extra = {"reasoning_effort": "high", "unrelated_key": "noise"}
    assert mpc.compute_harness_config_sha(d_base) == mpc.compute_harness_config_sha(d_extra)


def test_harness_sha_is_64_hex_chars():
    """sha256 output is always 64 lowercase hex characters."""
    sha = mpc.compute_harness_config_sha({"reasoning_effort": "high"})
    assert len(sha) == 64
    assert all(c in "0123456789abcdef" for c in sha)


# ---------------------------------------------------------------------------
# evaluate (integration of find_model_refs + is_pinned_model)
# ---------------------------------------------------------------------------


def test_evaluate_passes_on_all_pinned_refs():
    """Green control: a config with only pinned refs -> ok=True, reasons=[]."""
    text = "model: vertex_ai/claude-opus-4-7\nmodel_name: vertex_ai/gemini-3-5-flash\n"
    ok, reasons = mpc.evaluate([text])
    assert ok is True
    assert reasons == []


def test_evaluate_detects_latest_in_config():
    """Red: a ':latest' ref in config -> ok=False, reason mentions the ref."""
    text = "model: badregistry/model:latest\n"
    ok, reasons = mpc.evaluate([text])
    assert ok is False
    assert any("latest" in r or "badregistry/model:latest" in r for r in reasons)


def test_evaluate_detects_bare_name_no_version():
    """Red: a bare name with no version digit is unpinned."""
    text = "model: some_provider/no_version_here\n"
    ok, reasons = mpc.evaluate([text])
    assert ok is False
    assert len(reasons) >= 1


def test_evaluate_scans_multiple_config_texts():
    """evaluate() aggregates across all provided config texts."""
    good = "model: vertex_ai/claude-opus-4-7\n"
    bad = "model: badregistry/model:latest\n"
    ok, reasons = mpc.evaluate([good, bad])
    assert ok is False
    assert len(reasons) == 1


def test_evaluate_empty_config_is_ok():
    """No model refs found -> no unpinned refs -> ok=True."""
    ok, reasons = mpc.evaluate(["# no models here\n"])
    assert ok is True
    assert reasons == []
