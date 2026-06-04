#!/usr/bin/env python3
"""SP-G1 — golden eval corpus regression runner.

Loads the sha-pinned golden corpus from evals/golden/, validates every item
against the golden-item schema, re-scores per-item pass/fail vs the pinned
baseline (structural validation + recorded baseline hash), and exits non-zero on
drift or invalid items.

LLM-judge scoring is SP-06's job — the leaf that invokes a judge model is a
documented TODO stub here.

Usage (from repo root):
    python scripts/evals/golden_regression.py [--corpus evals/golden/corpus.yaml]
                                               [--sha-manifest evals/golden/SHA256SUMS]
                                               [--version-file evals/golden/VERSION]

Exit codes:
    0 — all items valid + no sha drift
    1 — one or more items invalid OR sha drift detected
    2 — usage / file-not-found error

STDLIB ONLY — no third-party imports.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Schema constants (faithful to PRD L176)
# ---------------------------------------------------------------------------

# Allowed glob characters that do NOT constitute a catch-all pattern.
# A glob that is purely **, *, ., or / (including combinations) is catch-all.
_CATCH_ALL_RE = re.compile(r"^[\*\./]+$")

# Required top-level fields for a golden item.
_ITEM_REQUIRED_FIELDS = frozenset(
    {"goal", "expected_dag", "expected_criteria", "expected_trajectory", "version", "notes"}
)

# Required fields for a TaskNode (PRD L176).
_NODE_REQUIRED_FIELDS = frozenset(
    {"id", "phase", "summary", "depends_on", "acceptance_ref", "allowed_paths"}
)

# Valid SDLC phase values (open-ended; new phases can be added).
_VALID_PHASES = frozenset(
    {"intake", "clarify", "plan", "build", "test", "review", "ship", "operate"}
)


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass
class ValidationError:
    item_goal: str
    field: str
    message: str

    def __str__(self) -> str:
        return f"[{self.item_goal!r}] {self.field}: {self.message}"


@dataclass
class ItemResult:
    goal: str
    version: str
    valid: bool
    errors: list[ValidationError] = field(default_factory=list)
    # Baseline hash comparison
    hash_ok: bool = True
    recorded_hash: str = ""
    computed_hash: str = ""

    @property
    def passed(self) -> bool:
        return self.valid and self.hash_ok


# ---------------------------------------------------------------------------
# Pure schema-validation functions
# ---------------------------------------------------------------------------


def validate_node(node: Any, item_goal: str) -> list[ValidationError]:
    """Validate a single TaskNode dict against the PRD L176 contract."""
    errors: list[ValidationError] = []
    if not isinstance(node, dict):
        errors.append(
            ValidationError(item_goal, "node", f"must be a dict, got {type(node).__name__}")
        )
        return errors

    missing = _NODE_REQUIRED_FIELDS - set(node.keys())
    for f in sorted(missing):
        errors.append(ValidationError(item_goal, f"node.{f}", "required field missing"))

    # id: non-empty string
    if "id" in node:
        if not isinstance(node["id"], str) or not node["id"].strip():
            errors.append(ValidationError(item_goal, "node.id", "must be a non-empty string"))

    # phase: must be a recognised SDLC phase string
    if "phase" in node:
        if not isinstance(node["phase"], str) or node["phase"] not in _VALID_PHASES:
            errors.append(
                ValidationError(
                    item_goal,
                    "node.phase",
                    f"must be one of {sorted(_VALID_PHASES)}, got {node.get('phase')!r}",
                )
            )

    # summary: non-empty string
    if "summary" in node:
        if not isinstance(node["summary"], str) or not node["summary"].strip():
            errors.append(ValidationError(item_goal, "node.summary", "must be a non-empty string"))

    # depends_on: list of strings (may be empty for root nodes)
    if "depends_on" in node:
        if not isinstance(node["depends_on"], list):
            errors.append(ValidationError(item_goal, "node.depends_on", "must be a list"))
        else:
            for dep in node["depends_on"]:
                if not isinstance(dep, str):
                    errors.append(
                        ValidationError(
                            item_goal,
                            "node.depends_on",
                            f"each dep must be a string, got {type(dep).__name__}",
                        )
                    )

    # acceptance_ref: non-empty string (references TaskSpec acceptance_criteria index or label)
    if "acceptance_ref" in node:
        if not isinstance(node["acceptance_ref"], str) or not node["acceptance_ref"].strip():
            errors.append(
                ValidationError(item_goal, "node.acceptance_ref", "must be a non-empty string")
            )

    # allowed_paths: non-empty list + each glob must NOT be catch-all
    if "allowed_paths" in node:
        ap = node["allowed_paths"]
        if not isinstance(ap, list) or len(ap) == 0:
            errors.append(
                ValidationError(item_goal, "node.allowed_paths", "must be a non-empty list")
            )
        else:
            for glob in ap:
                if not isinstance(glob, str):
                    errors.append(
                        ValidationError(
                            item_goal,
                            "node.allowed_paths",
                            f"each glob must be a string, got {type(glob).__name__}",
                        )
                    )
                elif _CATCH_ALL_RE.match(glob) or glob in ("**", "*", ".", "/", "**/*"):
                    errors.append(
                        ValidationError(
                            item_goal,
                            "node.allowed_paths",
                            f"catch-all glob forbidden per SP-02/2.6: {glob!r}",
                        )
                    )

    return errors


def validate_dag(nodes: list[Any], item_goal: str) -> list[ValidationError]:
    """Validate that the DAG is acyclic and all depends_on ids resolve."""
    errors: list[ValidationError] = []
    id_set: set[str] = set()

    # Collect ids first
    for n in nodes:
        if isinstance(n, dict) and isinstance(n.get("id"), str):
            nid = n["id"]
            if nid in id_set:
                errors.append(ValidationError(item_goal, "dag", f"duplicate node id {nid!r}"))
            id_set.add(nid)

    # Check depends_on resolution and detect cycles via DFS
    adj: dict[str, list[str]] = {}
    for n in nodes:
        if isinstance(n, dict) and isinstance(n.get("id"), str):
            deps = n.get("depends_on", [])
            if isinstance(deps, list):
                for dep in deps:
                    if isinstance(dep, str) and dep not in id_set:
                        errors.append(
                            ValidationError(
                                item_goal,
                                "dag",
                                f"node {n['id']!r} depends_on unknown id {dep!r}",
                            )
                        )
                adj[n["id"]] = [d for d in deps if isinstance(d, str)]

    # Cycle detection (DFS coloring)
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {nid: WHITE for nid in id_set}

    def dfs(node_id: str) -> bool:
        """Return True if a cycle is found."""
        color[node_id] = GRAY
        for dep in adj.get(node_id, []):
            if dep not in color:
                continue
            if color[dep] == GRAY:
                return True
            if color[dep] == WHITE and dfs(dep):
                return True
        color[node_id] = BLACK
        return False

    for nid in id_set:
        if color.get(nid) == WHITE:
            if dfs(nid):
                errors.append(
                    ValidationError(item_goal, "dag", "cycle detected in depends_on graph")
                )
                break

    return errors


def validate_item(item: Any) -> list[ValidationError]:
    """Validate a single golden item dict."""
    errors: list[ValidationError] = []
    if not isinstance(item, dict):
        errors.append(
            ValidationError("<unknown>", "item", f"must be a dict, got {type(item).__name__}")
        )
        return errors

    goal = item.get("goal", "<unknown>")
    if not isinstance(goal, str):
        goal = str(goal)

    # Required top-level fields
    missing = _ITEM_REQUIRED_FIELDS - set(item.keys())
    for f in sorted(missing):
        errors.append(ValidationError(goal, f, "required field missing"))

    # goal: non-empty string
    if "goal" in item:
        if not isinstance(item["goal"], str) or not item["goal"].strip():
            errors.append(ValidationError(goal, "goal", "must be a non-empty string"))

    # expected_dag: list of node dicts
    if "expected_dag" in item:
        dag = item["expected_dag"]
        if not isinstance(dag, list):
            errors.append(ValidationError(goal, "expected_dag", "must be a list"))
        else:
            if len(dag) == 0:
                errors.append(ValidationError(goal, "expected_dag", "must have at least one node"))
            for i, node in enumerate(dag):
                node_errors = validate_node(node, goal)
                errors.extend(node_errors)
            # DAG structural checks (acyclicity, id resolution)
            dag_errors = validate_dag(dag, goal)
            errors.extend(dag_errors)

    # expected_criteria: list of non-empty strings
    if "expected_criteria" in item:
        crit = item["expected_criteria"]
        if not isinstance(crit, list) or len(crit) == 0:
            errors.append(ValidationError(goal, "expected_criteria", "must be a non-empty list"))
        else:
            for c in crit:
                if not isinstance(c, str) or not c.strip():
                    errors.append(
                        ValidationError(
                            goal, "expected_criteria", "each criterion must be a non-empty string"
                        )
                    )

    # expected_trajectory: list (may be coarse phase-list placeholder)
    if "expected_trajectory" in item:
        traj = item["expected_trajectory"]
        if not isinstance(traj, list):
            errors.append(ValidationError(goal, "expected_trajectory", "must be a list"))

    # version: non-empty string
    if "version" in item:
        if not isinstance(item["version"], str) or not item["version"].strip():
            errors.append(ValidationError(goal, "version", "must be a non-empty string"))

    # notes: string (candidate marker required per corpus spec)
    if "notes" in item:
        if not isinstance(item["notes"], str):
            errors.append(ValidationError(goal, "notes", "must be a string"))

    return errors


# ---------------------------------------------------------------------------
# Hash utilities
# ---------------------------------------------------------------------------


def sha256_file(path: str) -> str:
    """Return the hex SHA-256 of the file at `path`."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def parse_sha_manifest(manifest_path: str) -> dict[str, str]:
    """Parse a BSD/GNU sha256sum manifest: `<hex>  <filename>` lines."""
    manifest: dict[str, str] = {}
    with open(manifest_path) as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split(None, 1)
            if len(parts) == 2:
                manifest[parts[1].strip()] = parts[0].strip()
    return manifest


