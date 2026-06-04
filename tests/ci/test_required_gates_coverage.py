"""
tests/ci/test_required_gates_coverage.py — drift-guard for required_status_checks coverage.

Three assertions (all hermetic — parse local files only, no GitHub API calls):

  (a) SYNC: every name in config/required_gates.txt appears verbatim in
      terraform/phase-0a-gcp/branch_protection.tf required_status_checks.contexts.

  (b) FIDELITY: every name in config/required_gates.txt corresponds to a real job
      `name:` emitted by some .github/workflows/*.yml  (no typos / drift).

  (c) SAFETY: no entry in config/required_gates.txt belongs to a workflow that is
      path-filtered or has continue-on-error at the JOB level (catches the
      "brick-all-merges" mistake where a non-universal gate becomes required).
"""

from __future__ import annotations

import re
import textwrap
from pathlib import Path

import pytest
import yaml

# ── Repo layout ─────────────────────────────────────────────────────────────

REPO_ROOT = Path(__file__).resolve().parents[2]
GATES_FILE = REPO_ROOT / "config" / "required_gates.txt"
TF_FILE = REPO_ROOT / "terraform" / "phase-0a-gcp" / "branch_protection.tf"
WORKFLOWS_DIR = REPO_ROOT / ".github" / "workflows"


# ── Helpers ──────────────────────────────────────────────────────────────────


def _load_gate_names() -> list[str]:
    """Return the ordered list of check-run names from config/required_gates.txt.

    Lines starting with '#' and blank lines are ignored.
    """
    names: list[str] = []
    for line in GATES_FILE.read_text().splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            names.append(stripped)
    return names


def _load_tf_contexts() -> list[str]:
    """Return the list of context strings from the required_status_checks block
    in branch_protection.tf.

    Parses the HCL contexts = [...] list by extracting quoted strings.
    This is intentionally simple (regex over quoted literals) — the .tf is
    author-controlled and the format is stable.
    """
    text = TF_FILE.read_text()
    # Find the required_status_checks { ... } block
    block_match = re.search(
        r"required_status_checks\s*\{(.+?)\}",
        text,
        re.DOTALL,
    )
    assert block_match, (
        f"Could not find required_status_checks block in {TF_FILE}.  "
        "Has the file been restructured?"
    )
    block = block_match.group(1)
    # Extract all double-quoted strings within the block
    return re.findall(r'"([^"]+)"', block)


def _load_workflow_job_names() -> dict[str, list[str]]:
    """Return a mapping of workflow-file-name → list of job `name:` values.

    Each job's `name:` is what GitHub Actions emits as the check-run name.
    """
    result: dict[str, list[str]] = {}
    for wf_path in sorted(WORKFLOWS_DIR.glob("*.yml")):
        try:
            data = yaml.safe_load(wf_path.read_text())
        except yaml.YAMLError:
            continue
        if not isinstance(data, dict):
            continue
        jobs = data.get("jobs", {})
        if not isinstance(jobs, dict):
            continue
        names = [job.get("name", job_id) for job_id, job in jobs.items() if isinstance(job, dict)]
        result[wf_path.name] = names
    return result


def _all_job_names(wf_jobs: dict[str, list[str]]) -> set[str]:
    """Flatten workflow→job-names mapping into a single set."""
    return {name for names in wf_jobs.values() for name in names}


def _workflow_for_job(job_name: str, wf_jobs: dict[str, list[str]]) -> str | None:
    """Return the workflow file name that declares a job with the given name."""
    for wf_file, names in wf_jobs.items():
        if job_name in names:
            return wf_file
    return None


def _workflow_is_path_filtered(wf_filename: str) -> bool:
    """Return True if the workflow has a pull_request trigger with a paths: filter."""
    wf_path = WORKFLOWS_DIR / wf_filename
    try:
        data = yaml.safe_load(wf_path.read_text())
    except yaml.YAMLError:
        return False
    if not isinstance(data, dict):
        return False
    on_block = data.get("on", data.get(True, {}))
    # Normalise: `on` can be a string, list, or dict
    if isinstance(on_block, str):
        return False
    if isinstance(on_block, list):
        return False
    if not isinstance(on_block, dict):
        return False
    pr_block = on_block.get("pull_request", {})
    if not isinstance(pr_block, dict):
        return False
    return "paths" in pr_block or "paths-ignore" in pr_block


