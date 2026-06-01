"""Tests for scripts/encrypt_secrets.sh — SP-00b: rm plaintext on successful encrypt.

Red-green contract:
  - FAILS against the original WARN-only script (no rm after success)
  - PASSES after the rm-on-success edit

Two cases asserted:
  1. success path:  mock sops exits 0  -> plaintext file REMOVED, .sops output PRESENT
  2. failure path:  mock sops exits 1  -> plaintext file PRESERVED (NOT removed), .sops absent
"""

from __future__ import annotations

import os
import stat
import subprocess
import textwrap
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = _REPO_ROOT / "scripts" / "encrypt_secrets.sh"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_fake_sops(tmp_path: Path, exit_code: int) -> Path:
    """Write a tiny shell script that shadows the real sops binary.

    Named 'sops' so that prepending its parent dir to PATH intercepts all sops
    calls made by encrypt_secrets.sh.

    On --encrypt it writes the --output destination so the script sees the
    side-effect.  On --decrypt it emits a non-matching string so that the
    idempotency check (diff -q) fails and the script proceeds to the encrypt
    step.  Exits with exit_code for --encrypt; always exits 0 for --decrypt.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    sops_bin = bin_dir / "sops"
    if exit_code == 0:
        sops_bin.write_text(
            textwrap.dedent("""\
            #!/usr/bin/env sh
            # Handles both --encrypt and --decrypt to cover all code paths in
            # encrypt_secrets.sh (idempotency check + actual encrypt).
            if [ "$1" = "--decrypt" ]; then
                # Return non-matching output so diff -q fails and the script
                # proceeds to the encrypt step.
                printf 'NOT-MATCHING\\n'
                exit 0
            fi
            # --encrypt: parse --output <dest> and write a sentinel.
            dest=""
            while [ $# -gt 0 ]; do
                if [ "$1" = "--output" ]; then
                    dest="$2"
                    shift 2
                else
                    shift
                fi
            done
            if [ -n "$dest" ]; then
                printf 'encrypted-content\\n' > "$dest"
            fi
            exit 0
        """)
        )
    else:
        sops_bin.write_text(
            textwrap.dedent("""\
            #!/usr/bin/env sh
            echo "sops: simulated failure" >&2
            exit 1
        """)
        )
    sops_bin.chmod(sops_bin.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return sops_bin


def _make_fake_age_key(tmp_path: Path) -> Path:
    key_file = tmp_path / "keys.txt"
    key_file.write_text("# fake age key\nAGE-SECRET-KEY-1FAKE\n")
    return key_file


def _make_sops_yaml(tmp_path: Path) -> Path:
    sops_yaml = tmp_path / ".sops.yaml"
    sops_yaml.write_text("creation_rules:\n  - age: age1fake\n")
    return sops_yaml


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def workdir(tmp_path: Path):
    """Lay out a minimal repo skeleton the script expects."""
    secrets_dir = tmp_path / "secrets"
    secrets_dir.mkdir()
    plaintext = secrets_dir / "hermes-provider.env"
    plaintext.write_text("SECRET_KEY=hunter2\n")
    return tmp_path


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_script_exists_and_is_executable():
    """Sanity: the script is present and executable."""
    assert SCRIPT.is_file(), f"missing: {SCRIPT}"
    assert os.access(SCRIPT, os.X_OK), f"not executable: {SCRIPT}"


def test_success_removes_plaintext(workdir: Path):
    """After a successful sops --encrypt, plaintext MUST be deleted.

    RED against the original WARN-only script; GREEN after the rm-on-success edit.
    """
    age_key = _make_fake_age_key(workdir)
    _make_sops_yaml(workdir)
    fake_sops = _make_fake_sops(workdir, exit_code=0)

    plaintext = workdir / "secrets" / "hermes-provider.env"
    encrypted = workdir / "secrets" / "hermes-provider.env.sops"

    assert plaintext.exists(), "fixture setup: plaintext must exist before run"

    env = {
        **os.environ,
        "PATH": f"{fake_sops.parent.resolve()}:{os.environ['PATH']}",
        "SOPS_AGE_KEY_FILE": str(age_key),
        "HOME": str(workdir),
    }
    result = subprocess.run(
        ["bash", str(SCRIPT)],
        env=env,
        capture_output=True,
        text=True,
        cwd=str(workdir),
    )

    assert (
        result.returncode == 0
    ), f"Script should exit 0 on success.\nstdout={result.stdout}\nstderr={result.stderr}"
    assert encrypted.exists(), f"Encrypted output must exist after success.\nstderr={result.stderr}"
    assert not plaintext.exists(), (
        "Plaintext MUST be removed after successful encryption.\n"
        f"stdout={result.stdout}\nstderr={result.stderr}\n"
        "(RED on original WARN-only script — GREEN after rm-on-success edit.)"
    )


def test_failure_preserves_plaintext(workdir: Path):
    """When sops --encrypt fails, plaintext MUST NOT be deleted."""
    age_key = _make_fake_age_key(workdir)
    _make_sops_yaml(workdir)
    fake_sops = _make_fake_sops(workdir, exit_code=1)

    plaintext = workdir / "secrets" / "hermes-provider.env"

    assert plaintext.exists(), "fixture setup: plaintext must exist before run"

    env = {
        **os.environ,
        "PATH": f"{fake_sops.parent.resolve()}:{os.environ['PATH']}",
        "SOPS_AGE_KEY_FILE": str(age_key),
        "HOME": str(workdir),
    }
    result = subprocess.run(
        ["bash", str(SCRIPT)],
        env=env,
        capture_output=True,
        text=True,
        cwd=str(workdir),
    )

    # set -euo pipefail propagates sops failure; script must exit non-zero.
    assert (
        result.returncode != 0
    ), f"Script must propagate sops failure.\nstdout={result.stdout}\nstderr={result.stderr}"
    assert plaintext.exists(), (
        "Plaintext MUST be preserved when sops fails.\n"
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )
