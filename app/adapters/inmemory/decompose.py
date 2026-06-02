"""InMemoryDecomposer — deterministic, hermetic, rule-based goal→DAG decomposer (SP-02).

CI runs against THIS (CLAUDE.md: "CI runs against adapters/inmemory/"). It is a pure
function of the locked `TaskSpec`: NO LLM, NO Vertex, NO clock/RNG — same spec in =>
byte-identical TaskGraph out. The Vertex/LLM concretion is the deferred
`app/adapters/gcp/decompose.py` stub.

DECOMPOSITION RULE (v1, deterministic):

  - ONE TaskNode per acceptance criterion (index i => node id ``n{i}``,
    ``acceptance_ref = str(i)``) — this trivially satisfies the SP-02 fidelity
    bijection (union of refs == criterion indices; no orphan, no invented node).
  - PHASE per node is heuristic over the criterion text (research/draft/refine/
    verify/ship keyword buckets), defaulting to ``draft`` — TaskSpec carries no
    phase, so this is the per-node phase the bridge later surfaces.
  - ALLOWED_PATHS per node is acceptance-DERIVED and NON-CATCH-ALL: real path-like
    tokens are extracted from the criterion text (e.g. ``app/core/widget.py``);
    when the text names none, a deterministic phase-shaped path is synthesized
    UNDER the spec's in-scope roots (never ``**``/``*``/``.``/``/``).
  - DEPENDS_ON is a strict chain (node i depends_on node i-1) — the simplest acyclic
    topology that still yields a multi-node, multi-phase DAG for a multi-step goal.
    Richer dependency inference is the Vertex concretion's job (deferred).
"""

from __future__ import annotations

import re
from typing import Literal

from app.core.decompose import SDLC_PHASES, AbstractDecomposer, is_catch_all_glob
from app.core.graph_state import TaskGraph, TaskNode
from lib.anchors.task_spec import TaskSpec

# Static mirror of `TaskNode["phase"]`'s Literal — used to type the classifier so a
# node's `phase` is the Literal, not a bare `str`. If this drifts from TaskNode's
# Literal, mypy fails at the `"phase": phase` assignment below (self-checking); the
# runtime `SDLC_PHASES` membership guard in app/core/decompose.py catches it too.
Phase = Literal["research", "draft", "refine", "verify", "ship"]

# Phase keyword buckets — first matching bucket wins (checked in this order).
_PHASE_KEYWORDS: list[tuple[Phase, tuple[str, ...]]] = [
    (
        "research",
        ("research", "investigate", "document the design", "design doc", "explore", "analyze"),
    ),
    ("ship", ("ship", "deploy", "release", "publish", "roll out", "rollout", "merge to main")),
    (
        "verify",
        ("verify", "test", "validate", "ensure", "check", "coverage", "green in ci", "passes"),
    ),
    ("refine", ("refine", "refactor", "optimize", "harden", "polish", "clean up", "cleanup")),
    ("draft", ("implement", "add", "build", "create", "write", "wire", "scaffold")),
]

# A path-like token: at least one "/" or a bare filename with a code-ish extension.
_PATH_TOKEN = re.compile(
    r"""(?<![\w/.])                         # not preceded by a path/word char
        (?:[\w.-]+/)+[\w.-]+\.[A-Za-z0-9]+  # dir/.../file.ext  (>=1 slash)
        |
        (?<![\w/.])[\w-]+\.(?:py|ya?ml|toml|md|tf|sh|json|cfg|ini|txt)\b  # bare file.ext
        |
        (?<![\w/.])(?:[\w.-]+/)+\*\*?       # dir/** or dir/*
    """,
    re.VERBOSE,
)

# Code extensions whose presence in a token makes it a concrete (non-catch-all) path.
_CODE_EXTS = ("py", "yaml", "yml", "toml", "md", "tf", "sh", "json", "cfg", "ini", "txt")

# Phase → default file-stem when the criterion names no path (synthesized under an
# in-scope root). Deterministic; never a bare wildcard.
_PHASE_DEFAULT_STEM: dict[str, str] = {
    "research": "docs/research/{slug}.md",
    "draft": "app/core/{slug}.py",
    "refine": "app/core/{slug}.py",
    "verify": "tests/unit/test_{slug}.py",
    "ship": "deploy/{slug}.yaml",
}