# ---------------------------------------------------------------------------
# Corpus loading
# ---------------------------------------------------------------------------


def load_corpus_yaml(path: str) -> list[dict]:
    """Load the YAML corpus file.  Uses stdlib json as fallback for .jsonl.

    STDLIB ONLY — we parse YAML by hand for the simple list-of-dicts format
    this corpus uses, OR fall back to the json module for .jsonl.
    """
    if path.endswith(".jsonl"):
        items = []
        with open(path) as fh:
            for line in fh:
                line = line.strip()
                if line:
                    items.append(json.loads(line))
        return items

    # Parse minimal YAML subset: list of dicts with scalar + list values.
    # The corpus format is intentionally simple so stdlib-only parsing is feasible.
    try:
        import yaml  # type: ignore[import]

        with open(path) as fh:
            data = yaml.safe_load(fh)
        if isinstance(data, list):
            return data
        raise ValueError(f"corpus must be a YAML list, got {type(data).__name__}")
    except ImportError:
        pass

    # Fallback: try json (for .yaml files that are valid JSON)
    try:
        with open(path) as fh:
            data = json.load(fh)
        if isinstance(data, list):
            return data
    except json.JSONDecodeError:
        pass

    raise RuntimeError(
        f"Cannot parse {path}: install PyYAML (uv run --with pyyaml) or use .jsonl format"
    )


