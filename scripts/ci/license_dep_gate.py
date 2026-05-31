#!/usr/bin/env python3
"""SP-00h — license + new-dep allowlist gate (blocking).

PRD §6 EPIC 0 SP-00h: block copyleft/unlicensed code in agent PRs; any dep the
agent ADDS must be pinned + pass a typosquat/allowlist check.

Note on scope (honest):
  - OSV/vuln scanning is NOT done here (OSV-Scanner workflow + Dependabot already
    cover that gap — this gate does NOT re-do it).
  - This gate covers three distinct checks:
      1. LICENSE check  — disallowed SPDX identifiers (GPL/AGPL/‘all rights reserved’/
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

# SPDX identifiers (or fragments) that are disallowed in agent-owned source.
# Lower-cased for comparison; matched as word-boundary substrings so
# 'GPL-2.0-or-later' and 'AGPL-3.0' are both caught.
_DISALLOWED_LICENSE_PATTERNS: list[re.Pattern] = [
    re.compile(r"\bGPL\b", re.IGNORECASE),  # GPL-2.0, GPL-3.0, LGPL-*
    re.compile(r"\bLGPL\b", re.IGNORECASE),
    re.compile(r"\bAGPL\b", re.IGNORECASE),
    re.compile(r"all rights reserved", re.IGNORECASE),
    # Unlicensed / proprietary markers
    re.compile(r"\bProprietary\b", re.IGNORECASE),
    re.compile(r"\bUNLICENSED\b", re.IGNORECASE),  # catch explicit UNLICENSED
]

# SPDX-License-Identifier line: if present, ONLY these identifiers are allowed.
_SPDX_HEADER_RE = re.compile(r"SPDX-License-Identifier:\s*([\w.+\-]+)", re.IGNORECASE)

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

# Regex: a dep specifier line inside pyproject.toml [dependencies] or extras.
# Matches lines like: `  "httpx>=0.27",` or `  'numpy==1.26',` or `  "bare-name",`
_DEP_LINE_RE = re.compile(
    r'^\+\s*[\'"]'
    r"([A-Za-z0-9_\-\.]+)"  # package name (group 1)
    r"([\s,;\[><=!@~'\"]|$)",  # version specifier, closing quote, or end-of-name
)

# Version specifier: must have one of ==, >=, ~=, <=, !=, @ (URL/git)
_VERSION_SPEC_RE = re.compile(r"(==|>=|~=|<=|!=|@)")


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
    """
    in_dep_section = False
    specs: list[str] = []

    for raw_line in pyproject_diff_text.splitlines():
        # Track section boundaries (diff context lines start with ' ' or '-')
        stripped = raw_line.lstrip("+ ")
        if stripped.startswith("["):
            # A TOML section header — toggle whether we're in a dep section.
            # Accept [project.dependencies], [project.optional-dependencies.*],
            # or bare variant names like [gcp], [a2a], [dev].
            in_dep_section = bool(
                re.match(
                    r"\[(project\.(?:optional-)?dependencies" r"|dev|gcp|a2a|test|extras)",
                    stripped,
                    re.IGNORECASE,
                )
            )
            continue

        # Only inspect ADDED lines ('+') inside a dep section
        if not raw_line.startswith("+"):
            continue
        if not in_dep_section:
            continue

        m = _DEP_LINE_RE.match(raw_line)
        if not m:
            continue

        # Extract the full specifier: everything from the opening quote to the
        # closing quote (or end of line).
        # Raw line looks like: +  "httpx>=0.27",   or +  'numpy',
        inner = raw_line[1:].strip()  # drop the leading '+'
        # Strip surrounding quote + trailing comma/whitespace
        inner = inner.strip("'\"")
        inner = inner.rstrip(",").strip()
        # Drop inline TOML comments
        if "#" in inner:
            inner = inner[: inner.index("#")].strip()
        inner = inner.strip("'\"")
        if inner:
            specs.append(inner)

    return specs


def is_pinned_dep(spec: str) -> bool:
    """Return True if the dep specifier carries a version constraint.

    Accepts: ==, >=, ~=, <=, !=, @ (URL / git ref).
    Rejects: bare name ('requests') or extras-only ('requests[security]').

    >>> is_pinned_dep('httpx>=0.27')
    True
    >>> is_pinned_dep('httpx==0.27.0')
    True
    >>> is_pinned_dep('httpx @ git+https://...')
    True
    >>> is_pinned_dep('httpx')
    False
    >>> is_pinned_dep('requests[security]')
    False
    """
    # Strip inline comments (text after '#') before checking for version specifiers,
    # so that a comment like "# >=2.28" is not mistaken for a real pin.
    spec_no_comment = spec.split("#")[0]
    return bool(_VERSION_SPEC_RE.search(spec_no_comment))


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
      1. If an SPDX-License-Identifier header is present, check it against the
         allowed SPDX set. Return the identifier if it is not allowed.
      2. If no SPDX header, scan the first 50 lines for disallowed patterns
         (GPL/AGPL/LGPL/‘all rights reserved’/Proprietary/UNLICENSED).
      3. If nothing found, return None (clean).

    NOTE: this function is NOT called for files in vendor/ or third-party
    directories — that filtering is done by the caller (CLI or workflow).
    """
    lines = file_text.splitlines()[:50]  # only scan the header region

    # 1. Check explicit SPDX-License-Identifier header
    for line in lines:
        m = _SPDX_HEADER_RE.search(line)
        if m:
            spdx_id = m.group(1).strip()
            if spdx_id not in _ALLOWED_SPDX:
                return spdx_id
            return None  # valid SPDX — no further scanning needed

    # 2. Pattern scan (first 50 lines)
    header_text = "\n".join(lines)
    for pat in _DISALLOWED_LICENSE_PATTERNS:
        match = pat.search(header_text)
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
      - An added source file containing a disallowed license marker.

    Not-a-hard-block (advisory, recorded in reasons with 'ADVISORY:' prefix):
      - A dep in the allowlist but lacking a pin (still a hard fail — pinning
        is always required regardless of allowlist status).
      - A dep NOT in the allowlist but not typosquatting (advisory: unknown dep,
        needs review for SP-G1 golden set).
    """
    if allowlist is None:
        allowlist = _BUILTIN_ALLOWLIST
    if popular is None:
        popular = _POPULAR_PACKAGES

    reasons: list[str] = []

    for spec in added_dep_specs:
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
            # Advisory: unknown dep, not a typosquat but not yet allowlisted.
            reasons.append(
                f"ADVISORY: added dep '{name}' is not in the SP-00h allowlist "
                f"(not a detected typosquat; add to _BUILTIN_ALLOWLIST once reviewed "
                f"— tracked under SP-G1)"
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
