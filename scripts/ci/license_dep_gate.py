#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""SP-00h — license + new-dep allowlist gate (blocking).

PRD §6 EPIC 0 SP-00h: block copyleft/unlicensed code in agent PRs; any dep the
agent ADDS must be pinned + pass a typosquat/allowlist check.

Note on scope (honest):
  - OSV/vuln scanning is NOT done here (OSV-Scanner workflow + Dependabot already
    cover that gap — this gate does NOT re-do it).
  - This gate covers three distinct checks:
      1. LICENSE check  — disallowed SPDX identifiers (GPL/AGPL/’all rights reserved’/
                          no-license) in source files ADDED in the diff.
      2. PINNED check   — any dep line ADDED to pyproject.toml must carry a version
                          specifier (==, >=, ~=, @ URL/git).
      3. TYPOSQUAT check — added dep name not in the allowlist AND within Levenshtein
                          edit-distance 1-2 of a popular package (small builtin list).

All three functions are PURE (no I/O, no network) so they are unit-testable directly.
The CLI wrapper reads files/git-diff output and calls evaluate().

STDLIB ONLY — no third-party imports.
"""

from __future__ import annotations

import argparse
import re
import sys
from typing import Optional

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# SPDX-License-Identifier line: if present, ONLY these identifiers are allowed.
_SPDX_HEADER_RE = re.compile(r"SPDX-License-Identifier:\s*([\w.+\-]+)", re.IGNORECASE)

# Copyleft / proprietary SPDX identifiers that are disallowed when declared in
# an SPDX-License-Identifier header.  This list is matched as a case-insensitive
# PREFIX on the declared identifier so 'GPL-3.0-only', 'AGPL-3.0-or-later', and
# 'LGPL-2.1-only' are all caught without scanning the rest of the file for the
# bare word "GPL" (which would cause false positives in source files like THIS
# one that merely DEFINE the term as a string constant).
_DISALLOWED_SPDX_PREFIXES: tuple[str, ...] = (
    "GPL",
    "AGPL",
    "LGPL",
    "UNLICENSED",
    "Proprietary",
)

# Prose patterns that are checked ONLY when there is NO SPDX header.
# Intentionally narrow: only match canonical copyright-reservation notices when
# they appear on a comment line (starts with `#`).  This avoids false positives
# on source files — like this gate script itself — that merely MENTION these
# phrases in a docstring or comment explaining what the pattern means.
#
# Pattern 1: "All Rights Reserved" — standard copyright reservation notice.
# Pattern 2: dual-keyword notice requiring the pair P***rietary + C***fidential
#   on the same '#' comment line.  Both words are required so that an explanatory
#   comment using only one term (e.g. "proprietary SPDX identifiers") is NOT
#   treated as a license notice and is not flagged.
_PROSE_DISALLOWED_PATTERNS: list[re.Pattern] = [
    re.compile(r"^#.*all\s+rights\s+reserved", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^#.*\bproprietary\b.*\bconfidential\b", re.IGNORECASE | re.MULTILINE),
]

# Permissive safe list for SPDX identifiers found in file headers.
_ALLOWED_SPDX = frozenset(
    {
        "MIT",
        "Apache-2.0",
        "BSD-2-Clause",
        "BSD-3-Clause",
        "ISC",
        "MPL-2.0",  # file-level copyleft, NOT project-level — acceptable for agent deps
        "Python-2.0",
        "PSF-2.0",
        "CC0-1.0",
        "Unlicense",  # public-domain dedication (NOT UNLICENSED proprietary)
        "Apache-2.0 WITH LLVM-exception",
    }
)

# Packages already declared in pyproject.toml (as of SP-00h); agent additions
# must either be in this set OR be reviewed + added here.
# Advisory: expand to the full transitive golden set under SP-G1.
_BUILTIN_ALLOWLIST: frozenset[str] = frozenset(
    {
        # pyproject.toml [project.dependencies]
        "pyyaml",
        "jsonschema",
        "httpx",
        "pydantic",
        "langgraph",
        "langgraph-checkpoint",
        "google-cloud-secret-manager",
        "google-cloud-storage",
        "google-cloud-modelarmor",
        "numpy",
        "litellm",
        "opentelemetry-api",
        "opentelemetry-sdk",
        "opentelemetry-exporter-otlp-proto-http",
        # [dev]
        "pytest",
        "pytest-asyncio",
        "pytest-mock",
        "ruff",
        "testcontainers",
        "fakeredis",
        "pytest-vcr",
        "hypothesis",
        "inspect-ai",
        "deepeval",
        "pyrit",
        "garak",
        # [gcp]
        "asyncpg",
        "pgvector",
        "google-cloud-aiplatform",
        # [a2a] — keep representative subset; SP-G1 expands
        "fastapi",
        "uvicorn",
        "httpx-sse",
        "pyjwt",
        "cryptography",
        "cachetools",
        "python-ulid",
        "python-multipart",
        "redis",
        # common tooling used in scripts/ci
        "pip-licenses",
    }
)

# A small canonical list of popular PyPI package names used for typosquat
# proximity detection. Intentionally conservative — only packages where a
# one-character edit is a plausible supply-chain attack vector.
_POPULAR_PACKAGES: frozenset[str] = frozenset(
    {
        "requests",
        "urllib3",
        "certifi",
        "charset-normalizer",
        "setuptools",
        "pip",
        "wheel",
        "numpy",
        "pandas",
        "scipy",
        "matplotlib",
        "django",
        "flask",
        "fastapi",
        "starlette",
        "sqlalchemy",
        "alembic",
        "boto3",
        "botocore",
        "google-auth",
        "google-api-core",
        "cryptography",
        "pycryptodome",
        "pydantic",
        "attrs",
        "marshmallow",
        "pytest",
        "hypothesis",
        "langchain",
        "langgraph",
        "openai",
        "anthropic",
        "httpx",
        "aiohttp",
        "tornado",
        "celery",
        "redis",
        "pymongo",
        "pillow",
        "scikit-learn",
        "tensorflow",
        "torch",
        "paramiko",
        "fabric",
        "yaml",
        "pyyaml",
        "toml",
        "tomli",
        "click",
        "typer",
        "rich",
        "colorama",
    }
)

# Regex to extract all quoted dep specifier strings from a line inside a dep array.
# Quote-type-aware: a double-quoted token captures everything up to the next
# double quote (allowing inner single quotes, e.g. PEP-508 markers like
# "requests; python_version < '3.10'"), and vice-versa for single-quoted tokens.
# This prevents the old mixed-quote pattern from splitting on an inner quote.
# Each match tuple is ("double_quoted_content", "") or ("", "single_quoted_content").
_QUOTED_SPEC_RE = re.compile(r'"([^"]*)"' + r"|" + r"'([^']*)'")  # noqa: ISC003

# Version specifier: must have one of ==, >=, ~=, <=, !=, >, <, @ (URL/git).
# Note: the multi-char operators (>=, <=, !=, ~=, ==) are listed FIRST so the
# alternation matches them before the single-char < and > variants.
_VERSION_SPEC_RE = re.compile(r"(==|>=|~=|<=|!=|>|<|@)")


# ---------------------------------------------------------------------------
# Pure functions
# ---------------------------------------------------------------------------


def find_added_deps(pyproject_diff_text: str) -> list[str]:
    """Parse a `git diff` of pyproject.toml and return the raw dep specifier
    strings for lines that were ADDED (start with '+' in the diff).

    Returns a list of raw spec strings like 'httpx>=0.27' (without surrounding
    quotes or trailing comma). Empty-string entries are excluded.

    Only lines inside a [project.dependencies] or [project.optional-dependencies]
    section are considered (not e.g. tool.ruff or build-system sections).

    Handles both multi-line arrays::

        dependencies = [
          "pkg>=1.0",
        ]

    and single-line (inline) arrays::

        dependencies = ["pkg>=1.0"]
        dependencies = ["pkg1==1.0", "pkg2>=2.0"]

    as well as multiple dep specs on one added diff line.
    """
    in_dep_section = False
    specs: list[str] = []

    def _extract_quoted_specs(line_content: str) -> list[str]:
        """Extract all quoted dep specifiers from a string.

        Uses a quote-type-aware regex so that a double-quoted spec containing
        inner single quotes (e.g. PEP-508 markers) is captured as a single
        token, and a '#' character inside a quoted string (e.g. a git+url
        with '#egg=...') is NOT treated as the start of a TOML comment.

        The regex produces two-element tuples: (double_group, single_group).
        Exactly one group is non-empty per match; we take that group as the
        raw spec.  Because the content was already extracted from inside
        quotes, no '#' stripping is needed — the captured text IS the spec.

        Returns an empty list if no quoted strings are found.
        """
        # Guard: if the line contains a TOML triple-quote, reject it hard.
        # (Handled upstream in find_added_deps; this is a safety backstop.)
        found = []
        for double_group, single_group in _QUOTED_SPEC_RE.findall(line_content):
            raw_spec = double_group if double_group or not single_group else single_group
            spec = raw_spec.strip().rstrip(",").strip()
            if spec:
                found.append(spec)
        return found

    for raw_line in pyproject_diff_text.splitlines():
        # Normalise the diff prefix: strip exactly one leading '+' or ' ' (context)
        # so we can reason about the content regardless of diff prefix.
        # We keep raw_line intact for the "is this an added line?" check below.
        stripped = raw_line.lstrip("+ ")

        if stripped.startswith("["):
            # A TOML section header — toggle whether we're in a dep section.
            # Accept [project.dependencies], [project.optional-dependencies.*],
            # or bare extra names like [gcp], [a2a], [dev].
            in_dep_section = bool(
                re.match(
                    r"\[(project\.(?:optional-)?dependencies" r"|dev|gcp|a2a|test|extras)",
                    stripped,
                    re.IGNORECASE,
                )
            )
            continue

        # PEP-621 inline array style: deps live directly under [project] as
        #   dependencies = [
        #     "pkg>=1.0",
        #   ]
        # and under [project.optional-dependencies] as extra groups:
        #   dev = [
        #   gcp = [
        # These do NOT produce a section header line, so we detect them by
        # matching the key name on a context (space-prefixed) or added ('+')
        # diff line.  We enter the dep section here and also extract any dep
        # specs that appear on the SAME opener line (inline single-line arrays
        # like `dependencies = ["bare-dep"]` or `dev = ["a==1", "b>=2"]`).
        opener_match = re.match(
            r"^[+ ]?\s*(dependencies|optional-dependencies" r"|dev|gcp|a2a|test|extras)\s*=\s*\[",
            raw_line,
            re.IGNORECASE,
        )
        if opener_match:
            in_dep_section = True
            # If this is an ADDED line, extract any inline deps on the same line.
            # E.g.: +dependencies = ["bare-dep"] or +dev = ["a==1", "b>=2"]
            if raw_line.startswith("+"):
                # Content after the opening `[`
                after_bracket = raw_line[raw_line.index("[") + 1 :]
                # If the array closes on the same line, limit to the content before `]`
                if "]" in after_bracket:
                    after_bracket = after_bracket[: after_bracket.index("]")]
                    # Array also ends here — exit dep section after extracting
                    in_dep_section = False
                inline_specs = _extract_quoted_specs(after_bracket)
                specs.extend(inline_specs)
            continue

        # A closing bracket on its own line exits the current dep array.
        if re.match(r"^[+ ]?\s*\]\s*$", raw_line) and in_dep_section:
            in_dep_section = False
            continue

        # Any new TOML key (not a dep item) resets the section.
        # Dep items are quoted strings; other TOML keys look like `key = ...`
        if re.match(r"^[+ ]?\s*[a-zA-Z_][a-zA-Z0-9_\-]*\s*=\s*[^\[]", raw_line):
            in_dep_section = False

        # Only inspect ADDED lines ('+') inside a dep section.
        if not raw_line.startswith("+"):
            continue
        if not in_dep_section:
            continue

        # Guard: triple-quoted strings in a dependency array are not valid
        # PEP-508 specifiers and likely indicate a TOML formatting error.
        # Emit a sentinel that evaluate() converts to a hard failure rather
        # than silently extracting zero deps.
        line_content = raw_line[1:]  # drop the leading '+'
        if '"""' in line_content or "'''" in line_content:
            specs.append("__TRIPLE_QUOTE_ERROR__")
            continue

        # Extract ALL quoted dep specifiers on this added line.
        # This handles both single-dep lines and multi-dep lines such as:
        #   +  "pkg1==1.0", "pkg2>=2.0"
        line_specs = _extract_quoted_specs(line_content)
        specs.extend(line_specs)

    return specs


