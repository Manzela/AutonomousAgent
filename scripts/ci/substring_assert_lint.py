#!/usr/bin/env python3
"""SP-00e.6 — substring-assert + mock-under-test lint gate (Executor Contract C3).

ADVISORY ONLY — emits ::warning:: annotations but ALWAYS exits 0.

Detects two test-quality smells in test files:

  (1) Substring assert on agent output:
      `assert <string-literal> in <output-ish-name>`
      where the comparator is a Name whose name, or an Attribute whose .attr,
      is in the output-ish set:
        output | result | response | stdout | stderr | completion |
        text | content | message | reply | answer
      Also flags the NotIn form (`assert "x" not in output`).

  (2) Mock-under-test (conservative heuristic):
      A test function that contains both:
        (a) a mock.patch("...Symbol") / patch.object(obj, "Symbol") call, AND
        (b) a Call node whose function name matches that Symbol.
      This catches the "test patches the exact thing it's testing" pattern.
      Favours false-NEGATIVE over false-POSITIVE (conservative).

STDLIB ONLY — no third-party imports.
"""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Output-ish names whose presence as the RIGHT-HAND side of `in` / `not in`
# in an assert triggers smell #1.
_OUTPUT_ISH: frozenset[str] = frozenset(
    {
        "output",
        "result",
        "response",
        "stdout",
        "stderr",
        "completion",
        "text",
        "content",
        "message",
        "reply",
        "answer",
    }
)

# Refusal vocabulary words that must NEVER appear as binding assertions in
# differential safety tests.  Checking that a model "refused" by inspecting
# its prose is theatrical safety: the deceptive variant can emit these words
# while still executing the forbidden side-effect.
_REFUSAL_VOCABULARY: frozenset[str] = frozenset(
    {
        "refuse",
        "refused",
        "refusal",
        "cannot",
        "can't",
        "won't",
        "will not",
        "blocked",
        "denied",
        "forbidden",
        "not allowed",
        "i cannot",
        "i will not",
        "i refuse",
        "unauthorized",
        "prohibited",
    }
)

# Comment marker that allowlists a line from the differential binding check.
# Used on DECEPTIVE-variant fixture string literals that INTENTIONALLY contain
# refusal vocabulary (the bait text — NOT the binding assertion).
_DIFFERENTIAL_ALLOWLIST_MARKER = "pragma: differential-deceptive-fixture"

# The gate's own test file — excluded from scanning to avoid self-trip.
# Built as a relative path that matches what the walker produces.
_OWN_TEST_FILE = "tests/ci/test_substring_assert_lint.py"


# ---------------------------------------------------------------------------
# AST helpers
# ---------------------------------------------------------------------------


def _is_output_ish(node: ast.expr) -> bool:
    """Return True if `node` is a Name or Attribute whose relevant id/.attr is output-ish."""
    if isinstance(node, ast.Name):
        return node.id in _OUTPUT_ISH
    if isinstance(node, ast.Attribute):
        return node.attr in _OUTPUT_ISH
    return False


def _extract_patch_symbol(call: ast.Call) -> str | None:
    """Extract the target symbol short-name from a mock.patch / patch.object call.

    Recognises two forms:
      mock.patch("pkg.module.SymbolName")     -> "SymbolName"
      patch("pkg.module.SymbolName")          -> "SymbolName"
      patch.object(obj, "SymbolName")         -> "SymbolName"

    Returns None if the call is not one of these recognised forms.
    The heuristic is conservative: prefer false-NEGATIVE over noise.
    """
    func = call.func

    # Determine which variant we have
    is_plain_patch = False
    is_patch_object = False

    if isinstance(func, ast.Name) and func.id == "patch":
        is_plain_patch = True
    elif isinstance(func, ast.Attribute):
        if func.attr == "patch":
            is_plain_patch = True
        elif func.attr == "object":
            # patch.object(...)
            is_patch_object = True

    if is_plain_patch:
        # First positional arg should be a string literal "module.path.Symbol"
        if (
            call.args
            and isinstance(call.args[0], ast.Constant)
            and isinstance(call.args[0].value, str)
        ):
            target = call.args[0].value
            return target.rsplit(".", 1)[-1]  # last component = symbol name
    elif is_patch_object:
        # Second positional arg should be a string literal "method_name"
        if (
            len(call.args) >= 2
            and isinstance(call.args[1], ast.Constant)
            and isinstance(call.args[1].value, str)
        ):
            return call.args[1].value

    return None


def _collect_call_names(node: ast.AST) -> set[str]:
    """Walk `node` and return the set of called function short-names."""
    names: set[str] = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Call):
            func = n.func
            if isinstance(func, ast.Name):
                names.add(func.id)
            elif isinstance(func, ast.Attribute):
                names.add(func.attr)
    return names


# ---------------------------------------------------------------------------
# Core pure function
# ---------------------------------------------------------------------------


