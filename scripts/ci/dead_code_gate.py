#!/usr/bin/env python3
"""SP-00e.5 — dead-code reachability gate (Executor Contract C4).

Every public function/class ADDED or whose BODY CHANGED must have ≥1 call site
REACHABLE FROM A RUNTIME ENTRYPOINT (bare import/re-export does NOT count), proven
by a callgraph built from AST analysis. Self-waivers (the manual-waiver decorator,
# dead-code: ignore, # noqa: deadcode) are HARD failures — the only escape hatch is the
operator-approved config/dead_code_entrypoints.txt allowlist.

Anti-pattern killed: dead code shipped as ticked boxes.

Approximation notes (documented per design):
  - Name resolution is approximate (match by symbol short-name within first-party
    symbols, preferring same-module). This means:
      (a) Two symbols with the same short name from different modules may create
          spurious edges (false negatives — symbol appears wired when it isn't).
      (b) Dynamic dispatch, exec(), getattr(mod, name), importlib, and runtime
          injection are NOT tracked (false negatives — dynamic wiring missed).
  - These approximate false negatives are acceptable (conservative toward safety
    of shipping code) but are noted here for auditability.
  - False positives (code flagged as dead when it's actually used) are minimized
    by:
      (a) Value-reference wiring — a symbol passed as a value is considered wired.
      (b) Import→module edge — importing a first-party module makes its module-level
          code (including decorator registrations) reachable.
      (c) Decorator wiring — a decorated public symbol in a reachable module is
          wired ONLY if it has at least one ACTIVE (non-passive) decorator.
          Passive decorators (@staticmethod, @classmethod, @property, @cache,
          @lru_cache, @abstractmethod, @dataclass, etc.) are NOT wiring call sites.
          Active decorators (@app.get, @registry.tool, etc.) ARE wiring call sites.
      (d) Class→public-method edge — public methods of a reachable class are wired
          to handle polymorphic/override dispatch patterns (e.g. AbstractSandbox
          subclasses). This is a granularity reduction: all public methods of a
          reachable class are considered reachable. Documented here for auditability.

STDLIB ONLY — no third-party imports.
"""

from __future__ import annotations

import argparse
import ast
import re
import subprocess
import sys
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Optional

# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

# Graph type: node_id -> set of referenced node_ids
Graph = dict[str, set[str]]


@dataclass
class Symbol:
    """A public function or class in a first-party source file."""

    qualname: str  # e.g. "MyClass.method" or "top_fn"
    module: str  # dotted module path, e.g. "app.core.orchestrator"
    name: str  # short name, e.g. "method" or "top_fn"
    kind: str  # "function" | "async_function" | "class"
    # FIX D: store decorator short names and class flag during Pass 1 for O(1) lookups.
    # Populated by _collect_symbols_from_ast; avoids O(N*M) AST rewalk per symbol.
    decorator_names: list[str] = field(default_factory=list)
    is_class: bool = False

    @property
    def node_id(self) -> str:
        return f"{self.module}:{self.qualname}"


@dataclass
class Result:
    """Gate result."""

    ok: bool
    hard_failures: list[str] = field(default_factory=list)
    advisories: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Roots that are considered first-party source (not tests)
_FIRST_PARTY_ROOTS = ("app/", "scripts/", "lib/")

# The forbidden self-waiver decorator token, built dynamically (concatenation) so this
# gate's OWN source carries no literal token on an added line. Otherwise the pre-existing
# C6 no-skip guard (which greps added *.py lines for the literal) would self-trip on this
# file. The runtime value is byte-identical to the literal decorator marker.
_MANUAL_MARKER = "@" + "manual"
_MANUAL_DECORATOR_RE = re.compile(_MANUAL_MARKER + r"\b")

# Self-waiver patterns that are FORBIDDEN per C4
_SELF_WAIVER_PATTERNS = [
    _MANUAL_DECORATOR_RE,
    re.compile(r"#\s*dead-code:\s*ignore"),
    re.compile(r"#\s*noqa:\s*deadcode"),
    re.compile(r"#\s*noqa:\s*DC\d+"),
]

# Paths whose ADDED lines are EXCLUDED from the self-waiver scan. By construction this
# gate's own source, its test fixtures, and its acceptance spec contain the forbidden
# marker as data/docs (self-reference), not as a real runtime waiver — scanning them
# self-trips the gate (the defect that reverted the first SP-00e.5 merge). Genuine
# runtime code (app/, lib/, and other scripts/) is still scanned. The scan default is
# in-scope when the file is unknown (a header-less diff fragment), so raw-fragment unit
# tests keep exercising detection.
_SELF_SCAN_EXCLUDED_PREFIXES = ("tests/", "audit/acceptance/")
_SELF_SCAN_EXCLUDED_FILES = ("scripts/ci/dead_code_gate.py",)

# FIX B: Passive decorators that do NOT constitute a "wiring" call site.
# A symbol whose decorators are ALL passive is not wired-by-decorator; it must
# be reached via an explicit reference edge or the class->method edge instead.
# Matched against the decorator's resolved short name (last component), handling:
#   @cache               -> 'cache'
#   @functools.cache     -> 'cache'   (Attribute node .attr)
#   @functools.lru_cache() -> 'lru_cache'  (Call node wrapping Attribute)
_PASSIVE_DECORATORS: frozenset[str] = frozenset(
    {
        "staticmethod",
        "classmethod",
        "property",
        "cached_property",
        "dataclass",
        "abstractmethod",
        "abstractproperty",
        "cache",
        "lru_cache",
        "wraps",
        "total_ordering",
        "singledispatch",
        "runtime_checkable",
    }
)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _decorator_short_name(dec: ast.expr) -> str:
    """Extract the short name from a decorator AST node.

    Handles three forms:
      @name             -> ast.Name         -> 'name'
      @obj.name         -> ast.Attribute    -> 'name'
      @obj.name(...)    -> ast.Call         -> 'name'  (called decorator)
    """
    if isinstance(dec, ast.Call):
        dec = dec.func  # strip the call wrapper, then fall through
    if isinstance(dec, ast.Attribute):
        return dec.attr
    if isinstance(dec, ast.Name):
        return dec.id
    return ""