def _job_has_continue_on_error(wf_filename: str, job_name: str) -> bool:
    """Return True if the named job has continue-on-error: true at the JOB level."""
    wf_path = WORKFLOWS_DIR / wf_filename
    try:
        data = yaml.safe_load(wf_path.read_text())
    except yaml.YAMLError:
        return False
    if not isinstance(data, dict):
        return False
    jobs = data.get("jobs", {})
    if not isinstance(jobs, dict):
        return False
    for job_id, job in jobs.items():
        if not isinstance(job, dict):
            continue
        if job.get("name", job_id) == job_name:
            return bool(job.get("continue-on-error", False))
    return False


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def gate_names() -> list[str]:
    assert GATES_FILE.exists(), f"config/required_gates.txt not found at {GATES_FILE}"
    names = _load_gate_names()
    assert names, "config/required_gates.txt is empty or has no non-comment lines"
    return names


@pytest.fixture(scope="module")
def tf_contexts() -> list[str]:
    assert TF_FILE.exists(), f"branch_protection.tf not found at {TF_FILE}"
    return _load_tf_contexts()


@pytest.fixture(scope="module")
def wf_jobs() -> dict[str, list[str]]:
    assert WORKFLOWS_DIR.exists(), f".github/workflows/ not found at {WORKFLOWS_DIR}"
    return _load_workflow_job_names()


# ── (a) SYNC: gates.txt ⊆ tf contexts ───────────────────────────────────────


def test_every_gate_in_tf_contexts(gate_names: list[str], tf_contexts: list[str]) -> None:
    """Every name in config/required_gates.txt must appear verbatim in
    branch_protection.tf required_status_checks.contexts.
    """
    tf_set = set(tf_contexts)
    missing = [name for name in gate_names if name not in tf_set]
    assert not missing, (
        "The following gates are listed in config/required_gates.txt but are MISSING "
        "from terraform/phase-0a-gcp/branch_protection.tf required_status_checks.contexts:\n"
        + textwrap.indent("\n".join(f"  - {m}" for m in missing), "  ")
        + "\n\nAdd them to the contexts list in branch_protection.tf."
    )


# ── (b) FIDELITY: gates.txt names exist as real job names ────────────────────


def test_every_gate_has_real_job(gate_names: list[str], wf_jobs: dict[str, list[str]]) -> None:
    """Every name in config/required_gates.txt must correspond to a real job
    `name:` in some .github/workflows/*.yml file (no typos / stale entries).
    """
    all_names = _all_job_names(wf_jobs)
    phantom = [name for name in gate_names if name not in all_names]
    assert not phantom, (
        "The following gates are listed in config/required_gates.txt but do NOT "
        "correspond to any job `name:` in .github/workflows/*.yml:\n"
        + textwrap.indent("\n".join(f"  - {p}" for p in phantom), "  ")
        + "\n\nEither fix the name (typo/drift) or remove the stale entry."
    )


# ── (c) SAFETY: no path-filtered or advisory gate in the list ────────────────


def test_no_path_filtered_gate(gate_names: list[str], wf_jobs: dict[str, list[str]]) -> None:
    """No gate in config/required_gates.txt may belong to a workflow whose
    pull_request trigger has a paths: or paths-ignore: filter.

    A path-filtered job does not run on every PR, so it would hang in "pending"
    forever on PRs that don't touch the filtered paths → blocks all such merges.
    """
    violations: list[str] = []
    for name in gate_names:
        wf_file = _workflow_for_job(name, wf_jobs)
        if wf_file and _workflow_is_path_filtered(wf_file):
            violations.append(f"{name!r}  (workflow: {wf_file})")
    assert not violations, (
        "The following gates are path-filtered (pull_request.paths:) but are listed "
        "in config/required_gates.txt.  They will hang PRs that don't touch those paths.\n"
        + textwrap.indent("\n".join(f"  - {v}" for v in violations), "  ")
        + "\n\nRemove them from config/required_gates.txt and add them to the EXCLUDED section."
    )


def test_no_advisory_job_level_gate(gate_names: list[str], wf_jobs: dict[str, list[str]]) -> None:
    """No gate in config/required_gates.txt may belong to a job with
    continue-on-error: true at the JOB level.

    A job-level continue-on-error means the check always "passes" from GitHub's
    perspective, so requiring it provides no enforcement and adds noise.
    """
    violations: list[str] = []
    for name in gate_names:
        wf_file = _workflow_for_job(name, wf_jobs)
        if wf_file and _job_has_continue_on_error(wf_file, name):
            violations.append(f"{name!r}  (workflow: {wf_file})")
    assert not violations, (
        "The following gates have continue-on-error: true at the JOB level but are "
        "listed in config/required_gates.txt.  They can never block a merge.\n"
        + textwrap.indent("\n".join(f"  - {v}" for v in violations), "  ")
        + "\n\nRemove them from config/required_gates.txt and add them to the EXCLUDED section."
    )