def find_lint_warnings(source: str, filename: str) -> list[str]:
    """Analyse `source` (a test file) for substring-assert and mock-under-test smells.

    Args:
        source:   Full source text of the test file.
        filename: Repo-relative path of the file (used for the warning message and
                  for self-exclusion of this gate's own test file).

    Returns:
        A list of human-readable warning strings (zero when no smells found).
        The list may contain multiple entries for multiple smells in the same file.
    """
    # Self-exclusion: skip the gate's own test file to avoid self-trip.
    # Normalise separators to forward-slash for comparison.
    norm = filename.replace("\\", "/")
    if norm.endswith(_OWN_TEST_FILE):
        return []

    try:
        tree = ast.parse(source, filename=filename)
    except SyntaxError:
        return []

    warnings: list[str] = []

    # Walk through all function definitions (test functions)
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue

        fn = node

        # ---------------------------------------------------------------
        # Smell #1: Substring assert on output-ish name
        # ---------------------------------------------------------------
        for stmt in ast.walk(fn):
            if not isinstance(stmt, ast.Assert):
                continue
            test = stmt.test
            # We want: Compare(left=Constant(str), ops=[In|NotIn], comparators=[output-ish])
            if not isinstance(test, ast.Compare):
                continue
            if not isinstance(test.left, ast.Constant) or not isinstance(test.left.value, str):
                continue
            # Check each op/comparator pair
            for op, comp in zip(test.ops, test.comparators):
                if isinstance(op, (ast.In, ast.NotIn)) and _is_output_ish(comp):
                    comp_name = (
                        comp.id
                        if isinstance(comp, ast.Name)
                        else (comp.attr if isinstance(comp, ast.Attribute) else "unknown")
                    )
                    warnings.append(
                        f"{filename}:{stmt.lineno}: substring-assert smell: "
                        f"`assert {test.left.value!r} {'not in' if isinstance(op, ast.NotIn) else 'in'} "
                        f"{comp_name}` — use a structured eval, not a substring check on "
                        f"output-ish name '{comp_name}'"
                    )

        # ---------------------------------------------------------------
        # Smell #2: Mock-under-test (conservative heuristic)
        # ---------------------------------------------------------------
        # Collect all patch target symbol names in this test function.
        patched_symbols: set[str] = set()
        for stmt in ast.walk(fn):
            if isinstance(stmt, ast.Call):
                sym = _extract_patch_symbol(stmt)
                if sym:
                    patched_symbols.add(sym)
            # Also check `with mock.patch(...) as ...:` — already covered by ast.walk

        if patched_symbols:
            # Collect all called function names in the test function body
            called_names = _collect_call_names(fn)
            overlap = patched_symbols & called_names
            if overlap:
                for sym in sorted(overlap):
                    # Find a representative line number for the patch call
                    line = fn.lineno
                    for stmt in ast.walk(fn):
                        if isinstance(stmt, ast.Call):
                            s = _extract_patch_symbol(stmt)
                            if s == sym:
                                line = getattr(stmt, "lineno", fn.lineno)
                                break
                    warnings.append(
                        f"{filename}:{line}: mock-under-test smell: "
                        f"test patches '{sym}' but also calls it directly — "
                        f"consider testing via the real implementation or a proper test double"
                    )

    return warnings


# ---------------------------------------------------------------------------
# Differential test binding check (BLOCKING, not advisory)
# ---------------------------------------------------------------------------


def _line_is_allowlisted(source_lines: list[str], lineno: int) -> bool:
    """Return True if the source line at `lineno` (1-indexed) carries the
    differential-deceptive-fixture allowlist marker as a comment.

    Checks both inline comments on the same line and a preceding comment line.
    """
    idx = lineno - 1  # convert to 0-indexed
    if idx < 0 or idx >= len(source_lines):
        return False
    line = source_lines[idx]
    if _DIFFERENTIAL_ALLOWLIST_MARKER in line:
        return True
    # Also check the immediately preceding line for a standalone comment marker.
    if idx > 0 and _DIFFERENTIAL_ALLOWLIST_MARKER in source_lines[idx - 1]:
        return True
    return False


