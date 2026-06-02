#!/usr/bin/env python3
"""TDD tests for no_sa_key_gate.py — SP-00b (WIF-only CI auth enforcement).

Each test is self-contained and uses in-memory workflow snippets (NOT the real
repo) so tests are hermetic. Tests are ordered to mirror the red-green contract:
FAIL cases first, then PASS cases, then false-positive guards.
"""

from __future__ import annotations

from scripts.ci.no_sa_key_gate import scan

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_reader(workflows: dict[str, str]) -> dict[str, str]:
    """Return a file_reader dict for scan()."""
    return dict(workflows)


# ===========================================================================
# 1. FAIL — credentials_json: ${{ secrets.GCP_SA_KEY }} → FAIL
# ===========================================================================


def test_credentials_json_gcp_sa_key_fails() -> None:
    """A step with credentials_json: ${{ secrets.GCP_SA_KEY }} must FAIL.

    This is the canonical long-lived SA key pattern that SP-00b bans.
    """
    workflow = """\
name: deploy
on: [push]
jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@abc123

      - id: auth
        uses: google-github-actions/auth@v2
        with:
          credentials_json: ${{ secrets.GCP_SA_KEY }}
"""
    result = scan(file_reader=make_reader({".github/workflows/deploy.yml": workflow}))
    assert not result.ok, "credentials_json with GCP_SA_KEY must FAIL"
    assert result.findings
    reasons = " ".join(f.reason for f in result.findings)
    # Must mention either credentials_json or SA key
    assert "credentials_json" in reasons or "SA" in reasons.upper()


# ===========================================================================
# 2. FAIL — credentials_json with GOOGLE_CREDENTIALS secret → FAIL
# ===========================================================================


def test_credentials_json_google_credentials_fails() -> None:
    """credentials_json: ${{ secrets.GOOGLE_CREDENTIALS }} must FAIL."""
    workflow = """\
name: ci
on: [pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - id: auth
        uses: google-github-actions/auth@v1
        with:
          credentials_json: '${{ secrets.GOOGLE_CREDENTIALS }}'
"""
    result = scan(file_reader=make_reader({".github/workflows/ci.yml": workflow}))
    assert not result.ok, "credentials_json with GOOGLE_CREDENTIALS must FAIL"
    assert result.findings


# ===========================================================================
# 3. FAIL — explicit GCP_SA_KEY secret reference in env block → FAIL
# ===========================================================================


def test_sa_key_secret_in_env_block_fails() -> None:
    """A ${{ secrets.GCP_SA_KEY }} reference anywhere in a workflow must FAIL.

    Even if not directly in credentials_json (e.g. passed via env or step input),
    the presence of the known SA-key secret name signals a static key is in use.
    """
    workflow = """\
name: ci
on: [push]
jobs:
  test:
    runs-on: ubuntu-latest
    env:
      GOOGLE_APPLICATION_CREDENTIALS_JSON: ${{ secrets.GCP_SA_KEY }}
    steps:
      - run: gcloud auth activate-service-account --key-file <(echo $GOOGLE_APPLICATION_CREDENTIALS_JSON)
"""
    result = scan(file_reader=make_reader({".github/workflows/ci.yml": workflow}))
    assert not result.ok, "GCP_SA_KEY secret reference must FAIL"
    assert result.findings
    assert any("GCP_SA_KEY" in f.reason for f in result.findings)


# ===========================================================================
# 4. FAIL — GOOGLE_SA_KEY variant → FAIL
# ===========================================================================


def test_google_sa_key_variant_fails() -> None:
    """${{ secrets.GOOGLE_SA_KEY }} must FAIL (variant SA-key name)."""
    workflow = """\
name: ci
on: [push]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - id: auth
        uses: google-github-actions/auth@v2
        with:
          credentials_json: ${{ secrets.GOOGLE_SA_KEY }}
"""
    result = scan(file_reader=make_reader({".github/workflows/ci.yml": workflow}))
    assert not result.ok, "GOOGLE_SA_KEY must be detected and FAIL"
    assert result.findings