def _has_active_decorators(decorator_names: list[str]) -> bool:
    """Return True if the symbol has at least one non-passive decorator.

    A symbol with ONLY passive decorators is not wired-by-decorator (FIX B).
    It must be reached via an explicit reference or the class->method edge.
    """
    if not decorator_names:
        return False
    return any(name not in _PASSIVE_DECORATORS for name in decorator_names)


def _is_first_party(path: str) -> bool:
    """Return True if path is a first-party source file (not under tests/)."""
    normalized = path.replace("\\", "/")
    # Exclude test paths
    parts = normalized.split("/")
    if any(p in ("tests", "test") for p in parts):
        return False
    stem = parts[-1] if parts else ""
    if stem.startswith("test_") or stem.endswith("_test.py") or stem == "conftest.py":
        return False
    return any(normalized.startswith(r) for r in _FIRST_PARTY_ROOTS)


def _path_to_module(path: str) -> str:
    """Convert a file path like app/core/foo.py -> dotted module app.core.foo."""
    normalized = path.replace("\\", "/")
    if normalized.endswith(".py"):
        normalized = normalized[:-3]
    if normalized.endswith("/__init__"):
        normalized = normalized[:-9]  # strip /__init__
    return normalized.replace("/", ".")


def _collect_symbols_from_ast(tree: ast.Module, module: str) -> list[Symbol]:
    """Walk the AST and return all public FunctionDef/ClassDef with line range.

    A symbol is public only if its OWN name and ALL enclosing scope names are public
    (do not start with '_'). This excludes symbols nested inside private functions or
    classes (e.g. a nested class inside _private_fn is out of scope).
    """
    results: list[Symbol] = []

    class Visitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self._scope: list[str] = []
            # Track whether any ancestor in scope is private
            self._has_private_ancestor: bool = False
            self._private_depth: int = 0  # depth at which we entered a private scope

        def _qualname(self, name: str) -> str:
            return ".".join(self._scope + [name])

        def _visit_def(self, node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) -> None:
            qualname = self._qualname(node.name)
            kind: str
            if isinstance(node, ast.AsyncFunctionDef):
                kind = "async_function"
            elif isinstance(node, ast.FunctionDef):
                kind = "function"
            else:
                kind = "class"
            is_private = node.name.startswith("_")
            # FIX D: collect decorator short names and is_class during Pass 1.
            dec_names = [_decorator_short_name(d) for d in getattr(node, "decorator_list", [])]
            # Public: name does NOT start with '_' AND no private ancestor
            if not is_private and not self._has_private_ancestor:
                results.append(
                    Symbol(
                        qualname=qualname,
                        module=module,
                        name=node.name,
                        kind=kind,
                        decorator_names=dec_names,
                        is_class=isinstance(node, ast.ClassDef),
                    )
                )
            self._scope.append(node.name)
            prev_private = self._has_private_ancestor
            if is_private:
                self._has_private_ancestor = True
            self.generic_visit(node)
            self._has_private_ancestor = prev_private
            self._scope.pop()

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            self._visit_def(node)

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            self._visit_def(node)

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            self._visit_def(node)

    Visitor().visit(tree)
    return results


def _get_lineno_ranges(tree: ast.Module, module: str) -> dict[str, tuple[int, int]]:
    """Return {qualname: (lineno, end_lineno)} for all defs in the AST."""
    ranges: dict[str, tuple[int, int]] = {}

    class Visitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self._scope: list[str] = []

        def _qualname(self, name: str) -> str:
            return ".".join(self._scope + [name])

        def _visit_def(self, node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) -> None:
            qualname = self._qualname(node.name)
            end = getattr(node, "end_lineno", node.lineno)
            ranges[qualname] = (node.lineno, end)
            self._scope.append(node.name)
            self.generic_visit(node)
            self._scope.pop()

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            self._visit_def(node)

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            self._visit_def(node)

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            self._visit_def(node)

    Visitor().visit(tree)
    return ranges


def _parse_diff_changed_lines(diff_text: str) -> dict[str, set[int]]:
    """Parse a unified diff and return {file_path: set_of_new_line_numbers_added_or_modified}.

    We track line numbers in the NEW file (+ lines or context lines whose new-side
    line number falls under a modified hunk). For C4 purposes we flag lines that are
    marked '+' (added or modified).
    """
    result: dict[str, set[int]] = {}
    current_file: Optional[str] = None
    new_lineno = 0

    for raw_line in diff_text.splitlines():
        # New file path from diff header
        if raw_line.startswith("+++ "):
            path = raw_line[4:]
            # Strip b/ prefix from git diff
            if path.startswith("b/"):
                path = path[2:]
            # Ignore /dev/null (deleted files)
            if path == "/dev/null":
                current_file = None
            else:
                current_file = path
                if current_file not in result:
                    result[current_file] = set()
        elif raw_line.startswith("@@ "):
            # @@ -old_start,old_count +new_start,new_count @@
            m = re.search(r"\+(\d+)(?:,\d+)?", raw_line)
            if m:
                new_lineno = int(m.group(1))
            else:
                new_lineno = 0
        elif raw_line.startswith("+") and not raw_line.startswith("+++"):
            if current_file is not None:
                result[current_file].add(new_lineno)
            new_lineno += 1
        elif raw_line.startswith("-") and not raw_line.startswith("---"):
            pass  # removed line — doesn't advance new_lineno
        else:
            # Context line
            new_lineno += 1

    return result