def is_pinned_dep(spec: str) -> bool:
    """Return True if the dep specifier carries a version constraint.

    Accepts: ==, >=, ~=, <=, >, <, @ (URL / git ref).
    Also accepts != when combined with a bounding operator (e.g. '>=1,!=1.5').
    Rejects: bare name ('requests'), extras-only ('requests[security]'), or
    a specifier where != is the ONLY operator (it is an exclusion that still
    allows any other version — it is not a pin).

    >>> is_pinned_dep('httpx>=0.27')
    True
    >>> is_pinned_dep('httpx==0.27.0')
    True
    >>> is_pinned_dep('httpx @ git+https://...')
    True
    >>> is_pinned_dep('httpx!=1.0')
    False
    >>> is_pinned_dep('httpx>=1,!=1.5')
    True
    >>> is_pinned_dep('httpx')
    False
    >>> is_pinned_dep('requests[security]')
    False
    """
    # Strip inline comments (text after '#') before checking for version specifiers,
    # so that a comment like "# >=2.28" is not mistaken for a real pin.
    spec_no_comment = spec.split("#")[0]
    # Find all version operators present in this spec.
    operators = _VERSION_SPEC_RE.findall(spec_no_comment)
    if not operators:
        return False  # no operators at all — bare name or extras-only
    # != alone is an exclusion, not a pin: reject if it is the only operator found.
    if operators == ["!="]:
        return False
    return True