# ---------------------------------------------------------------------------
# LLM-judge stub (SP-06's job)
# ---------------------------------------------------------------------------


def _judge_score_stub(item: dict) -> dict:
    """TODO (SP-06): invoke the LLM-judge DAGMetric to score this golden item.

    For now this is a structural-validation-only baseline; the LLM judge leaf is
    SP-06's responsibility. When SP-06 is wired, replace this stub with a call to
    the DeepEval DAGMetric and return a verdict dict with at minimum:
        {"passed": bool, "score": float, "details": str}

    The runner records the structural-validity result and the hash-match as the
    baseline. SP-06 adds the semantic judge score on top.
    """
    return {"passed": True, "score": 1.0, "details": "stub: structural-only (SP-06 TODO)"}


# ---------------------------------------------------------------------------
# Regression run
# ---------------------------------------------------------------------------


def run_regression(
    corpus_path: str,
    sha_manifest_path: str,
    version_file_path: str,
) -> tuple[list[ItemResult], bool]:
    """Run the full regression suite.

    Returns:
        (results, overall_ok) where overall_ok is True iff every item passed.
    """
    errors_before: list[str] = []

    # 1. Version file must exist
    if not os.path.isfile(version_file_path):
        errors_before.append(f"VERSION file not found: {version_file_path}")

    # 2. SHA manifest must exist and corpus must not have drifted
    sha_drift: dict[str, tuple[str, str]] = {}  # filename -> (recorded, computed)
    if not os.path.isfile(sha_manifest_path):
        errors_before.append(f"SHA256SUMS not found: {sha_manifest_path}")
    else:
        manifest = parse_sha_manifest(sha_manifest_path)
        # Check each pinned file
        corpus_dir = os.path.dirname(sha_manifest_path)
        for filename, recorded_hash in manifest.items():
            full_path = os.path.join(corpus_dir, filename)
            if not os.path.isfile(full_path):
                sha_drift[filename] = (recorded_hash, "<missing>")
            else:
                actual = sha256_file(full_path)
                if actual != recorded_hash:
                    sha_drift[filename] = (recorded_hash, actual)

    if errors_before:
        for msg in errors_before:
            print(f"::error::golden-regression: {msg}", file=sys.stderr)
        return [], False

    # 3. Load corpus
    items = load_corpus_yaml(corpus_path)

    # 4. Validate each item
    results: list[ItemResult] = []
    for raw_item in items:
        goal = raw_item.get("goal", "<unknown>") if isinstance(raw_item, dict) else "<unknown>"
        version = raw_item.get("version", "") if isinstance(raw_item, dict) else ""
        validation_errors = validate_item(raw_item)
        valid = len(validation_errors) == 0

        # Compute a content hash of this item for drift detection
        item_bytes = json.dumps(raw_item, sort_keys=True, ensure_ascii=False).encode()
        computed_hash = sha256_bytes(item_bytes)

        r = ItemResult(
            goal=goal,
            version=version,
            valid=valid,
            errors=validation_errors,
            computed_hash=computed_hash,
        )

        # Structural judge (stub — SP-06 TODO)
        _judge_score_stub(raw_item)  # called for side-effects / future wiring

        results.append(r)

    # 5. Report sha drift
    sha_ok = len(sha_drift) == 0
    if not sha_ok:
        for filename, (recorded, computed) in sha_drift.items():
            print(
                f"::error::golden-regression: SHA drift on {filename}: "
                f"recorded={recorded} computed={computed}",
                file=sys.stderr,
            )

    overall_ok = sha_ok and all(r.passed for r in results)
    return results, overall_ok


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------