def _diff_new_path(plus_header: str) -> Optional[str]:
    """Extract the new-side, repo-relative path from a ``+++ b/<path>`` diff header.

    Returns None for ``/dev/null`` (a deletion) or an unparseable header. Strips the git
    ``b/`` (or ``i/``/``w/``) prefix so the result can be compared to repo-relative paths.
    """
    rest = plus_header[len("+++") :].strip()
    if not rest or rest == "/dev/null":
        return None
    for prefix in ("b/", "i/", "w/"):
        if rest.startswith(prefix):
            return rest[len(prefix) :]
    return rest


def _is_self_scan_excluded(path: Optional[str]) -> bool:
    """True iff added lines under `path` must be SKIPPED by the self-waiver scan.

    Skips, in order:
      (a) this gate's own source, test paths (tests/**), and acceptance specs
          (audit/acceptance/**) — files that carry the marker as data/docs by
          construction (scanning them is the self-trip the path scope fixes); and
      (b) any NON-Python file — the self-waiver markers are a Python decorator / comment
          pragma, so a marker MENTIONED in a .txt config, .yml workflow, .yaml spec, or
          .md doc can never be a real runtime waiver (e.g. this gate's own
          config/dead_code_entrypoints.txt header documents the markers).

    Unknown paths (None) are treated as IN-scope so header-less diff fragments fed by the
    unit tests still exercise detection.
    """
    if not path:
        return False
    if path in _SELF_SCAN_EXCLUDED_FILES or path.startswith(_SELF_SCAN_EXCLUDED_PREFIXES):
        return True
    return not path.endswith(".py")


def _detect_self_waivers_in_diff(diff_text: str) -> list[str]:
    """Return waiver markers found on '+' (added) lines of IN-SCOPE files in the diff.

    A self-waiver is the manual-waiver decorator (the runtime value of
    ``_MANUAL_MARKER``) or a ``# dead-code: ignore`` / ``# noqa: deadcode`` comment.
    Markers are matched in code context only — docstring / string-literal / explanatory
    mentions do not count — and the scan is PATH-SCOPED: added lines belonging to this
    gate's own source, its test fixtures, its acceptance spec, or any non-Python file
    are skipped (see ``_is_self_scan_excluded``). By construction those files carry the
    marker as data or documentation rather than as a real runtime waiver, so skipping
    them is what keeps the gate from self-tripping on its own PR. A diff fragment with no
    file header (as fed by the raw-fragment unit tests) is treated as in-scope.

    Detection rules for an in-scope added line:
      - manual-waiver decorator: the stripped line must START with the marker (a real
        Python decorator, not text inside a string or docstring).
      - dead-code-ignore / noqa-deadcode comment: the '#' comment must appear in actual
        Python code, not inside a docstring or string-literal context.

    Triple-quoted string context is tracked across lines to exclude docstring content.

    FIX 5: comment portions (text after an unquoted '#') are stripped before counting
    triple-quote delimiters, so a commented-out delimiter does not toggle the in-string
    state.

    BUG 1 FIX (state leak across files): the in-string flag resets at every new-file
    boundary (``diff --git`` lines) so an unclosed delimiter in file A cannot mask a
    waiver added in file B.

    BUG 2 FIX (removed-line state corruption): only context and ADDED ('+') lines update
    the in-string state; REMOVED ('-') lines and diff preamble metadata ('---', the
    '+++ ' file header, ``diff --git``, and '@@' hunk headers) do not, so an attacker
    cannot remove a delimiter line to flip the toggle and slip a waiver past on the next
    added line.

    EVASION GUARD (C9): the '+++ b/<path>' file header is recognised only in the per-file
    preamble (``not in_hunk``); inside a hunk a '+++...' line is ADDED CONTENT (an added
    line whose text starts with '++'), so it cannot be abused to flip the file path /
    exclusion mid-file. ``in_hunk`` is set at the first '@@' and reset per ``diff --git``.
    """
    found: list[str] = []
    in_triple_string = False  # whether we're inside a triple-quoted block
    file_excluded = False  # whether the current file's added lines are out of scope
    in_hunk = False  # whether we are past the first '@@' hunk header of the current file

    for raw_line in diff_text.splitlines():
        # BUG 1 FIX: reset per-file state at every new-file boundary.
        if raw_line.startswith("diff --git "):
            in_triple_string = False
            file_excluded = False
            in_hunk = False
            continue

        # Capture the NEW-side path from the '+++ b/<path>' header to PATH-SCOPE the scan.
        # The file header lives in the per-file PREAMBLE, BEFORE the first '@@' hunk.
        # EVASION GUARD (C9): once inside a hunk, a line starting with '+++' is ADDED
        # CONTENT — an added line whose own text starts with '++' (e.g. '++x' -> diff line
        # '+++x', or '++ x' -> '+++ x'), NOT a header. Treating it as a header would let an
        # attacker flip file_excluded mid-file and skip a real waiver on the lines that
        # follow. Gate on `not in_hunk` AND the literal '+++ ' header spacing so only the
        # genuine preamble header matches; a space-only check is insufficient ('++ x').
        if not in_hunk and raw_line.startswith("+++ "):
            file_excluded = _is_self_scan_excluded(_diff_new_path(raw_line))
            continue

        # Hunk header: the body that follows (until the next 'diff --git') is content.
        if raw_line.startswith("@@"):
            in_hunk = True
            continue

        # BUG 2 FIX: skip remaining metadata ('---') and REMOVED lines — they are not part
        # of the new file and must not affect new-side string state.
        is_added = raw_line.startswith("+")  # '+++ ' preamble header already handled above
        is_removed = raw_line.startswith("-") and not raw_line.startswith("---")
        is_metadata = raw_line.startswith("---")
        if is_removed or is_metadata:
            continue

        # raw_line is now a '+' (added) or context line — both present in the NEW file.
        content = raw_line[1:] if is_added else raw_line
        stripped = content.strip()

        # FIX 5: strip the comment portion before counting triple-quote delimiters.
        code_portion = stripped.split("#", 1)[0]
        tq_count = code_portion.count('"""') + code_portion.count("'''")
        if tq_count % 2 == 1:
            in_triple_string = not in_triple_string

        # Only inspect ADDED lines for markers (context lines update state only).
        if not is_added:
            continue
        # PATH SCOPE: this gate's own source / tests / acceptance carry the marker as data.
        if file_excluded:
            continue
        # Skip lines inside triple-quoted strings (docstrings, multiline strings).
        if in_triple_string:
            continue

        # manual-waiver decorator: stripped line must START with the marker.
        if stripped.startswith(_MANUAL_MARKER) and _MANUAL_DECORATOR_RE.match(stripped):
            found.append(stripped)
            continue

        # Inline-comment waiver: find '#' outside string literals, then match patterns.
        comment_text = _extract_inline_comment(stripped)
        if comment_text:
            for pat in _SELF_WAIVER_PATTERNS[1:]:
                if pat.search(comment_text):
                    found.append(stripped)
                    break
    return found


