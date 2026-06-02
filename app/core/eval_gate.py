"""SP-06 (slice 1) — hermetic PRD-conformance scope-root gate (NO LLM/Vertex).

The deterministic HARD ROOT of the SP-06 eval gate (PRD §6 EPIC-2 SP-06 / 2.6): a PR
whose ``git diff base...head`` touches a path OUTSIDE the locked ``TaskSpec``'s derived
``allowed_paths`` (excluding the shared net-new test set, 2.2/SP-02) FAILS
deterministically — independent of any LLM judge leaf, which is deferred.

This module is pure stdlib + pydantic and imports NEITHER ``deepeval`` NOR ``inspect_ai``.
That is load-bearing: ``deepeval``'s ``GEval``/``HallucinationMetric`` default to
``model=None`` → ``initialize_model`` → ``GPTModel`` (OpenAI), so any accidental import on
the gate path would (a) make it non-hermetic and (b) risk routing a judge to OpenAI (the
nightly cluster-b mis-wiring). The LLM leaf, when it lands in a later slice, must pass an
explicit Vertex/Gemini ``DeepEvalBaseLLM`` — never ``model=None``.

Authority = the sha-pinned LOCKED ``TaskSpec`` (``status=='locked'`` + matching
``spec_sha``); the runtime ``TaskNode.allowed_paths`` are derived from it by the SP-02
``InMemoryDecomposer`` (CI adapter). Catch-all globs are already banned upstream
(``is_catch_all_glob`` + ``assert_decomposition_fidelity``), so every allowed glob is a
concrete file or directory prefix and plain ``fnmatch``/prefix matching is sound.

Deferred to later SP-06 slices: the GEval/Faithfulness Vertex judge leaf, the 6.2
judge-class inequality, Inspect-AI patch-apply, 2.7 diff-scoped mutation, the 5.2
user-perspective oracle, and the per-node ``audit/acceptance/<node>.yaml`` frozen copy.
"""

from __future__ import annotations

import fnmatch
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath

from app.adapters.inmemory.decompose import InMemoryDecomposer
from app.core.graph_state import TaskGraph
from lib.anchors.spec_store import SpecStore, compute_spec_sha


@dataclass
class ScopeVerdict:
    """Machine-readable verdict (sorted-key JSON; mirrors golden_regression's shape)."""

    gate: str
    spec_sha: str
    base: str
    head: str
    passed: bool
    violations: list[dict] = field(default_factory=list)
    allowed_globs: list[str] = field(default_factory=list)
    net_new_tests_excluded: list[str] = field(default_factory=list)
    tests_added: bool = False

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True)


def _is_test_path(path: str) -> bool:
    """The shared net-new test selector (same semantics as test-integrity-gate's globs):
    any path under a ``tests/`` dir, or a ``test_*.py`` / ``*_test.py`` / ``conftest.py``.

    Uses ``PurePosixPath`` because ``git diff`` emits POSIX ('/') separators on every
    platform (so this is portable, not host-dependent)."""
    p = PurePosixPath(path)
    name = p.name
    return (
        "tests" in p.parts
        or (name.startswith("test_") and name.endswith(".py"))
        or name.endswith("_test.py")
        or name == "conftest.py"
    )


def _matches_any(path: str, globs: list[str]) -> bool:
    """True iff ``path`` is in scope of any allowed glob. Globs are concrete (catch-alls
    are banned upstream), so an exact match, an fnmatch, or a directory-prefix all count.
    The exact-match fast-path is kept deliberately: it is glob-metacharacter-safe (e.g. a
    file literally named ``a[b].py`` would be mis-parsed by fnmatch's ``[`` class)."""
    for g in globs:
        if path == g or fnmatch.fnmatch(path, g):
            return True
        if path.startswith(g.rstrip("/") + "/"):  # directory-prefix allow
            return True
    return False


def scope_root_verdict(
    changed: list[tuple[str, str]],
    allowed_globs: list[str],
    *,
    base: str,
    head: str,
    spec_sha: str,
    symlink_paths: tuple[str, ...] | list[str] = (),
) -> ScopeVerdict:
    """Deterministic scope verdict over a ``(status, path)`` diff.

    A changed path is a violation iff it is NOT matched by any allowed glob AND it is NOT
    a net-new (status ``A``) test in the shared selector. PASS iff zero violations.

    ``symlink_paths`` (C9 hardening): any changed path that is a symlink ALWAYS violates —
    even if in-scope or a net-new test. A symlink committed inside an allowed dir can point
    out-of-scope (e.g. ``../../pyproject.toml``); ``git diff`` reports the in-scope symlink
    path, so without this a non-conformant change would sneak through. The CLI entrypoint
    populates this from ``Path(repo_root, p).is_symlink()`` on the checked-out PR head.
    """
    symlinks = set(symlink_paths)
    net_new_tests = [p for (s, p) in changed if s == "A" and _is_test_path(p) and p not in symlinks]
    excluded = set(net_new_tests)
    violations: list[dict] = []
    for _status, path in changed:
        if path in symlinks:
            violations.append({"path": path, "reason": "symlink-escapes-scope"})
            continue
        if path in excluded:
            continue
        if _matches_any(path, allowed_globs):
            continue
        violations.append({"path": path, "reason": "out-of-scope"})

    return ScopeVerdict(
        gate="SP-06.scope-root",
        spec_sha=spec_sha,
        base=base,
        head=head,
        passed=not violations,
        violations=violations,
        allowed_globs=sorted(set(allowed_globs)),
        net_new_tests_excluded=sorted(net_new_tests),
        tests_added=len(net_new_tests) >= 1,
    )


def load_locked_plan(store_root, spec_id, expected_sha: str) -> TaskGraph:
    """Load the sha-pinned LOCKED TaskSpec and decompose it to the authoritative plan.

    Raises ValueError if the spec is not ``status=='locked'`` (can't score scope against
    an unsealed spec) or if its recomputed ``spec_sha`` != the PR-claimed pin (tamper).
    """
    spec = SpecStore(Path(store_root)).get_by_id(str(spec_id))
    if spec is None:
        raise FileNotFoundError(f"no TaskSpec {spec_id} under {store_root}")
    if spec.status != "locked":
        raise ValueError(
            f"TaskSpec {spec_id} is status={spec.status!r}, not 'locked' — "
            "cannot score scope against an unsealed spec"
        )
    actual = compute_spec_sha(spec)
    if actual != expected_sha:
        raise ValueError(
            f"spec_sha mismatch (tamper-evidence): claimed {expected_sha[:12]}…, "
            f"recomputed {actual[:12]}…"
        )
    return InMemoryDecomposer().decompose(spec)
