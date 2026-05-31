#!/usr/bin/env python3
"""SP-00i — model-pin gate + harness-config sha stamp.

Enforces that EVERY model ref in the scanned config files is pinned to a specific
version or digest — no bare 'latest' tags, no version-free identifiers. Also
computes a deterministic sha256 over the harness-config fields
{reasoning_effort, system_prompt_sha, thinking, compaction} so a bump to any
of those fields is detectable and (when SP-G1 is built) can gate a regression
re-run.

Current model IDs (verified 2026-05-31, all pinned):
  config/hermes/model-tiers.yaml  — vertex_ai/gemini-3-1-pro-preview, vertex_ai/claude-opus-4-7,
                                     vertex_ai/gemini-3-5-flash, hosted_vllm/Qwen/Qwen3-Coder-30B-A3B-Instruct
  config/limits.yaml              — vertex_ai/claude-sonnet-4-6, vertex_ai/claude-opus-4-7,
                                     vertex_ai/gemini-3.1-pro-preview
  deploy/litellm/config.yaml      — same set + openrouter/anthropic/* + openrouter/google/*

All are pinned to a specific named version — the gap is ENFORCEMENT on future changes.

Pure-function surface (unit-testable, no I/O):
  find_model_refs(text)               -> list[str]
  is_pinned_model(ref)                -> bool
  compute_harness_config_sha(d)       -> str (sha256 hex)
  evaluate(config_texts)              -> tuple[bool, list[str]]

CLI:
  model_pin_check.py [--paths file1,file2,...] [--harness-config key=val,...]

Exit 0 iff every scanned model ref is pinned.

SP-G1 regression gate: ADVISORY — see module-level docstring section below.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from typing import Iterable

# ---------------------------------------------------------------------------
# Regex that extracts candidate model identifiers from config text.
#
# Model ID patterns found in this repo:
#   vertex_ai/claude-opus-4-7
#   vertex_ai/gemini-3.1-pro-preview
#   vertex_ai/gemini-3-5-flash
#   hosted_vllm/Qwen/Qwen3-Coder-30B-A3B-Instruct
#   openrouter/anthropic/claude-opus-4
#   openrouter/google/gemini-2.5-pro
#
# We capture the full provider/model string (alphanumeric + . - / _) that
# appears as a YAML value (after a colon or a quote) to avoid matching prose.
# The pattern is deliberately conservative: anything with a '/' that looks
# like provider/model is a candidate; is_pinned_model() then adjudicates.
# ---------------------------------------------------------------------------
_MODEL_REF_RE = re.compile(
    r"(?:model|model_name|default)\s*:\s*[\"']?([a-zA-Z0-9_][a-zA-Z0-9_.\-/@:]*\/[a-zA-Z0-9_.\-/@:]+)[\"']?"
)

# A ref ending in ':latest' (case-insensitive) is unpinned.
_LATEST_RE = re.compile(r":latest\s*$", re.IGNORECASE)

# Known pinned-version suffixes/patterns. A ref is pinned if it:
#   (a) does NOT end in :latest, AND
#   (b) its last path segment contains a digit (i.e., is versioned), OR
#   (c) it contains @sha256: (digest-pinned), OR
#   (d) it matches a known-pinned set pattern (== or == prefix).
#
# Bare names with no version component (e.g., "openai/gpt") are considered
# UNPINNED because there is no specific version to diff against.
_DIGEST_RE = re.compile(r"@sha256:[a-fA-F0-9]{8,}")
_VERSION_COMPONENT_RE = re.compile(r"[0-9]")


def find_model_refs(text: str) -> list[str]:
    """Extract all model ID strings from YAML/config text.

    Returns the raw captured strings (e.g. 'vertex_ai/claude-opus-4-7').
    Deduplicates while preserving first-occurrence order.
    """
    seen: dict[str, None] = {}
    for m in _MODEL_REF_RE.finditer(text):
        ref = m.group(1).strip()
        if ref not in seen:
            seen[ref] = None
    return list(seen.keys())


def is_pinned_model(ref: str) -> bool:
    """Return True iff the model ref is pinned to a specific version or digest.

    Unpinned cases:
      - ends with ':latest' (case-insensitive)
      - the final path segment has no digit (bare name, no version)
      - equals a known-unpinned sentinel like 'latest'

    Pinned cases:
      - contains '@sha256:<hex>' (digest-pinned)
      - ends with ':<version>' where the version part contains a digit
      - the last path segment itself contains a digit (e.g. claude-opus-4-7,
        gemini-3-5-flash, Qwen3-Coder-30B-A3B-Instruct)
    """
    if not ref or ref.strip().lower() == "latest":
        return False

    # Digest-pinned always wins.
    if _DIGEST_RE.search(ref):
        return True

    # ':latest' tag
    if _LATEST_RE.search(ref):
        return False

    # Decompose: if there's a ':' after the last '/', that is the tag.
    # Otherwise the last path segment IS the version-carrying component.
    last_segment = ref.rsplit("/", 1)[-1]  # e.g. 'claude-opus-4-7', 'gemini-3.1-pro-preview'

    # If the last segment has a ':<tag>' suffix, inspect the tag part.
    if ":" in last_segment:
        tag = last_segment.rsplit(":", 1)[-1]
        return bool(_VERSION_COMPONENT_RE.search(tag))

    # No ':tag' — the segment itself must carry a digit (version embedded in name).
    return bool(_VERSION_COMPONENT_RE.search(last_segment))


def compute_harness_config_sha(d: dict) -> str:
    """Compute a stable sha256 over the harness-config fields.

    The canonical set of fields is: reasoning_effort, system_prompt_sha,
    thinking, compaction. Only keys present in `d` are included (missing keys
    are silently omitted, not treated as errors — they may not exist yet).

    Canonicalisation: JSON with sorted keys + no extra whitespace, UTF-8 encoded.
    This ensures dict insertion order does not affect the digest.
    """
    _HARNESS_KEYS = ("reasoning_effort", "system_prompt_sha", "thinking", "compaction")
    canonical = {k: d[k] for k in _HARNESS_KEYS if k in d}
    serialised = json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(serialised).hexdigest()


def evaluate(config_texts: Iterable[str]) -> tuple[bool, list[str]]:
    """Scan all config texts; return (ok, reasons).

    `ok` is True iff every discovered model ref is pinned.
    `reasons` lists one entry per unpinned ref (empty on success).
    """
    reasons: list[str] = []
    for text in config_texts:
        for ref in find_model_refs(text):
            if not is_pinned_model(ref):
                reasons.append(f"unpinned model ref: {ref!r}")
    return (len(reasons) == 0, reasons)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

_DEFAULT_PATHS = (
    "config/hermes/model-tiers.yaml",
    "config/limits.yaml",
    "deploy/litellm/config.yaml",
)


def _parse_harness_kv(raw: str) -> dict:
    """Parse 'key=val,key2=val2' into a dict (values are kept as strings)."""
    result: dict = {}
    for pair in raw.split(","):
        pair = pair.strip()
        if not pair:
            continue
        if "=" not in pair:
            raise ValueError(f"harness-config entry must be key=val, got: {pair!r}")
        k, v = pair.split("=", 1)
        result[k.strip()] = v.strip()
    return result


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="SP-00i model-pin gate + harness-config sha stamp")
    ap.add_argument(
        "--paths",
        default=",".join(_DEFAULT_PATHS),
        help=(
            f"Comma-separated list of config files to scan (default: {','.join(_DEFAULT_PATHS)})"
        ),
    )
    ap.add_argument(
        "--harness-config",
        default="",
        help=(
            "Optional harness-config overrides as key=val,... pairs. "
            "Keys: reasoning_effort, system_prompt_sha, thinking, compaction. "
            "When provided, the sha is stamped into the step summary."
        ),
    )
    args = ap.parse_args(argv)

    paths = [p.strip() for p in args.paths.split(",") if p.strip()]

    # Load config texts.
    config_texts: list[str] = []
    for path in paths:
        try:
            with open(path) as fh:
                config_texts.append(fh.read())
        except OSError as e:
            print(f"::error::model-pin-gate: cannot read {path}: {e}")
            return 1

    # Evaluate all refs.
    ok, reasons = evaluate(config_texts)
    for reason in reasons:
        print(f"::error::model-pin-gate: {reason}")

    # Compute harness-config sha if provided.
    harness_sha: str | None = None
    if args.harness_config.strip():
        try:
            hc = _parse_harness_kv(args.harness_config)
            harness_sha = compute_harness_config_sha(hc)
        except ValueError as e:
            print(f"::error::model-pin-gate: --harness-config parse error: {e}")
            return 1

    # Emit the harness sha into GITHUB_STEP_SUMMARY if running in GHA.
    import os

    summary_path = os.environ.get("GITHUB_STEP_SUMMARY", "")

    total_refs = sum(len(find_model_refs(t)) for t in config_texts)
    status = "PASS" if ok else "FAIL"

    print(
        f"== model-pin-gate: scanned {len(paths)} config file(s), "
        f"{total_refs} model ref(s), {len(reasons)} unpinned => {status} =="
    )
    if harness_sha:
        print(f"harness-config-sha: {harness_sha}")

    if summary_path:
        with open(summary_path, "a") as sf:
            sf.write("### SP-00i model-pin-gate\n")
            sf.write(f"- Status: **{status}**\n")
            sf.write(f"- Scanned {len(paths)} config file(s), {total_refs} model ref(s)\n")
            if reasons:
                sf.write("- Unpinned refs:\n")
                for r in reasons:
                    sf.write(f"  - `{r}`\n")
            if harness_sha:
                sf.write(f"- Harness-config sha: `{harness_sha}`\n")
            sf.write("\n")
            sf.write(
                "#### SP-G1 regression gate (ADVISORY — SP-G1 not yet built)\n"
                "When a model or harness-config change is detected in the PR diff, "
                "the SP-G1 golden set MUST be re-run with no regression before merge. "
                "**This check is advisory only until SP-G1 is built (task #11). "
                "See docs/architecture/sp-g1-advisory.md for the no-op control.**\n\n"
                "No-op control: a PR that changes NEITHER a scanned config file NOR the "
                "harness-config sha does not trigger an SP-G1 re-run.\n"
            )

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
