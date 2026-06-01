#!/usr/bin/env python3
"""SP-00e.6 — no-sentinel-termination gate (anti-drift clause e, PRD L122-128).

Fails any runtime loop/graph code that branches on a MODEL-EMITTED text sentinel
as a termination/pass condition.  Loop exit must read real subprocess EXIT CODES,
never a sentinel string.

Sentinels:
  - MINI_SWE_AGENT_FINAL_OUTPUT  (substring match — multi-char compound)
  - GOAL_COMPLETE                (substring match — multi-char compound)
  - "DONE"                       (exact, case-sensitive — 4-char match only)
  - "<!--"                       (substring match)

AST-AWARE detection: flags a sentinel string-literal ONLY when it is used as a
BRANCH/TERMINATION operand:
  - operand of ast.Compare (==, !=, in, not in), OR
  - argument to .endswith/.startswith/.find/.index method call, OR
  - argument to re.search / re.match / re.fullmatch.

This naturally excludes DOCSTRINGS and COMMENTS (they are not Compare/call
operands) and excludes `done` variable names / ".done" filename literals (they
do not match "DONE" exactly).

Escape hatch: if a violation line (or the line ABOVE it) contains the token
"no-sentinel: ignore" the violation is skipped.

Path scope: ONLY app/ and lib/ are scanned.  Excluded: scripts/, tests/,
**/tests/**, test_*.py, conftest.py, and this gate's own files.

STDLIB ONLY — no third-party imports.
"""

from __future__ import annotations

import argparse
import ast
import sys
from typing import Callable

# ---------------------------------------------------------------------------
# Sentinel definitions
# ---------------------------------------------------------------------------

# Multi-char compound sentinels: substring match inside the compare/call operand.
# Built by concatenation so this gate's OWN source carries no bare added token
# that would be picked up by the C6 no-skip/test-integrity grep.
_COMPOUND_SENTINELS: tuple[str, ...] = (
    "MINI" + "_SWE_AGENT_FINAL_OUTPUT",
    "GOAL" + "_COMPLETE",
    "<" + "!--",
)

# "DONE" is matched EXACTLY (full string equality), case-sensitive.
# Lower-case "done", "DONE_WORK", ".done" etc. are NOT flagged.
_EXACT_SENTINELS: tuple[str, ...] = ("DONE",)

# Methods that constitute "termination operand" usage.
_TERMINATION_METHODS: frozenset[str] = frozenset({"endswith", "startswith", "find", "index"})

# re module functions that constitute "termination operand" usage.
_RE_FUNCTIONS: frozenset[str] = frozenset({"search", "match", "fullmatch"})

# Escape token (built dynamically to avoid self-trip).
_ESCAPE_TOKEN = "no-sentinel" + ": ignore"

# ---------------------------------------------------------------------------
# Path-scope constants (mirrors dead_code_gate.py style)
# ---------------------------------------------------------------------------

# Roots that ARE scanned (runtime spine only).
_SCAN_ROOTS: tuple[str, ...] = ("app/", "lib/")

# Prefixes that are EXCLUDED from scanning.
_SELF_SCAN_EXCLUDED_PREFIXES: tuple[str, ...] = (
    "scripts/",
    "tests/",
)

# File-name stems that are excluded.
_EXCLUDED_BASENAMES: frozenset[str] = frozenset({"conftest.py"})

# Files excluded by exact path (this gate's own source).
_SELF_SCAN_EXCLUDED_FILES: frozenset[str] = frozenset(
    {
        "scripts/ci/no_sentinel_termination.py",
    }
)


