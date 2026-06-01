#!/usr/bin/env python3
"""TDD unit tests for scripts/evals/golden_regression.py — SP-G1.

Each test is self-contained and uses in-memory fixtures (NOT the real corpus).
Tests are ordered to mirror the red-green cycle described in SP-G1 acceptance:
  - valid item passes
  - invalid item (missing field, catch-all glob, bad phase) fails
  - tampered SHA triggers drift failure
  - runner emits per-item pass/fail

These tests MUST be hermetic (no network, no disk for corpus content).
They import the runner module directly and call its pure functions.
"""

from __future__ import annotations

import os
import sys
import textwrap

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts", "evals"))

from golden_regression import (  # noqa: E402
    parse_sha_manifest,
    run_regression,
    sha256_bytes,
    sha256_file,
    validate_dag,
    validate_item,
    validate_node,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_VALID_NODE = {
    "id": "n1",
    "phase": "build",
    "summary": "implement the feature",
    "depends_on": [],
    "acceptance_ref": "SP-00.1 / some-criterion",
    "allowed_paths": ["app/core/feature.py"],
}

_VALID_ITEM = {
    "goal": "Add a feature",
    "version": "v0.1.0",
    "notes": "CANDIDATE — needs-operator-signoff",
    "expected_dag": [dict(_VALID_NODE)],
    "expected_criteria": ["feature works"],
    "expected_trajectory": [{"phase": "build", "description": "implement"}],
}


def _item(**overrides) -> dict:
    """Return a copy of _VALID_ITEM with overrides applied."""
    item = {**_VALID_ITEM, "expected_dag": [dict(_VALID_NODE)]}
    item.update(overrides)
    return item


def _node(**overrides) -> dict:
    """Return a copy of _VALID_NODE with overrides applied."""
    node = dict(_VALID_NODE)
    node.update(overrides)
    return node


def _write_corpus_and_manifest(items: list[dict], tmp_dir: str) -> tuple[str, str, str]:
    """Write corpus.yaml + SHA256SUMS + VERSION to tmp_dir.  Returns (corpus, manifest, version) paths."""
    import yaml  # PyYAML must be available in the test environment

    corpus_path = os.path.join(tmp_dir, "corpus.yaml")
    manifest_path = os.path.join(tmp_dir, "SHA256SUMS")
    version_path = os.path.join(tmp_dir, "VERSION")

    with open(corpus_path, "w") as fh:
        yaml.dump(items, fh, allow_unicode=True, default_flow_style=False)

    with open(version_path, "w") as fh:
        fh.write("v0.1.0\n")

    # Compute hashes and write manifest
    corpus_hash = sha256_file(corpus_path)
    version_hash = sha256_file(version_path)

    with open(manifest_path, "w") as fh:
        fh.write(f"{corpus_hash}  corpus.yaml\n")
        fh.write(f"{version_hash}  VERSION\n")

    return corpus_path, manifest_path, version_path


# ---------------------------------------------------------------------------
# validate_node tests
# ---------------------------------------------------------------------------


def test_valid_node_has_no_errors():
    errors = validate_node(_VALID_NODE, "test goal")
    assert errors == [], f"unexpected errors: {errors}"


def test_node_missing_required_field():
    for field in ("id", "phase", "summary", "depends_on", "acceptance_ref", "allowed_paths"):
        node = _node()
        del node[field]
        errors = validate_node(node, "test goal")
        assert any(field in e.field for e in errors), f"missing {field} not flagged"


def test_node_catch_all_glob_rejected():
    """SP-02/2.6: catch-all globs are FORBIDDEN."""
    for catch_all in ("**", "*", ".", "/", "**/*"):
        node = _node(allowed_paths=[catch_all])
        errors = validate_node(node, "test goal")
        assert any(
            "catch-all" in e.message for e in errors
        ), f"catch-all glob {catch_all!r} was not rejected"


def test_node_concrete_glob_passes():
    """A concrete (non-catch-all) glob is allowed."""
    node = _node(allowed_paths=["app/core/feature.py", "tests/unit/test_feature.py"])
    errors = validate_node(node, "test goal")
    assert errors == [], f"unexpected errors: {errors}"


def test_node_empty_allowed_paths_rejected():
    node = _node(allowed_paths=[])
    errors = validate_node(node, "test goal")
    assert any("allowed_paths" in e.field for e in errors), "empty allowed_paths not rejected"


def test_node_invalid_phase_rejected():
    node = _node(phase="undefined_phase_xyz")
    errors = validate_node(node, "test goal")
    assert any("phase" in e.field for e in errors), "invalid phase not flagged"


def test_node_valid_phases_accepted():
    """All valid SDLC phases should pass."""
    for phase in ("intake", "clarify", "plan", "build", "test", "review", "ship", "operate"):
        node = _node(phase=phase)
        errors = validate_node(node, "test goal")
        phase_errors = [e for e in errors if "phase" in e.field]
        assert not phase_errors, f"valid phase {phase!r} was rejected: {phase_errors}"


# ---------------------------------------------------------------------------
# validate_dag tests
# ---------------------------------------------------------------------------


def test_dag_acyclic_passes():
    nodes = [
        _node(id="a", depends_on=[]),
        _node(id="b", depends_on=["a"]),
        _node(id="c", depends_on=["b"]),
    ]
    errors = validate_dag(nodes, "test goal")
    cycle_errors = [e for e in errors if "cycle" in e.message]
    assert not cycle_errors, f"acyclic DAG was flagged: {cycle_errors}"


def test_dag_cycle_detected():
    """A depends_on cycle must be detected."""
    nodes = [
        _node(id="a", depends_on=["b"]),
        _node(id="b", depends_on=["a"]),
    ]
    errors = validate_dag(nodes, "test goal")
    assert any("cycle" in e.message for e in errors), "cycle not detected"


def test_dag_unknown_dep_flagged():
    nodes = [_node(id="a", depends_on=["nonexistent_id"])]
    errors = validate_dag(nodes, "test goal")
    assert any("nonexistent_id" in e.message for e in errors), "unknown dep not flagged"


def test_dag_duplicate_ids_flagged():
    nodes = [_node(id="a"), _node(id="a")]
    errors = validate_dag(nodes, "test goal")
    assert any("duplicate" in e.message for e in errors), "duplicate id not flagged"


# ---------------------------------------------------------------------------
# validate_item tests
# ---------------------------------------------------------------------------


def test_valid_item_has_no_errors():
    errors = validate_item(_VALID_ITEM)
    assert errors == [], f"unexpected errors: {errors}"


def test_item_missing_required_field():
    for field in (
        "goal",
        "expected_dag",
        "expected_criteria",
        "expected_trajectory",
        "version",
        "notes",
    ):
        item = _item()
        del item[field]
        errors = validate_item(item)
        assert any(field in e.field for e in errors), f"missing {field} not flagged"


def test_item_empty_dag_rejected():
    item = _item(expected_dag=[])
    errors = validate_item(item)
    assert any("expected_dag" in e.field for e in errors), "empty dag not rejected"


def test_item_empty_criteria_rejected():
    item = _item(expected_criteria=[])
    errors = validate_item(item)
    assert any("expected_criteria" in e.field for e in errors), "empty criteria not rejected"


def test_item_catch_all_in_dag_is_rejected():
    """A golden item with a catch-all allowed_paths should fail validation.

    This is the C9 focus check: the corpus must enforce non-catch-all.
    """
    item = _item(expected_dag=[_node(allowed_paths=["**"])])
    errors = validate_item(item)
    assert any(
        "catch-all" in e.message for e in errors
    ), "item with catch-all allowed_paths in DAG was not rejected"


def test_item_dag_with_concrete_paths_passes():
    item = _item(
        expected_dag=[
            _node(id="a", depends_on=[], allowed_paths=["app/core/foo.py"]),
            _node(id="b", depends_on=["a"], allowed_paths=["tests/unit/test_foo.py"]),
        ]
    )
    errors = validate_item(item)
    assert errors == [], f"valid multi-node DAG was rejected: {errors}"


# ---------------------------------------------------------------------------
# sha256 utilities
# ---------------------------------------------------------------------------


def test_sha256_bytes_deterministic():
    data = b"hello world"
    h1 = sha256_bytes(data)
    h2 = sha256_bytes(data)
    assert h1 == h2
    assert len(h1) == 64


def test_sha256_file_matches_bytes(tmp_path):
    p = tmp_path / "test.txt"
    p.write_bytes(b"golden corpus content")
    assert sha256_file(str(p)) == sha256_bytes(b"golden corpus content")


def test_parse_sha_manifest(tmp_path):
    manifest_content = textwrap.dedent("""\
        # a comment
        abc123  corpus.yaml
        def456  schema.json
    """)
    p = tmp_path / "SHA256SUMS"
    p.write_text(manifest_content)
    manifest = parse_sha_manifest(str(p))
    assert manifest == {"corpus.yaml": "abc123", "schema.json": "def456"}


# ---------------------------------------------------------------------------
# Tamper detection — the load-bearing red-green
# ---------------------------------------------------------------------------


def test_tampered_corpus_triggers_drift(tmp_path):
    """CRITICAL: a corpus file tampered after SHA pinning must cause run_regression to fail.

    This is the 'tamper->fail' non-vacuous red-green required by SP-G1 acceptance.
    """
    try:
        import yaml  # noqa: F401  (availability probe: skip if PyYAML absent)
    except ImportError:
        import pytest

        pytest.skip("PyYAML not available — install with: uv run --with pyyaml")

    items = [_VALID_ITEM]
    corpus_path, manifest_path, version_path = _write_corpus_and_manifest(items, str(tmp_path))

    # Tamper: add an extra item AFTER the hash was recorded
    with open(corpus_path, "a") as fh:
        fh.write("\n- goal: injected item\n")

    # Reload manifest to confirm the recorded hash doesn't match the tampered file
    manifest = parse_sha_manifest(manifest_path)
    recorded_hash = manifest.get("corpus.yaml", "")
    actual_hash = sha256_file(corpus_path)
    assert recorded_hash != actual_hash, "test setup error: tamper did not change hash"

    # run_regression must detect the drift and return ok=False
    _results, ok = run_regression(corpus_path, manifest_path, version_path)
    assert not ok, "tampered corpus must cause run_regression to return ok=False"


def test_untampered_corpus_passes(tmp_path):
    """GREEN control: a correctly pinned corpus returns ok=True."""
    try:
        import yaml  # noqa: F401  (availability probe: skip if PyYAML absent)
    except ImportError:
        import pytest

        pytest.skip("PyYAML not available — install with: uv run --with pyyaml")

    items = [_VALID_ITEM]
    corpus_path, manifest_path, version_path = _write_corpus_and_manifest(items, str(tmp_path))

    results, ok = run_regression(corpus_path, manifest_path, version_path)
    assert ok, f"untampered corpus must pass; results={results}"
    assert len(results) == 1
    assert results[0].passed


# ---------------------------------------------------------------------------
# Invalid item in corpus causes runner to fail
# ---------------------------------------------------------------------------


def test_invalid_item_in_corpus_fails_runner(tmp_path):
    """An item with a catch-all allowed_paths must fail the runner.

    Non-vacuous: runner exit is non-zero, not just a warning.
    """
    try:
        import yaml  # noqa: F401  (availability probe: skip if PyYAML absent)
    except ImportError:
        import pytest

        pytest.skip("PyYAML not available — install with: uv run --with pyyaml")

    # Build an item that is structurally invalid (catch-all glob)
    bad_item = _item(expected_dag=[_node(allowed_paths=["**"])])
    items = [bad_item]
    corpus_path, manifest_path, version_path = _write_corpus_and_manifest(items, str(tmp_path))

    results, ok = run_regression(corpus_path, manifest_path, version_path)
    assert not ok, "invalid item must cause ok=False"
    assert len(results) == 1
    assert not results[0].valid


def test_valid_item_passes_runner(tmp_path):
    """GREEN control: a single valid item passes the runner."""
    try:
        import yaml  # noqa: F401  (availability probe: skip if PyYAML absent)
    except ImportError:
        import pytest

        pytest.skip("PyYAML not available — install with: uv run --with pyyaml")

    items = [_VALID_ITEM]
    corpus_path, manifest_path, version_path = _write_corpus_and_manifest(items, str(tmp_path))

    results, ok = run_regression(corpus_path, manifest_path, version_path)
    assert ok, f"valid item must pass; results={results}"


# ---------------------------------------------------------------------------
# Per-item pass/fail output (SP-G1 acceptance: "emits per-item pass/fail")
# ---------------------------------------------------------------------------


def test_results_list_has_per_item_result(tmp_path):
    """run_regression returns one ItemResult per corpus item."""
    try:
        import yaml  # noqa: F401  (availability probe: skip if PyYAML absent)
    except ImportError:
        import pytest

        pytest.skip("PyYAML not available")

    items = [_VALID_ITEM, _item(goal="Second goal")]
    corpus_path, manifest_path, version_path = _write_corpus_and_manifest(items, str(tmp_path))

    results, _ok = run_regression(corpus_path, manifest_path, version_path)
    assert len(results) == 2, f"expected 2 results, got {len(results)}"
    goals = {r.goal for r in results}
    assert "Add a feature" in goals
    assert "Second goal" in goals