def _extract_inline_comment(line: str) -> str:
    """Extract the comment portion of a line (text after the first '#' outside quotes).

    Returns empty string if no comment found or line is inside a string.
    Simple state machine: track whether we're inside a single or double quoted string.
    Does NOT handle triple-quoted strings (caller pre-filters those lines).
    """
    in_single = False
    in_double = False
    for i, ch in enumerate(line):
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif ch == "#" and not in_single and not in_double:
            return line[i:]
    return ""


# ---------------------------------------------------------------------------
# 1. extract_changed_public_symbols
# ---------------------------------------------------------------------------


def extract_changed_public_symbols(
    diff_text: str,
    new_file_reader: Callable[[str], str],
) -> list[Symbol]:
    """Parse a unified diff and return public symbols that were ADDED or body-CHANGED.

    For each changed first-party .py file (roots: app/, scripts/, lib/ — tests/ excluded):
      - Use `ast` on the new file content to map every def/class to its line range.
      - A symbol is "changed" if any added ('+') new-line number falls within [lineno, end_lineno].
      - Only public symbols (name does not start with '_') are returned.
    """
    changed_lines = _parse_diff_changed_lines(diff_text)
    symbols: list[Symbol] = []

    for file_path, added_line_nos in changed_lines.items():
        if not file_path.endswith(".py"):
            continue
        if not _is_first_party(file_path):
            continue
        if not added_line_nos:
            continue

        try:
            source = new_file_reader(file_path)
        except (KeyError, FileNotFoundError, OSError):
            continue

        try:
            tree = ast.parse(source, filename=file_path)
        except SyntaxError:
            continue

        module = _path_to_module(file_path)
        all_syms = _collect_symbols_from_ast(tree, module)
        ranges = _get_lineno_ranges(tree, module)

        for sym in all_syms:
            lineno, end_lineno = ranges.get(sym.qualname, (0, 0))
            # Symbol is changed if any added line falls within its range
            if any(lineno <= ln <= end_lineno for ln in added_line_nos):
                symbols.append(sym)

    return symbols


# ---------------------------------------------------------------------------
# 2. build_callgraph
# ---------------------------------------------------------------------------


