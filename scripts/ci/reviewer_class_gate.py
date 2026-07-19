#!/usr/bin/env python3
"""C-04 / H-05 — C9 reviewer-model-class gate: enforce model-inequality for P0/P1 PRs.

RULE (operator-resolved 2026-06-01):
    A DIFFERENT MODEL constitutes a different class.  The "class" IS the specific
    model.  Two commits both authored by claude-opus-4-8 — same model — FORBIDDEN.
    A commit authored by claude-sonnet-4-6 reviewed by claude-opus-4-8 — ALLOWED.

IMPLEMENTER DETECTION:
    Scans the `Co-Authored-By:` trailers in each commit message on the PR's commit
    list.  The `git log --format=%B` text is the input; we extract lines of the form:
        Co-Authored-By: <Canonical Model Name> <noreply@anthropic.com>
    and normalise the display name to a model-key via MODEL_NORM.

REVIEWER DETECTION:
    Reads the `Reviewer model:` line from the PR body (PR template field), normalises
    it via MODEL_NORM.

CLOSED-DEFAULT (hard fail, never silent pass):
    - `Reviewer model:` line is missing or unparseable → FAIL
    - No Co-Authored-By trailer found in any commit → FAIL

MIXED-AUTHORSHIP RULE:
    A PR may carry commits from BOTH a Sonnet and an Opus author (e.g. an Opus
    orchestrator added fixup commits to a Sonnet-authored PR).  If the declared
    reviewer model ALSO appears as a code co-author in ANY commit, that is a real
    independence concern — the reviewer must not have co-authored the code under
    review.  FAIL with an explicit message directing the author to use a genuinely
    independent reviewer.

SCOPE:
    P0/P1 PRs: hard gate — exit non-zero on any failure.
    Other PRs: advisory only — print warnings but exit 0.
    A PR is P0/P1 if:
        - It has a GitHub label matching /^P[01]$/i  (passed via --labels), OR
        - Its body contains a `Priority:` field (case-insensitive) whose value starts
          with P0 or P1 (markdown-tolerant: leading `**`, `-`, `>`, whitespace and
          trailing text after the token are all accepted), OR
        - The PR touches a SENSITIVE PATH (security/governance-critical prefix) — the
          fail-closed safety net so the gate does not depend solely on author-controlled
          body text.  Pass changed files via --changed-files.
    If no signal is present, the gate runs in advisory mode.

SENSITIVE PATHS (path-based fail-closed trigger):
    Any PR whose changed files include a path under one of these prefixes triggers the
    hard gate regardless of labels or PR body:
        audit/acceptance/
        .github/
        scripts/ci/
        terraform/
        deploy/
        lib/scrubber
        lib/a2a/
        app/core/trust
        CLAUDE.md
        config/dead_code_entrypoints.txt
    This ensures governance-critical changes are always cross-model reviewed even when
    the author omits labels or the Priority: field.
    Note: the broader "hard-gate ALL PRs by default" policy is an operator activation
    decision left for a future iteration.

SPOOF-RESISTANCE LIMITATION (documented, not solved):
    This gate parses SELF-DECLARED metadata — the `Reviewer model:` line in the PR
    body and `Co-Authored-By:` trailers in commit messages.  It cannot
    cryptographically PROVE that the review was performed by the declared model; a
    human or a different model could write any string they like.  The "live
    cross-vendor invocation" hardening (verifiable reviewer credentials) is explicitly
    deferred, pending SP-06 / SP-27 and a dedicated reviewer-credential mechanism.
    This gate raises the cost of accidental same-model review to a deliberate lie, which
    is sufficient for the gate-0 threat model.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from typing import NamedTuple

# Default location of the CODEOWNERS-controlled reviewer allowlist (corroboration source).
# Relative to repo root; the workflow passes --reviewer-allowlist explicitly, but this
# constant lets the gate self-locate the committed file when run from repo_root.
DEFAULT_REVIEWER_ALLOWLIST_PATH = "config/c9-reviewer-allowlist.txt"

# ---------------------------------------------------------------------------
# Model normalisation map
# ---------------------------------------------------------------------------
# Keys: canonical model-keys used for EQUALITY comparison (the "class").
# Values: patterns that normalise to that key.
# Order matters for the longest-match case: put more-specific entries first.
#
# Convention adopted in Co-Authored-By trailers:
#   "Claude Opus 4.8"        → claude-opus-4-8
#   "Claude Opus 4.7"        → claude-opus-4-7
#   "Claude Sonnet 4.6"      → claude-sonnet-4-6
#   "Claude Sonnet 4.5"      → claude-sonnet-4-5
#   "Claude Haiku 3.5"       → claude-haiku-3-5
#   "Gemini 3.1 Pro"         → gemini-3-1-pro
#   "Gemini 2.5 Pro"         → gemini-2-5-pro
#   etc.

MODEL_NORM: dict[str, list[str]] = {
    # Anthropic Claude — 5 family (added when the org's default model line moved
    # to Sonnet 5 / Haiku 4.5 / Fable 5, alongside the still-current Opus 4.8).
    "claude-sonnet-5": [
        "claude-sonnet-5",
        "claude sonnet 5",
        "sonnet 5",
        "sonnet-5",
    ],
    "claude-haiku-4-5": [
        "claude-haiku-4-5",
        "claude haiku 4.5",
        "claude haiku 4-5",
        "haiku 4.5",
        "haiku-4-5",
    ],
    "claude-fable-5": [
        "claude-fable-5",
        "claude fable 5",
        "fable 5",
        "fable-5",
    ],
    # Anthropic Claude — Opus
    "claude-opus-4-8": [
        "claude-opus-4-8",
        "claude opus 4.8",
        "claude opus 4-8",
        "opus 4.8",
        "opus-4-8",
    ],
    "claude-opus-4-7": [
        "claude-opus-4-7",
        "claude opus 4.7",
        "claude opus 4-7",
        "opus 4.7",
        "opus-4-7",
    ],
    "claude-opus-4-5": [
        "claude-opus-4-5",
        "claude opus 4.5",
        "opus 4.5",
    ],
    "claude-opus-3": [
        "claude-opus-3",
        "claude opus 3",
        "claude-3-opus",
        "claude 3 opus",
    ],
    # Anthropic Claude — Sonnet
    "claude-sonnet-4-6": [
        "claude-sonnet-4-6",
        "claude sonnet 4.6",
        "claude sonnet 4-6",
        "sonnet 4.6",
        "sonnet-4-6",
    ],
    "claude-sonnet-4-5": [
        "claude-sonnet-4-5",
        "claude sonnet 4.5",
        "sonnet 4.5",
    ],
    "claude-sonnet-3-7": [
        "claude-sonnet-3-7",
        "claude sonnet 3.7",
        "claude-3-7-sonnet",
        "claude 3.7 sonnet",
    ],
    "claude-sonnet-3-5": [
        "claude-sonnet-3-5",
        "claude sonnet 3.5",
        "claude-3-5-sonnet",
        "claude 3.5 sonnet",
    ],
    # Anthropic Claude — Haiku
    "claude-haiku-3-5": [
        "claude-haiku-3-5",
        "claude haiku 3.5",
        "claude-3-5-haiku",
        "claude 3.5 haiku",
    ],
    "claude-haiku-3": [
        "claude-haiku-3",
        "claude haiku 3",
        "claude-3-haiku",
        "claude 3 haiku",
    ],
    # Google — Gemini
    "gemini-3-1-pro": [
        "gemini-3.1-pro",
        "gemini 3.1 pro",
        "gemini-3-1-pro",
        "gemini 3.1 pro preview",
        "gemini-3.1-pro-preview",
    ],
    "gemini-2-5-pro": [
        "gemini-2.5-pro",
        "gemini 2.5 pro",
        "gemini-2-5-pro",
        "gemini 2.5 pro preview",
        "gemini-2.5-pro-preview",
    ],
    "gemini-2-0-flash": [
        "gemini-2.0-flash",
        "gemini 2.0 flash",
        "gemini-2-0-flash",
    ],
    "gemini-1-5-pro": [
        "gemini-1.5-pro",
        "gemini 1.5 pro",
        "gemini-1-5-pro",
    ],
    # OpenAI
    "gpt-4o": ["gpt-4o", "gpt4o"],
    "gpt-4-turbo": ["gpt-4-turbo", "gpt 4 turbo"],
    "o3": ["o3"],
    "o1": ["o1"],
}

# Pre-build a lookup: normalised_lower_variant → canonical_key
_NORM_LOOKUP: dict[str, str] = {}
for _key, _variants in MODEL_NORM.items():
    for _v in _variants:
        _NORM_LOOKUP[_v.lower().strip()] = _key


def normalise_model(raw: str) -> str | None:
    """Return the canonical model-key for *raw*, or None if unrecognised."""
    raw = raw.strip()
    # Direct lookup first
    candidate = _NORM_LOOKUP.get(raw.lower())
    if candidate:
        return candidate
    # Longest-prefix match: find all keys whose variant is a substring of raw
    raw_lower = raw.lower()
    matches: list[tuple[int, str]] = []
    for variant, key in _NORM_LOOKUP.items():
        if variant in raw_lower:
            matches.append((len(variant), key))
    if matches:
        matches.sort(key=lambda x: -x[0])
        return matches[0][1]
    return None


# ---------------------------------------------------------------------------
# PR-body helpers (reuse extract_section from pr_meta_checks style)
# ---------------------------------------------------------------------------

_HEADER_RE = re.compile(r"^\s*##\s+(.*\S)\s*$")
_FENCE_RE = re.compile(r"^\s*```")


def extract_section(md: str, header: str) -> str | None:
    """Return the body of a `## <header>` section, or None.

    Code-fence-aware (matches pr_meta_checks.extract_section).
    """
    collecting = False
    in_fence = False
    body: list[str] = []
    target = header.strip().lower()
    for line in md.splitlines():
        if _FENCE_RE.match(line):
            in_fence = not in_fence
            if collecting:
                body.append(line)
            continue
        m = _HEADER_RE.match(line)
        if m and not in_fence:
            if collecting:
                break
            htext = m.group(1).strip().lower()
            if htext == target or htext.startswith(target + " "):
                collecting = True
            continue
        if collecting:
            body.append(line)
    return "\n".join(body) if collecting else None


_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)


def _strip_html_comments(text: str) -> str:
    """Remove HTML comment blocks from text (handles single-line and multi-line)."""
    return _HTML_COMMENT_RE.sub("", text)


def extract_reviewer_model(pr_body: str) -> tuple[str | None, str | None]:
    """Return (raw_value, canonical_key) from the `Reviewer model:` line.

    Searches the `## Reviewer model` section body first (PR template layout),
    then falls back to a bare `Reviewer model:` line anywhere in the body.
    Returns (None, None) if the line is absent or blank.
    Returns (raw_value, None) if present but unrecognisable.

    HTML comments (<!-- ... -->) in the section are stripped before parsing, so
    the PR template's guidance comment does not interfere.
    """
    # Try section body first — strip HTML comments so template guidance is ignored
    section = extract_section(pr_body, "Reviewer model")
    if section is not None:
        clean_section = _strip_html_comments(section)
        for line in clean_section.splitlines():
            line = line.strip()
            if not line:
                continue
            # The section body starts with "Reviewer model: <value>" (explicit key) or
            # just "<value>" (bare value after the comment is stripped).
            m = re.match(r"(?:reviewer\s+model\s*:\s*)?(.*)", line, re.IGNORECASE)
            if m:
                raw = m.group(1).strip()
                if raw:
                    return raw, normalise_model(raw)
    # Fallback: bare `Reviewer model: <value>` line anywhere in body (outside a section)
    clean_body = _strip_html_comments(pr_body)
    for line in clean_body.splitlines():
        m = re.match(r"^\s*Reviewer\s+model\s*:\s*(.+)", line, re.IGNORECASE)
        if m:
            raw = m.group(1).strip()
            if raw:
                return raw, normalise_model(raw)
    return None, None


# ---------------------------------------------------------------------------
# Commit-trailer helpers
# ---------------------------------------------------------------------------

_CO_AUTHORED_RE = re.compile(
    r"Co-Authored-By:\s*(.+?)\s*<[^>]+>",
    re.IGNORECASE,
)


def extract_implementer_models(commits_text: str) -> list[str]:
    """Extract canonical model-keys from Co-Authored-By trailers in *commits_text*.

    *commits_text* is the concatenation of `git log --format=%B` for all commits
    in the PR (separated by NUL or newlines — any whitespace-delimited block works).
    Returns a deduplicated list preserving first-occurrence order.
    """
    seen: dict[str, None] = {}
    for m in _CO_AUTHORED_RE.finditer(commits_text):
        display_name = m.group(1).strip()
        key = normalise_model(display_name)
        if key and key not in seen:
            seen[key] = None
    return list(seen.keys())


# ---------------------------------------------------------------------------
# Reviewer allowlist (CI-stamped corroboration source — spoof-resistance hardening)
# ---------------------------------------------------------------------------


def load_reviewer_allowlist(path: str | None) -> set[str] | None:
    """Load the CODEOWNERS-controlled reviewer-model allowlist from *path*.

    The allowlist is the CI-stamped corroboration source for the self-declared
    `Reviewer model:` line: on a hard-gated PR the declared reviewer must be one of
    the governance-approved reviewer models listed in this committed, CODEOWNERS-guarded
    file.  This narrows the spoof surface from arbitrary free text to a 4-eyes-controlled
    set of values.

    Format: one entry per line (canonical key OR a display name that normalises via
    MODEL_NORM); `#` starts a comment; blank lines ignored.  Each parsed entry is run
    through ``normalise_model`` so display-name forms are accepted.

    Returns:
        A set of canonical model-keys, OR ``None`` when *path* is falsy / missing /
        unreadable.  ``None`` means corroboration is OFF (back-compat: the gate behaves
        exactly as it did before this hardening — used by the C-04 acceptance harness,
        which constructs PRs directly via ``evaluate`` without an allowlist).
    """
    if not path or not os.path.isfile(path):
        return None
    allow: set[str] = set()
    try:
        with open(path, encoding="utf-8") as fh:
            for raw_line in fh:
                line = raw_line.split("#", 1)[0].strip()
                if not line:
                    continue
                key = normalise_model(line)
                # Keep the literal too, in case a future key is not yet in MODEL_NORM;
                # the canonical key (when resolvable) is what evaluate() compares against.
                if key:
                    allow.add(key)
    except OSError:
        return None
    return allow or None


# ---------------------------------------------------------------------------
# Priority detection
# ---------------------------------------------------------------------------

_PRIORITY_LINE_RE = re.compile(
    # Markdown-tolerant priority detection.  Handles forms including:
    #   Priority: P0                      (plain)
    #   - Priority: P0                    (list item)
    #   > Priority: P1                    (blockquote)
    #   **Priority:** P0                  (bold label, value outside closing **)
    #   **Priority: P1**                  (entire field bolded)
    #   Priority: P0 (blocker text)       (trailing text)
    # Leading **, -, >, whitespace are all absorbed.
    # Optional ** between the colon and the P0/P1 token (for **Priority:** P0 style).
    r"^[\s>*\-]*Priority\*{0,2}\s*:\s*\*{0,2}\s*(P[01])\b",
    re.IGNORECASE | re.MULTILINE,
)
_LABEL_P01_RE = re.compile(r"^P[01]$", re.IGNORECASE)

# Path prefixes that trigger the hard gate regardless of labels/body (fail-closed safety net).
# Any PR touching security/governance-critical paths MUST have cross-model review.
SENSITIVE_PATH_PREFIXES: tuple[str, ...] = (
    "audit/acceptance/",
    ".github/",
    "scripts/ci/",
    "terraform/",
    "deploy/",
    "lib/scrubber",
    "lib/a2a/",
    "app/core/trust",
    "CLAUDE.md",
    "config/dead_code_entrypoints.txt",
    # The reviewer allowlist is itself a governance-integrity surface: any change to
    # the corroboration source must be cross-model reviewed (fail-closed self-guard).
    "config/c9-reviewer-allowlist.txt",
)


def _touches_sensitive_path(changed_files: list[str]) -> bool:
    """Return True if any changed file falls under a sensitive-path prefix."""
    for path in changed_files:
        path = path.strip()
        for prefix in SENSITIVE_PATH_PREFIXES:
            if path == prefix or path.startswith(prefix):
                return True
    return False


def is_p0_or_p1(pr_body: str, labels: list[str], changed_files: list[str] | None = None) -> bool:
    """Return True if this PR should be hard-gated (P0 or P1).

    Hard gate fires when ANY of the following is true:
      (a) GitHub label matches /^P[01]$/i
      (b) PR body contains a Priority: field (markdown-tolerant) whose value is P0 or P1
      (c) Changed files touch a sensitive/governance-critical path prefix
    """
    # (a) Check GitHub labels
    for label in labels:
        if _LABEL_P01_RE.match(label.strip()):
            return True
    # (b) Check Priority: field in body (markdown-tolerant)
    m = _PRIORITY_LINE_RE.search(pr_body)
    if m:
        return True
    # (c) Path-based fail-closed trigger
    if changed_files and _touches_sensitive_path(changed_files):
        return True
    return False


# ---------------------------------------------------------------------------
# Core evaluation
# ---------------------------------------------------------------------------


class GateResult(NamedTuple):
    ok: bool
    failures: list[str]
    warnings: list[str]
    advisory_only: bool  # True if P2+ (non-gating even on failure)


def evaluate(
    pr_body: str,
    commits_text: str,
    labels: list[str],
    changed_files: list[str] | None = None,
    reviewer_allowlist: set[str] | None = None,
) -> GateResult:
    """Evaluate the C9 reviewer-model-class rule.

    Args:
        pr_body:       Full PR body markdown.
        commits_text:  Concatenated commit-message text for all PR commits
                       (e.g. from `git log origin/main..HEAD --format=%B`).
        labels:        List of GitHub label names on the PR.
        changed_files: List of file paths changed in this PR (e.g. from
                       `git diff --name-only origin/<base>...HEAD`).
                       Used for the path-based fail-closed trigger.
        reviewer_allowlist: Optional set of canonical reviewer model-keys permitted by the
                       CODEOWNERS-controlled allowlist (see ``load_reviewer_allowlist``).
                       When provided (non-None), the self-declared `Reviewer model:` value
                       is CORROBORATED against it: a recognised-but-off-allowlist reviewer
                       hard-fails a P0/P1 PR.  This narrows the spoof surface from arbitrary
                       free text to a 4-eyes-controlled set.  When None (the C-04 acceptance
                       default), corroboration is OFF and behavior is unchanged (back-compat).

    Returns:
        GateResult with ok=True iff all checks pass.
    """
    failures: list[str] = []
    warnings: list[str] = []

    advisory_only = not is_p0_or_p1(pr_body, labels, changed_files)

    # 1. Extract reviewer
    reviewer_raw, reviewer_key = extract_reviewer_model(pr_body)
    if reviewer_raw is None:
        failures.append(
            "CLOSED-DEFAULT: `Reviewer model:` line is missing or blank in the PR body. "
            "Add it under `## Reviewer model` (e.g. `Reviewer model: claude-opus-4-8`)."
        )
    elif reviewer_key is None:
        failures.append(
            f"CLOSED-DEFAULT: `Reviewer model:` value '{reviewer_raw}' is not recognised. "
            f"Use a known model id (e.g. claude-opus-4-8, gemini-3-1-pro). "
            f"Known models: {sorted(MODEL_NORM.keys())}"
        )

    # 2. Extract implementers
    implementer_keys = extract_implementer_models(commits_text)
    if not implementer_keys:
        failures.append(
            "CLOSED-DEFAULT: no `Co-Authored-By:` trailer from a known LLM model found in "
            "any commit.  Ensure commits carry a trailer like "
            "`Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>`."
        )

    # 3. Only do model-equality checks if we have both parties
    if reviewer_key and implementer_keys:
        # MIXED-AUTHORSHIP check: reviewer must not have co-authored the code under review
        if reviewer_key in implementer_keys:
            failures.append(
                f"MIXED-AUTHORSHIP / INDEPENDENCE VIOLATION: the declared reviewer model "
                f"'{reviewer_key}' also appears as a code co-author in this PR's commits. "
                f"The reviewer must not have co-authored the code under review. "
                f"Use a genuinely independent reviewer model. "
                f"Implementer model(s): {implementer_keys}"
            )

    # 4. CI-STAMPED CORROBORATION (spoof-resistance hardening, P1-4):
    #    When a CODEOWNERS-controlled allowlist is supplied, the self-declared reviewer
    #    must be one of the governance-approved reviewer models.  A recognised-but-
    #    off-allowlist value is a documented spoof vector (an author typing an arbitrary
    #    "cross-vendor" string that no approved reviewer actually corresponds to) and is
    #    rejected.  When reviewer_allowlist is None, this layer is OFF (back-compat).
    if reviewer_allowlist is not None and reviewer_key and reviewer_key not in reviewer_allowlist:
        failures.append(
            f"REVIEWER NOT CORROBORATED: declared reviewer model '{reviewer_key}' is not in the "
            f"CODEOWNERS-controlled reviewer allowlist (config/c9-reviewer-allowlist.txt). "
            f"The `Reviewer model:` line is self-declared; it must match a governance-approved "
            f"reviewer model to be trusted. Approved: {sorted(reviewer_allowlist)}"
        )

    summary_parts = []
    if reviewer_key:
        summary_parts.append(f"reviewer={reviewer_key}")
    elif reviewer_raw:
        summary_parts.append(f"reviewer='{reviewer_raw}'(unrecognised)")
    else:
        summary_parts.append("reviewer=MISSING")
    if implementer_keys:
        summary_parts.append(f"implementer(s)={implementer_keys}")
    else:
        summary_parts.append("implementer(s)=NONE-FOUND")

    if advisory_only and failures:
        # Downgrade failures to warnings for non-P0/P1 PRs
        warnings.extend(failures)
        return GateResult(ok=True, failures=[], warnings=warnings, advisory_only=True)

    ok = len(failures) == 0
    return GateResult(ok=ok, failures=failures, warnings=warnings, advisory_only=advisory_only)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(
        description=(
            "C9 reviewer-model-class gate (C-04): enforce model-inequality for P0/P1 PRs. "
            "CLOSED-DEFAULT: fails if Reviewer model line missing/unparseable or no "
            "Co-Authored-By trailer found. SCOPE: hard-gates P0/P1; advisory for others."
        )
    )
    ap.add_argument("--pr-body", required=True, help="file containing the PR body markdown")
    ap.add_argument(
        "--commits",
        required=True,
        help=(
            "file containing concatenated commit messages for all PR commits "
            "(e.g. from `git log origin/main..HEAD --format=%%B`)"
        ),
    )
    ap.add_argument(
        "--labels",
        default="",
        help="comma-separated list of GitHub label names on this PR (e.g. 'P0,bug')",
    )
    ap.add_argument(
        "--changed-files",
        default="",
        help=(
            "file containing newline-separated list of changed file paths "
            "(e.g. from `git diff --name-only origin/<base>...HEAD > /tmp/changed.txt`). "
            "Used for the path-based fail-closed trigger on sensitive/governance-critical paths."
        ),
    )
    ap.add_argument(
        "--reviewer-allowlist",
        default=DEFAULT_REVIEWER_ALLOWLIST_PATH,
        help=(
            "path to the CODEOWNERS-controlled reviewer-model allowlist used to corroborate "
            "the self-declared `Reviewer model:` line "
            f"(default: {DEFAULT_REVIEWER_ALLOWLIST_PATH}). "
            "A recognised-but-off-allowlist reviewer hard-fails a P0/P1 PR. Pass '' or a "
            "missing path to disable corroboration (legacy behavior)."
        ),
    )
    args = ap.parse_args(argv)

    try:
        with open(args.pr_body) as fh:
            pr_body = fh.read()
    except OSError as e:
        print(f"::error::c9-reviewer-class-gate: cannot read PR body file: {e}")
        return 1

    try:
        with open(args.commits) as fh:
            commits_text = fh.read()
    except OSError as e:
        print(f"::error::c9-reviewer-class-gate: cannot read commits file: {e}")
        return 1

    changed_files: list[str] = []
    if args.changed_files:
        try:
            with open(args.changed_files) as fh:
                changed_files = [ln.strip() for ln in fh.read().splitlines() if ln.strip()]
        except OSError as e:
            print(f"::error::c9-reviewer-class-gate: cannot read changed-files file: {e}")
            return 1

    labels = [lb.strip() for lb in args.labels.split(",") if lb.strip()]
    reviewer_allowlist = load_reviewer_allowlist(args.reviewer_allowlist)
    result = evaluate(pr_body, commits_text, labels, changed_files, reviewer_allowlist)

    scope_tag = "advisory" if result.advisory_only else "hard-gate"
    print(f"== c9-reviewer-class-gate [{scope_tag}] ==")
    if reviewer_allowlist is not None:
        print(f"[info] reviewer corroboration ON — allowlist: {sorted(reviewer_allowlist)}")
    else:
        print("[info] reviewer corroboration OFF — no allowlist loaded")

    for w in result.warnings:
        print(f"[WARN] {w}")

    for f in result.failures:
        print(f"[FAIL] {f}")
        print(f"::error::c9-reviewer-class-gate: {f}")

    if result.ok:
        print("== c9-reviewer-class-gate: PASS ==")
        return 0
    else:
        print("== c9-reviewer-class-gate: FAIL ==")
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
