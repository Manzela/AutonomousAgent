"""Tests for the SP-00e `acceptance-frozen` gate (C8/C10).

The gate proves the C8 contract: a task's sha-pinned acceptance file MUST NOT
drift relative to the CODEOWNERS-guarded manifest `audit/acceptance/SHA256SUMS`.
A PR that mutates an acceptance file without an operator-approved manifest update
is caught here (sha drift); the manifest + dir are CODEOWNERS-guarded so a
deliberate same-PR goalpost-move requires 4-eyes (C10).

These exercise the pure verifier directly (no filesystem) so the behavior — not a
mock — is under test.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts", "ci"))

import acceptance_frozen as af  # noqa: E402


def _sha(data: bytes) -> str:
    return af.compute_sha256(data)


def test_clean_tree_has_no_errors():
    files = {"SP-00.yaml": b"task: SP-00\n", "SP-01.yaml": b"task: SP-01\n"}
    manifest = {name: _sha(body) for name, body in files.items()}
    assert af.verify(manifest, files) == []


def test_sha_drift_is_detected():
    """An acceptance file edited without updating the manifest FAILS the gate."""
    original = b"task: SP-00\nexpect_exit: 0\n"
    files = {"SP-00.yaml": original}
    manifest = {"SP-00.yaml": _sha(original)}
    # mutate the file content only (the C8 goalpost-move)
    files["SP-00.yaml"] = b"task: SP-00\nexpect_exit: 1\n"
    errors = af.verify(manifest, files)
    assert errors, "drift must be reported"
    assert any("SP-00.yaml" in e and "drift" in e.lower() for e in errors)


def test_untracked_acceptance_file_is_detected():
    """A new acceptance file not recorded in the manifest FAILS the gate."""
    files = {"SP-00.yaml": b"task: SP-00\n", "SP-99.yaml": b"task: SP-99\n"}
    manifest = {"SP-00.yaml": _sha(files["SP-00.yaml"])}
    errors = af.verify(manifest, files)
    assert any("SP-99.yaml" in e and "untracked" in e.lower() for e in errors)


def test_missing_file_for_manifest_entry_is_detected():
    """A manifest entry whose acceptance file was deleted FAILS the gate."""
    files = {"SP-00.yaml": b"task: SP-00\n"}
    manifest = {
        "SP-00.yaml": _sha(files["SP-00.yaml"]),
        "SP-01.yaml": _sha(b"task: SP-01\n"),
    }
    errors = af.verify(manifest, files)
    assert any("SP-01.yaml" in e and "missing" in e.lower() for e in errors)


def test_parse_manifest_roundtrip_sha256sum_format():
    """Manifest is standard `sha256sum` format: '<64-hex>  <relpath>' (computed, not literal)."""
    sha_a = af.compute_sha256(b"task: SP-00\n")
    sha_b = af.compute_sha256(b"task: SP-01\n")
    text = f"{sha_a}  SP-00.yaml\n# a comment line is skipped\n{sha_b}  SP-01.yaml\n"
    parsed = af.parse_manifest(text)
    assert parsed["SP-00.yaml"] == sha_a
    assert parsed["SP-01.yaml"] == sha_b
    assert len(parsed) == 2  # the comment line was skipped
