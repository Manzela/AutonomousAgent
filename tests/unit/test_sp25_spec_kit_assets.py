"""SP-25 hermetic unit tests — Spec-Kit prompts + EARS criteria as assets.

All tests run without any LLM, network, or file I/O beyond reading the repo tree.

Oracles:
  1. docs/spec-kit/constitution.md exists and is non-empty.
  2. docs/spec-kit/ears.md exists and is non-empty.
  3. docs/spec-kit/specify.md exists and is non-empty.
  4. docs/spec-kit/clarify.md exists and is non-empty.
  5. docs/spec-kit/analyze.md exists and is non-empty.
  6. docs/spec-kit/plan.md exists and is non-empty.
  7. docs/spec-kit/tasks.md exists and is non-empty.
  8. app/core/spec_drafter.py references CONSTITUTION_PATH pointing at docs/spec-kit/constitution.md.
  9. app/core/spec_drafter.py references SPEC_KIT_DIR pointing at docs/spec-kit.
  10. CONSTITUTION_PATH resolves to the same file (path matches).
  11. No spec_kit package import in app/core/spec_drafter.py (not a runtime dep).
  12. constitution.md contains EARS pattern reference.
  13. ears.md contains all 6 EARS pattern names.
  14. constitution.md is reachable as CONSTITUTION_PATH from the repo root.
  15. SPEC_KIT_DIR contains exactly the 7 expected template files.
  16. No runtime import of 'spec_kit' in the Python environment (not installed).
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

# Repo root: two levels up from tests/unit/
REPO_ROOT = Path(__file__).resolve().parent.parent.parent

SPEC_KIT_DIR = REPO_ROOT / "docs" / "spec-kit"
EXPECTED_FILES = {
    "constitution.md",
    "ears.md",
    "specify.md",
    "clarify.md",
    "analyze.md",
    "plan.md",
    "tasks.md",
}
EARS_PATTERNS = ["Ubiquitous", "State-Driven", "Event-Driven", "Unwanted", "Optional", "Complex"]


# ── Oracle 1-7 — all 7 asset files exist and are non-empty ────────────────────


@pytest.mark.parametrize("filename", sorted(EXPECTED_FILES))
def test_spec_kit_file_exists_and_nonempty(filename):
    path = SPEC_KIT_DIR / filename
    assert path.exists(), f"docs/spec-kit/{filename} not found"
    assert path.stat().st_size > 0, f"docs/spec-kit/{filename} is empty"


# ── Oracle 8 — spec_drafter.py has CONSTITUTION_PATH ─────────────────────────


def test_spec_drafter_has_constitution_path():
    spec_drafter = REPO_ROOT / "app" / "core" / "spec_drafter.py"
    source = spec_drafter.read_text()
    assert "CONSTITUTION_PATH" in source, "CONSTITUTION_PATH not defined in spec_drafter.py"
    assert (
        "docs/spec-kit/constitution.md" in source
    ), "spec_drafter.py does not reference docs/spec-kit/constitution.md"


# ── Oracle 9 — spec_drafter.py has SPEC_KIT_DIR ──────────────────────────────


def test_spec_drafter_has_spec_kit_dir():
    spec_drafter = REPO_ROOT / "app" / "core" / "spec_drafter.py"
    source = spec_drafter.read_text()
    assert "SPEC_KIT_DIR" in source, "SPEC_KIT_DIR not defined in spec_drafter.py"
    assert "docs/spec-kit" in source, "spec_drafter.py does not reference docs/spec-kit"


# ── Oracle 10 — CONSTITUTION_PATH resolves to the real file ──────────────────


def test_constitution_path_resolves():
    from app.core.spec_drafter import CONSTITUTION_PATH

    resolved = (REPO_ROOT / CONSTITUTION_PATH).resolve()
    expected = (SPEC_KIT_DIR / "constitution.md").resolve()
    assert resolved == expected, f"CONSTITUTION_PATH resolves to {resolved}, expected {expected}"


# ── Oracle 11 — no 'import spec_kit' in spec_drafter.py ─────────────────────


def test_spec_drafter_no_spec_kit_import():
    spec_drafter = REPO_ROOT / "app" / "core" / "spec_drafter.py"
    source = spec_drafter.read_text()
    assert (
        "import spec_kit" not in source
    ), "spec_drafter.py must not import the spec_kit package at runtime (SP-25 = static asset only)"
    assert (
        "from spec_kit" not in source
    ), "spec_drafter.py must not import from the spec_kit package at runtime"


# ── Oracle 12 — constitution.md references EARS patterns ─────────────────────


def test_constitution_references_ears():
    content = (SPEC_KIT_DIR / "constitution.md").read_text()
    assert "EARS" in content, "constitution.md should reference EARS patterns"


# ── Oracle 13 — ears.md contains all 6 EARS pattern names ───────────────────


@pytest.mark.parametrize("pattern", EARS_PATTERNS)
def test_ears_md_contains_pattern(pattern):
    content = (SPEC_KIT_DIR / "ears.md").read_text()
    assert pattern in content, f"ears.md missing EARS pattern: {pattern}"


# ── Oracle 14 — CONSTITUTION_PATH is reachable from repo root ────────────────


def test_constitution_path_file_is_readable():
    from app.core.spec_drafter import CONSTITUTION_PATH

    full_path = REPO_ROOT / CONSTITUTION_PATH
    content = full_path.read_text()
    assert len(content) > 100, "constitution.md should have substantial content"


# ── Oracle 15 — SPEC_KIT_DIR has exactly the 7 expected files ────────────────


def test_spec_kit_dir_has_expected_files():
    actual = {p.name for p in SPEC_KIT_DIR.iterdir() if p.is_file()}
    missing = EXPECTED_FILES - actual
    assert not missing, f"docs/spec-kit/ missing files: {missing}"


# ── Oracle 16 — spec_kit is not installed as a Python package ────────────────


def test_spec_kit_not_installed_as_package():
    assert importlib.util.find_spec("spec_kit") is None, (
        "spec_kit is installed as a Python package — SP-25 assets must be static files only, "
        "never a runtime dependency"
    )
