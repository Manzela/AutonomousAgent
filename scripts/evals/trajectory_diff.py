"""SP-15 semantic-drift gate — V-3 (RED off-spec fails / GREEN equivalent-refactor passes).

A deterministic, LLM-free drift scorer. It compares an agent TRAJECTORY (a list of step
strings — the AgentNote-stream precursor SP-27 will produce per-step) against a GOLDEN
trajectory using the SAME ``AbstractEmbedder`` SP-27 reuses for its per-step semantic-distance
signal (PRD §6 SP-27 L278: "reuse SP-15 embedder, per-step"). This module is that reuse anchor.

Scoring (the load-bearing design choice, empirically grounded in HashingEmbedder's behaviour —
order-invariant + token-sensitive):
  - Each trajectory step's similarity is its BEST cosine match to ANY golden step
    (best-match, NOT positional). Best-match is order-INsensitive at the step level, so a
    semantically-equivalent step REORDER scores ~1.0 and PASSES (positional alignment would
    false-flag a mere reorder as drift — verified). A step with novel off-spec tokens has no
    good golden match → low cosine.
  - The verdict FAILS iff ANY step's best-match drops below the threshold (``min`` drives the
    verdict, not ``mean``): a single injected off-spec step in an otherwise-good trajectory
    must fail (PRD §6 SP-15 "an injected off-spec step fails"); ``mean`` would average it out.
  - ``min``, ``mean``, and per-step similarities are all reported so SP-27 can consume the
    per-step signal and an operator can calibrate ``threshold`` against the sha-pinned golden
    corpus before the gate becomes merge-blocking.

HERMETIC: ``HashingEmbedder`` (deterministic SHA-256 buckets) needs no network/LLM, so RED/GREEN
are stable across machines. DEFERRED (staging tier, NOT built here): the Vertex
text-embedding judge + a DeepEval Faithfulness leaf + C9 cross-vendor judge-class inequality
(eval_gate.py documents the same SP-06 LLM-leaf deferral), the SP-27 per-step monitor that
consumes this scorer, and making the gate merge-blocking after corpus calibration.

CLI (the "drift score per eval run" capability): ``python scripts/evals/trajectory_diff.py
--trajectory traj.json --golden golden.json`` prints the verdict JSON and exits 0 (PASS) / 1
(FAIL).
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass, field

import numpy as np

# Default aggregate-cosine threshold. A reordered/high-lexical-overlap step scores high; an
# off-spec novel-token step scores low. Documented default — the locked, merge-blocking value
# is an operator/eval-owner calibration against the sha-pinned golden corpus (see module docstring).
DEFAULT_THRESHOLD = 0.75


@dataclass
class DriftVerdict:
    """Machine-readable drift verdict (sorted-key JSON; mirrors eval_gate.ScopeVerdict)."""

    gate: str
    spec_sha: str
    passed: bool
    threshold: float
    min_similarity: float
    mean_similarity: float
    per_step_similarities: list[float] = field(default_factory=list)
    drifted_steps: list[int] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True)


def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    """Cosine similarity of two L2-normalised vectors (AbstractEmbedder guarantees the norm),
    so the dot product IS the cosine. A zero vector (empty step) yields 0.0."""
    return float(np.dot(a, b))


def semantic_drift_score(
    trajectory_steps: list[str],
    golden_steps: list[str],
    *,
    embedder,
    threshold: float = DEFAULT_THRESHOLD,
) -> dict:
    """Score an agent trajectory against a golden one. Returns a dict with min/mean/per-step
    cosine similarities (best-match alignment), the drifted step indices, and a PASS/FAIL
    verdict (FAIL iff any step < threshold). Deterministic for a deterministic embedder."""
    if not trajectory_steps:
        # An empty trajectory has done nothing off-spec — nothing to score.
        return {
            "min_similarity": 1.0,
            "mean_similarity": 1.0,
            "per_step_similarities": [],
            "drifted_steps": [],
            "verdict": "PASS",
            "threshold": threshold,
        }
    if not golden_steps:
        # No golden to compare against → cannot assert drift. Conservative PASS (documented);
        # a real eval run always supplies a golden corpus.
        per = [1.0] * len(trajectory_steps)
        return {
            "min_similarity": 1.0,
            "mean_similarity": 1.0,
            "per_step_similarities": per,
            "drifted_steps": [],
            "verdict": "PASS",
            "threshold": threshold,
        }
    golden_vecs = [embedder.embed(g) for g in golden_steps]
    per_step: list[float] = []
    for step in trajectory_steps:
        sv = embedder.embed(step)
        best = max(_cosine(sv, gv) for gv in golden_vecs)
        per_step.append(round(best, 6))
    min_similarity = min(per_step)
    mean_similarity = round(sum(per_step) / len(per_step), 6)
    drifted = [i for i, s in enumerate(per_step) if s < threshold]
    return {
        "min_similarity": min_similarity,
        "mean_similarity": mean_similarity,
        "per_step_similarities": per_step,
        "drifted_steps": drifted,
        "verdict": "FAIL" if drifted else "PASS",
        "threshold": threshold,
    }


def drift_verdict(
    trajectory_steps: list[str],
    golden_steps: list[str],
    *,
    spec_sha: str,
    embedder,
    threshold: float = DEFAULT_THRESHOLD,
    gate: str = "SP-15.semantic-drift",
) -> DriftVerdict:
    """The machine-readable verdict callable (the eval-gate / SP-27 entry point)."""
    s = semantic_drift_score(trajectory_steps, golden_steps, embedder=embedder, threshold=threshold)
    return DriftVerdict(
        gate=gate,
        spec_sha=spec_sha,
        passed=(s["verdict"] == "PASS"),
        threshold=threshold,
        min_similarity=s["min_similarity"],
        mean_similarity=s["mean_similarity"],
        per_step_similarities=s["per_step_similarities"],
        drifted_steps=s["drifted_steps"],
    )


def _default_embedder():
    """The hermetic CI/nightly embedder (deterministic, no network). Staging swaps the Vertex
    text-embedding adapter at runtime via the same AbstractEmbedder seam.

    Run as a direct script (``python scripts/evals/trajectory_diff.py``), only scripts/evals/
    is on sys.path, so ``app`` is not importable — add the repo root before importing it."""
    import os

    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)
    from app.adapters.inmemory.embedder import HashingEmbedder

    return HashingEmbedder()


def _load_steps(path: str) -> list[str]:
    with open(path) as fh:
        data = json.load(fh)
    if not isinstance(data, list) or not all(isinstance(x, str) for x in data):
        raise ValueError(f"{path}: expected a JSON list of step strings")
    return data


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="SP-15 semantic-drift gate (V-3)")
    ap.add_argument("--trajectory", required=True, help="JSON list of agent step strings")
    ap.add_argument("--golden", required=True, help="JSON list of golden step strings")
    ap.add_argument("--spec-sha", default="", help="the locked TaskSpec sha (verdict metadata)")
    ap.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    args = ap.parse_args(argv)
    verdict = drift_verdict(
        _load_steps(args.trajectory),
        _load_steps(args.golden),
        spec_sha=args.spec_sha,
        embedder=_default_embedder(),
        threshold=args.threshold,
    )
    print(verdict.to_json())
    return 0 if verdict.passed else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
