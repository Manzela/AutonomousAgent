"""Tests for SP-00e.3 import-hygiene (C5).

C5: a clean-container `uv sync --frozen` then imports every first-party module; any
ModuleNotFoundError fails the PR (catches framework-cosplay / a deleted dep / the
garak-removal-breaks-import class). Other import-time exceptions are env-dependent → WARN.

Pure functions (module discovery + exception classification) are unit-tested directly.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts", "ci"))

import import_hygiene as ih  # noqa: E402


def test_discover_modules_derives_dotted_names(tmp_path):
    pkg = tmp_path / "pkg"
    (pkg / "sub").mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "mod.py").write_text("")
    (pkg / "sub" / "__init__.py").write_text("")
    (pkg / "sub" / "deep.py").write_text("")
    cache = pkg / "__pycache__"
    cache.mkdir()
    (cache / "ignored.py").write_text("")  # must be skipped
    got = ih.discover_modules(["pkg"], str(tmp_path))
    assert got == ["pkg", "pkg.mod", "pkg.sub", "pkg.sub.deep"]


def test_classify_module_not_found_is_fail():
    assert ih.classify_import_error(ModuleNotFoundError("No module named 'x'")) == "fail"


def test_classify_plain_import_error_is_fail():
    # a non-MNFE ImportError (e.g., cannot import name) is a DEFINITIVE code breakage, not env-dependent
    assert ih.classify_import_error(ImportError("cannot import name 'Y'")) == "fail"


def test_classify_syntax_error_is_fail():
    assert ih.classify_import_error(SyntaxError("invalid syntax")) == "fail"


def test_classify_runtime_error_is_warn():
    # a RuntimeError at import (e.g., needs a live service / env var) CAN be env-dependent -> warn
    assert ih.classify_import_error(RuntimeError("needs a live service")) == "warn"


def test_discover_modules_excludes_test_modules(tmp_path):
    """Tests are not runtime modules (need the dev group); they must be excluded from C5 scope."""
    pkg = tmp_path / "pkg"
    (pkg / "tests").mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "core.py").write_text("")
    (pkg / "conftest.py").write_text("")  # excluded
    (pkg / "thing_test.py").write_text("")  # excluded (*_test)
    (pkg / "tests" / "__init__.py").write_text("")  # excluded (under tests/)
    (pkg / "tests" / "test_x.py").write_text("")  # excluded
    got = ih.discover_modules(["pkg"], str(tmp_path))
    assert got == ["pkg", "pkg.core"]