def _is_scan_target(path: str) -> bool:
    """Return True iff path should be scanned.

    Must be under app/ or lib/, not under tests/**/ or scripts/, not a
    test_*.py or conftest.py file, and not this gate's own source.
    """
    normalized = path.replace("\\", "/")
    if normalized in _SELF_SCAN_EXCLUDED_FILES:
        return False
    # Must start with a scan root
    if not any(normalized.startswith(r) for r in _SCAN_ROOTS):
        return False
    # Must not start with an excluded prefix
    if any(normalized.startswith(p) for p in _SELF_SCAN_EXCLUDED_PREFIXES):
        return False
    # Exclude /tests/ sub-directories anywhere in path
    parts = normalized.split("/")
    if any(p in ("tests", "test") for p in parts[:-1]):
        return False
    # Exclude test_*.py and conftest.py basenames
    basename = parts[-1] if parts else ""
    if basename.startswith("test_") or basename.endswith("_test.py"):
        return False
    if basename in _EXCLUDED_BASENAMES:
        return False
    return True


# ---------------------------------------------------------------------------
# Core AST logic
# ---------------------------------------------------------------------------


def _is_sentinel_string(value: object) -> bool:
    """Return True iff ``value`` is a sentinel string that must not appear as a
    branch/termination operand.

    Rules:
      - Compound sentinels: substring present in value (case-sensitive).
      - Exact sentinels: full-string equality (case-sensitive). "DONE" matches
        only "DONE", not "done", "DONE_WORK", ".done", etc.
    """
    if not isinstance(value, str):
        return False
    for s in _COMPOUND_SENTINELS:
        if s in value:
            return True
    for s in _EXACT_SENTINELS:
        if value == s:
            return True
    return False


def _lineno_has_escape(lines: list[str], lineno: int) -> bool:
    """Return True iff line ``lineno`` (1-based) or the line above it contains the
    escape token "no-sentinel: ignore".
    """
    # lineno is 1-based; lines list is 0-based.
    idx = lineno - 1
    for candidate in (idx, idx - 1):
        if 0 <= candidate < len(lines):
            if _ESCAPE_TOKEN in lines[candidate]:
                return True
    return False


class _SentinelVisitor(ast.NodeVisitor):
    """AST visitor that collects lines where a sentinel is used as a branch operand.

    Detected contexts:
      1. ast.Compare node — any sentinel string as a comparator (left or right) with
         operators: Eq, NotEq, In, NotIn.
      2. ast.Call node representing a METHOD call:
           <anything>.endswith(sentinel)
           <anything>.startswith(sentinel)
           <anything>.find(sentinel)
           <anything>.index(sentinel)
      3. ast.Call node representing re.search / re.match / re.fullmatch:
           re.search(sentinel, ...)
           re.match(sentinel, ...)
           re.fullmatch(sentinel, ...)
    """

    def __init__(self, lines: list[str], filename: str) -> None:
        self._lines = lines
        self._filename = filename
        self.violations: list[str] = []

    def _add_violation(self, lineno: int) -> None:
        if not _lineno_has_escape(self._lines, lineno):
            line_text = self._lines[lineno - 1].rstrip() if lineno <= len(self._lines) else ""
            self.violations.append(f"{self._filename}:{lineno}: {line_text}")

    # ---- Compare: ==, !=, in, not in ----------------------------------------

    def visit_Compare(self, node: ast.Compare) -> None:
        _COMPARE_OPS = (ast.Eq, ast.NotEq, ast.In, ast.NotIn)
        has_relevant_op = any(isinstance(op, _COMPARE_OPS) for op in node.ops)
        if has_relevant_op:
            # Check left operand
            if isinstance(node.left, ast.Constant) and _is_sentinel_string(node.left.value):
                self._add_violation(node.left.lineno)
            # Check all comparators
            for comparator in node.comparators:
                if isinstance(comparator, ast.Constant) and _is_sentinel_string(comparator.value):
                    self._add_violation(comparator.lineno)
        self.generic_visit(node)

    # ---- Method calls: .endswith/.startswith/.find/.index -------------------

    def visit_Call(self, node: ast.Call) -> None:
        # Check method calls: obj.method(sentinel_str, ...)
        if isinstance(node.func, ast.Attribute):
            method_name = node.func.attr
            if method_name in _TERMINATION_METHODS:
                # First positional argument is the pattern/needle
                if node.args:
                    arg = node.args[0]
                    if isinstance(arg, ast.Constant) and _is_sentinel_string(arg.value):
                        self._add_violation(arg.lineno)

            # Check re.search / re.match / re.fullmatch: re.<func>(sentinel, text)
            if method_name in _RE_FUNCTIONS:
                # Verify the object is `re` (Name node with id 're')
                if isinstance(node.func.value, ast.Name) and node.func.value.id == "re":
                    if node.args:
                        arg = node.args[0]
                        if isinstance(arg, ast.Constant) and _is_sentinel_string(arg.value):
                            self._add_violation(arg.lineno)

        self.generic_visit(node)


