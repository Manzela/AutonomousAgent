#!/usr/bin/env python3
"""SP-00e.5 — dead-code reachability gate (Executor Contract C4).

Every public function/class ADDED or whose BODY CHANGED must have ≥1 call site
REACHABLE FROM A RUNTIME ENTRYPOINT (bare import/re-export does NOT count), proven
by a callgraph built from AST analysis. Self-waivers (@manual, # dead-code: ignore,
# noqa: deadcode) are HARD failures — the only escape hatch is the operator-approved
config/dead_code_entrypoints.txt allowlist.

Anti-pattern killed: dead code shipped as ticked boxes.

Approximation note (documented per design):
  - Name resolution is approximate (match by symbol short-name within first-party
    symbols, preferring same-module). This means:
      (a) Two symbols with the same short name from different modules may create
          spurious edges (false negatives — symbol appears wired when it isn't).
      (b) Dynamic dispatch, exec(), getattr(mod, name), importlib, and runtime
          injection are NOT tracked (false negatives — dynamic wiring missed).
  - These approximate false negatives are acceptable (conservative toward safety
    of shipping code) but are noted here for auditability.
  - False positives (code flagged as dead when it's actually used) are minimized
    by including value-reference wiring (see build_callgraph).

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

# Self-waiver patterns that are FORBIDDEN per C4
_SELF_WAIVER_PATTERNS = [
    re.compile(r"@manual\b"),
    re.compile(r"#\s*dead-code:\s*ignore"),
    re.compile(r"#\s*noqa:\s*deadcode"),
    re.compile(r"#\s*noqa:\s*DC\d+"),
]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


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
            # Public: name does NOT start with '_' AND no private ancestor
            if not is_private and not self._has_private_ancestor:
                results.append(
                    Symbol(
                        qualname=qualname,
                        module=module,
                        name=node.name,
                        kind=kind,
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


def _detect_self_waivers_in_diff(diff_text: str) -> list[str]:
    """Return list of waiver markers found on '+' (added) lines in the diff.

    Patterns are matched in code context only to avoid false positives from docstrings,
    string literals, or explanatory comments that MENTION waiver terms:

      - @manual decorator: the stripped line must START with '@manual' (it's an
        actual Python decorator, not text inside a string or docstring).
      - # dead-code: ignore / # noqa: deadcode: the '#' comment must appear in actual
        Python code, not inside a docstring or string literal context.

    We track triple-quoted string context across lines to exclude docstring content.
    """
    found: list[str] = []
    in_triple_string = False  # tracks whether we're inside a """...""" or '''...''' block

    for raw_line in diff_text.splitlines():
        content = raw_line[1:] if raw_line.startswith("+") else raw_line
        stripped = content.strip()

        # Track triple-quoted string context for all lines (+ and context)
        # Count triple-quote occurrences; an odd count toggles the state
        tq_count = stripped.count('"""') + stripped.count("'''")
        if tq_count % 2 == 1:
            in_triple_string = not in_triple_string

        # Only inspect added lines for waivers
        if not raw_line.startswith("+") or raw_line.startswith("+++"):
            continue

        # Skip lines inside triple-quoted strings (docstrings, multiline strings)
        # If this line itself toggles the triple-string state, the line IS the
        # opening/closing delimiter — it might have code before/after the triple-quote.
        # Conservatively, skip if we're currently in triple-string context AFTER toggle.
        if in_triple_string:
            continue

        # @manual as a decorator: stripped line must start with '@manual'
        if stripped.startswith("@manual") and re.match(r"@manual\b", stripped):
            found.append(stripped)
            continue

        # Check for inline comment containing waiver: find '#' outside of string literals
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

    Edges (REFERENCES, NOT imports):
      - Inside a function/method/class body or module-level code, every Name or
        Attribute that matches a known first-party symbol's short name creates an
        edge from the enclosing node to the referenced symbol.
      - Value references (passed as argument, decorator, list element, etc.) are
        included — a symbol passed as a value is considered wired.
      - `import` / `from x import y` statements do NOT create edges (C4: bare
        import/re-export excluded).

    Approximation: resolution is by short name (last component of qualname).
    Two symbols with the same short name may create spurious edges (false negatives
    from the gate's perspective — code incorrectly appears reachable).
    """
    graph: Graph = {}

    # First pass: collect all known symbols from all first-party files
    all_symbols: dict[str, list[Symbol]] = {}  # short_name -> list of symbols
    module_to_symbols: dict[str, list[Symbol]] = {}

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

        # Add edges from <module> to each top-level symbol defined in this module.
        # Rationale: a `def fn():` statement at module level IS executed by the module's
        # top-level code (it defines the function in the module namespace). Any module
        # that has `if __name__ == "__main__":` is an entrypoint root — its <module> node
        # transitively reaches all its top-level definitions.
        module_node = f"{module}:<module>"
        if module_node not in graph:
            graph[module_node] = set()
        for sym in syms_in_module:
            # Only wire top-level symbols (not nested class methods etc.)
            if "." not in sym.qualname:
                graph[module_node].add(sym.node_id)

        # Walk the tree to resolve references
        _add_reference_edges(tree, module, graph, all_symbols, sym_qualnames_in_module)

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
) -> None:
    """Walk an AST and add reference edges into `graph`.

    For each Name or Attribute node that resolves to a known first-party symbol,
    add an edge from the enclosing scope node to the referenced symbol's node.
    Import statements are explicitly skipped.
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

        # Skip import statements — they do NOT create reference edges (C4)
        def visit_Import(self, node: ast.Import) -> None:
            pass  # intentionally skip

        def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
            pass  # intentionally skip

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
    """
    entrypoints: set[str] = set()

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
            entrypoints.add(f"{normalized}:<module>")

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
            f"C4 forbids @manual/self-waiver; exemptions are operator-approved "
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