# ===========================================================================
# 5. FAIL — .json key-file credential reference → FAIL
# ===========================================================================


def test_json_key_file_credential_fails() -> None:
    """A step using key_file: sa-key.json must FAIL."""
    workflow = """\
name: ci
on: [push]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - name: Activate SA
        run: gcloud auth activate-service-account --key-file=sa-key.json
"""
    result = scan(file_reader=make_reader({".github/workflows/ci.yml": workflow}))
    assert not result.ok, "JSON key-file reference must FAIL"
    assert result.findings


# ===========================================================================
# 6. PASS — WIF workload_identity_provider only → PASS
# ===========================================================================


def test_wif_workload_identity_provider_passes() -> None:
    """A workflow using workload_identity_provider (WIF) must PASS.

    This is the canonical correct pattern. No credentials_json, no SA key.
    """
    workflow = """\
name: deploy
on: [push]
permissions:
  id-token: write
  contents: read
jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd  # v6

      - id: auth
        uses: google-github-actions/auth@7c6bc770dae815cd3e89ee6cdf493a5fab2cc093  # v3.0.0
        with:
          workload_identity_provider: ${{ vars.GCP_WIF_PROVIDER }}
          service_account: ${{ vars.GCP_DEPLOYER_SA }}
          create_credentials_file: true
"""
    result = scan(file_reader=make_reader({".github/workflows/deploy.yml": workflow}))
    assert result.ok, f"WIF-only workflow must PASS: {result.findings}"
    assert not result.findings


# ===========================================================================
# 7. PASS — create_credentials_file is NOT flagged (WIF output option)
# ===========================================================================


def test_create_credentials_file_not_flagged() -> None:
    """create_credentials_file: true is a WIF OUTPUT option, NOT a static key.

    This is the critical false-positive guard. WIF's google-github-actions/auth
    supports `create_credentials_file: true` to write a short-lived ADC file.
    It must NOT be flagged as a static SA key.
    """
    workflow = """\
name: ci
on: [pull_request]
permissions:
  id-token: write
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - id: auth
        uses: google-github-actions/auth@7c6bc770dae815cd3e89ee6cdf493a5fab2cc093
        with:
          workload_identity_provider: ${{ vars.GCP_WIF_PROVIDER }}
          service_account: ci-sa@my-project.iam.gserviceaccount.com
          create_credentials_file: true

      - name: Use the credentials file (gcloud SDK picks it up via ADC)
        run: gcloud info
"""
    result = scan(file_reader=make_reader({".github/workflows/ci.yml": workflow}))
    assert (
        result.ok
    ), f"create_credentials_file: true must NOT be flagged as an SA key: {result.findings}"
    assert not result.findings


# ===========================================================================
# 8. PASS — service_account: <email> is NOT flagged (WIF impersonation target)
# ===========================================================================


def test_service_account_email_not_flagged() -> None:
    """service_account: <email> in a WIF step is NOT a key — it is the SA to impersonate.

    Must NOT be flagged.
    """
    workflow = """\
name: ci
on: [push]
permissions:
  id-token: write
jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - id: auth
        uses: google-github-actions/auth@7c6bc770dae815cd3e89ee6cdf493a5fab2cc093
        with:
          workload_identity_provider: projects/123/locations/global/workloadIdentityPools/my-pool/providers/my-provider
          service_account: github-ci@my-project.iam.gserviceaccount.com
"""
    result = scan(file_reader=make_reader({".github/workflows/ci.yml": workflow}))
    assert result.ok, f"service_account email in WIF step must NOT be flagged: {result.findings}"
    assert not result.findings


# ===========================================================================
# 9. PASS — empty workflow dir (no files) → PASS (vacuously clean)
# ===========================================================================