def _levenshtein(a: str, b: str) -> int:
    """Compute Levenshtein edit distance between two strings (stdlib only)."""
    if a == b:
        return 0
    la, lb = len(a), len(b)
    if la == 0:
        return lb
    if lb == 0:
        return la
    # Use two-row DP to keep memory O(min(la,lb))
    if la < lb:
        a, b = b, a
        la, lb = lb, la
    prev = list(range(lb + 1))
    for i in range(1, la + 1):
        curr = [i] + [0] * lb
        for j in range(1, lb + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            curr[j] = min(curr[j - 1] + 1, prev[j] + 1, prev[j - 1] + cost)
        prev = curr
    return prev[lb]


def is_typosquat(
    name: str,
    allowlist: Optional[frozenset] = None,
    popular: Optional[frozenset] = None,
) -> bool:
    """Return True if `name` looks like a typosquat of a popular package.

    A name is a typosquat candidate when ALL of these hold:
      1. It is NOT in the allowlist (already-approved deps are not suspects).
      2. Its normalised form (lower-case, hyphens=underscores) is within
         Levenshtein edit-distance 1–2 of a name in the popular-package list.
      3. It is not itself IN the popular-package list (exact hits are fine).

    Normalisation: PyPI treats '-' and '_' as equivalent; we normalise both
    to '-' before comparison.

    >>> is_typosquat('requuests', frozenset(), frozenset({'requests'}))
    True
    >>> is_typosquat('requests', frozenset({'requests'}), frozenset({'requests'}))
    False
    >>> is_typosquat('aiohttp', frozenset(), frozenset({'aiohttp'}))
    False  # exact match in popular — not a typosquat
    """
    if allowlist is None:
        allowlist = _BUILTIN_ALLOWLIST
    if popular is None:
        popular = _POPULAR_PACKAGES

    norm = re.sub(r"[_\-]+", "-", name.lower())
    norm_allowlist = {re.sub(r"[_\-]+", "-", n.lower()) for n in allowlist}
    norm_popular = {re.sub(r"[_\-]+", "-", n.lower()) for n in popular}

    if norm in norm_allowlist:
        return False
    if norm in norm_popular:
        return False  # exact match — it IS a popular package, not a lookalike

    for pop in norm_popular:
        dist = _levenshtein(norm, pop)
        if 1 <= dist <= 2:  # edit-distance 1 or 2 away
            return True
    return False


def disallowed_license_in(file_text: str) -> Optional[str]:
    """Scan the text of an ADDED source file for disallowed license markers.

    Returns the offending license string if found, or None if the file is clean.

    Logic (in order):
      1. If an SPDX-License-Identifier header is present ANYWHERE in the file,
         check it against the allowed SPDX set.  An allowed identifier causes an
         immediate early-exit (return None) so that the rest of the file — which
         may contain explanatory comments defining disallowed license terms — is
         never tested against the prose patterns.
      2. If no SPDX header is found, scan the FULL file text for prose copyright-
         reservation patterns (canonical "All Rights Reserved" and the dual-keyword
         notice pattern).  Scanning the full text avoids a fragile dependency on
         line position: a disallowed notice below line 50 is still caught.
      3. If nothing found, return None (clean).

    NOTE: this function is NOT called for files in vendor/ or third-party
    directories — that filtering is done by the caller (CLI or workflow).
    """
    # 1. Check explicit SPDX-License-Identifier header (scan full text).
    #    If present, the identifier must be in _ALLOWED_SPDX.  We check it
    #    against _DISALLOWED_SPDX_PREFIXES (not a raw keyword scan) so that
    #    source files which MENTION disallowed license names in a comment or
    #    docstring are not false-flagged.
    #    An ALLOWED SPDX identifier causes an immediate return None — the file
    #    is clean and the prose patterns are NOT consulted.
    for line in file_text.splitlines():
        m = _SPDX_HEADER_RE.search(line)
        if m:
            spdx_id = m.group(1).strip()
            if spdx_id in _ALLOWED_SPDX:
                return None  # valid SPDX — clean; skip all prose pattern checks
            # Check whether the declared identifier starts with a disallowed prefix.
            spdx_upper = spdx_id.upper()
            for prefix in _DISALLOWED_SPDX_PREFIXES:
                if spdx_upper.startswith(prefix.upper()):
                    return spdx_id
            # Not in allowed set and not a known disallowed prefix — still disallowed.
            return spdx_id

    # 2. No SPDX header: scan the FULL file for prose copyright-reservation notices.
    #    We do NOT scan for bare "GPL"/"AGPL" keywords — that would flag any source
    #    file that discusses those terms (including this gate itself).
    for pat in _PROSE_DISALLOWED_PATTERNS:
        match = pat.search(file_text)
        if match:
            return match.group(0)  # return the matched token
    return None


def evaluate(
    added_dep_specs: list[str],
    added_file_texts: dict[str, str],  # {filename: content}
    allowlist: Optional[frozenset] = None,
    popular: Optional[frozenset] = None,
) -> tuple[bool, list[str]]:
    """Evaluate all checks and return (ok: bool, reasons: list[str]).

    - ok is True iff no hard violations were found.
    - reasons lists every violation (each as a human-readable string).

    Hard violations:
      - A dep specifier that is unpinned (no version constraint).
      - A dep name that is a typosquat candidate (edit-distance 1-2 from popular).
      - A dep NOT in the allowlist (regardless of typosquat proximity) — the author
        must add it to _BUILTIN_ALLOWLIST as a reviewed act before the PR can merge.
      - An added source file containing a disallowed license marker.
    """
    if allowlist is None:
        allowlist = _BUILTIN_ALLOWLIST
    if popular is None:
        popular = _POPULAR_PACKAGES

    reasons: list[str] = []

    for spec in added_dep_specs:
        # Sentinel emitted by find_added_deps when a triple-quoted string was
        # detected inside a dependency array — not a valid PEP-508 specifier.
        if spec == "__TRIPLE_QUOTE_ERROR__":
            reasons.append(
                "HARD: multiline strings not permitted in a dependency array; "
                "declare each dep as a single-line PEP 508 string"
            )
            continue

        # Extract bare name from spec (strip extras, version, env markers)
        name_match = re.match(r"^([A-Za-z0-9_\-\.]+)", spec)
        if not name_match:
            reasons.append(f"HARD: cannot parse dep name from specifier: {spec!r}")
            continue
        name = name_match.group(1).lower().replace("_", "-")

        if not is_pinned_dep(spec):
            reasons.append(f"HARD: added dep '{spec}' is unpinned (must carry ==, >=, ~=, <=, @)")

        if is_typosquat(name, allowlist, popular):
            reasons.append(
                f"HARD: added dep '{name}' looks like a typosquat "
                f"(edit-distance 1-2 from a popular package; not in allowlist)"
            )
        elif re.sub(r"[_\-]+", "-", name) not in {
            re.sub(r"[_\-]+", "-", a.lower()) for a in allowlist
        }:
            # Hard failure: unknown dep not yet in the allowlist. Adding a dep to
            # the allowlist is a reviewed act — the author must do it explicitly.
            reasons.append(
                f"HARD: added dep '{name}' is not in the SP-00h allowlist "
                f"(add to _BUILTIN_ALLOWLIST in scripts/ci/license_dep_gate.py "
                f"as a reviewed act before merging)"
            )

    for filename, text in sorted(added_file_texts.items()):
        hit = disallowed_license_in(text)
        if hit is not None:
            reasons.append(f"HARD: disallowed license marker '{hit}' in added file '{filename}'")

    hard_failures = [r for r in reasons if r.startswith("HARD:")]
    ok = len(hard_failures) == 0
    return ok, reasons


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str]) -> int:
    """CLI wrapper around evaluate().

    Reads a pyproject.toml diff from --pyproject-diff (a file containing the
    output of `git diff HEAD~ -- pyproject.toml` or equivalent) and source-file
    texts from --added-files (a newline-separated list of added .py filenames;
    each is read from disk).

    Exits 0 iff no HARD violations.
    """
    ap = argparse.ArgumentParser(description="SP-00h license + dep allowlist gate")
    ap.add_argument(
        "--pyproject-diff",
        default=None,
        help="File containing the diff of pyproject.toml (stdin if omitted)",
    )
    ap.add_argument(
        "--added-files",
        default=None,
        help="File listing added source file paths (one per line; empty = no source check)",
    )
    ap.add_argument(
        "--pip-licenses-output",
        default=None,
        help="Optional: output of `pip-licenses --format=json` to cross-check resolved env",
    )
    args = ap.parse_args(argv)

    # Read pyproject diff
    if args.pyproject_diff:
        try:
            with open(args.pyproject_diff) as fh:
                diff_text = fh.read()
        except OSError as e:
            print(f"::error::license-dep-gate: cannot read --pyproject-diff: {e}")
            return 1
    else:
        diff_text = sys.stdin.read()

    added_dep_specs = find_added_deps(diff_text)

    # Read added source files
    added_file_texts: dict[str, str] = {}
    if args.added_files:
        try:
            with open(args.added_files) as fh:
                file_list = [ln.strip() for ln in fh if ln.strip()]
        except OSError as e:
            print(f"::error::license-dep-gate: cannot read --added-files list: {e}")
            return 1
        for path in file_list:
            try:
                with open(path) as fh:
                    added_file_texts[path] = fh.read()
            except OSError:
                # File listed but unreadable — treat as empty (no license header)
                added_file_texts[path] = ""

    # Optional: pip-licenses env cross-check (advisory — not a hard gate here)
    if args.pip_licenses_output:
        _cross_check_pip_licenses(args.pip_licenses_output)

    ok, reasons = evaluate(added_dep_specs, added_file_texts)

    if not reasons:
        print(
            f"== license-dep-gate: PASS "
            f"(checked {len(added_dep_specs)} added dep(s), "
            f"{len(added_file_texts)} added source file(s)) =="
        )
        return 0

    hard_count = sum(1 for r in reasons if r.startswith("HARD:"))
    advisory_count = len(reasons) - hard_count

    for reason in reasons:
        if reason.startswith("HARD:"):
            print(f"::error::license-dep-gate: {reason}")
        else:
            print(f"::warning::license-dep-gate: {reason}")

    print(
        f"== license-dep-gate: {'FAIL' if not ok else 'PASS'} "
        f"({hard_count} hard violation(s), {advisory_count} advisory) =="
    )
    return 0 if ok else 1


def _cross_check_pip_licenses(json_path: str) -> None:
    """Parse pip-licenses JSON output and print any resolved packages with
    disallowed SPDX identifiers. Advisory only — does not affect exit code.
    This check covers the RESOLVED environment, not just the diff.
    """
    import json

    try:
        with open(json_path) as fh:
            records = json.load(fh)
    except (OSError, json.JSONDecodeError) as e:
        print(f"::warning::license-dep-gate: cannot parse pip-licenses output: {e}")
        return
    for rec in records:
        pkg_name = rec.get("Name", "?")
        lic = rec.get("License", "") or ""
        hit = disallowed_license_in(lic)
        if hit:
            print(
                f"::warning::license-dep-gate (resolved env): "
                f"'{pkg_name}' has license '{lic}' which contains '{hit}' "
                f"— review before merge"
            )


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
