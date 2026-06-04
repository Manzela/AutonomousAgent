"""Unit tests for prompt and model config integrity verification."""

from __future__ import annotations

import pytest
from lib.guardrails.integrity import verify_integrity, IntegrityError


def test_integrity_passes_on_unmodified_repo():
    # Verify that in the default workspace, our manifest check succeeds
    assert verify_integrity(fail_closed=True) is True


def test_integrity_fails_when_manifest_missing(tmp_path):
    # Verify behavior when manifest does not exist
    manifest = tmp_path / "NONEXISTENT.sha256"
    with pytest.raises(IntegrityError, match="Integrity manifest not found"):
        verify_integrity(manifest_path=manifest, fail_closed=True)

    # Warn posture returns False instead of raising
    assert verify_integrity(manifest_path=manifest, fail_closed=False) is False


def test_integrity_fails_when_file_mismatched(tmp_path):
    # Create a mock workspace
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    file_to_check = workspace / "config_file.yaml"
    file_to_check.write_bytes(b"original content")

    # Expected SHA256 of "original content"
    import hashlib

    expected_sha = hashlib.sha256(b"original content").hexdigest()

    # Write a manifest pointing to it
    manifest = tmp_path / "manifest.sha256"
    manifest.write_text(f"{expected_sha}  config_file.yaml\n")

    # Passes initially
    assert (
        verify_integrity(manifest_path=manifest, workspace_root=workspace, fail_closed=True) is True
    )

    # Mutate the file -> should fail!
    file_to_check.write_bytes(b"modified content")

    with pytest.raises(IntegrityError, match="SHA mismatch for config_file.yaml"):
        verify_integrity(manifest_path=manifest, workspace_root=workspace, fail_closed=True)

    assert (
        verify_integrity(manifest_path=manifest, workspace_root=workspace, fail_closed=False)
        is False
    )