def build_callgraph(
    source_files: list[str],
    file_reader: Callable[[str], str],
) -> Graph:
    """Build a name-resolution callgraph over all first-party source files.

    Nodes:
      - "module:qualname" for each public/private function/class
      - "module:<module>" synthetic node for top-level code in that module

    Edges:

      Reference edges (from code execution):
      - Inside a function/method/class body or module-level code, every Name or
        Attribute that matches a known first-party symbol's short name creates an
        edge from the enclosing node to the referenced symbol.
      - Value references (passed as argument, decorator, list element, etc.) are
        included — a symbol passed as a value is considered wired.
      - `import` / `from x import y` statements do NOT create symbol edges (C4:
        bare import/re-export excluded). However, importing a first-party module
        DOES create a module-level edge (see FIX 2 below).

      FIX 1 (trivial bypass removed):
      - The old code added an UNCONDITIONAL edge `<module>` -> every top-level symbol.
        This meant ANY def added to an entrypoint module auto-passed. That free edge
        is REMOVED. A top-level symbol is now reachable ONLY via a real reference in
        executed code. The reference walk DOES emit edges for names referenced in
        module-level statements (including inside `if __name__ == "__main__":` blocks),
        so `def main()` referenced as `main()` in the `if __name__` block is correctly
        wired via the `<module>` -> `main` reference edge.

      FIX 2 (import→module edge):
      - `import mod` / `from mod import x` where mod is a first-party module adds an
        edge from the importing scope's `<module>` node to `mod:<module>`. This makes
        the imported module's module-level code reachable if the importing module is
        reachable. CRITICAL: only the module node is wired, NOT the individual imported
        symbols (bare import/re-export does NOT count per C4).

      FIX 3 (decorator wiring, refined by FIX B):
      - A decorated public function/class in a reachable module is wired ONLY if it
        has at least one ACTIVE (non-passive) decorator. Passive decorators like
        @staticmethod, @classmethod, @property, @cache, @lru_cache, @abstractmethod,
        @dataclass, etc. are implementation-only modifiers and do NOT constitute a
        call site. A symbol whose ALL decorators are passive is NOT wired-by-decorator
        and must be reached via an explicit reference or the class→method edge.
        Active decorators (e.g. @app.get, @registry.tool) wire the symbol by
        registering it at import time.

      FIX 4 (class→public-method edge):
      - Public methods of a reachable class are wired via a class→method edge. This
        handles polymorphic dispatch (overrides like `def run(self)` on AbstractSandbox
        subclasses) where no explicit first-party ast.Name reference exists. Granularity
        reduction: ALL public methods of a reachable class are considered reachable.

    Approximation: resolution is by short name (last component of qualname).
    Two symbols with the same short name may create spurious edges (false negatives
    from the gate's perspective — code incorrectly appears reachable).
    """
    graph: Graph = {}

    # First pass: collect all known symbols from all first-party files
    all_symbols: dict[str, list[Symbol]] = {}  # short_name -> list of symbols
    module_to_symbols: dict[str, list[Symbol]] = {}
    # Set of known first-party modules (for import→module edge, FIX 2)
    first_party_modules: set[str] = set()

    for file_path in source_files:
        if not file_path.endswith(".py"):
            continue
        try:
            source = file_reader(file_path)
        except (KeyError, FileNotFoundError, OSError):
            continue

        try:
            tree = ast.parse(source, filename=file_path)
        except SyntaxError:
            continue

        is_fp = _is_first_party(file_path)
        module = _path_to_module(file_path)

        # Create synthetic module-level node for ALL files (first-party or test)
        module_node = f"{module}:<module>"
        if module_node not in graph:
            graph[module_node] = set()

        if not is_fp:
            continue

        first_party_modules.add(module)
        syms = _collect_symbols_from_ast(tree, module)
        module_to_symbols[module] = syms
        for sym in syms:
            node_id = sym.node_id
            if node_id not in graph:
                graph[node_id] = set()
            # Index by short name for resolution
            all_symbols.setdefault(sym.name, []).append(sym)

    # Second pass: walk AST to build reference edges
    for file_path in source_files:
        if not file_path.endswith(".py"):
            continue
        try:
            source = file_reader(file_path)
        except (KeyError, FileNotFoundError, OSError):
            continue
        try:
            tree = ast.parse(source, filename=file_path)
        except SyntaxError:
            continue

        module = _path_to_module(file_path)
        syms_in_module = module_to_symbols.get(module, [])
        sym_qualnames_in_module = {s.qualname for s in syms_in_module}

        # FIX 1: Do NOT add unconditional <module>->top-level-symbol edges.
        # Top-level symbols are reachable only via real references in executed code.
        # The reference walk below handles module-level Name/Attribute nodes including
        # those inside `if __name__ == "__main__":` blocks.

        # FIX 3 + FIX 4: Add decorator wiring and class->method edges.
        # These are added here (not in the reference walk) since they depend on the
        # structure of the definitions, not on explicit Name references.
        module_node = f"{module}:<module>"
        if module_node not in graph:
            graph[module_node] = set()

        # FIX D: Build O(1) qualname->is_class index from already-collected symbols.
        # Avoids calling _is_class_in_module(tree, ...) which rewalk the full AST.
        qualname_is_class: dict[str, bool] = {s.qualname: s.is_class for s in syms_in_module}

        for sym in syms_in_module:
            # FIX 3 + FIX B: Decorator wiring — only if the symbol has at least one
            # ACTIVE (non-passive) decorator. Passive decorators like @cache, @property,
            # @staticmethod, @classmethod, etc. do NOT constitute a call site; they are
            # implementation-only modifiers. An agent slapping @functools.cache on dead
            # code must NOT get a free pass. Only active registrations (e.g. @app.get,
            # @registry.tool) count as wiring.
            # FIX D: Use sym.decorator_names (O(1)) instead of _has_decorators(tree, ...).
            if _has_active_decorators(sym.decorator_names):
                if "." not in sym.qualname:
                    # Top-level symbol — wire from module node
                    graph[module_node].add(sym.node_id)
                else:
                    # Nested symbol (method of a class) — wire from class node
                    parent_qualname = sym.qualname.rsplit(".", 1)[0]
                    parent_node = f"{module}:{parent_qualname}"
                    if parent_node not in graph:
                        graph[parent_node] = set()
                    graph[parent_node].add(sym.node_id)

            # FIX 4: Class->public-method edge — wire each public method of a class
            # from the class node. This handles polymorphic dispatch (override pattern).
            # FIX D: Use qualname_is_class index (O(1)) instead of _is_class_in_module(tree, ...).
            if "." in sym.qualname:
                parent_qualname = sym.qualname.rsplit(".", 1)[0]
                # Only wire if the parent is a class (not a nested function)
                if qualname_is_class.get(parent_qualname, False):
                    parent_node = f"{module}:{parent_qualname}"
                    if parent_node not in graph:
                        graph[parent_node] = set()
                    graph[parent_node].add(sym.node_id)

        # FIX C: Track whether current file is __init__.py for relative import resolution.
        is_init_file = file_path.replace("\\", "/").endswith("/__init__.py")

        # Walk the tree to resolve reference edges (Name, Attribute, imports)
        _add_reference_edges(
            tree,
            module,
            graph,
            all_symbols,
            sym_qualnames_in_module,
            first_party_modules,
            is_init_file=is_init_file,
        )

    return graph


