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
  - argument to .endswith/.startswith/.find/.index/__contains__ method call
    (positional OR keyword arguments), OR
  - argument to re.search / re.match / re.fullmatch (both `re.X` / alias.X
    for `import re as alias`, and bare `X` when imported via
    `from re import X [as Y]`) — positional OR keyword arguments, OR
  - a match/case pattern: MatchValue, MatchOr, MatchAs, MatchSequence,
    MatchMapping keys, MatchClass positional + keyword sub-patterns.

Additionally resolves:
  - Named-constant aliases: `NAME = <sentinel-literal>` at any scope causes
    uses of NAME in the above contexts to be treated as the sentinel.
  - BinOp string concatenation: `"DO" + "NE"` folds to "DONE".
  - f-strings with no real interpolation: `f"DONE"` folds to "DONE".

This naturally excludes DOCSTRINGS and COMMENTS (they are not Compare/call
operands) and excludes `done` variable names / ".done" filename literals (they
do not match "DONE" exactly).

Escape hatch: if a violation line (or the line ABOVE it) contains the token
"no-sentinel: ignore" the violation is skipped.

Path scope: ONLY app/ and lib/ are scanned.  Excluded: scripts/, tests/,
**/tests/**, test_*.py, conftest.py, and this gate's own files.

STDLIB ONLY — no third-party imports.

Threat-model boundary
---------------------
This gate is a GUARDRAIL against NATURAL/ACCIDENTAL model-sentinel termination:
the readable forms a well-meaning developer writes (string literals, named
constants, BinOp folds, f-strings, re.* calls, match/case patterns).  It is
NOT a defence against DELIBERATE runtime obfuscation — getattr-based dispatch,
exec(), base64-decoded markers, dynamic string assembly, char-by-char building,
or any technique that reconstructs the sentinel at runtime outside static AST
reach.  No purely static gate can stop a determined evader, and that is
explicitly OUT OF SCOPE (same rationale as the C3 substring-lint being advisory
rather than blocking).  Covered forms: Compare operands (Eq/NotEq/In/NotIn),
method-call positional + keyword args (.endswith/.startswith/.find/.index/
.__contains__), re.*/alias.* positional + keyword args, MatchValue, MatchOr,
MatchAs, MatchSequence, MatchMapping keys + value-patterns, MatchClass
positional + keyword sub-patterns, named-constant aliases, BinOp string folds,
constant f-strings.
"""

from __future__ import annotations

import argparse
import ast
import sys
from typing import Callable, Dict, Optional

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
_TERMINATION_METHODS: frozenset[str] = frozenset(
    {"endswith", "startswith", "find", "index", "__contains__"}
)

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


def _static_str_value(node: ast.AST, aliases: Dict[str, str]) -> Optional[str]:
    """Return the static string value of *node* if it can be resolved at
    analysis time; return None otherwise.

    Handles:
      - ast.Constant(str)  — direct string literal
      - ast.BinOp(op=Add)  — constant-fold two string operands (recursive)
      - ast.JoinedStr       — f-string whose every part is ast.Constant(str)
      - ast.Name            — lookup in ``aliases`` (named-constant resolution)
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value

    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _static_str_value(node.left, aliases)
        right = _static_str_value(node.right, aliases)
        if left is not None and right is not None:
            return left + right
        return None

    if isinstance(node, ast.JoinedStr):
        # f-string: only resolve when every value is a plain Constant (no
        # FormattedValue nodes — those indicate real interpolation).
        parts: list[str] = []
        for part in node.values:
            if isinstance(part, ast.Constant) and isinstance(part.value, str):
                parts.append(part.value)
            else:
                # Real interpolation (FormattedValue) → cannot fold
                return None
        return "".join(parts)

    if isinstance(node, ast.Name):
        return aliases.get(node.id)

    return None


def _node_is_sentinel(node: ast.AST, aliases: Dict[str, str]) -> bool:
    """Return True iff *node* statically resolves to a sentinel string."""
    v = _static_str_value(node, aliases)
    return v is not None and _is_sentinel_string(v)


def _build_aliases(tree: ast.AST) -> Dict[str, str]:
    """Walk *tree* and collect every assignment where a Name is bound to a
    static sentinel string.

    Handles:
      - ``NAME = <static-sentinel>``   (ast.Assign with single Name target)
      - ``NAME: type = <static-sentinel>``  (ast.AnnAssign with Name target)

    Conservative: a name ever bound to a sentinel literal is recorded.
    Does NOT attempt scope analysis or track reassignments.
    """
    # First pass: collect ALL static string assignments (sentinel or not) so
    # that we can resolve chains like A = "MINI" + "_SWE..." in one walk.
    # We use a simple dict; non-sentinel values stored as-is; the caller
    # (_node_is_sentinel) will filter by _is_sentinel_string.
    raw: Dict[str, str] = {}

    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            if len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                name = node.targets[0].id
                # We need a partially-built alias dict to fold BinOp, but
                # for the initial pass use what we have so far (raw).
                val = _static_str_value(node.value, raw)
                if val is not None:
                    raw[name] = val
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and node.value is not None:
                name = node.target.id
                val = _static_str_value(node.value, raw)
                if val is not None:
                    raw[name] = val

    # Filter: keep only names whose resolved value is a sentinel string.
    return {k: v for k, v in raw.items() if _is_sentinel_string(v)}


