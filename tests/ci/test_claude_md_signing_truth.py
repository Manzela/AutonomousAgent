"""Doc-truth gate for the CLAUDE.md "Commit signing" section (C-2 / P1-3).

Background
----------
CLAUDE.md previously claimed:

    Pre-commit hook `verify-signed-commits` enforces locally.

That hook does NOT exist. The only `repo: local` pre-commit hooks are
`block-plaintext-in-secrets` and `block-update-plan-scripts`
(.pre-commit-config.yaml). This test pins the corrected doc so the false
claim cannot regress, and it stays honest by deriving the ground truth from
.pre-commit-config.yaml at runtime rather than hard-coding a snapshot.

These are expected-green assertions: no skip, no xfail.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CLAUDE_MD = REPO_ROOT / "CLAUDE.md"
PRECOMMIT = REPO_ROOT / ".pre-commit-config.yaml"

# The hook name the doc historically (and falsely) referenced.
PHANTOM_HOOK = "verify-signed-commits"


def _precommit_hook_ids() -> set[str]:
    """Collect every `- id: <name>` declared in .pre-commit-config.yaml."""
    text = PRECOMMIT.read_text(encoding="utf-8")
    return set(re.findall(r"^\s*-\s*id:\s*(\S+)\s*$", text, flags=re.MULTILINE))


def test_phantom_hook_is_not_defined_in_precommit_config() -> None:
    """Ground truth: there is no `verify-signed-commits` pre-commit hook."""
    assert PHANTOM_HOOK not in _precommit_hook_ids(), (
        f"Unexpected: a {PHANTOM_HOOK!r} hook now exists in "
        ".pre-commit-config.yaml. Update CLAUDE.md and remove this guard."
    )


def test_claude_md_does_not_claim_phantom_local_signing_hook() -> None:
    """CLAUDE.md must not assert a local hook that does not exist.

    Guards the specific false claim
    "Pre-commit hook `verify-signed-commits` enforces locally."
    """
    body = CLAUDE_MD.read_text(encoding="utf-8")
    false_claim = re.compile(
        r"Pre-commit hook\s+`?verify-signed-commits`?\s+enforces\s+locally",
        flags=re.IGNORECASE,
    )
    assert false_claim.search(body) is None, (
        "CLAUDE.md falsely claims a local `verify-signed-commits` pre-commit "
        "hook enforces signing. No such hook exists in .pre-commit-config.yaml. "
        "Correct the 'Commit signing' section to reflect reality."
    )


def test_claude_md_signing_section_is_grounded_in_reality() -> None:
    """The 'Commit signing' section names the real local mechanism.

    Local signing is provided by `commit.gpgsign=true` (git config), not by a
    bespoke pre-commit hook. The corrected doc must reference the real lever.
    """
    body = CLAUDE_MD.read_text(encoding="utf-8")
    assert "## Commit signing" in body, "Commit signing section is missing."
    assert "commit.gpgsign" in body, (
        "The 'Commit signing' section must name the real local mechanism "
        "(git config commit.gpgsign), not a non-existent pre-commit hook."
    )
