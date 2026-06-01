import yaml
from pathlib import Path


def test_deploy_workflow_gate_structure():
    """Verify that phase-0a-deploy.yml has the SP-00d supply-chain gate logic."""
    wf_path = Path(".github/workflows/phase-0a-deploy.yml")
    assert wf_path.exists()

    with open(wf_path, "r") as f:
        wf = yaml.safe_load(f)

    jobs = wf.get("jobs", {})

    # 1. Check all required gate jobs exist in the workflow
    assert "build-and-push" in jobs
    assert "osv-scanner" in jobs
    assert "trivy" in jobs
    assert "scorecard" in jobs
    assert "deploy" in jobs

    # 2. Check that deploy job enforces the gate by needing all scans
    deploy_job = jobs["deploy"]
    needs = deploy_job.get("needs", [])
    assert "build-and-push" in needs
    assert "osv-scanner" in needs
    assert "trivy" in needs
    assert "scorecard" in needs

    # 3. Check build-and-push job signs images after push
    bp_steps = jobs["build-and-push"].get("steps", [])
    sign_steps = [s for s in bp_steps if s.get("run") and "cosign sign" in s["run"]]
    # We expect at least 2 signing commands (one for hermes, one for shell-sandbox)
    assert len(sign_steps) >= 2

    # 4. Check deploy job verifies signatures before deployment
    deploy_steps = deploy_job.get("steps", [])
    verify_step = next(
        (s for s in deploy_steps if s.get("name") == "Verify image signatures"), None
    )
    assert verify_step is not None
    assert "cosign verify" in verify_step["run"]
    assert "phase-0a-deploy" in verify_step["run"]

    # 5. Check VM script also has verification logic
    ssh_step = next((s for s in deploy_steps if s.get("name") == "Deploy via IAP SSH"), None)
    assert ssh_step is not None
    ssh_cmd = ssh_step.get("run", "")
    assert "cosign verify" in ssh_cmd
    assert "phase-0a-deploy" in ssh_cmd


def test_workflow_permissions():
    """Verify that the workflow jobs have the required permissions for OIDC/signing."""
    wf_path = Path(".github/workflows/phase-0a-deploy.yml")
    with open(wf_path, "r") as f:
        wf = yaml.safe_load(f)

    # build-and-push needs id-token: write for keyless signing via Fulcio/Rekor
    bp_job = wf["jobs"]["build-and-push"]
    assert bp_job.get("permissions", {}).get("id-token") == "write"

    # deploy needs id-token: write for GCP WIF auth
    deploy_job = wf["jobs"]["deploy"]
    assert deploy_job.get("permissions", {}).get("id-token") == "write"