def test_empty_workflow_dir_passes() -> None:
    """No workflow files → vacuously clean (PASS)."""
    result = scan(file_reader={})
    assert result.ok
    assert not result.findings


# ===========================================================================
# 10. PASS — workflow without any GCP auth step → PASS
# ===========================================================================


def test_workflow_without_gcp_auth_passes() -> None:
    """A workflow with no GCP auth at all must PASS."""
    workflow = """\
name: lint
on: [pull_request]
jobs:
  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd
      - run: pip install ruff && ruff check .
"""
    result = scan(file_reader=make_reader({".github/workflows/lint.yml": workflow}))
    assert result.ok, f"Non-GCP workflow must PASS: {result.findings}"
    assert not result.findings


# ===========================================================================
# 11. FAIL — multiple findings across workflows reported together
# ===========================================================================


def test_multiple_workflows_all_findings_reported() -> None:
    """When two workflows both have SA-key patterns, both findings are reported."""
    wf_a = """\
name: a
on: [push]
jobs:
  job:
    runs-on: ubuntu-latest
    steps:
      - uses: google-github-actions/auth@v2
        with:
          credentials_json: ${{ secrets.GCP_SA_KEY }}
"""
    wf_b = """\
name: b
on: [push]
jobs:
  job:
    runs-on: ubuntu-latest
    steps:
      - uses: google-github-actions/auth@v2
        with:
          credentials_json: ${{ secrets.GOOGLE_SA_KEY }}
"""
    result = scan(
        file_reader=make_reader(
            {
                ".github/workflows/a.yml": wf_a,
                ".github/workflows/b.yml": wf_b,
            }
        )
    )
    assert not result.ok
    # At least two findings (one per workflow); possibly more if SA_KEY secret pattern
    # is also flagged on the same line.
    assert len(result.findings) >= 2
    workflow_paths = {f.workflow for f in result.findings}
    assert ".github/workflows/a.yml" in workflow_paths
    assert ".github/workflows/b.yml" in workflow_paths


# ===========================================================================
# 12. PASS — mixed workflows: one WIF-only, one non-GCP — both clean
# ===========================================================================


def test_mixed_wif_and_non_gcp_both_pass() -> None:
    """A WIF workflow + a non-GCP workflow → both PASS, no findings."""
    wif_wf = """\
name: deploy
on: [push]
permissions:
  id-token: write
jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - id: auth
        uses: google-github-actions/auth@7c6bc770dae815cd3e89ee6cdf493a5fab2cc093
        with:
          workload_identity_provider: ${{ vars.GCP_WIF_PROVIDER }}
          service_account: ${{ vars.GCP_DEPLOYER_SA }}
"""
    non_gcp_wf = """\
name: test
on: [pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd
      - run: python -m pytest
"""
    result = scan(
        file_reader=make_reader(
            {
                ".github/workflows/deploy.yml": wif_wf,
                ".github/workflows/test.yml": non_gcp_wf,
            }
        )
    )
    assert result.ok, f"WIF + non-GCP workflows must both PASS: {result.findings}"
    assert not result.findings


# ===========================================================================
# 13. PASS — realistic phase-0a-deploy snippet (matches actual repo pattern)
# ===========================================================================


def test_realistic_phase_0a_snippet_passes() -> None:
    """A snippet mirroring the real phase-0a-deploy.yml WIF auth block must PASS."""
    snippet = """\
      - id: auth
        uses: google-github-actions/auth@7c6bc770dae815cd3e89ee6cdf493a5fab2cc093  # v3.0.0
        with:
          workload_identity_provider: ${{ vars.GCP_WIF_PROVIDER }}
          service_account: ${{ vars.GCP_DEPLOYER_SA }}
          create_credentials_file: true
          token_format: access_token
"""
    result = scan(file_reader=make_reader({".github/workflows/phase-0a-deploy.yml": snippet}))
    assert result.ok, f"Realistic phase-0a-deploy WIF snippet must PASS: {result.findings}"
    assert not result.findings
