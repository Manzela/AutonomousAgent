#!/usr/bin/env python3
"""SP-06 scope-root gate entrypoint — fails a PR whose diff leaves the locked-spec scope.

Loads the sha-pinned LOCKED TaskSpec, derives the authoritative allowed_paths (SP-02
decompose), computes ``git diff --name-status base...head``, and exits 1 if any changed
path is outside scope (excluding the shared net-new test set). Emits a machine-readable
scope-verdict.json. Hermetic — no deepeval/inspect/Vertex import (see app/core/eval_gate).
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from app.core.eval_gate import load_locked_plan, scope_root_verdict


def _git_name_status(base: str, head: str, repo_root: str) -> list[tuple[str, str]]:
    out = subprocess.run(
        ["git", "diff", "--name-status", f"{base}...{head}"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    changed: list[tuple[str, str]] = []
    for line in out.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        status = parts[0][:1]  # A/M/D/R… (rename shows the new path last)
        path = parts[-1]
        changed.append((status, path))
    return changed


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="SP-06 scope-root gate")
    ap.add_argument("--base", required=True)
    ap.add_argument("--head", required=True)
    ap.add_argument("--spec-id", required=True, help="locked TaskSpec UUID")
    ap.add_argument("--spec-sha", required=True, help="PR-claimed sha-pin of the locked spec")
    ap.add_argument("--store-root", required=True, help="SpecStore root holding <spec_id>.json")
    ap.add_argument("--out", default="scope-verdict.json")
    ap.add_argument("--repo-root", default=".")
    args = ap.parse_args(argv)

    plan = load_locked_plan(args.store_root, args.spec_id, args.spec_sha)
    allowed = [g for node in plan["nodes"] for g in node["allowed_paths"]]
    changed = _git_name_status(args.base, args.head, args.repo_root)
    # C9 hardening: flag symlinks on the checked-out head — a symlink inside an allowed
    # dir can point out-of-scope while git diff reports the in-scope symlink path.
    repo = Path(args.repo_root)
    symlinks = [p for (_s, p) in changed if (repo / p).is_symlink()]
    verdict = scope_root_verdict(
        changed,
        allowed,
        base=args.base,
        head=args.head,
        spec_sha=args.spec_sha,
        symlink_paths=symlinks,
    )
    Path(args.out).write_text(verdict.to_json())

    for v in verdict.violations:
        print(f"::error::[FAIL] SP-06 scope-root: out-of-scope path {v['path']}")
    if verdict.passed:
        print(
            f"[PASS] SP-06 scope-root: all {len(changed)} changed path(s) in scope "
            f"(tests_added={verdict.tests_added}); verdict -> {args.out}"
        )
        return 0
    print(f"== SP-06 scope-root: FAIL ({len(verdict.violations)} out-of-scope path(s)) ==")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
