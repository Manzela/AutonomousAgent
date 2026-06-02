"""tests/ci/_required_gates_drift.py — pure parser + drift-detection helpers
for the PD-18a branch-protection drift guard.

These helpers are intentionally side-effect-free and take explicit *text*
inputs (not file paths) so the drift detector can be exercised against synthetic
fixtures in a hermetic red-green test, independent of the live repo files.

Two source-of-truth artefacts are reconciled:

  * config/required_gates.txt           — the curated list of INTENDED hard /
                                           blocking gate check-run names.
  * terraform/phase-0a-gcp/branch_protection.tf
                                        — the required_status_checks.contexts
                                           that an operator actually applies.

Drift = an INTENDED gate that is absent from the terraform contexts. That is the
high-stakes failure: a blocking gate that the audit/PRD says must guard `main`
but which is not in the applied branch-protection contract, so merges are not
actually gated by it.
"""

from __future__ import annotations

import re

__all__ = [
    "parse_intended_gates",
    "parse_tf_contexts",
    "find_missing_gates",
]


def parse_intended_gates(gates_txt: str) -> list[str]:
    """Parse the INTENDED gate check-run names from required_gates.txt text.

    One name per line. Blank lines and lines whose first non-space character is
    '#' (comments) are ignored. Ordering and duplicates are preserved as-read so
    callers can report them verbatim; de-duplication is the caller's choice.
    """
    names: list[str] = []
    for line in gates_txt.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        names.append(stripped)
    return names


def parse_tf_contexts(tf_text: str) -> list[str]:
    """Parse required_status_checks.contexts from branch_protection.tf text.

    Locates the ``required_status_checks { ... }`` block and returns every
    double-quoted literal inside it. The HCL is author-controlled and the format
    is stable, so a regex over quoted literals is sufficient and avoids an HCL
    dependency.

    Raises ValueError if the block cannot be found, so a structural change to the
    .tf surfaces loudly instead of silently reporting "no contexts" (which would
    make the drift guard pass vacuously).
    """
    block_match = re.search(
        r"required_status_checks\s*\{(.+?)\n\s*\}",
        tf_text,
        re.DOTALL,
    )
    if not block_match:
        raise ValueError(
            "Could not locate a 'required_status_checks { ... }' block in the "
            "branch_protection.tf text. Has the file been restructured?"
        )
    block = block_match.group(1)
    return re.findall(r'"([^"]+)"', block)


def find_missing_gates(intended: list[str], tf_contexts: list[str]) -> list[str]:
    """Return INTENDED gates that are absent from the terraform contexts.

    This is the drift set. A non-empty result means at least one blocking gate
    that the audit intends to guard `main` is NOT in the applied
    branch-protection contract → DRIFT → the guard must go red.

    Order of the intended list is preserved; the comparison is exact-string (a
    typo or rename counts as drift, which is the desired behaviour).
    """
    tf_set = set(tf_contexts)
    return [name for name in intended if name not in tf_set]