def _build_re_local_names(tree: ast.AST) -> tuple[frozenset[str], frozenset[str]]:
    """Return a pair of frozensets for re-module detection.

    Returns:
        (re_bare_names, re_module_aliases)

    re_bare_names: local names of individual re functions imported via
        ``from re import search``, ``from re import search as rsrch``, etc.
        These are bare Name calls: ``search(...)`` or ``rsrch(...)``.

    re_module_aliases: local names bound to the ``re`` MODULE itself via
        ``import re`` (→ {"re"}) or ``import re as regex`` (→ {"regex"}).
        These are used as the object in Attribute calls: ``regex.search(...)``.

    Handles:
      - ``from re import search``          → bare_names: {"search"}
      - ``from re import search as rsrch`` → bare_names: {"rsrch"}
      - ``from re import search, match``   → bare_names: {"search", "match"}
      - ``import re``                      → module_aliases: {"re"}
      - ``import re as regex``             → module_aliases: {"regex"}
    """
    bare_names: set[str] = set()
    module_aliases: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "re":
            for alias in node.names:
                orig = alias.name
                if orig in _RE_FUNCTIONS:
                    local = alias.asname if alias.asname else orig
                    bare_names.add(local)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "re":
                    local = alias.asname if alias.asname else "re"
                    module_aliases.add(local)
    return frozenset(bare_names), frozenset(module_aliases)


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
           <anything>.endswith(sentinel)   — positional or keyword arg
           <anything>.startswith(sentinel) — positional or keyword arg
           <anything>.find(sentinel)       — positional or keyword arg
           <anything>.index(sentinel)      — positional or keyword arg
           <anything>.__contains__(sentinel) — positional or keyword arg
      3. ast.Call node representing re.search / re.match / re.fullmatch:
           re.search(sentinel, ...)        — Attribute form, positional or keyword arg
           regex.search(sentinel, ...)     — Attribute form via `import re as regex`
           search(sentinel, ...)           — bare Name form (from re import search)
      4. ast.Match node — MatchValue, MatchOr, MatchAs, MatchSequence,
         MatchMapping keys + value-patterns, MatchClass positional + keyword
         sub-patterns.
    """

    def __init__(
        self,
        lines: list[str],
        filename: str,
        aliases: Dict[str, str],
        re_bare_names: frozenset[str],
        re_module_aliases: frozenset[str],
    ) -> None:
        self._lines = lines
        self._filename = filename
        self._aliases = aliases
        self._re_bare_names = re_bare_names
        self._re_module_aliases = re_module_aliases
        self.violations: list[str] = []

    def _add_violation(self, lineno: int) -> None:
        if not _lineno_has_escape(self._lines, lineno):
            line_text = self._lines[lineno - 1].rstrip() if lineno <= len(self._lines) else ""
            self.violations.append(f"{self._filename}:{lineno}: {line_text}")

    def _check_node(self, node: ast.AST, lineno: int) -> None:
        """Flag if node resolves to a sentinel string."""
        if _node_is_sentinel(node, self._aliases):
            self._add_violation(lineno)

    # ---- Compare: ==, !=, in, not in ----------------------------------------

    def visit_Compare(self, node: ast.Compare) -> None:
        _COMPARE_OPS = (ast.Eq, ast.NotEq, ast.In, ast.NotIn)
        has_relevant_op = any(isinstance(op, _COMPARE_OPS) for op in node.ops)
        if has_relevant_op:
            # Check left operand
            self._check_node(node.left, node.left.lineno)
            # Check all comparators
            for comparator in node.comparators:
                self._check_node(comparator, comparator.lineno)
        self.generic_visit(node)

    # ---- Method calls: .endswith/.startswith/.find/.index/__contains__ ------

    def _check_args_and_keywords(self, node: ast.Call, lineno: int) -> None:
        """Check both positional args[0] and all keyword values for a sentinel."""
        if node.args:
            self._check_node(node.args[0], node.args[0].lineno)
        for kw in node.keywords:
            self._check_node(kw.value, lineno)

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Attribute):
            method_name = node.func.attr
            if method_name in _TERMINATION_METHODS:
                # First positional argument or any keyword value is the pattern/needle
                self._check_args_and_keywords(node, node.lineno)

            # Check re.search / re.match / re.fullmatch:
            #   re.<func>(sentinel, ...)     — `re` or any `import re as alias` name
            if method_name in _RE_FUNCTIONS:
                obj = node.func.value
                if isinstance(obj, ast.Name) and obj.id in self._re_module_aliases:
                    self._check_args_and_keywords(node, node.lineno)

        # Bare Name call — covers `from re import search` then `search(...)`.
        elif isinstance(node.func, ast.Name):
            if node.func.id in self._re_bare_names:
                self._check_args_and_keywords(node, node.lineno)

        self.generic_visit(node)

    # ---- match statement (Python 3.10+) -------------------------------------

    def visit_Match(self, node: ast.AST) -> None:  # type: ignore[override]
        """Visit ast.Match nodes.  Guarded with hasattr/getattr so the method
        is harmless on Python < 3.10 where ast.Match may not exist.
        """
        for case in getattr(node, "cases", []):
            self._visit_match_pattern(case.pattern)
        self.generic_visit(node)  # type: ignore[arg-type]

    def _visit_match_pattern(self, pattern: ast.AST) -> None:
        """Recursively inspect a match-case pattern for sentinel values."""
        # MatchValue: case "DONE": — the value is an ast.Constant/etc.
        if hasattr(ast, "MatchValue") and isinstance(pattern, ast.MatchValue):  # type: ignore[attr-defined]
            val_node = pattern.value  # type: ignore[attr-defined]
            self._check_node(val_node, val_node.lineno)
        # MatchOr: case "A" | "B": — recurse into each pattern
        elif hasattr(ast, "MatchOr") and isinstance(pattern, ast.MatchOr):  # type: ignore[attr-defined]
            for p in pattern.patterns:  # type: ignore[attr-defined]
                self._visit_match_pattern(p)
        # MatchAs: case x as y: — recurse if there's a nested pattern
        elif hasattr(ast, "MatchAs") and isinstance(pattern, ast.MatchAs):  # type: ignore[attr-defined]
            if pattern.pattern is not None:  # type: ignore[attr-defined]
                self._visit_match_pattern(pattern.pattern)  # type: ignore[attr-defined]
        # MatchSequence: recurse into sub-patterns
        elif hasattr(ast, "MatchSequence") and isinstance(pattern, ast.MatchSequence):  # type: ignore[attr-defined]
            for p in pattern.patterns:  # type: ignore[attr-defined]
                self._visit_match_pattern(p)
        # MatchMapping: check KEYS (dict-key literals) AND recurse value-patterns
        #   e.g. case {"DONE": v}: — "DONE" is a key node, not a MatchValue
        elif hasattr(ast, "MatchMapping") and isinstance(pattern, ast.MatchMapping):  # type: ignore[attr-defined]
            for key_node in getattr(pattern, "keys", []):  # type: ignore[attr-defined]
                self._check_node(key_node, key_node.lineno)
            for p in getattr(pattern, "patterns", []):  # type: ignore[attr-defined]
                self._visit_match_pattern(p)
        # MatchClass: recurse positional sub-patterns AND keyword sub-patterns
        #   e.g. case Resp(status="DONE"): or case C("DONE"):
        elif hasattr(ast, "MatchClass") and isinstance(pattern, ast.MatchClass):  # type: ignore[attr-defined]
            for p in getattr(pattern, "patterns", []):  # type: ignore[attr-defined]
                self._visit_match_pattern(p)
            for p in getattr(pattern, "kwd_patterns", []):  # type: ignore[attr-defined]
                self._visit_match_pattern(p)


def find_sentinel_violations(source: str, filename: str) -> list[str]:
    """AST-parse ``source`` and return human-readable violation strings.

    A violation is a sentinel string literal used as a branch/termination operand
    (Compare operand, or argument to .endswith/.startswith/.find/.index/__contains__,
    or first argument to re.search/re.match/re.fullmatch (both attribute and bare
    Name form), or a match/case MatchValue).

    Resolved forms: named-constant aliases, BinOp string concatenation,
    f-strings with no real interpolation.

    Docstrings and comments are naturally excluded (they are not AST operands).

    Returns a list of "filename:lineno: line_text" strings.
    """
    try:
        tree = ast.parse(source, filename=filename)
    except SyntaxError:
        return []

    lines = source.splitlines()
    aliases = _build_aliases(tree)
    re_bare_names, re_module_aliases = _build_re_local_names(tree)
    visitor = _SentinelVisitor(lines, filename, aliases, re_bare_names, re_module_aliases)
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
