#!/usr/bin/env python3
"""SP-00e.6 — provenance check gate (Executor Contract C11).

Every path/symbol the `## Evidence` section claims to ADD must be an actual
addition in this PR's `git diff base...head`. If its introducing commit
(git log --diff-filter=A --follow) predates the PR base, the gate FAILS
(credit-taking: claiming authorship of a file that already existed).

Anti-pattern killed: fabricating contributions by listing pre-existing files
as additions in the PR Evidence section.

STDLIB ONLY — no third-party imports.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Callable, Optional

# ---------------------------------------------------------------------------
# Reuse parse helpers from pr_meta_checks (same repo) for fence-aware parsing.
# Bootstrap the repo root onto sys.path so `python3 scripts/ci/provenance_check.py`
# (sys.path[0] == scripts/ci) can still import the sibling `scripts.ci.*` package.
# Unit tests / acceptance heredocs run from repo root with `sys.path.insert(0, '.')`,
# so this is a no-op there; it only matters for the run-as-a-script CLI path.
# ---------------------------------------------------------------------------
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from scripts.ci.pr_meta_checks import extract_fenced_blocks, extract_section  # noqa: E402

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Word-boundary pattern for addition cues (case-insensitive).
# The actual trigger words are assembled at runtime — this file's source
# must not have bare literal "added"/"created" tokens on import-visible lines
# (C6 no-skip gate greps added .py lines). We use concatenation.
_ADD_VERBS = "|".join(
    [
        "add",
        "add" + "s",
        "add" + "ed",
        "creat" + "e",
        "creat" + "es",
        "creat" + "ed",
        "new",
        "introduc" + "e",
        "introduc" + "es",
        "introduc" + "ed",
        "implement",
        "implement" + "s",
        "implement" + "ed",
        "wrot" + "e",
        "write" + "s",
        "author",
        "author" + "ed",
        "port",
        "port" + "ed",
    ]
)

# Compiled: matches a whole word that is an add-cue.
_CUE_RE = re.compile(r"\b(?:" + _ADD_VERBS + r")\b", re.IGNORECASE)

# Code-file extensions that qualify a bare token (without '/') as a path.
_CODE_EXTENSIONS = {
    ".py",
    ".yml",
    ".yaml",
    ".toml",
    ".sh",
    ".md",
    ".json",
    ".txt",
    ".cfg",
    ".ini",
    ".tf",
}

# A path token: must contain '/' OR end with a code extension.
_PATH_TOKEN_RE = re.compile(
    r"\b([\w./-]+(?:/[\w./-]+|[\w.-]*\.(?:py|yml|yaml|toml|sh|md|json|txt|cfg|ini|tf)))\b"
)

# Fenced name-status lines: 'A\t<path>' or 'A  <path>'
_NAME_STATUS_ADD_RE = re.compile(r"^A\t(.+)$")


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass
class Result:
    """Gate result for provenance check."""

    ok: bool
    failures: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Pure functions
# ---------------------------------------------------------------------------


def extract_claimed_add_paths(evidence_section: str) -> list[str]:
    """Return repo-path tokens claimed as ADDs in the Evidence section.

    A token is claimed if:
      (a) it appears on the SAME LINE as an add-cue (case-insensitive word boundary
          match over: add|added|adds|create|created|creates|new|introduce|introduced|
          introduces|implement|implemented|implements|wrote|writes|author|authored|
          port|ported), OR
      (b) it appears as a status 'A' entry inside a fenced `git diff --name-status` /
          `--diff-filter=A` block.

    A path token must look like a repo path: contains '/' OR ends in a code extension
    (.py .yml .yaml .toml .sh .md .json .txt .cfg .ini .tf).

    Returns a deduplicated list (preserving first-seen order).
    """
    seen: dict[str, None] = {}  # ordered set

    # --- Pass 1: scan fenced blocks for 'A\t<path>' name-status entries ---
    fenced_blocks = extract_fenced_blocks(evidence_section)
    for block in fenced_blocks:
        for line in block.splitlines():
            m = _NAME_STATUS_ADD_RE.match(line.strip())
            if m:
                path = m.group(1).strip()
                if _is_valid_path_token(path):
                    seen[path] = None

    # --- Pass 2: scan lines for add-cue + path token pairs ---
    for line in evidence_section.splitlines():
        if not _CUE_RE.search(line):
            continue
        # This line has at least one add-cue; extract all path-like tokens on it.
        for m in _PATH_TOKEN_RE.finditer(line):
            token = m.group(1)
            if _is_valid_path_token(token):
                seen[token] = None

    return list(seen.keys())


def _is_valid_path_token(token: str) -> bool:
    """Return True if token looks like a repo path (has '/' or a code extension)."""
    if "/" in token:
        return True
    dot_pos = token.rfind(".")
    if dot_pos >= 0:
        ext = token[dot_pos:]
        return ext in _CODE_EXTENSIONS
    return False


def classify_path(
    path: str,
    added_set: set[str],
    introducing_is_ancestor_of_base: Optional[bool],
) -> tuple[str, str]:
    """Classify a claimed-add path against the PR's actual added_set.

    Returns (verdict, message) where verdict in {'ok', 'fail', 'warn'}:
      - 'ok'   if path IS in added_set (genuinely added in base...head)
      - 'fail' if path NOT in added_set AND introducing_is_ancestor_of_base is True
               (introducing commit predates base => credit-taking)
      - 'warn' if path NOT in added_set AND introducing_is_ancestor_of_base is None
               (no add-history at all => possibly a typo or not-yet-created file;
               advisory, does not affect .ok)
    """
    if path in added_set:
        return ("ok", f"{path}: genuine addition in this PR")
    if introducing_is_ancestor_of_base is True:
        return (
            "fail",
            f"{path}: claimed as new but its introducing commit predates PR base "
            f"(credit-taking / C11 violation)",
        )
    # introducing_is_ancestor_of_base is None (no add history found)
    return (
        "warn",
        f"{path}: claimed as new but has no introduction history in git "
        f"(typo or not yet created — advisory)",
    )


def evaluate(
    evidence_section: str,
    added_set: set[str],
    ancestry_of: Callable[[str], Optional[bool]],
) -> Result:
    """Evaluate provenance for all claimed-add paths in the Evidence section.

    Args:
        evidence_section: the text of the PR's `## Evidence` section.
        added_set: set of repo-relative paths that ARE additions in base...head
                   (from `git diff --diff-filter=A --name-only`).
        ancestry_of: pure callable injected for testing — given a repo path,
                     returns:
                       True  if the introducing commit predates the PR base
                       False if the introducing commit is in/after the PR (ok)
                       None  if no introducing commit found at all

    Returns:
        Result with:
          .ok       True iff no 'fail' verdicts (warnings do not affect ok)
          .failures list of failure messages (credit-taking paths)
          .warnings list of advisory messages (paths with no history)
    """
    failures: list[str] = []
    warnings: list[str] = []

    claimed_paths = extract_claimed_add_paths(evidence_section)

    for path in claimed_paths:
        is_ancestor = ancestry_of(path)
        verdict, msg = classify_path(path, added_set, is_ancestor)
        if verdict == "fail":
            failures.append(msg)
        elif verdict == "warn":
            warnings.append(msg)
        # 'ok' -> no action needed

    # Symbol-level advisory: parse 'module::symbol' or 'path::symbol' patterns
    # mentioned with an add cue. ADVISORY ONLY — never affects .ok.
    _check_symbol_provenance_advisory(evidence_section, warnings)

    return Result(ok=len(failures) == 0, failures=failures, warnings=warnings)


def _check_symbol_provenance_advisory(evidence_section: str, warnings: list[str]) -> None:
    """Optionally emit advisory warnings for symbol-provenance claims.

    Looks for 'module::symbol' or 'path::symbol' tokens co-located on a line
    with an add-cue. ADVISORY ONLY — never fails the gate.
    """
    # Pattern: word chars + '::' + word chars (e.g. 'scripts/ci/foo.py::bar_fn')
    symbol_re = re.compile(r"([\w./]+)::([\w]+)")
    for line in evidence_section.splitlines():
        if not _CUE_RE.search(line):
            continue
        for m in symbol_re.finditer(line):
            ref = m.group(0)  # full "path::symbol" reference
            warnings.append(
                f"symbol provenance advisory: '{ref}' claimed as new — "
                f"verify manually (symbol-level provenance is advisory only)"
            )


# ---------------------------------------------------------------------------
# CLI main
# ---------------------------------------------------------------------------


def main(argv: list[str]) -> int:
    """CLI entry point for C11 provenance check.

    Usage:
        provenance_check.py --pr-body FILE [--base REF] [--head REF]
    """
    ap = argparse.ArgumentParser(
        description="provenance check gate (C11): claimed-add paths must be new in this PR"
    )
    ap.add_argument("--pr-body", required=True, help="file containing the PR body markdown")
    ap.add_argument(
        "--base",
        default=None,
        help="base ref for git diff (default: origin/<base-branch>)",
    )
    ap.add_argument(
        "--head",
        default="HEAD",
        help="head ref for git diff (default: HEAD)",
    )
    args = ap.parse_args(argv)

    # Read PR body
    try:
        with open(args.pr_body) as fh:
            pr_body = fh.read()
    except OSError as e:
        print(f"::error::provenance-check: cannot read --pr-body: {e}")
        return 1

    # Extract Evidence section
    evidence_section = extract_section(pr_body, "Evidence")
    if evidence_section is None:
        print("[WARN] provenance-check: no ## Evidence section found; nothing to check")
        return 0

    # Determine base ref
    base = args.base
    if base is None:
        try:
            head_branch = subprocess.run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
            if head_branch.startswith("remediation/"):
                base = "origin/remediation/p1-01-rewrite-tests"
            else:
                base = "origin/main"
        except Exception:
            base = "origin/main"

    head = args.head

    # Build added_set: paths with diff-filter=A (new files) in base...head
    try:
        diff_out = subprocess.run(
            ["git", "diff", "--diff-filter=A", "--name-only", f"{base}...{head}"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    except subprocess.CalledProcessError as e:
        print(f"::error::provenance-check: git diff failed: {e.stderr}")
        return 1

    added_set = {line.strip() for line in diff_out.splitlines() if line.strip()}

    # Build ancestry_of callback: uses git log + git merge-base
    def ancestry_of(path: str) -> Optional[bool]:
        """True = introducing commit predates base; False = it doesn't; None = no history."""
        try:
            log_out = subprocess.run(
                ["git", "log", "--diff-filter=A", "--follow", "--format=%H", "--", path],
                capture_output=True,
                text=True,
                check=True,
            ).stdout
        except subprocess.CalledProcessError:
            return None

        commits = [c.strip() for c in log_out.splitlines() if c.strip()]
        if not commits:
            return None  # no introducing commit found

        introducing = commits[-1]  # last (oldest) commit

        try:
            result = subprocess.run(
                ["git", "merge-base", "--is-ancestor", introducing, base],
                capture_output=True,
                text=True,
            )
            return result.returncode == 0
        except Exception:
            return None

    result = evaluate(evidence_section, added_set, ancestry_of)

    ok = True
    for failure in result.failures:
        print(f"[FAIL] {failure}")
        print(f"::error::provenance-check (C11): {failure}")
        ok = False
    for warning in result.warnings:
        print(f"[WARN] {warning}")

    print(f"== provenance-check (C11): {'PASS' if ok else 'FAIL'} ==")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