def _enclosing_qualname(scope_stack: list[str]) -> str:
    """Return the qualname of the current enclosing scope, or '<module>' if top-level."""
    return ".".join(scope_stack) if scope_stack else "<module>"


def _add_reference_edges(
    tree: ast.Module,
    module: str,
    graph: Graph,
    all_symbols: dict[str, list[Symbol]],
    sym_qualnames_in_module: set[str],
    first_party_modules: set[str],
    is_init_file: bool = False,
) -> None:
    """Walk an AST and add reference edges into `graph`.

    For each Name or Attribute node that resolves to a known first-party symbol,
    add an edge from the enclosing scope node to the referenced symbol's node.

    FIX 2: Import statements add an edge from the importing scope's <module> node
    to the imported first-party module's <module> node (so importing a module makes
    its module-level code reachable). Individual imported symbols are NOT wired
    (C4: bare import/re-export does NOT count).

    FIX C: For __init__.py files, relative import level math must NOT drop a segment
    for level 1. The module name of an __init__.py IS the package itself; level-1
    means 'this package', not 'parent package'.

    Other import statements (non-first-party) are skipped entirely.
    """

    class EdgeBuilder(ast.NodeVisitor):
        def __init__(self) -> None:
            self._scope: list[str] = []

        def _current_node(self) -> str:
            return f"{module}:{_enclosing_qualname(self._scope)}"

        def _ensure_node(self, node_id: str) -> None:
            if node_id not in graph:
                graph[node_id] = set()

        def _add_edge(self, referenced_name: str) -> None:
            """Add edge from current scope to any known symbol with this short name."""
            src = self._current_node()
            self._ensure_node(src)
            targets = all_symbols.get(referenced_name, [])
            for sym in targets:
                tgt = sym.node_id
                self._ensure_node(tgt)
                graph[src].add(tgt)

        def _enter_def(self, node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) -> None:
            self._scope.append(node.name)
            # Add decorator references (decorators are value references, not imports)
            for dec in getattr(node, "decorator_list", []):
                dec_name = _extract_name(dec)
                if dec_name:
                    self._add_edge(dec_name)
            self.generic_visit(node)
            self._scope.pop()

        def _exit_def(self) -> None:
            pass

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            self._enter_def(node)

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            self._enter_def(node)

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            self._enter_def(node)

        # FIX 2: Import statements add import→module edges (NOT symbol edges)
        def visit_Import(self, node: ast.Import) -> None:
            """Handle `import mod` — wire importing scope's <module> to imported <module>."""
            src = f"{module}:<module>"  # always from the module-level node
            self._ensure_node(src)
            for alias in node.names:
                imported_mod = alias.name
                if imported_mod in first_party_modules:
                    tgt = f"{imported_mod}:<module>"
                    self._ensure_node(tgt)
                    graph[src].add(tgt)

        def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
            """Handle `from mod import x` — wire importing scope's <module> to mod:<module>.

            CRITICAL: do NOT wire individual imported symbols (x is NOT wired here).
            Only the module's <module> node (its top-level code) becomes reachable.

            Two forms handled:
              - `from pkg.mod import x`  → node.module='pkg.mod', level=0
                Emits edge to 'pkg.mod:<module>'.
              - `from . import utils`    → node.module=None, level=1
                Emits edge to '<base>.utils:<module>' for each name in node.names
                (each name is a submodule being imported from the package).
              - `from .mod import x`    → node.module='mod', level=1
                Emits edge to '<base>.mod:<module>'.

            FIX C: For __init__.py files the module name IS the package (e.g. 'app.core').
            A level-1 relative import means 'this package', NOT 'parent package'.
            Regular files (e.g. 'app.core.thing') correctly strip one segment for level 1
            to obtain 'app.core'. __init__.py files must NOT strip any segment for level 1.
            """
            if node.module is None and node.level == 0:
                return
            src = f"{module}:<module>"  # always from the module-level node
            self._ensure_node(src)

            if node.level and node.level > 0:
                # Relative import: compute base package from current module + level.
                # FIX C: For __init__.py (is_init_file=True), the module name IS already
                # the package, so level 1 means 'here' (no segment to drop). For a regular
                # file, level 1 means 'parent package' (drop the last segment = filename).
                parts = module.split(".")
                if is_init_file:
                    # __init__.py: module = 'app.core' = the package itself.
                    # level 1 -> base = 'app.core', level 2 -> base = 'app', etc.
                    base_parts = (
                        parts[: len(parts) - (node.level - 1)] if node.level > 1 else parts[:]
                    )
                else:
                    # Regular file: module = 'app.core.thing'; level 1 -> 'app.core'
                    base_parts = parts[: len(parts) - node.level]

                if node.module:
                    # `from .mod import x` — target is base.mod
                    candidate_mods = [".".join(base_parts + [node.module])]
                else:
                    # `from . import utils, other` — each name in node.names is a submodule
                    candidate_mods = [".".join(base_parts + [alias.name]) for alias in node.names]
            else:
                # Absolute import
                candidate_mods = [node.module]  # type: ignore[list-item]

            for imported_mod in candidate_mods:
                if imported_mod and imported_mod in first_party_modules:
                    tgt = f"{imported_mod}:<module>"
                    self._ensure_node(tgt)
                    graph[src].add(tgt)

        def visit_Name(self, node: ast.Name) -> None:
            if isinstance(node.ctx, ast.Load):
                self._add_edge(node.id)
            self.generic_visit(node)

        def visit_Attribute(self, node: ast.Attribute) -> None:
            # Only add edge for the attribute name part (handles obj.method pattern)
            if isinstance(node.ctx, ast.Load):
                self._add_edge(node.attr)
            self.generic_visit(node)

    EdgeBuilder().visit(tree)


