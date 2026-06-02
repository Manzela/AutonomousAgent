#!/usr/bin/env python3
"""SP-00b — no-SA-key gate (WIF-only CI auth enforcement).

Scans `.github/workflows/*.yml` and FAILS if any `google-github-actions/auth`
step uses a long-lived service-account key for GCP authentication instead of
Workload Identity Federation (WIF).

FLAGGED — static SA-key patterns (long-lived credential):
  credentials_json: <anything that resolves to an SA JSON key>
    • credentials_json: ${{ secrets.GCP_SA_KEY }}
    • credentials_json: ${{ secrets.GOOGLE_CREDENTIALS }}
    • any credentials_json: value that is NOT the literal string
      'create_credentials_file: true' itself (which is a sibling key, not
      credentials_json).
  Explicit SA-key secret references:
    • ${{ secrets.GCP_SA_KEY }}
    • ${{ secrets.GOOGLE_SA_KEY }}
  A step referencing a *.json key file as a credential input.

NOT FLAGGED — WIF legitimate patterns:
  workload_identity_provider:  — OIDC-based WIF, the correct pattern.
  create_credentials_file: true/false — a WIF output option that writes a
    short-lived OIDC-minted Application Default Credential file, NOT a
    long-lived static JSON key. This is explicitly allowed.
  service_account: <email>  — specifies which SA to impersonate via WIF,
    NOT a key.

Design note: the gate is intentionally conservative on false negatives
(it might miss a cleverly obfuscated key reference) but must NOT false-positive
on the WIF-legitimate pattern `create_credentials_file`.

STDLIB ONLY — no third-party imports.
"""

from __future__ import annotations

import argparse
import glob
import os
import re
import sys
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Detection patterns
# ---------------------------------------------------------------------------

# Pattern 1: credentials_json: <value> in a workflow step.
# WIF does NOT use credentials_json at all; only static-key auth does.
# Note: we match `credentials_json:` followed by optional whitespace and a
# non-empty value.  We deliberately exclude lines that are only the key name
# with no value (bare `credentials_json:` with an empty value is unusual but
# safe — it resolves to nothing).
_CREDENTIALS_JSON_RE = re.compile(
    r"^\s*credentials_json\s*:\s*(\S.*)$",
    re.MULTILINE,
)

# Pattern 2: explicit GCP SA key secret references anywhere in a workflow.
# These indicate a long-lived key is being stored and used.
_SA_KEY_SECRET_RE = re.compile(
    r"\$\{\{\s*secrets\.(GCP_SA_KEY|GOOGLE_SA_KEY|GCP_SERVICE_ACCOUNT_KEY"
    r"|GOOGLE_SERVICE_ACCOUNT_KEY|SERVICE_ACCOUNT_KEY)\s*\}\}",
)

# Pattern 3: a .json file used as a GCP credential.
# Catches both:
#   YAML input form:  key_file: sa-key.json  /  credential_file: ./key.json
#   CLI flag form:    --key-file=sa-key.json  (in gcloud `run:` steps)
# Both indicate a static JSON key on disk, which is banned.
_JSON_KEY_FILE_RE = re.compile(
    r"(?:"
    # YAML key: value form
    r"(?:key_file|credentials|credential_file)\s*:\s*\S+\.json\b"
    r"|"
    # CLI --flag=value form
    r"--(?:key-file|credential-file)=\S+\.json\b"
    r")",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass
class Finding:
    workflow: str
    line_number: int
    line: str
    reason: str


@dataclass
class GateResult:
    ok: bool
    findings: list[Finding] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Core scanning logic
# ---------------------------------------------------------------------------


def _scan_workflow(path: str, content: str) -> list[Finding]:
    """Scan a single workflow file and return any SA-key findings."""
    findings: list[Finding] = []
    lines = content.splitlines()

    for lineno, line in enumerate(lines, start=1):
        stripped = line.strip()

        # --- Pattern 1: credentials_json: <value> ---
        m = _CREDENTIALS_JSON_RE.match(line)
        if m:
            value = m.group(1).strip()
            # Non-empty value → static key reference.
            # `create_credentials_file` is a SIBLING key in the YAML step, not
            # a value of credentials_json, so it won't appear after `credentials_json:`.
            if value:
                findings.append(
                    Finding(
                        workflow=path,
                        line_number=lineno,
                        line=line.rstrip(),
                        reason=(
                            "credentials_json: with a value detected — "
                            "this passes a long-lived SA JSON key. "
                            "Use workload_identity_provider (WIF) instead."
                        ),
                    )
                )

        # --- Pattern 2: explicit SA-key secret name ---
        m2 = _SA_KEY_SECRET_RE.search(line)
        if m2:
            findings.append(
                Finding(
                    workflow=path,
                    line_number=lineno,
                    line=line.rstrip(),
                    reason=(
                        f"SA-key secret reference '{m2.group(0)}' detected — "
                        "long-lived SA keys must not be used in CI. "
                        "Migrate to workload_identity_provider (WIF)."
                    ),
                )
            )

        # --- Pattern 3: *.json key-file credential ---
        m3 = _JSON_KEY_FILE_RE.search(stripped)
        if m3:
            findings.append(
                Finding(
                    workflow=path,
                    line_number=lineno,
                    line=line.rstrip(),
                    reason=(
                        f"JSON key-file credential '{m3.group(0)}' detected — "
                        "static key files must not be used in CI. "
                        "Use workload_identity_provider (WIF) instead."
                    ),
                )
            )

    return findings


def scan(
    workflow_dir: str = ".github/workflows",
    *,
    file_reader: dict[str, str] | None = None,
) -> GateResult:
    """Scan all workflow YAML files in *workflow_dir*.

    Args:
        workflow_dir: path to the directory containing ``*.yml`` workflow files.
        file_reader: optional dict mapping file paths to their contents (for
            testing without touching the filesystem).

    Returns:
        A :class:`GateResult` with ``ok=True`` iff no SA-key patterns were found.
    """
    all_findings: list[Finding] = []

    if file_reader is not None:
        # Hermetic test mode: use supplied contents, keyed by path.
        paths = sorted(file_reader.keys())
        for path in paths:
            content = file_reader[path]
            all_findings.extend(_scan_workflow(path, content))
    else:
        # Filesystem mode: glob real workflow files.
        pattern = os.path.join(workflow_dir, "*.yml")
        paths = sorted(glob.glob(pattern))
        if not paths:
            # Also accept .yaml extension
            pattern_yaml = os.path.join(workflow_dir, "*.yaml")
            paths = sorted(glob.glob(pattern_yaml))
        for path in paths:
            with open(path) as fh:
                content = fh.read()
            all_findings.extend(_scan_workflow(path, content))

    return GateResult(ok=len(all_findings) == 0, findings=all_findings)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(
        description=(
            "SP-00b: no-SA-key gate — fail if any GHA workflow uses a long-lived "
            "SA JSON key instead of Workload Identity Federation (WIF)."
        )
    )
    ap.add_argument(
        "--workflow-dir",
        default=".github/workflows",
        help="Directory containing GitHub Actions *.yml workflow files (default: .github/workflows)",
    )
    args = ap.parse_args(argv)

    result = scan(workflow_dir=args.workflow_dir)

    if result.ok:
        print("== no-sa-key-gate: PASS — all GCP auth uses WIF (no static SA keys found) ==")
        return 0

    print(f"== no-sa-key-gate: FAIL ({len(result.findings)} finding(s)) ==")
    for f in result.findings:
        print(f"::error file={f.workflow},line={f.line_number}::{f.reason}")
        print(f"  {f.workflow}:{f.line_number}: {f.line}")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