def _print_results(results: list[ItemResult], version_file_path: str) -> None:
    version = "<unknown>"
    if os.path.isfile(version_file_path):
        with open(version_file_path) as fh:
            version = fh.read().strip()

    print(f"== golden-regression: corpus version {version} ==")
    for r in results:
        status = "PASS" if r.passed else "FAIL"
        print(f"  [{status}] {r.goal!r} (version={r.version})")
        if r.errors:
            for err in r.errors:
                print(f"         ERR: {err}")
        if not r.hash_ok:
            print(f"         HASH DRIFT: recorded={r.recorded_hash} computed={r.computed_hash}")
    passed = sum(1 for r in results if r.passed)
    total = len(results)
    print(f"== {passed}/{total} items passed ==")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(
        description="SP-G1 golden eval corpus regression runner",
    )
    ap.add_argument(
        "--corpus",
        default="evals/golden/corpus.yaml",
        help="Path to the golden corpus (YAML list or .jsonl)",
    )
    ap.add_argument(
        "--sha-manifest",
        default="evals/golden/SHA256SUMS",
        dest="sha_manifest",
        help="Path to the SHA256SUMS manifest pinning corpus files",
    )
    ap.add_argument(
        "--version-file",
        default="evals/golden/VERSION",
        dest="version_file",
        help="Path to the VERSION file",
    )
    args = ap.parse_args(argv)

    try:
        results, ok = run_regression(
            corpus_path=args.corpus,
            sha_manifest_path=args.sha_manifest,
            version_file_path=args.version_file,
        )
    except Exception as exc:
        print(f"::error::golden-regression: fatal: {exc}", file=sys.stderr)
        return 2

    _print_results(results, args.version_file)

    total = len(results)
    passed = sum(1 for r in results if r.passed)
    print(
        f"== golden-regression: {len([r for r in results if not r.passed])} item(s) failed | "
        f"{passed}/{total} passed | "
        f"=> {'FAIL' if not ok else 'PASS'} =="
    )
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