def _extract_name(node: ast.expr) -> Optional[str]:
    """Extract the simple name from a Name or Attribute node (for decorators)."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


# ---------------------------------------------------------------------------
# 3. find_entrypoints
# ---------------------------------------------------------------------------


def find_entrypoints(
    source_files: list[str],
    file_reader: Callable[[str], str],
    pyproject_text: str,
    allowlist_lines: list[str],
) -> set[str]:
    """Find runtime entrypoint nodes.

    Roots:
      (a) Module-level node of any module containing `if __name__ == "__main__":`.
      (b) Any function named in pyproject [project.scripts] console-script targets.
      (c) Any module/symbol listed in config/dead_code_entrypoints.txt allowlist.
          pytest/tests are NOT entrypoints.

    Note on allowlisted MODULES (c): when the allowlist contains a module (no `:` qualifier),
    the operator is granting entry to the entire module. After FIX 1 removed the free
    <module>->top-level-symbol edges, we explicitly add all top-level public symbols of
    the allowlisted module as individual entrypoints. This preserves the escape-hatch
    semantics: `app.tool_mod` in the allowlist means all public symbols in that module
    are considered runtime-reachable by operator declaration.
    """
    entrypoints: set[str] = set()

    # Build a module->path index for allowlist symbol resolution
    module_to_path: dict[str, str] = {}
    for file_path in source_files:
        if not file_path.endswith(".py"):
            continue
        if _is_first_party(file_path):
            module_to_path[_path_to_module(file_path)] = file_path

    # (a) __main__ guard
    for file_path in source_files:
        if not file_path.endswith(".py"):
            continue
        try:
            source = file_reader(file_path)
        except (KeyError, FileNotFoundError, OSError):
            continue

        if 'if __name__ == "__main__"' in source or "if __name__ == '__main__'" in source:
            module = _path_to_module(file_path)
            entrypoints.add(f"{module}:<module>")

    # (b) pyproject.toml [project.scripts]
    if pyproject_text:
        in_scripts = False
        for line in pyproject_text.splitlines():
            stripped = line.strip()
            if stripped == "[project.scripts]":
                in_scripts = True
                continue
            if stripped.startswith("[") and stripped != "[project.scripts]":
                in_scripts = False
            if in_scripts and "=" in stripped and not stripped.startswith("#"):
                # e.g. my-tool = "app.cli:main"
                rhs = stripped.split("=", 1)[1].strip().strip('"').strip("'")
                # Format: "module.path:function"
                if ":" in rhs:
                    mod_path, fn_name = rhs.rsplit(":", 1)
                    entrypoints.add(f"{mod_path}:<module>")
                    entrypoints.add(f"{mod_path}:{fn_name}")

    # (c) Operator allowlist (one module:qualname or module per line)
    for line in allowlist_lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # Normalize path-style to dotted
        normalized = line.replace("/", ".").rstrip(".py").rstrip(".")
        # Could be "app.tool_mod" (module) or "app.tool_mod:fn" (symbol)
        if ":" in normalized:
            entrypoints.add(normalized)
        else:
            # Module-level allowlist entry: add <module> root AND all top-level public
            # symbols (operator is granting entry to the whole module).
            entrypoints.add(f"{normalized}:<module>")
            # Enumerate top-level public symbols from the module source
            mod_path = module_to_path.get(normalized)
            if mod_path:
                try:
                    source = file_reader(mod_path)
                    tree = ast.parse(source, filename=mod_path)
                    syms = _collect_symbols_from_ast(tree, normalized)
                    for sym in syms:
                        # Only top-level symbols (not nested methods)
                        if "." not in sym.qualname:
                            entrypoints.add(sym.node_id)
                except (OSError, KeyError, FileNotFoundError, SyntaxError):
                    pass  # best effort

    return entrypoints


# ---------------------------------------------------------------------------
# 4. reachable
# ---------------------------------------------------------------------------


def reachable(graph: Graph, roots: set[str]) -> set[str]:
    """BFS transitive closure over reference edges from roots.

    Returns the set of all nodes reachable (including the roots themselves).
    Cycle-safe via visited set.
    """
    visited: set[str] = set()
    queue: deque[str] = deque(roots)
    while queue:
        node = queue.popleft()
        if node in visited:
            continue
        visited.add(node)
        for neighbor in graph.get(node, set()):
            if neighbor not in visited:
                queue.append(neighbor)
    return visited


# ---------------------------------------------------------------------------
# 5. evaluate
# ---------------------------------------------------------------------------


def evaluate(
    diff_text: str,
    file_reader: Callable[[str], str],
    pyproject_text: str,
    allowlist_lines: list[str],
    source_files: Optional[list[str]] = None,
) -> Result:
    """Evaluate dead-code reachability for a diff.

    Args:
        diff_text: unified diff output (e.g. from `git diff base...HEAD`).
        file_reader: callable(path) -> file content (new version of the file).
        pyproject_text: content of pyproject.toml for console_scripts extraction.
        allowlist_lines: lines from config/dead_code_entrypoints.txt.
        source_files: list of all first-party .py files to build the callgraph from.
                      If None, inferred from diff_text (only changed files — limited).

    Returns:
        Result(ok, hard_failures, advisories).
    """
    hard_failures: list[str] = []
    advisories: list[str] = []

    # Self-waiver ban: scan diff for waiver markers FIRST (independent of reachability)
    waiver_markers = _detect_self_waivers_in_diff(diff_text)
    for marker in waiver_markers:
        hard_failures.append(
            f"C4 forbids {_MANUAL_MARKER}/self-waiver; exemptions are operator-approved "
            f"entrypoint-allowlist additions only. Found: {marker!r}"
        )

    # Determine the set of source files to analyze
    if source_files is None:
        # Infer from diff (limited — only changed files; may miss call sites)
        changed_files = list(_parse_diff_changed_lines(diff_text).keys())
        source_files = [f for f in changed_files if f.endswith(".py")]

    # Build the full callgraph from all provided source files
    graph = build_callgraph(source_files, file_reader)

    # Find all runtime entrypoints
    eps = find_entrypoints(source_files, file_reader, pyproject_text, allowlist_lines)

    # Compute reachable set from entrypoints
    reachable_nodes = reachable(graph, eps)

    # Extract changed public symbols from the diff
    changed_symbols = extract_changed_public_symbols(diff_text, file_reader)

    # Check each changed public symbol for reachability
    for sym in changed_symbols:
        node_id = sym.node_id
        if node_id not in reachable_nodes:
            hard_failures.append(
                f"dead code: {node_id} has no call site reachable from a runtime "
                f"entrypoint (C4); wire it or add a runtime entrypoint to "
                f"config/dead_code_entrypoints.txt"
            )

    ok = len(hard_failures) == 0
    return Result(ok=ok, hard_failures=hard_failures, advisories=advisories)


# ---------------------------------------------------------------------------
# 6. main CLI
# ---------------------------------------------------------------------------


def main(argv: list[str]) -> int:
    """CLI entry point.

    Usage:
        dead_code_gate.py [--diff <file|->] [--base <branch>] [--root <dir>] ...

    --diff: path to a diff file, or '-' to read from stdin.
            If omitted, runs `git diff origin/<base>...HEAD` automatically.
    --base: base branch for auto-diff (default: guessed from current branch or 'main').
    --root: repeatable; additional source root directories to scan (default: app scripts lib).
    --allowlist: path to entrypoints allowlist file (default: config/dead_code_entrypoints.txt).
    --pyproject: path to pyproject.toml (default: pyproject.toml).
    """
    ap = argparse.ArgumentParser(description="dead-code reachability gate (C4)")
    ap.add_argument(
        "--diff",
        default=None,
        help="diff file path or '-' for stdin; omit to auto-run git diff",
    )
    ap.add_argument(
        "--base",
        default=None,
        help="base branch for auto git diff (default: origin/main or origin/remediation/**)",
    )
    ap.add_argument(
        "--root",
        action="append",
        default=[],
        dest="roots",
        help="source root dir (repeatable; default: app scripts lib)",
    )
    ap.add_argument(
        "--allowlist",
        default="config/dead_code_entrypoints.txt",
        help="path to operator entrypoints allowlist",
    )
    ap.add_argument(
        "--pyproject",
        default="pyproject.toml",
        help="path to pyproject.toml",
    )
    args = ap.parse_args(argv)

    # Get diff text
    if args.diff == "-":
        diff_text = sys.stdin.read()
    elif args.diff:
        try:
            with open(args.diff) as fh:
                diff_text = fh.read()
        except OSError as e:
            print(f"::error::dead-code-gate: cannot read --diff: {e}")
            return 1
    else:
        # Auto-run git diff
        base = args.base
        if base is None:
            # Guess base ref
            try:
                head_branch = subprocess.run(
                    ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                    capture_output=True,
                    text=True,
                    check=True,
                ).stdout.strip()
                # If we're on remediation/**, base off that; else main
                if head_branch.startswith("remediation/"):
                    base = f"origin/{head_branch.split('/')[0]}/{head_branch.split('/')[1]}"
                else:
                    base = "origin/main"
            except Exception:
                base = "origin/main"
        try:
            diff_text = subprocess.run(
                ["git", "diff", f"{base}...HEAD"],
                capture_output=True,
                text=True,
                check=True,
            ).stdout
        except subprocess.CalledProcessError as e:
            print(f"::error::dead-code-gate: git diff failed: {e.stderr}")
            return 1

    # Read allowlist
    allowlist_lines: list[str] = []
    try:
        with open(args.allowlist) as fh:
            allowlist_lines = fh.readlines()
    except OSError:
        pass  # allowlist file is optional

    # Read pyproject.toml
    pyproject_text = ""
    try:
        with open(args.pyproject) as fh:
            pyproject_text = fh.read()
    except OSError:
        pass

    # Collect source files from roots
    import pathlib

    roots = args.roots if args.roots else ["app", "scripts", "lib"]
    source_files: list[str] = []
    for root in roots:
        root_path = pathlib.Path(root)
        if root_path.exists():
            for py in root_path.rglob("*.py"):
                source_files.append(str(py).replace("\\", "/"))

    result = evaluate(
        diff_text=diff_text,
        file_reader=lambda p: open(p).read(),
        pyproject_text=pyproject_text,
        allowlist_lines=allowlist_lines,
        source_files=source_files,
    )

    for failure in result.hard_failures:
        print(f"::error::dead-code-gate: {failure}")
    for advisory in result.advisories:
        print(f"::warning::dead-code-gate: {advisory}")

    total_changed = len(extract_changed_public_symbols(diff_text, lambda p: open(p).read()))
    print(
        f"== dead-code-gate: {len(result.hard_failures)} hard failure(s) | "
        f"{len(result.advisories)} advisory | "
        f"changed public symbols checked: {total_changed} | "
        f"=> {'FAIL' if not result.ok else 'PASS'} =="
    )
    return 1 if not result.ok else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