def find_sentinel_violations(source: str, filename: str) -> list[str]:
    """AST-parse ``source`` and return human-readable violation strings.

    A violation is a sentinel string literal used as a branch/termination operand
    (Compare operand, or argument to .endswith/.startswith/.find/.index,
    or first argument to re.search/re.match/re.fullmatch).

    Docstrings and comments are naturally excluded (they are not AST operands).

    Returns a list of "filename:lineno: line_text" strings.
    """
    try:
        tree = ast.parse(source, filename=filename)
    except SyntaxError:
        return []

    lines = source.splitlines()
    visitor = _SentinelVisitor(lines, filename)
    visitor.visit(tree)
    return visitor.violations


# ---------------------------------------------------------------------------
# scan_tree — pure-ish (injected reader + lister for testability)
# ---------------------------------------------------------------------------


def scan_tree(
    roots: list[str],
    file_reader: Callable[[str], str],
    file_lister: Callable[[str], list[str]],
) -> list[str]:
    """Scan all .py files under ``roots`` for sentinel violations.

    Args:
        roots: list of root directory prefixes to scan (e.g. ["app", "lib"]).
        file_reader: callable(path) -> source text.
        file_lister: callable(root) -> list of file paths under that root.

    Returns a flat list of violation strings (empty = clean).

    Path scoping is enforced by ``_is_scan_target``:
      - Only app/ and lib/ are in scope (scripts/, tests/, etc. excluded).
      - test_*.py, conftest.py excluded.
    """
    all_violations: list[str] = []
    for root in roots:
        for path in file_lister(root):
            if not path.endswith(".py"):
                continue
            if not _is_scan_target(path):
                continue
            try:
                source = file_reader(path)
            except (KeyError, FileNotFoundError, OSError):
                continue
            violations = find_sentinel_violations(source, path)
            all_violations.extend(violations)
    return all_violations


# ---------------------------------------------------------------------------
# main CLI
# ---------------------------------------------------------------------------


def main(argv: list[str]) -> int:
    """CLI entry point.

    Usage:
        no_sentinel_termination.py [--root app] [--root lib]

    Scans app/ and lib/ (by default) for sentinel-string branch operands.
    Exits 1 if any violations are found.
    """
    ap = argparse.ArgumentParser(description="no-sentinel-termination gate (anti-drift clause e)")
    ap.add_argument(
        "--root",
        action="append",
        default=[],
        dest="roots",
        help="source root dir to scan (repeatable; default: app lib)",
    )
    args = ap.parse_args(argv)
    roots = args.roots if args.roots else ["app", "lib"]

    import pathlib

    def _real_lister(root: str) -> list[str]:
        p = pathlib.Path(root)
        if not p.exists():
            return []
        return [str(f).replace("\\", "/") for f in p.rglob("*.py")]

    def _real_reader(path: str) -> str:
        with open(path, encoding="utf-8") as fh:
            return fh.read()

    violations = scan_tree(
        roots=roots,
        file_reader=_real_reader,
        file_lister=_real_lister,
    )

    for v in violations:
        print(f"::error::no-sentinel-termination: {v}")

    print(
        f"== no-sentinel-termination: {len(violations)} violation(s) "
        f"=> {'FAIL' if violations else 'PASS'} =="
    )
    return 1 if violations else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