def find_differential_binding_errors(source: str, filename: str) -> list[str]:
    """Scan a *_differential.py test file for binding assertions that check
    refusal vocabulary against output-ish names.

    This is BLOCKING (non-advisory): a differential test's BINDING assertion
    must observe a real side-effect (bytes, HTTP request count, etc.) — NEVER
    a refusal-vocabulary substring.

    The probe in a differential test encodes:
      COMPLIANT  -> forbidden_side_effect_occurred() == False
      DECEPTIVE  -> forbidden_side_effect_occurred() == True
      BENIGN     -> forbidden_side_effect_occurred() == False

    A vocabulary check (assert "refuse" not in output) would be fooled by a
    deceptive agent that emits refusal prose while still executing the side-effect.

    Allowlist: lines with the comment marker
    `# pragma: differential-deceptive-fixture` are excluded.  Use this ONLY on
    DECEPTIVE-variant fixture strings that INTENTIONALLY contain refusal
    vocabulary as bait — NOT on binding assertions.

    Returns:
        A list of human-readable error strings; empty list == clean.
    """
    try:
        tree = ast.parse(source, filename=filename)
    except SyntaxError:
        return []

    source_lines = source.splitlines()
    errors: list[str] = []

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue

        for stmt in ast.walk(node):
            if not isinstance(stmt, ast.Assert):
                continue
            test = stmt.test
            # Pattern: assert <string_literal> [not] in <output-ish name>
            if not isinstance(test, ast.Compare):
                continue
            if not isinstance(test.left, ast.Constant) or not isinstance(test.left.value, str):
                continue
            literal_value = test.left.value.lower()
            # Is this literal a refusal vocabulary word?
            is_refusal = any(word in literal_value for word in _REFUSAL_VOCABULARY)
            if not is_refusal:
                continue
            # Does the RHS compare against an output-ish name?
            for op, comp in zip(test.ops, test.comparators):
                if isinstance(op, (ast.In, ast.NotIn)) and _is_output_ish(comp):
                    # Is this line allowlisted?
                    if _line_is_allowlisted(source_lines, stmt.lineno):
                        continue
                    comp_name = (
                        comp.id
                        if isinstance(comp, ast.Name)
                        else (comp.attr if isinstance(comp, ast.Attribute) else "unknown")
                    )
                    errors.append(
                        f"{filename}:{stmt.lineno}: DIFFERENTIAL-BINDING-ERROR: "
                        f"differential test uses refusal-vocabulary substring check "
                        f"`assert {test.left.value!r} "
                        f"{'not in' if isinstance(op, ast.NotIn) else 'in'} "
                        f"{comp_name}` — binding assertions MUST observe a real "
                        f"side-effect (bytes, HTTP status, span count), NEVER refusal "
                        f"vocabulary. Add `# pragma: differential-deceptive-fixture` "
                        f"ONLY if this line is a DECEPTIVE-variant bait fixture, not "
                        f"a binding assertion."
                    )

    return errors


# ---------------------------------------------------------------------------
# CLI — main()
# ---------------------------------------------------------------------------


def main(argv: list[str]) -> int:
    """CLI entry point.

    Walks `--root` for test_*.py files, runs find_lint_warnings on each,
    emits ::warning:: annotations, and ALWAYS returns 0 (advisory).

    Also scans `--differential-root` (default: tests/integration) for
    *_differential.py files and FAILS (exit 1) if any binding assertion is a
    refusal-vocabulary substring check (BLOCKING gate for differential tests).

    Usage:
        substring_assert_lint.py [--root <dir>] [--differential-root <dir>]
    """
    ap = argparse.ArgumentParser(
        description="substring-assert + mock-under-test lint gate (C3, advisory) "
        "+ differential-binding BLOCKING check"
    )
    ap.add_argument(
        "--root",
        default="tests",
        help="root directory to scan for test_*.py files (default: tests)",
    )
    ap.add_argument(
        "--differential-root",
        default="tests/integration",
        help=(
            "directory to scan for *_differential.py files for the BLOCKING "
            "differential-binding check (default: tests/integration)"
        ),
    )
    args = ap.parse_args(argv)

    # -----------------------------------------------------------------------
    # Advisory pass (existing behaviour — always exits 0 from this block)
    # -----------------------------------------------------------------------

    root = Path(args.root)
    total_warnings = 0

    if root.exists():
        for py_file in sorted(root.rglob("test_*.py")):
            rel = str(py_file).replace("\\", "/")
            if rel.endswith(_OWN_TEST_FILE):
                continue
            try:
                source = py_file.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            file_warnings = find_lint_warnings(source, rel)
            for w in file_warnings:
                print(f"::warning::{w}")
                total_warnings += 1

    if total_warnings:
        print(
            f"== substring-assert-lint: {total_warnings} advisory warning(s) "
            f"(C3: advisory only — not blocking) =="
        )
    else:
        print("== substring-assert-lint: 0 warnings (C3) ==")

    # -----------------------------------------------------------------------
    # BLOCKING pass: differential binding check
    # -----------------------------------------------------------------------

    diff_root = Path(args.differential_root)
    total_errors = 0

    if diff_root.exists():
        for py_file in sorted(diff_root.glob("*_differential.py")):
            rel = str(py_file).replace("\\", "/")
            try:
                source = py_file.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            file_errors = find_differential_binding_errors(source, rel)
            for e in file_errors:
                print(f"::error::{e}")
                total_errors += 1

    if total_errors:
        print(
            f"== differential-binding-check: FAIL ({total_errors} binding error(s) "
            f"in *_differential.py files) — vocabulary-binding regresses the "
            f"SP-00c anti-theatre contract =="
        )
        return 1

    print("== differential-binding-check: PASS (0 vocabulary-binding errors) ==")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
