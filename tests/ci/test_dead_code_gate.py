#!/usr/bin/env python3
"""TDD tests for dead_code_gate.py — SP-00e.5 (Executor Contract C4).

Each test is self-contained and uses in-memory fake file_reader dicts (NOT the real
repo) so tests are hermetic. Tests are ordered to mirror the red-green-refactor cycle
described in the PRD seed.
"""

from __future__ import annotations

import textwrap
from typing import Callable

# Import the module under test — will fail at collection time until the gate exists.
from scripts.ci.dead_code_gate import (
    evaluate,
    extract_changed_public_symbols,
    find_entrypoints,
    reachable,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_reader(files: dict[str, str]) -> Callable[[str], str]:
    """Return a file_reader callable backed by an in-memory dict."""

    def reader(path: str) -> str:
        return files[path]

    return reader


def diff_adding(module_path: str, code: str) -> str:
    """Produce a minimal unified-diff snippet that adds `code` to `module_path`."""
    lines = textwrap.dedent(code).splitlines()
    added = "\n".join("+" + line for line in lines)
    return (
        f"diff --git a/{module_path} b/{module_path}\n"
        f"--- a/{module_path}\n"
        f"+++ b/{module_path}\n"
        f"@@ -0,0 +1,{len(lines)} @@\n"
        f"{added}\n"
    )


def diff_modifying(
    module_path: str, old_lines: list[str], new_lines: list[str], hunk_start: int = 2
) -> str:
    """Produce a diff that modifies existing lines (simulate body change).

    hunk_start: the new-file line number at which the change begins.
    Default is 2, which lands inside a function that starts at line 1.
    """
    removed = "\n".join("-" + line for line in old_lines)
    added = "\n".join("+" + line for line in new_lines)
    return (
        f"diff --git a/{module_path} b/{module_path}\n"
        f"--- a/{module_path}\n"
        f"+++ b/{module_path}\n"
        f"@@ -{hunk_start},{len(old_lines)} +{hunk_start},{len(new_lines)} @@\n"
        f"{removed}\n"
        f"{added}\n"
    )


# ===========================================================================
# 1. Dead symbol: added public def with no call site → HARD fail
# ===========================================================================


def test_dead_symbol_is_hard_failure() -> None:
    """A diff adding `def orphan()` that is never called → HARD failure naming it."""
    # Module with orphan function — not called anywhere
    orphan_code = textwrap.dedent("""\
        def orphan():
            return 42
    """)
    # Entrypoint module — calls nothing from app.orphan_mod
    ep_code = textwrap.dedent("""\
        def main():
            pass

        if __name__ == "__main__":
            main()
    """)
    files = {
        "app/orphan_mod.py": orphan_code,
        "scripts/ci/dead_code_gate.py": ep_code,  # itself is an entrypoint
    }
    reader = make_reader(files)
    diff = diff_adding("app/orphan_mod.py", orphan_code)

    result = evaluate(
        diff_text=diff,
        file_reader=reader,
        pyproject_text="",
        allowlist_lines=[],
        source_files=list(files.keys()),
    )
    assert not result.ok
    assert result.hard_failures
    assert any("orphan" in f for f in result.hard_failures), result.hard_failures
    assert any("C4" in f or "dead code" in f.lower() for f in result.hard_failures)


# ===========================================================================
# 2. Wired-by-call: helper called by entrypoint-reachable function → PASS
# ===========================================================================


def test_wired_by_call_passes() -> None:
    """def helper() added AND called from entrypoint-reachable code → PASS."""
    helper_code = textwrap.dedent("""\
        def helper():
            return 1
    """)
    ep_code = textwrap.dedent("""\
        from app.helper_mod import helper

        def run():
            return helper()

        if __name__ == "__main__":
            run()
    """)
    files = {
        "app/helper_mod.py": helper_code,
        "app/entry.py": ep_code,
    }
    reader = make_reader(files)
    diff = diff_adding("app/helper_mod.py", helper_code)

    result = evaluate(
        diff_text=diff,
        file_reader=reader,
        pyproject_text="",
        allowlist_lines=[],
        source_files=list(files.keys()),
    )
    assert result.ok, result.hard_failures


# ===========================================================================
# 3. Wired-by-value (framework pattern): passed as value from reachable code → PASS
# ===========================================================================


def test_wired_by_value_framework_passes() -> None:
    """def node_fn() added and passed as a value from reachable code → PASS.

    This is the key false-positive guard: framework registrations like
    `graph.add_node(node_fn)` must wire the symbol without requiring a literal call.
    """
    node_code = textwrap.dedent("""\
        def node_fn():
            return "node"
    """)
    ep_code = textwrap.dedent("""\
        from app.node_mod import node_fn

        class Graph:
            def add_node(self, fn):
                pass

        def build():
            g = Graph()
            g.add_node(node_fn)

        if __name__ == "__main__":
            build()
    """)
    files = {
        "app/node_mod.py": node_code,
        "app/builder.py": ep_code,
    }
    reader = make_reader(files)
    diff = diff_adding("app/node_mod.py", node_code)

    result = evaluate(
        diff_text=diff,
        file_reader=reader,
        pyproject_text="",
        allowlist_lines=[],
        source_files=list(files.keys()),
    )
    assert result.ok, result.hard_failures


# ===========================================================================
# 4. Bare import only: __init__ re-export, never referenced → HARD fail
# ===========================================================================


def test_bare_import_only_is_hard_failure() -> None:
    """A symbol only `from mod import sym` re-exported in __init__; never referenced → HARD fail."""
    sym_code = textwrap.dedent("""\
        def public_fn():
            return "data"
    """)
    # __init__ just re-exports — a bare import, no call site
    init_code = textwrap.dedent("""\
        from app.sym_mod import public_fn  # re-export
    """)
    ep_code = textwrap.dedent("""\
        if __name__ == "__main__":
            pass
    """)
    files = {
        "app/sym_mod.py": sym_code,
        "app/__init__.py": init_code,
        "app/entry.py": ep_code,
    }
    reader = make_reader(files)
    diff = diff_adding("app/sym_mod.py", sym_code)

    result = evaluate(
        diff_text=diff,
        file_reader=reader,
        pyproject_text="",
        allowlist_lines=[],
        source_files=list(files.keys()),
    )
    assert not result.ok
    assert any("public_fn" in f for f in result.hard_failures), result.hard_failures


# ===========================================================================
# 5. Test-only reference: symbol referenced only from tests/ → HARD fail
# ===========================================================================


def test_test_only_reference_is_hard_failure() -> None:
    """Symbol referenced ONLY from tests/ → HARD fail (tests aren't runtime entrypoints)."""
    sym_code = textwrap.dedent("""\
        def test_helper():
            return "help"
    """)
    # Note: test_helper starts with 'test_' — wait, that's a pytest convention.
    # Let's use a clean public name that happens to only be used by tests.
    sym_code = textwrap.dedent("""\
        def compute_result():
            return 99
    """)
    test_code = textwrap.dedent("""\
        from app.compute_mod import compute_result

        def test_compute():
            assert compute_result() == 99
    """)
    ep_code = textwrap.dedent("""\
        if __name__ == "__main__":
            pass
    """)
    files = {
        "app/compute_mod.py": sym_code,
        "tests/unit/test_compute.py": test_code,
        "app/entry.py": ep_code,
    }
    reader = make_reader(files)
    diff = diff_adding("app/compute_mod.py", sym_code)

    result = evaluate(
        diff_text=diff,
        file_reader=reader,
        pyproject_text="",
        allowlist_lines=[],
        source_files=list(files.keys()),
    )
    assert not result.ok
    assert any("compute_result" in f for f in result.hard_failures), result.hard_failures


# ===========================================================================
# 6. Private excluded: added def _private() unreferenced → NOT failed
# ===========================================================================


def test_private_symbol_not_flagged() -> None:
    """Added def _private() unreferenced → NOT a hard failure (private out of scope)."""
    code = textwrap.dedent("""\
        def _private():
            return "secret"

        def __dunder__():
            pass
    """)
    ep_code = textwrap.dedent("""\
        if __name__ == "__main__":
            pass
    """)
    files = {
        "app/private_mod.py": code,
        "app/entry.py": ep_code,
    }
    reader = make_reader(files)
    diff = diff_adding("app/private_mod.py", code)

    result = evaluate(
        diff_text=diff,
        file_reader=reader,
        pyproject_text="",
        allowlist_lines=[],
        source_files=list(files.keys()),
    )
    # No private symbols should appear in failures
    assert not any("_private" in f for f in result.hard_failures)
    assert not any("__dunder__" in f for f in result.hard_failures)


# ===========================================================================
# 7. Transitive reachability: A(reachable)->B->C(added) passes; C only via dead D → fails
# ===========================================================================


def test_transitive_reachable_passes() -> None:
    """A (reachable) -> B -> C (added); C passes via transitive edge."""
    c_code = textwrap.dedent("""\
        def leaf_c():
            return "c"
    """)
    b_code = textwrap.dedent("""\
        from app.c_mod import leaf_c

        def middle_b():
            return leaf_c()
    """)
    ep_code = textwrap.dedent("""\
        from app.b_mod import middle_b

        def run():
            return middle_b()

        if __name__ == "__main__":
            run()
    """)
    files = {
        "app/c_mod.py": c_code,
        "app/b_mod.py": b_code,
        "app/entry.py": ep_code,
    }
    reader = make_reader(files)
    diff = diff_adding("app/c_mod.py", c_code)

    result = evaluate(
        diff_text=diff,
        file_reader=reader,
        pyproject_text="",
        allowlist_lines=[],
        source_files=list(files.keys()),
    )
    assert result.ok, result.hard_failures


def test_transitive_only_via_dead_fails() -> None:
    """C added but only reachable via dead D (D itself unreachable) → C fails."""
    c_code = textwrap.dedent("""\
        def leaf_c():
            return "c"
    """)
    # D references C but D is unreachable
    d_code = textwrap.dedent("""\
        from app.c_mod import leaf_c

        def dead_d():
            return leaf_c()
    """)
    ep_code = textwrap.dedent("""\
        if __name__ == "__main__":
            pass
    """)
    files = {
        "app/c_mod.py": c_code,
        "app/d_mod.py": d_code,
        "app/entry.py": ep_code,
    }
    reader = make_reader(files)
    diff = diff_adding("app/c_mod.py", c_code)

    result = evaluate(
        diff_text=diff,
        file_reader=reader,
        pyproject_text="",
        allowlist_lines=[],
        source_files=list(files.keys()),
    )
    assert not result.ok
    assert any("leaf_c" in f for f in result.hard_failures), result.hard_failures


# ===========================================================================
# 8. Entrypoint allowlist escape hatch → PASS
# ===========================================================================


def test_allowlist_escape_hatch_passes() -> None:
    """An otherwise-dead added symbol listed in allowlist_lines → PASS."""
    sym_code = textwrap.dedent("""\
        def standalone_tool():
            return "runs standalone"
    """)
    ep_code = textwrap.dedent("""\
        if __name__ == "__main__":
            pass
    """)
    files = {
        "app/tool_mod.py": sym_code,
        "app/entry.py": ep_code,
    }
    reader = make_reader(files)
    diff = diff_adding("app/tool_mod.py", sym_code)
    # Allowlist the module — operator escape hatch
    allowlist = ["app.tool_mod"]

    result = evaluate(
        diff_text=diff,
        file_reader=reader,
        pyproject_text="",
        allowlist_lines=allowlist,
        source_files=list(files.keys()),
    )
    assert result.ok, result.hard_failures


# ===========================================================================
# 9. Self-waiver ban: @manual / # dead-code: ignore → HARD fail
# ===========================================================================


def test_self_waiver_manual_decorator_is_hard_failure() -> None:
    """A changed symbol with an added @manual → HARD fail (C4 forbids self-waiver)."""
    code_with_waiver = textwrap.dedent("""\
        @manual
        def waived_fn():
            return "waived"
    """)
    ep_code = textwrap.dedent("""\
        if __name__ == "__main__":
            pass
    """)
    files = {
        "app/waiver_mod.py": code_with_waiver,
        "app/entry.py": ep_code,
    }
    reader = make_reader(files)
    diff = diff_adding("app/waiver_mod.py", code_with_waiver)

    result = evaluate(
        diff_text=diff,
        file_reader=reader,
        pyproject_text="",
        allowlist_lines=[],
        source_files=list(files.keys()),
    )
    assert not result.ok
    # Must mention self-waiver / @manual explicitly
    combined = " ".join(result.hard_failures).lower()
    assert "manual" in combined or "self-waiver" in combined or "waiver" in combined


def test_self_waiver_noqa_comment_is_hard_failure() -> None:
    """A changed symbol with # dead-code: ignore → HARD fail."""
    code_with_noqa = textwrap.dedent("""\
        def annotated_fn():  # dead-code: ignore
            return "ignored"
    """)
    ep_code = textwrap.dedent("""\
        if __name__ == "__main__":
            pass
    """)
    files = {
        "app/noqa_mod.py": code_with_noqa,
        "app/entry.py": ep_code,
    }
    reader = make_reader(files)
    diff = diff_adding("app/noqa_mod.py", code_with_noqa)

    result = evaluate(
        diff_text=diff,
        file_reader=reader,
        pyproject_text="",
        allowlist_lines=[],
        source_files=list(files.keys()),
    )
    assert not result.ok
    combined = " ".join(result.hard_failures).lower()
    assert (
        "waiver" in combined
        or "self-waiver" in combined
        or "ignore" in combined
        or "manual" in combined
    )


# ===========================================================================
# 10. Body-changed (not just added): pre-existing public fn whose BODY changes
# ===========================================================================


def test_body_changed_unreachable_fails() -> None:
    """A pre-existing public fn whose BODY changes but is unreachable → flagged."""
    old_body = ["    return 1"]
    new_body = ["    return 2"]
    new_file_content = textwrap.dedent("""\
        def existing_fn():
            return 2
    """)
    ep_code = textwrap.dedent("""\
        if __name__ == "__main__":
            pass
    """)
    files = {
        "app/existing_mod.py": new_file_content,
        "app/entry.py": ep_code,
    }
    reader = make_reader(files)
    diff = diff_modifying("app/existing_mod.py", old_body, new_body)

    result = evaluate(
        diff_text=diff,
        file_reader=reader,
        pyproject_text="",
        allowlist_lines=[],
        source_files=list(files.keys()),
    )
    assert not result.ok
    assert any("existing_fn" in f for f in result.hard_failures), result.hard_failures


def test_body_changed_reachable_passes() -> None:
    """A pre-existing public fn whose BODY changes AND IS reachable → passes."""
    old_body = ["    return 1"]
    new_body = ["    return 2"]
    new_file_content = textwrap.dedent("""\
        def existing_fn():
            return 2
    """)
    ep_code = textwrap.dedent("""\
        from app.existing_mod import existing_fn

        def run():
            return existing_fn()

        if __name__ == "__main__":
            run()
    """)
    files = {
        "app/existing_mod.py": new_file_content,
        "app/entry.py": ep_code,
    }
    reader = make_reader(files)
    diff = diff_modifying("app/existing_mod.py", old_body, new_body)

    result = evaluate(
        diff_text=diff,
        file_reader=reader,
        pyproject_text="",
        allowlist_lines=[],
        source_files=list(files.keys()),
    )
    assert result.ok, result.hard_failures


# ===========================================================================
# 11. Gate passes on its own source (self-rooted via __main__)
# ===========================================================================


def test_gate_passes_on_own_source() -> None:
    """evaluate() on a diff that adds dead_code_gate.py's own functions → PASS.

    The gate script itself has `if __name__ == "__main__": sys.exit(main(...))`,
    and main() calls evaluate() which calls build_callgraph / find_entrypoints /
    reachable / extract_changed_public_symbols. All public functions must be
    transitively reachable from the main() call in the __main__ block.

    After FIX 1, each function must be EXPLICITLY referenced (called or passed as
    value) in the call chain — the free <module>->top-level edge is gone. This
    fixture mirrors the real gate's structure where main calls evaluate, evaluate
    calls build_callgraph / find_entrypoints / reachable / extract_changed_public_symbols.
    """
    # Realistic fixture: main -> evaluate -> build_callgraph, find_entrypoints,
    # reachable, extract_changed_public_symbols (all referenced by name in evaluate body)
    gate_code = textwrap.dedent("""\
        def extract_changed_public_symbols(diff_text, new_file_reader):
            return []

        def build_callgraph(source_files, file_reader):
            return {}

        def find_entrypoints(source_files, file_reader, pyproject_text, allowlist_lines):
            return set()

        def reachable(graph, roots):
            return set()

        def evaluate(diff_text, file_reader, pyproject_text, allowlist_lines, source_files=None):
            graph = build_callgraph(source_files or [], file_reader)
            eps = find_entrypoints(source_files or [], file_reader, pyproject_text, allowlist_lines)
            r = reachable(graph, eps)
            syms = extract_changed_public_symbols(diff_text, file_reader)
            return syms

        def main(argv):
            result = evaluate("", lambda p: "", "", [])
            return 0

        if __name__ == "__main__":
            import sys
            sys.exit(main(sys.argv[1:]))
    """)
    files = {
        "scripts/ci/dead_code_gate.py": gate_code,
    }
    reader = make_reader(files)
    diff = diff_adding("scripts/ci/dead_code_gate.py", gate_code)

    result = evaluate(
        diff_text=diff,
        file_reader=reader,
        pyproject_text="",
        allowlist_lines=[],
        source_files=list(files.keys()),
    )
    assert result.ok, result.hard_failures


# ===========================================================================
# 12. Unit tests for pure helper functions
# ===========================================================================


class TestExtractChangedPublicSymbols:
    """Unit tests for extract_changed_public_symbols."""

    def test_returns_added_public_function(self) -> None:
        code = textwrap.dedent("""\
            def public_fn():
                return 1
        """)
        files = {"app/mod.py": code}
        diff = diff_adding("app/mod.py", code)
        symbols = extract_changed_public_symbols(diff, make_reader(files))
        names = [s.name for s in symbols]
        assert "public_fn" in names

    def test_excludes_private_function(self) -> None:
        code = textwrap.dedent("""\
            def _private():
                return 1
        """)
        files = {"app/mod.py": code}
        diff = diff_adding("app/mod.py", code)
        symbols = extract_changed_public_symbols(diff, make_reader(files))
        assert not any(s.name.startswith("_") for s in symbols)

    def test_excludes_test_files(self) -> None:
        code = textwrap.dedent("""\
            def public_fn():
                return 1
        """)
        files = {"tests/unit/test_mod.py": code}
        diff = diff_adding("tests/unit/test_mod.py", code)
        symbols = extract_changed_public_symbols(diff, make_reader(files))
        assert not symbols, "test files should not contribute symbols"

    def test_returns_class(self) -> None:
        code = textwrap.dedent("""\
            class MyClass:
                def public_method(self):
                    pass
        """)
        files = {"app/mod.py": code}
        diff = diff_adding("app/mod.py", code)
        symbols = extract_changed_public_symbols(diff, make_reader(files))
        names = [s.name for s in symbols]
        assert "MyClass" in names


class TestFindEntrypoints:
    """Unit tests for find_entrypoints."""

    def test_main_guard_is_entrypoint(self) -> None:
        ep_code = textwrap.dedent("""\
            def run():
                pass

            if __name__ == "__main__":
                run()
        """)
        files = {"app/entry.py": ep_code}
        reader = make_reader(files)
        eps = find_entrypoints(
            source_files=list(files.keys()),
            file_reader=reader,
            pyproject_text="",
            allowlist_lines=[],
        )
        assert any("app.entry" in ep or "app/entry" in ep for ep in eps)

    def test_allowlist_adds_entrypoint(self) -> None:
        code = textwrap.dedent("""\
            def standalone():
                pass
        """)
        files = {"app/tool.py": code}
        reader = make_reader(files)
        eps = find_entrypoints(
            source_files=list(files.keys()),
            file_reader=reader,
            pyproject_text="",
            allowlist_lines=["app.tool"],
        )
        assert any("app.tool" in ep or "app/tool" in ep for ep in eps)


class TestReachable:
    """Unit tests for reachable BFS/DFS."""

    def test_direct_edge(self) -> None:
        graph: dict[str, set[str]] = {"A": {"B"}, "B": set()}
        result = reachable(graph, {"A"})
        assert "B" in result

    def test_transitive(self) -> None:
        graph: dict[str, set[str]] = {"A": {"B"}, "B": {"C"}, "C": set()}
        result = reachable(graph, {"A"})
        assert "C" in result

    def test_cycle_safe(self) -> None:
        """Cycle in graph should not infinite-loop."""
        graph: dict[str, set[str]] = {"A": {"B"}, "B": {"A"}}
        result = reachable(graph, {"A"})
        assert "B" in result
        assert "A" in result


# ===========================================================================
# 13. FIX 1 — Trivial bypass closed: orphan in entrypoint module → HARD fail
# ===========================================================================


def test_fix1_orphan_in_entrypoint_module_still_fails() -> None:
    """FIX 1: def orphan() added to an entrypoint module (has if __name__) but
    never referenced → still HARD fails. The old free <module>->top-level edge
    would have made this pass — verify the bypass is closed."""
    # Entrypoint module containing BOTH a wired main() AND an unreferenced orphan().
    entry_with_orphan = textwrap.dedent("""\
        def orphan():
            return 42

        def main():
            pass

        if __name__ == "__main__":
            main()
    """)
    files = {"scripts/ci/entry_with_orphan.py": entry_with_orphan}
    reader = make_reader(files)
    diff = diff_adding("scripts/ci/entry_with_orphan.py", entry_with_orphan)

    result = evaluate(
        diff_text=diff,
        file_reader=reader,
        pyproject_text="",
        allowlist_lines=[],
        source_files=list(files.keys()),
    )
    assert not result.ok, "orphan() in entrypoint module must still fail"
    assert any("orphan" in f for f in result.hard_failures), result.hard_failures
    # main() IS referenced in the if __name__ block → must NOT be flagged
    assert not any(
        "main" in f for f in result.hard_failures
    ), "main() is referenced in if __name__ block — must not be flagged"


def test_fix1_main_referenced_in_dunder_main_passes() -> None:
    """FIX 1 complement: def main() called in the if __name__ block → PASSES.
    The reference walk emits a <module>->main edge from the Name node in the block."""
    ep_code = textwrap.dedent("""\
        def main():
            return 0

        if __name__ == "__main__":
            main()
    """)
    files = {"scripts/ci/ep_main.py": ep_code}
    reader = make_reader(files)
    diff = diff_adding("scripts/ci/ep_main.py", ep_code)

    result = evaluate(
        diff_text=diff,
        file_reader=reader,
        pyproject_text="",
        allowlist_lines=[],
        source_files=list(files.keys()),
    )
    assert result.ok, result.hard_failures


# ===========================================================================
# 14. FIX 2 — Import→module edge: decorated handler in imported module → PASS
# ===========================================================================


def test_fix2_bare_from_import_does_not_wire_symbol() -> None:
    """FIX 2 (bare import): `from mod import helper` does NOT wire `helper` directly.
    Only the module's <module> node becomes reachable (C4: bare import/re-export excluded).
    helper must still be unreachable unless explicitly referenced or decorated."""
    helper_code = textwrap.dedent("""\
        def helper():
            return 1
    """)
    ep_code = textwrap.dedent("""\
        from app.helper_bare import helper

        if __name__ == "__main__":
            pass
    """)
    files = {
        "app/helper_bare.py": helper_code,
        "app/entry.py": ep_code,
    }
    reader = make_reader(files)
    diff = diff_adding("app/helper_bare.py", helper_code)

    result = evaluate(
        diff_text=diff,
        file_reader=reader,
        pyproject_text="",
        allowlist_lines=[],
        source_files=list(files.keys()),
    )
    # helper is imported but never called/decorated → must fail
    assert not result.ok, "bare import without reference must still fail"
    assert any("helper" in f for f in result.hard_failures), result.hard_failures


def test_fix2_and_fix3_decorated_handler_in_imported_module_passes() -> None:
    """FIX 2 + FIX 3: A module-level @app.get handler in a module that is imported
    by a reachable module → handler PASSES (import wires module; decorator wires handler).
    Same handler in a module imported by nobody → fails."""
    routes_code = textwrap.dedent("""\
        class app:
            @staticmethod
            def get(path):
                def decorator(fn): return fn
                return decorator

        @app.get('/items')
        def get_items():
            return []
    """)
    # Entry imports routes → routes:<module> becomes reachable → decorator wires get_items
    ep_code = textwrap.dedent("""\
        import app.routes_mod

        if __name__ == "__main__":
            pass
    """)
    files = {
        "app/routes_mod.py": routes_code,
        "app/entry.py": ep_code,
    }
    reader = make_reader(files)
    diff = diff_adding("app/routes_mod.py", routes_code)

    result = evaluate(
        diff_text=diff,
        file_reader=reader,
        pyproject_text="",
        allowlist_lines=[],
        source_files=list(files.keys()),
    )
    assert result.ok, "decorated handler in module imported by entrypoint must pass: " + str(
        result.hard_failures
    )


def test_fix2_handler_in_unimported_module_fails() -> None:
    """FIX 2 complement: same decorated handler in a module that nobody imports → fails."""
    routes_code = textwrap.dedent("""\
        class app:
            @staticmethod
            def get(path):
                def decorator(fn): return fn
                return decorator

        @app.get('/items')
        def get_items():
            return []
    """)
    ep_code = textwrap.dedent("""\
        if __name__ == "__main__":
            pass
    """)
    files = {
        "app/routes_orphan.py": routes_code,
        "app/entry.py": ep_code,
    }
    reader = make_reader(files)
    diff = diff_adding("app/routes_orphan.py", routes_code)

    result = evaluate(
        diff_text=diff,
        file_reader=reader,
        pyproject_text="",
        allowlist_lines=[],
        source_files=list(files.keys()),
    )
    assert not result.ok, "decorated handler in unimported module must fail"
    assert any("get_items" in f for f in result.hard_failures), result.hard_failures


# ===========================================================================
# 15. FIX 3 — Decorator wiring
# ===========================================================================


def test_fix3_decorated_public_fn_in_reachable_module_passes() -> None:
    """FIX 3: A decorated public function (any decorator) in a reachable module → PASSES."""
    # Module with a tool decorator pattern — the decorated fn is registered by the decorator
    tool_code = textwrap.dedent("""\
        class registry:
            tools = []
            @classmethod
            def tool(cls, fn):
                cls.tools.append(fn)
                return fn

        @registry.tool
        def search_files():
            return []
    """)
    ep_code = textwrap.dedent("""\
        import app.tool_registry

        if __name__ == "__main__":
            pass
    """)
    files = {
        "app/tool_registry.py": tool_code,
        "app/entry.py": ep_code,
    }
    reader = make_reader(files)
    diff = diff_adding("app/tool_registry.py", tool_code)

    result = evaluate(
        diff_text=diff,
        file_reader=reader,
        pyproject_text="",
        allowlist_lines=[],
        source_files=list(files.keys()),
    )
    assert result.ok, "decorated public fn in reachable module must pass: " + str(
        result.hard_failures
    )


def test_fix3_undecorated_unreferenced_fn_in_reachable_module_fails() -> None:
    """FIX 3 complement: undecorated, unreferenced public fn in a reachable module → FAILS."""
    mod_code = textwrap.dedent("""\
        def unreferenced_fn():
            return 42
    """)
    ep_code = textwrap.dedent("""\
        import app.bare_mod

        if __name__ == "__main__":
            pass
    """)
    files = {
        "app/bare_mod.py": mod_code,
        "app/entry.py": ep_code,
    }
    reader = make_reader(files)
    diff = diff_adding("app/bare_mod.py", mod_code)

    result = evaluate(
        diff_text=diff,
        file_reader=reader,
        pyproject_text="",
        allowlist_lines=[],
        source_files=list(files.keys()),
    )
    assert not result.ok, "undecorated unreferenced fn must fail"
    assert any("unreferenced_fn" in f for f in result.hard_failures), result.hard_failures


# ===========================================================================
# 16. FIX 4 — Class→public-method edge
# ===========================================================================


def test_fix4_public_method_on_reachable_class_passes() -> None:
    """FIX 4: A public method on a reachable class that is never explicitly called by
    first-party name → PASSES. Handles polymorphic/override dispatch pattern."""
    concrete_code = textwrap.dedent("""\
        class GCPSandbox:
            def run(self):
                return "gcp"

            def public_helper(self):
                return "helper"
    """)
    ep_code = textwrap.dedent("""\
        from app.gcp_sandbox import GCPSandbox

        def main():
            s = GCPSandbox()

        if __name__ == "__main__":
            main()
    """)
    files = {
        "app/gcp_sandbox.py": concrete_code,
        "app/entry.py": ep_code,
    }
    reader = make_reader(files)
    diff = diff_adding("app/gcp_sandbox.py", concrete_code)

    result = evaluate(
        diff_text=diff,
        file_reader=reader,
        pyproject_text="",
        allowlist_lines=[],
        source_files=list(files.keys()),
    )
    assert result.ok, "public method on reachable class must pass via class->method edge: " + str(
        result.hard_failures
    )


def test_fix4_public_method_on_unreachable_class_fails() -> None:
    """FIX 4 complement: a public method on an UNREACHABLE class → still fails."""
    dead_code = textwrap.dedent("""\
        class DeadSandbox:
            def run(self):
                return "dead"
    """)
    ep_code = textwrap.dedent("""\
        if __name__ == "__main__":
            pass
    """)
    files = {
        "app/dead_sandbox.py": dead_code,
        "app/entry.py": ep_code,
    }
    reader = make_reader(files)
    diff = diff_adding("app/dead_sandbox.py", dead_code)

    result = evaluate(
        diff_text=diff,
        file_reader=reader,
        pyproject_text="",
        allowlist_lines=[],
        source_files=list(files.keys()),
    )
    assert not result.ok, "public method on unreachable class must still fail"
    # Both class and method should be flagged (or at least method)
    failures_str = " ".join(result.hard_failures)
    assert "DeadSandbox" in failures_str or "run" in failures_str, result.hard_failures


# ===========================================================================
# 17. FIX 5 — Self-waiver comment evasion: # """ decoy line
# ===========================================================================


def test_fix5_triple_quote_in_comment_decoy_does_not_bypass_waiver_ban() -> None:
    """FIX 5: A `# \"\"\"` comment line must NOT toggle the triple-string tracker,
    so a subsequent @manual on an added line is still caught as a HARD fail.

    The bug: the old code counted triple-quotes INCLUDING those in comments,
    so `# \"\"\"` flipped in_triple_string and the @manual line was treated
    as 'inside a triple-string' and skipped — evading the ban."""
    # All lines are added (+), including the # """ decoy
    diff_lines = [
        "diff --git a/app/evade_mod.py b/app/evade_mod.py",
        "--- a/app/evade_mod.py",
        "+++ b/app/evade_mod.py",
        "@@ -0,0 +1,4 @@",
        '+# """',
        "+@manual",
        "+def evaded_fn():",
        '+    return "evaded"',
    ]
    diff = "\n".join(diff_lines) + "\n"
    code_with_decoy = textwrap.dedent("""\
        # \"\"\"
        @manual
        def evaded_fn():
            return "evaded"
    """)
    ep_code = textwrap.dedent("""\
        if __name__ == "__main__":
            pass
    """)
    files = {"app/evade_mod.py": code_with_decoy, "app/entry.py": ep_code}
    reader = make_reader(files)

    result = evaluate(
        diff_text=diff,
        file_reader=reader,
        pyproject_text="",
        allowlist_lines=[],
        source_files=list(files.keys()),
    )
    assert not result.ok, '@manual must be caught even after # """ decoy line'
    # Must mention self-waiver specifically (not just dead code)
    combined = " ".join(result.hard_failures).lower()
    assert "manual" in combined or "waiver" in combined, (
        "failure must mention @manual/self-waiver: " + str(result.hard_failures)
    )


def test_fix5_triple_quote_comment_with_dead_code_ignore_also_caught() -> None:
    """FIX 5: # dead-code: ignore on a line preceded by # \"\"\" decoy → still HARD fail."""
    diff_lines = [
        "diff --git a/app/evade2_mod.py b/app/evade2_mod.py",
        "--- a/app/evade2_mod.py",
        "+++ b/app/evade2_mod.py",
        "@@ -0,0 +1,3 @@",
        '+# """',
        "+def evaded2():  # dead-code: ignore",
        "+    return 0",
    ]
    diff = "\n".join(diff_lines) + "\n"
    code = textwrap.dedent("""\
        # \"\"\"
        def evaded2():  # dead-code: ignore
            return 0
    """)
    ep_code = textwrap.dedent("""\
        if __name__ == "__main__":
            pass
    """)
    files = {"app/evade2_mod.py": code, "app/entry.py": ep_code}
    reader = make_reader(files)

    result = evaluate(
        diff_text=diff,
        file_reader=reader,
        pyproject_text="",
        allowlist_lines=[],
        source_files=list(files.keys()),
    )
    assert not result.ok, '# dead-code: ignore must be caught even after # """ decoy'
    combined = " ".join(result.hard_failures).lower()
    assert "waiver" in combined or "ignore" in combined or "manual" in combined, (
        "failure must mention self-waiver: " + str(result.hard_failures)
    )