class InMemoryDecomposer(AbstractDecomposer):
    """Deterministic rule-based decomposer for CI (no LLM)."""

    def _decompose(self, spec: TaskSpec) -> TaskGraph:
        roots = _scope_roots(spec)
        nodes: list[TaskNode] = []
        edges: list[tuple[str, str]] = []

        prev_id: str | None = None
        for i, criterion in enumerate(spec.acceptance_criteria):
            node_id = f"n{i}"
            phase = _classify_phase(criterion)
            paths = _derive_allowed_paths(criterion, phase, roots, node_id)
            depends_on = [prev_id] if prev_id is not None else []
            node: TaskNode = {
                "id": node_id,
                "phase": phase,
                "summary": criterion.strip(),
                "depends_on": depends_on,
                "acceptance_ref": str(i),
                "allowed_paths": paths,
            }
            nodes.append(node)
            if prev_id is not None:
                edges.append((prev_id, node_id))
            prev_id = node_id

        return {"nodes": nodes, "edges": edges}


# ── helpers (pure, deterministic) ───────────────────────────────────────────
def _classify_phase(text: str) -> Phase:
    low = text.lower()
    for phase, kws in _PHASE_KEYWORDS:
        if any(kw in low for kw in kws):
            return phase
    return "draft"


def _scope_roots(spec: TaskSpec) -> list[str]:
    """In-scope path roots, normalized; fall back to ['app/'] so a path always exists
    under a real subtree (never the repo root)."""
    roots: list[str] = []
    for item in spec.scope.in_scope:
        token = item.strip().strip("/")
        # Keep only path-ish in-scope items (contain a slash or a code-ish dir name).
        if token and not is_catch_all_glob(item):
            roots.append(token)
    return roots or ["app"]


def _slug(text: str, node_id: str) -> str:
    words = re.findall(r"[a-z0-9]+", text.lower())
    # Drop generic verbs/stopwords so the slug is about the noun, not the action.
    stop = {
        "research",
        "implement",
        "add",
        "build",
        "create",
        "write",
        "the",
        "and",
        "a",
        "an",
        "to",
        "of",
        "with",
        "for",
        "verify",
        "test",
        "tests",
        "ship",
        "deploy",
        "covering",
        "class",
        "is",
        "green",
        "ci",
        "service",
        "pipeline",
        "document",
        "design",
        "surface",
        "api",
    }
    keep = [w for w in words if w not in stop][:3]
    slug = "-".join(keep) if keep else node_id
    return slug


def _derive_allowed_paths(text: str, phase: str, roots: list[str], node_id: str) -> list[str]:
    """Acceptance-DERIVED, NON-CATCH-ALL allowed_paths for one node.

    Prefer real path tokens named in the criterion; else synthesize a deterministic
    phase-shaped path under an in-scope root. Catch-all tokens are filtered out, and
    a non-empty result is guaranteed.
    """
    found = _extract_path_tokens(text)
    paths = [p for p in found if not is_catch_all_glob(p)]
    if paths:
        # Stable order, de-duplicated.
        seen: dict[str, None] = {}
        for p in paths:
            seen.setdefault(p, None)
        return list(seen.keys())

    # Synthesize: phase-shaped path. Anchor under an in-scope root unless the template
    # already names a concrete top-level dir (tests/, docs/, deploy/).
    slug = _slug(text, node_id)
    template = _PHASE_DEFAULT_STEM[phase]
    synthesized = template.format(slug=slug)
    if synthesized.split("/", 1)[0] not in {"tests", "docs", "deploy"}:
        root = roots[0]
        synthesized = f"{root.rstrip('/')}/{synthesized}"
    return [synthesized]


def _extract_path_tokens(text: str) -> list[str]:
    out: list[str] = []
    for m in _PATH_TOKEN.finditer(text):
        tok = m.group(0).strip().rstrip(".,;:)")
        if tok and ("/" in tok or tok.rsplit(".", 1)[-1].lower() in _CODE_EXTS):
            out.append(tok)
    return out


# Re-exported for callers that want the phase tuple from the adapter namespace.
__all__ = ["InMemoryDecomposer", "SDLC_PHASES"]
