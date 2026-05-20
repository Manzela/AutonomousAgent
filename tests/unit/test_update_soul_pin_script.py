"""Smoke tests for scripts/update_soul_pin.sh.

Asserts that:
  1. running the script against a synthetic repo with a matching pin
     leaves the file byte-identical (idempotent — the happy path the
     CONTRIBUTING.md docs promise);
  2. running it against a stale pin rewrites the line and only that
     line (no collateral edits, no trailing whitespace, no comment
     stomping).

These tests intentionally do NOT touch the real config/limits.yaml or
the real SOUL.md — they spin up a tmp_path "mini repo" and point the
script at it via $BASH_SOURCE relative-path resolution.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import stat
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "update_soul_pin.sh"


def _build_fake_repo(tmp_path: Path, soul_content: bytes, pinned_hash: str) -> Path:
    """Lay out scripts/, config/, config/hermes/ + copy the real script in."""
    (tmp_path / "scripts").mkdir()
    (tmp_path / "config" / "hermes").mkdir(parents=True)

    target_script = tmp_path / "scripts" / "update_soul_pin.sh"
    shutil.copy2(SCRIPT, target_script)
    # Ensure +x on the copy regardless of how the original landed on disk.
    target_script.chmod(target_script.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    (tmp_path / "config" / "hermes" / "SOUL.md").write_bytes(soul_content)
    (tmp_path / "config" / "limits.yaml").write_text(
        "# minimal fixture\n"
        "integrity:\n"
        f"  soul_md_sha256: {pinned_hash}  # pragma: allowlist secret\n"
    )
    return target_script


def test_script_exists_and_is_executable():
    """Sanity: the script we ship in the repo is +x."""
    assert SCRIPT.is_file(), f"missing: {SCRIPT}"
    assert os.access(SCRIPT, os.X_OK), f"not executable: {SCRIPT}"


def test_idempotent_when_pin_matches(tmp_path):
    """If the pin is already current, the YAML must be byte-identical after."""
    soul = b"# fake persona\nhonesty: high\n"
    digest = hashlib.sha256(soul).hexdigest()
    script = _build_fake_repo(tmp_path, soul, digest)

    yaml_path = tmp_path / "config" / "limits.yaml"
    before = yaml_path.read_bytes()

    result = subprocess.run(
        ["bash", str(script)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"script failed: {result.stderr}"
    assert "already current" in result.stdout, result.stdout
    assert yaml_path.read_bytes() == before, "limits.yaml mutated on a no-op run"


def test_updates_stale_pin(tmp_path):
    """If the pin is stale, the line is rewritten — and only that line."""
    soul = b"# fake persona v2\nhonesty: very high\n"
    new_digest = hashlib.sha256(soul).hexdigest()
    stale = "0" * 64
    script = _build_fake_repo(tmp_path, soul, stale)

    yaml_path = tmp_path / "config" / "limits.yaml"
    before_lines = yaml_path.read_text().splitlines()

    result = subprocess.run(
        ["bash", str(script)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"script failed: {result.stderr}"

    after_lines = yaml_path.read_text().splitlines()
    assert len(before_lines) == len(after_lines), "line count changed"

    diffs = [(b, a) for b, a in zip(before_lines, after_lines) if b != a]
    assert len(diffs) == 1, f"expected exactly one line changed, got {diffs}"
    before, after = diffs[0]
    assert stale in before
    assert new_digest in after
    # Preserve the inline pragma comment so detect-secrets stays happy.
    assert "pragma: allowlist secret" in after


def test_fails_loudly_if_yaml_missing(tmp_path):
    """Missing config/limits.yaml -> non-zero exit with a clear message."""
    (tmp_path / "scripts").mkdir()
    (tmp_path / "config" / "hermes").mkdir(parents=True)
    script = tmp_path / "scripts" / "update_soul_pin.sh"
    shutil.copy2(SCRIPT, script)
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    (tmp_path / "config" / "hermes" / "SOUL.md").write_bytes(b"x")
    # NOTE: no limits.yaml written.

    result = subprocess.run(
        ["bash", str(script)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "limits.yaml" in result.stderr or "missing" in result.stderr
