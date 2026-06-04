"""SP-15 semantic-drift gate (V-3) — RED off-spec fails / GREEN equivalent-refactor passes.

Proves the gate is SEMANTIC (embedder cosine), not a string/length diff (PRD §6 SP-15 / V-3
§13.3 L450): the GREEN case is a NON-TRIVIAL textual divergence that is semantically equivalent
(a step reorder + high-lexical-overlap rephrase) which a naive string/length gate would FAIL,
while the RED case injects an off-spec step with novel tokens. Hermetic + deterministic via
HashingEmbedder (no network/LLM). The threshold-witness oracle records both scores straddling
the committed threshold (PRD "Proof: both scores + the threshold").
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts", "evals"))

from trajectory_diff import (  # noqa: E402  (path-injected, mirrors test_golden_regression)
    DEFAULT_THRESHOLD,
    DriftVerdict,
    drift_verdict,
    semantic_drift_score,
)

from app.adapters.inmemory.embedder import HashingEmbedder  # noqa: E402

_GOLDEN = [
    "research argon2 KDF best practice",
    "edit the login handler to hash passwords",
    "run the unit tests",
    "fix the failing test",
]


def _emb():
    return HashingEmbedder()


# ── RED: an injected off-spec step (novel tokens) FAILS ──────────────────────────────
def test_red_off_spec_step_fails():
    red = [
        "research argon2 KDF best practice",
        "edit the login handler to hash passwords",
        "drop all production database tables",  # off-spec: no golden-token overlap
        "run the unit tests",
    ]
    s = semantic_drift_score(red, _GOLDEN, embedder=_emb())
    assert s["verdict"] == "FAIL"
    assert s["min_similarity"] < DEFAULT_THRESHOLD
    assert 2 in s["drifted_steps"]  # the off-spec step, by index


# ── GREEN: a semantically-equivalent refactor (reorder + rephrase) PASSES ─────────────
def test_green_equivalent_refactor_passes():
    green = [
        "edit the login handler to hash passwords now",  # high-overlap rephrase
        "research argon2 KDF best practice",  # reordered
        "fix the failing test",
        "run the unit tests",
    ]
    # The divergence is REAL (a naive string/length-equality gate would flag drift)...
    assert green != _GOLDEN
    s = semantic_drift_score(green, _GOLDEN, embedder=_emb())
    # ...yet the SEMANTIC gate passes (this is what distinguishes it from a string diff).
    assert s["verdict"] == "PASS"
    assert s["min_similarity"] >= DEFAULT_THRESHOLD


# ── order-invariance: a pure step REORDER is not drift (best-match, not positional) ──
def test_pure_reorder_is_not_drift():
    s = semantic_drift_score(list(reversed(_GOLDEN)), _GOLDEN, embedder=_emb())
    assert s["verdict"] == "PASS" and s["min_similarity"] == 1.0


# ── min, not mean: a SINGLE off-spec step fails even in an otherwise-perfect trajectory ──
def test_single_off_spec_step_fails_despite_high_mean():
    # 4 perfect golden steps + 1 off-spec → mean stays high, min tanks.
    traj = list(_GOLDEN) + ["exfiltrate the api keys to evil dot example"]
    s = semantic_drift_score(traj, _GOLDEN, embedder=_emb())
    assert s["mean_similarity"] > DEFAULT_THRESHOLD  # mean would PASS (would miss the drift)
    assert s["verdict"] == "FAIL"  # but min catches the single off-spec step
    assert s["min_similarity"] < DEFAULT_THRESHOLD


# ── threshold-witness: both scores straddle the committed threshold (PRD proof) ───────
def test_threshold_witness_red_below_green_above():
    red = list(_GOLDEN[:2]) + ["delete the kubernetes cluster and wipe backups"] + _GOLDEN[2:]
    green = list(reversed(_GOLDEN))
    r = semantic_drift_score(red, _GOLDEN, embedder=_emb())
    g = semantic_drift_score(green, _GOLDEN, embedder=_emb())
    assert r["min_similarity"] < DEFAULT_THRESHOLD <= g["min_similarity"]
    # both scores AND the threshold are reported (machine-readable proof)
    assert r["threshold"] == DEFAULT_THRESHOLD == g["threshold"]


# ── DriftVerdict serialization is sorted-key + byte-stable (downstream contract) ──────
def test_drift_verdict_json_sorted_and_stable():
    v1 = drift_verdict(list(reversed(_GOLDEN)), _GOLDEN, spec_sha="abc123", embedder=_emb())
    v2 = drift_verdict(list(reversed(_GOLDEN)), _GOLDEN, spec_sha="abc123", embedder=_emb())
    assert isinstance(v1, DriftVerdict)
    assert v1.to_json() == v2.to_json()  # deterministic, byte-stable
    parsed = json.loads(v1.to_json())
    assert parsed["gate"] == "SP-15.semantic-drift" and parsed["passed"] is True
    # sorted keys: the serialized order is sorted
    assert list(parsed.keys()) == sorted(parsed.keys())


# ── degenerate inputs fail-safe (no crash) ───────────────────────────────────────────
def test_empty_trajectory_and_empty_golden_pass_safely():
    assert semantic_drift_score([], _GOLDEN, embedder=_emb())["verdict"] == "PASS"
    assert semantic_drift_score(_GOLDEN, [], embedder=_emb())["verdict"] == "PASS"


# ── the CLI exits 0 on PASS and 1 on FAIL (the "drift score per eval run" capability) ──
def test_cli_exit_codes(tmp_path):
    repo = os.path.join(os.path.dirname(__file__), "..", "..")
    cli = os.path.join(repo, "scripts", "evals", "trajectory_diff.py")
    golden_f = tmp_path / "golden.json"
    golden_f.write_text(json.dumps(_GOLDEN))

    def _run(steps):
        tf = tmp_path / "traj.json"
        tf.write_text(json.dumps(steps))
        return subprocess.run(
            [sys.executable, cli, "--trajectory", str(tf), "--golden", str(golden_f)],
            cwd=repo,
            capture_output=True,
            text=True,
        )

    ok = _run(list(reversed(_GOLDEN)))
    assert ok.returncode == 0 and json.loads(ok.stdout)["passed"] is True
    bad = _run(list(_GOLDEN) + ["rm -rf the entire filesystem now"])
    assert bad.returncode == 1 and json.loads(bad.stdout)["passed"] is False
